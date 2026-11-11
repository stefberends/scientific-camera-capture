"""Thread-safe stream selection. Disk work happens on the requesting thread."""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .errors import CaptureTimeoutError, ConfigurationError, LifecycleError, NoMatchingFrameError
from .recording.schema import atomic_json, json_bytes
from .timestamps import anchored_utc
from .types import Frame, SnapshotResult

if TYPE_CHECKING:
    from .recording.writer import RecordingHandle
    from .session import AcquisitionSession


@dataclass
class PendingSnapshot:
    after_index: int
    frame: Frame | None = None
    recorder: RecordingHandle | None = None


def save_raw(frame: Frame, destination: Path) -> tuple[str, ...]:
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    raw = destination.with_suffix(".raw")
    sidecar = destination.with_suffix(".json")
    if sidecar.exists():
        raise FileExistsError(sidecar)
    # Never overwrite a previous scientific image.
    with raw.open("xb") as output:
        output.write(frame.payload)
        output.flush()
        import os

        os.fsync(output.fileno())
    atomic_json(
        sidecar,
        {
            "metadata": asdict(frame.metadata),
            "representation": "original raw payload",
            "raw_file": raw.name,
        },
    )
    return str(raw.absolute()), str(sidecar.absolute())


def snapshot(
    stream: AcquisitionSession,
    *,
    policy: str = "next",
    save: bool = True,
    event_monotonic_ns: int | None = None,
    window_ns: int = 100_000_000,
    timeout: float = 5.0,
    output: Path | None = None,
    _on_registered=None,
    _acquisition_mode="stream_selection",
) -> SnapshotResult:
    if policy not in ("next", "nearest") or timeout <= 0 or window_ns < 0:
        raise ConfigurationError("invalid snapshot policy, window or timeout")
    identifier = str(uuid.uuid4())
    requested = time.monotonic_ns()
    deadline = time.monotonic() + timeout
    pending = None
    recorder = None
    try:
        with stream._condition:
            if stream._stopping.is_set():
                raise LifecycleError("snapshot requires a live stream")
            if len(stream._snapshots) >= stream._max_snapshot_requests:
                raise ConfigurationError("maximum concurrent snapshot requests exceeded")
            pending = PendingSnapshot(stream._last_index)
            stream._snapshots[identifier] = pending
            if policy == "nearest":
                if event_monotonic_ns is None:
                    raise ConfigurationError("nearest selection requires event_monotonic_ns")
                frames = [f for f in stream._ring if f.metadata.status == "complete"]
                if not frames:
                    raise NoMatchingFrameError("no complete retained frames")
                pending.frame = min(
                    frames,
                    key=lambda f: (
                        abs(f.metadata.host_received_monotonic_ns - event_monotonic_ns),
                        f.metadata.acquisition_index,
                    ),
                )
                if (
                    abs(pending.frame.metadata.host_received_monotonic_ns - event_monotonic_ns)
                    > window_ns
                ):
                    raise NoMatchingFrameError("nearest retained frame is outside requested window")
                stream._pin_snapshot_recorder(pending)
        if _on_registered:
            _on_registered()
        with stream._condition:
            while pending.frame is None:
                if stream._error:
                    raise stream._error
                if stream._stopping.is_set():
                    raise LifecycleError("stream stopped before snapshot arrived")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise CaptureTimeoutError("snapshot timed out waiting for next complete frame")
                stream._condition.wait(remaining)
            frame = pending.frame
            recorder = pending.recorder
        result = SnapshotResult(
            identifier,
            frame.metadata.acquisition_index,
            _acquisition_mode,
            policy,
            requested,
            frame.metadata.host_received_monotonic_ns,
            association_delta_ns=(frame.metadata.host_received_monotonic_ns - event_monotonic_ns)
            if event_monotonic_ns is not None
            else None,
            frame=frame,
        )
        if save:
            if recorder and output is None:
                recorder.wait_durable(result.frame_id, max(0.001, deadline - time.monotonic()))
                result = replace(result, reference=f"{recorder.directory}#frame={result.frame_id}")
                recorder.append_link("snapshot", snapshot_row(result, recorder.stream_id))
            else:
                files = save_raw(frame, output or stream.snapshot_directory / identifier)
                result = replace(result, files=files)
                atomic_json(
                    Path(files[1]),
                    {
                        "metadata": asdict(frame.metadata),
                        "snapshot": snapshot_row(result, None),
                        "representation": "original raw payload",
                    },
                )
        return result
    except Exception:
        with stream._condition:
            stream._metrics.increment("snapshot_failures")
        raise
    finally:
        with stream._condition:
            stream._snapshots.pop(identifier, None)
            if pending and pending.recorder:
                pending.recorder._link_users -= 1
                stream._condition.notify_all()


