"""Synchronous sessions over a dedicated acquisition worker and a bounded retention ring."""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .backend import CameraDevice
from .errors import CaptureTimeoutError, ConfigurationError, LifecycleError
from .metrics import Metrics
from .timestamps import anchor
from .types import AcquisitionStats, CameraCapabilities, CameraConfiguration, Frame, SnapshotResult

if TYPE_CHECKING:
    from .pixels.demosaic import PreviewWorker
    from .recording.writer import RecordingHandle

logger = logging.getLogger(__name__)


class CameraSession:
    def __init__(self, device: CameraDevice, release):
        self.device = device
        self.info = device.info
        self._release = release
        self._lock = threading.RLock()
        self._stream: AcquisitionSession | None = None
        self.state = "opening"
        self.state = "configured"

    def _ensure_open(self):
        if self.state == "closed":
            raise LifecycleError("camera is closed")

    def capabilities(self) -> CameraCapabilities:
        with self._lock:
            self._ensure_open()
            return self.device.capabilities()

    @property
    def configuration(self) -> CameraConfiguration:
        return self.device.configuration

    def read_feature(self, name: str) -> Any:
        with self._lock:
            self._ensure_open()
            return self.device.read_feature(name)

    def configure(self, config: CameraConfiguration) -> CameraConfiguration:
        with self._lock:
            self._ensure_open()
            if self._stream is not None:
                raise ConfigurationError("stop stream before changing configuration")
            return self.device.configure(config)

    def start_stream(
        self,
        *,
        retention_frames: int = 16,
        retention_bytes: int = 64 * 1024 * 1024,
        max_snapshot_requests: int = 8,
        snapshot_directory: Path = Path("snapshots"),
        event_window_ns: int = 100_000_000,
    ) -> AcquisitionSession:
        with self._lock:
            self._ensure_open()
            if self._stream is not None:
                raise LifecycleError("camera already has an active stream")
            stream = AcquisitionSession(self, retention_frames, retention_bytes)
            if max_snapshot_requests < 1 or event_window_ns < 0:
                raise ConfigurationError("invalid snapshot request/window limit")
            stream._max_snapshot_requests = max_snapshot_requests
            stream.snapshot_directory = Path(snapshot_directory)
            stream.event_window_ns = event_window_ns
            self.device.start()
            self._stream = stream
            self.state = "streaming"
            stream._thread.start()
            return stream

    def capture_single(
        self,
        trigger: str = "free_run",
        *,
        timeout: float = 5.0,
        save: bool = False,
        output: Path | None = None,
    ) -> SnapshotResult:
        from .errors import UnsupportedFeatureError
        from .snapshots import snapshot

        with self._lock:
            self._ensure_open()
            if self._stream:
                raise LifecycleError("capture_single requires an idle camera; use stream.snapshot")
            if trigger not in ("software", "free_run"):
                raise UnsupportedFeatureError(f"unsupported independent capture trigger {trigger}")
            caps = self.capabilities()
            if trigger == "software" and "Software" not in caps.trigger_sources:
                raise UnsupportedFeatureError("software trigger not advertised")
            original = self.configuration
            try:
                if trigger == "software":
                    self.configure(
                        CameraConfiguration(
                            trigger_mode="On",
                            trigger_source="Software",
                            trigger_selector="FrameStart",
                        )
                    )
                elif "Off" in caps.trigger_modes:
                    self.configure(CameraConfiguration(trigger_mode="Off"))
                elif original.trigger_mode not in (None, "Off"):
                    raise UnsupportedFeatureError("cannot disable camera trigger")
                with self.start_stream() as stream:
                    result = snapshot(
                        stream,
                        timeout=timeout,
                        save=save,
                        output=output,
                        _on_registered=self.device.software_trigger
                        if trigger == "software"
                        else None,
                        _acquisition_mode="software_trigger"
                        if trigger == "software"
                        else "single_free_run",
                    )
                    return replace(
                        result,
                        acquisition_mode="software_trigger"
                        if trigger == "software"
                        else "single_free_run",
                    )
            finally:
                if not self._stream:
                    self.configure(original)

    def close(self):
        with self._lock:
            if self.state == "closed":
                return
            try:
                if self._stream:
                    self._stream.stop()
            finally:
                try:
                    self.device.close()
                finally:
                    self.state = "closed"
                    self._release()

    def __enter__(self):
        self._ensure_open()
        return self

    def __exit__(self, *_):
        self.close()


