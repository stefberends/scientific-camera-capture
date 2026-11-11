"""Session-local counters. Caller serializes access with the stream condition."""

from dataclasses import asdict
from time import monotonic

from .types import AcquisitionStats


class Metrics:
    def __init__(self, modulus: int | None):
        self.values = asdict(AcquisitionStats())
        self.values["errors"] = []
        self.started = monotonic()
        self.stopped: float | None = None
        self.modulus = modulus
        self.previous: int | None = None

    def increment(self, key: str, count: int = 1) -> None:
        self.values[key] += count

    def counter(self, current: int | None) -> None:
        if current is not None and self.previous is not None:
            delta = current - self.previous
            if self.modulus:
                delta %= self.modulus
            if delta != 1:
                # Large backwards jumps/restarts are ambiguous, never proven frame losses.
                if delta > 1 and (self.modulus is None or delta < self.modulus // 2):
                    self.increment("frame_id_gaps", delta - 1)
                else:
                    self.increment("frame_id_discontinuities")
        self.previous = current

    def snapshot(self, telemetry: dict) -> AcquisitionStats:
        result = dict(self.values)
        result.update(telemetry)
        elapsed = max((self.stopped or monotonic()) - self.started, 1e-9)
        result["actual_fps"] = result["delivered_frames"] / elapsed
        result["disk_mb_s"] = result["recorded_bytes"] / elapsed / 1_000_000
        result["errors"] = tuple(result["errors"])
        if (
            result["errors"]
            or result["application_drops"]
            or result["incomplete_frames"]
            or result["transport_buffer_failures"]
            or result["transport_buffer_underruns"]
        ):
            result["integrity"] = "partial"
        elif any(
            result[k] is None for k in ("device_reported_losses", "transport_reported_losses")
        ):
            result["integrity"] = "unverifiable"
        elif any(
            result[k]
            for k in (
                "device_reported_losses",
                "transport_reported_losses",
                "frame_id_gaps",
                "frame_id_discontinuities",
            )
        ):
            result["integrity"] = "partial"
        else:
            result["integrity"] = "verified"
        return AcquisitionStats(**result)
