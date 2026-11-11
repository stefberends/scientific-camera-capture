"""Bounded asynchronous writer. Acquisition submits only owned frames, never disk work."""

from __future__ import annotations

import logging
import math
import os
import platform
import queue
import subprocess
import threading
import time
import uuid
import zlib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..errors import ConfigurationError, DiskWriteError, LifecycleError, RecordingOverflowError
from ..timestamps import anchor
from ..types import Frame
from .schema import RecordingLock, atomic_json, frame_row, json_bytes

if TYPE_CHECKING:
    from ..session import AcquisitionSession

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WriterOptions:
    queue_frames: int = 16
    queue_bytes: int = 128 * 1024 * 1024
    segment_bytes: int = 1024 * 1024 * 1024
    fsync_interval_seconds: float = 1.0
    # Explicit failure-injection controls, not alternate recording paths.
    write_delay_seconds: float = 0.0
    fail_after_bytes: int | None = None

    def __post_init__(self):
        if min(self.queue_frames, self.queue_bytes, self.segment_bytes) < 1:
            raise ConfigurationError("writer limits must be positive")
        if any(
            not math.isfinite(v) or v < 0
            for v in (self.fsync_interval_seconds, self.write_delay_seconds)
        ):
            raise ConfigurationError("writer delays must be nonnegative")
        if self.fail_after_bytes is not None and self.fail_after_bytes < 0:
            raise ConfigurationError("failure byte offset must be nonnegative")