class AcquisitionSession:
    def __init__(self, camera: CameraSession, retention_frames: int, retention_bytes: int):
        if retention_frames < 1 or retention_bytes < 1:
            raise ConfigurationError("retention frame and byte limits must be positive")
        self.camera = camera
        self.state = "streaming"
        self._condition = threading.Condition(threading.RLock())
        self._stop_lock = threading.RLock()
        self._stopping = threading.Event()
        self._ring: deque[Frame] = deque()
        self._retention_frames = retention_frames
        self._retention_bytes = retention_bytes
        self._ring_bytes = 0
        self._last_index = -1
        self._error: Exception | None = None
        self._capabilities = camera.capabilities()
        self._metrics = Metrics(self._capabilities.frame_id_modulus)
        self._recorder: RecordingHandle | None = None
        self._snapshots: dict = {}
        self._max_snapshot_requests = 8
        self.snapshot_directory = Path("snapshots")
        self.event_window_ns = 100_000_000
        self.clock_anchor = anchor()
        self._preview: PreviewWorker | None = None
        self._thread = threading.Thread(target=self._run, name="scicam-acquisition", daemon=True)

    def _run(self):
        try:
            while not self._stopping.is_set():
                frame = self.camera.device.receive(0.1)
                if frame is None:
                    continue
                with self._condition:
                    self._last_index += 1
                    frame = Frame(
                        replace(frame.metadata, acquisition_index=self._last_index), frame.payload
                    )
                    self._metrics.increment("delivered_frames")
                    self._metrics.increment(
                        "complete_frames"
                        if frame.metadata.status == "complete"
                        else "incomplete_frames"
                    )
                    self._metrics.counter(frame.metadata.device_frame_id)
                    if self._recorder:
                        self._recorder.submit(frame)
                    else:
                        self._metrics.increment("deliberately_discarded")
                    if frame.metadata.status == "complete":
                        for pending in self._snapshots.values():
                            if (
                                pending.frame is None
                                and frame.metadata.frame_id > pending.after_index
                            ):
                                pending.frame = frame
                                self._pin_snapshot_recorder(pending)
                    if len(frame.payload) <= self._retention_bytes:
                        self._ring.append(frame)
                        self._ring_bytes += len(frame.payload)
                    else:
                        self._metrics.increment("retention_evictions")
                    while (
                        len(self._ring) > self._retention_frames
                        or self._ring_bytes > self._retention_bytes
                    ):
                        self._ring_bytes -= len(self._ring.popleft().payload)
                        self._metrics.increment("retention_evictions")
                    self._condition.notify_all()
        except Exception as exc:
            logger.exception("acquisition_failed", extra={"camera_id": self.camera.info.id})
            with self._condition:
                self._error = exc
                self.state = "failed"
                self.camera.state = "failed"
                self._metrics.values["errors"].append(str(exc))
                if self._recorder:
                    self._recorder.fail(exc)
                self._condition.notify_all()
        finally:
            self._stopping.set()
            with self._condition:
                self._metrics.stopped = time.monotonic()
                self._condition.notify_all()

    def next_frame(self, *, after_index: int | None = None, timeout: float = 5.0) -> Frame:
        """Block for a complete frame. Slow consumers may miss evicted frames; recorder cannot."""
        deadline = time.monotonic() + timeout
        with self._condition:
            after = self._last_index if after_index is None else after_index
            while True:
                for frame in self._ring:
                    if (
                        frame.metadata.acquisition_index > after
                        and frame.metadata.status == "complete"
                    ):
                        return frame
                if self._error:
                    raise self._error
                if self._stopping.is_set():
                    raise LifecycleError("stream is stopped")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise CaptureTimeoutError("no retained complete frame before timeout")
                self._condition.wait(remaining)

    def stats(self) -> AcquisitionStats:
        with self._condition:
            return self._metrics.snapshot(self.camera.device.statistics())

    def _pin_snapshot_recorder(self, pending):
        recorder = self._recorder
        if (
            recorder
            and not recorder.error
            and pending.frame is not None
            and pending.frame.metadata.frame_id >= recorder.first_acquisition_index
        ):
            pending.recorder = recorder
            recorder._link_users += 1

    def snapshot(self, **kwargs: Any) -> SnapshotResult:
        from .snapshots import snapshot

        return snapshot(self, **kwargs)

    def mark_event(self, name: str, payload: dict, event_monotonic_ns: int | None = None) -> dict:
        from .snapshots import mark_event

        return mark_event(self, name, payload, event_monotonic_ns)

    def start_preview(self, *, max_dimension: int = 256):
        from .pixels.demosaic import PreviewWorker

        with self._condition:
            if self._preview is not None or self._stopping.is_set():
                raise LifecycleError("preview requires a live stream without an existing preview")
            self._preview = PreviewWorker(self, max_dimension)
            return self._preview

    def start_recording(self, directory: Path | str, *, options=None) -> RecordingHandle:
        from .recording.writer import RecordingHandle

        # Serialize recorder lifecycle without holding the acquisition condition over disk I/O.
        with self._stop_lock:
            with self._condition:
                if self._stopping.is_set() or self._recorder:
                    raise LifecycleError(
                        "recording requires a live stream without an active recorder"
                    )
            recorder = RecordingHandle(self, directory, options)
            with self._condition:
                recorder.first_acquisition_index = self._last_index + 1
                self._recorder = recorder
                self.state = "recording"
                self.camera.state = "recording"
                recorder._thread.start()
            return recorder

    def stop(self):
        with self._stop_lock:
            if self.state == "closed":
                return
            self._stopping.set()
            with self._condition:
                self._condition.notify_all()
            self._thread.join(2.0)
            if self._thread.is_alive():
                raise LifecycleError("backend receive exceeded shutdown deadline")
            if self._preview:
                self._preview.stop()
            stop_error = None
            try:
                self.camera.device.stop()
            except Exception as exc:
                stop_error = exc
            try:
                if self._recorder:
                    self._recorder.stop()
            finally:
                self.state = "closed"
                self.camera._stream = None
                if self.camera.state != "closed":
                    self.camera.state = "configured"
            if stop_error:
                raise stop_error

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.stop()