def snapshot_row(result: SnapshotResult, stream_id: str | None) -> dict:
    # Avoid dataclasses.asdict(result), which would copy a potentially enormous frame.
    return {
        "schema_version": 1,
        "snapshot_id": result.snapshot_id,
        "stream_id": stream_id,
        "frame_id": result.frame_id,
        "acquisition_mode": result.acquisition_mode,
        "policy": result.policy,
        "request_monotonic_ns": result.request_monotonic_ns,
        "arrival_monotonic_ns": result.arrival_monotonic_ns,
        "timing_provenance": result.timing_provenance,
        "uncertainty_ns": result.uncertainty_ns,
        "association_delta_ns": result.association_delta_ns,
        "reference": result.reference,
        "files": result.files,
    }


def mark_event(
    stream: AcquisitionSession,
    name: str,
    payload: dict[str, Any],
    event_monotonic_ns: int | None = None,
) -> dict:
    stamp = time.monotonic_ns() if event_monotonic_ns is None else event_monotonic_ns
    if not name or not isinstance(stamp, int):
        raise ConfigurationError("event requires a name and integer monotonic timestamp")
    with stream._condition:
        if stream._stopping.is_set():
            raise LifecycleError("event requires a live stream")
        recorder = stream._recorder
        if recorder:
            recorder._link_users += 1
        frames = [f for f in stream._ring if f.metadata.status == "complete"]
        nearest = (
            min(frames, key=lambda f: abs(f.metadata.host_received_monotonic_ns - stamp))
            if frames
            else None
        )
        if (
            nearest
            and abs(nearest.metadata.host_received_monotonic_ns - stamp) > stream.event_window_ns
        ):
            nearest = None
        event: dict[str, Any] = {
            "schema_version": 1,
            "event_id": str(uuid.uuid4()),
            "name": name,
            "host_monotonic_ns": stamp,
            "payload": payload,
            "timestamp_uncertainty_ns": None,
            "frame_id": nearest.metadata.frame_id if nearest else None,
            "missing_association_reason": None
            if nearest
            else "no retained frame within host receipt window",
            "association_provenance": "host receipt only; exposure timing unknown",
        }
    try:
        event["utc_best_effort"] = anchored_utc(
            recorder.clock_anchor if recorder else stream.clock_anchor, stamp
        )
        json_bytes(event)  # Validate caller payload before mutating any log.
        if recorder:
            # Association may be outside this recorder; do not publish a dangling frame reference.
            if event["frame_id"] is not None:
                if event["frame_id"] < recorder.first_acquisition_index:
                    event["frame_id"] = None
                    event["missing_association_reason"] = "retained frame predates recording"
                else:
                    recorder.wait_durable(event["frame_id"])
            recorder.append_link("event", event)
        else:
            directory = stream.snapshot_directory / "events"
            directory.mkdir(parents=True, exist_ok=True)
            atomic_json(directory / (event["event_id"] + ".json"), event)
        return event
    finally:
        if recorder:
            with stream._condition:
                recorder._link_users -= 1
                stream._condition.notify_all()