class RecordingHandle:
    def __init__(
        self,
        stream: AcquisitionSession,
        directory: Path | str,
        options: WriterOptions | None = None,
    ):
        self.stream = stream
        self.directory = Path(directory).absolute()
        self.options = options or WriterOptions()
        self.state = "opening"
        self.error: Exception | None = None
        self.stream_id = str(uuid.uuid4())
        self.first_acquisition_index = -1
        self.clock_anchor = anchor()
        self._queue: queue.Queue[Frame] = queue.Queue(self.options.queue_frames)
        self._pending_bytes = 0
        self._pending_frames = 0
        self._link_users = 0
        self._finish = threading.Event()
        self._failed = threading.Event()
        self._stop_lock = threading.Lock()
        self._io_lock = threading.RLock()
        self._durable_condition = threading.Condition()
        self._last_written = -1
        self._last_durable = -1
        self._written_frames = 0
        self._bytes = 0
        self._segment_number = -1
        self._segment = None
        self._offset = 0
        self._last_sync = time.monotonic()
        self._started = time.monotonic()
        self._files: list[Any] = []
        self._lock_file = None
        self.directory.mkdir(parents=True, exist_ok=False)
        try:
            (self.directory / "raw").mkdir()
            self._lock_file = RecordingLock(self.directory)
            commit = None
            try:
                revision = subprocess.run(
                    ["git", "rev-parse", "HEAD"],
                    cwd=Path(__file__).resolve().parents[3],
                    capture_output=True,
                    text=True,
                    timeout=1,
                )
                if revision.returncode == 0:
                    commit = revision.stdout.strip()
            except (OSError, subprocess.TimeoutExpired) as exc:
                logger.debug("source_revision_unavailable", extra={"reason": str(exc)})
            firmware = None
            # start_recording owns the stream lifecycle lock; do not invert it with
            # CameraSession.close's camera -> stream lock order.
            if "DeviceFirmwareVersion" in stream._capabilities.readonly_features:
                firmware = stream.camera.device.read_feature("DeviceFirmwareVersion")
            self.manifest = {
                "schema_version": 1,
                "app_version": "0.1.0",
                "git_commit": commit,
                "session_id": self.stream_id,
                "stream_id": self.stream_id,
                "state": "recording",
                "camera": asdict(stream.camera.info),
                "firmware": firmware,
                "configuration": asdict(stream.camera.configuration),
                "intended_fps": stream.camera.configuration.frame_rate,
                "machine_architecture": platform.machine(),
                "os": platform.platform(),
                "raw_format": "original payload bytes",
                "checksum": "IEEE CRC32",
                "host_utc_anchor": self.clock_anchor,
                "timestamp_domains": "host receipt monotonic ns; device ticks when reported; not synchronized",
                "durability": f"raw then index fsync every {self.options.fsync_interval_seconds}s and on stop/snapshot",
                "warnings": [
                    "host receipt is not exposure start; rolling shutter has row-dependent timing"
                ],
                "writer_options": asdict(self.options),
            }
            atomic_json(self.directory / "manifest.json", self.manifest)
            self._index = self._open("frames.jsonl")
            self._events = self._open("events.jsonl")
            self._snapshots = self._open("snapshots.jsonl")
        except BaseException:
            for file in self._files:
                file.close()
            if self._lock_file:
                self._lock_file.close()
            raise
        self.state = "recording"
        self._thread = threading.Thread(target=self._run, name="scicam-writer", daemon=True)

    def _open(self, name):
        output = (self.directory / name).open("xb", buffering=0)
        self._files.append(output)
        return output

    def submit(self, frame: Frame):
        """Called with the stream condition held; never waits or writes to disk."""
        if self._failed.is_set() or self._finish.is_set():
            self.stream._metrics.increment("application_drops")
            return
        if (
            self._pending_frames >= self.options.queue_frames
            or self._pending_bytes + len(frame.payload) > self.options.queue_bytes
        ):
            self.stream._metrics.increment("application_drops")
            self.fail(
                RecordingOverflowError("bounded recording queue saturated; recording is partial")
            )
            return
        self._pending_frames += 1
        self._pending_bytes += len(frame.payload)
        self._queue.put_nowait(frame)
        self.stream._metrics.increment("enqueued_frames")
        self.stream._metrics.values["queue_high_water_mark"] = max(
            self.stream._metrics.values["queue_high_water_mark"], self._pending_frames
        )

    def fail(self, error: Exception):
        """Set failure state without disk I/O; safe in acquisition worker."""
        with self.stream._condition:
            if not self._failed.is_set():
                self.error = error
                self.state = "failed"
                self.stream.state = "failed"
                self.stream.camera.state = "failed"
                self.stream._metrics.values["errors"].append(str(error))
                self._failed.set()
                self._finish.set()
        with self._durable_condition:
            self._durable_condition.notify_all()

    @staticmethod
    def _write_all(output, payload: bytes):
        data = memoryview(payload)
        while data:
            count = output.write(data)
            if not count:
                raise OSError("short write made no progress")
            data = data[count:]

    def _sync(self):
        if self._segment:
            os.fsync(self._segment.fileno())
        os.fsync(self._index.fileno())
        with self._durable_condition:
            self._last_durable = self._last_written
            self._durable_condition.notify_all()
        self._last_sync = time.monotonic()

    def _write_frame(self, frame: Frame):
        payload = frame.payload
        if self._segment is None or (
            self._offset and self._offset + len(payload) > self.options.segment_bytes
        ):
            if self._segment:
                self._sync()
                self._segment.close()
                self._files.remove(self._segment)
            self._segment_number += 1
            self._segment_name = f"segment_{self._segment_number:06d}.bin"
            self._segment = self._open("raw/" + self._segment_name)
            self._offset = 0
        if self.options.fail_after_bytes is not None:
            available = self.options.fail_after_bytes - self._bytes
            if available < len(payload):
                self._write_all(self._segment, payload[: max(0, available)])
                raise OSError("injected disk full / partial raw write")
        self._write_all(self._segment, payload)
        row = frame_row(
            frame.metadata, self.stream_id, self._segment_name, self._offset, zlib.crc32(payload)
        )
        self._write_all(self._index, json_bytes(row))
        self._offset += len(payload)
        self._bytes += len(payload)
        self._written_frames += 1
        self._last_written = frame.metadata.acquisition_index
        with self.stream._condition:
            self.stream._metrics.increment("recorded_frames")
            self.stream._metrics.increment("recorded_bytes", len(payload))
        if (
            self._link_users
            or time.monotonic() - self._last_sync >= self.options.fsync_interval_seconds
        ):
            self._sync()

    def _run(self):
        try:
            while not self._finish.is_set() or not self._queue.empty() or self._link_users:
                try:
                    frame = self._queue.get(timeout=0.05)
                except queue.Empty:
                    with self._io_lock:
                        if self._link_users or (
                            time.monotonic() - self._last_sync
                            >= self.options.fsync_interval_seconds
                        ):
                            self._sync()
                    continue
                try:
                    if self.options.write_delay_seconds:
                        # Interruptible delay so injected pressure cannot hang shutdown.
                        self._failed.wait(self.options.write_delay_seconds)
                    with self._io_lock:
                        self._write_frame(frame)
                except Exception:
                    with self.stream._condition:
                        if self._last_written < frame.metadata.frame_id:
                            self.stream._metrics.increment("application_drops")
                    raise
                finally:
                    with self.stream._condition:
                        self._pending_frames -= 1
                        self._pending_bytes -= len(frame.payload)
                    self._queue.task_done()
        except Exception as exc:
            logger.exception("recording_write_failed", extra={"directory": str(self.directory)})
            self.fail(DiskWriteError(f"recording is recoverable: {exc}"))
            while True:
                try:
                    abandoned = self._queue.get_nowait()
                except queue.Empty:
                    break
                with self.stream._condition:
                    self._pending_frames -= 1
                    self._pending_bytes -= len(abandoned.payload)
                    self.stream._metrics.increment("application_drops")
                self._queue.task_done()
        finally:
            try:
                with self._io_lock:
                    self._sync()
                    duration = max(time.monotonic() - self._started, 1e-9)
                    self.summary = {
                        "schema_version": 1,
                        "state": "failed" if self.error else "complete",
                        "recorded_frames": self._written_frames,
                        "recorded_bytes": self._bytes,
                        "duration_seconds": duration,
                        "disk_mb_s": self._bytes / duration / 1_000_000,
                        "error": str(self.error) if self.error else None,
                        "stats": asdict(self.stream.stats()),
                    }
                    atomic_json(self.directory / "summary.json", self.summary)
                    self.manifest["state"] = self.summary["state"]
                    atomic_json(self.directory / "manifest.json", self.manifest)
            except Exception as exc:
                self.fail(DiskWriteError(f"could not finalize; use repair: {exc}"))
            finally:
                for file in self._files:
                    if not file.closed:
                        file.close()
                if self._lock_file:
                    self._lock_file.close()
                with self._durable_condition:
                    self._durable_condition.notify_all()

    def wait_durable(self, frame_id: int, timeout: float = 5):
        deadline = time.monotonic() + timeout
        with self._durable_condition:
            while self._last_durable < frame_id:
                if self.error:
                    raise self.error
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise DiskWriteError("timed out waiting for indexed frame durability")
                self._durable_condition.wait(min(remaining, 0.05))

    def append_link(self, kind: str, value: dict):
        """Caller thread only; flush raw and index before publishing a durable snapshot link."""
        try:
            with self._io_lock:
                if self._index.closed or self.error:
                    raise LifecycleError("recorder is no longer writable")
                self._sync()
                output = self._events if kind == "event" else self._snapshots
                self._write_all(output, json_bytes(value))
                os.fsync(output.fileno())
        except OSError as exc:
            error = DiskWriteError(f"could not save {kind}: {exc}")
            self.fail(error)
            raise error from exc

    def stop(self, *, raise_on_error: bool = True):
        with self._stop_lock:
            with self.stream._condition:
                if self.stream._recorder is self:
                    self.stream._recorder = None
                self._finish.set()
            self._thread.join(10.0)
            if self._thread.is_alive():
                raise DiskWriteError("writer shutdown timed out; recording requires repair")
            if self.error:
                self.state = "failed"
                if raise_on_error:
                    raise self.error
            else:
                self.state = "closed"
                if self.stream.state == "recording":
                    self.stream.state = "streaming"
                    self.stream.camera.state = "streaming"

    def __enter__(self):
        return self

    def __exit__(self, exc_type, *_):
        self.stop(raise_on_error=exc_type is None)
