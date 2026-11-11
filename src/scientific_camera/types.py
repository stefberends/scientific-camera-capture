"""Public immutable values. Host timestamps are monotonic ns, never exposure-start claims."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class CameraInfo:
    id: str
    backend: str
    vendor: str | None = None
    model: str | None = None
    serial: str | None = None
    transport: str = "unknown"
    display_label: str = ""


@dataclass(frozen=True)
class NumericRange:
    minimum: float
    maximum: float
    increment: float | None = None
    unit: str | None = None


@dataclass(frozen=True)
class CameraCapabilities:
    pixel_formats: tuple[str, ...] = ()
    width: NumericRange | None = None
    height: NumericRange | None = None
    offset_x: NumericRange | None = None
    offset_y: NumericRange | None = None
    frame_rate: NumericRange | None = None
    exposure_us: NumericRange | None = None
    gain: NumericRange | None = None
    trigger_modes: tuple[str, ...] = ()
    trigger_sources: tuple[str, ...] = ()
    writable_features: tuple[str, ...] = ()
    readonly_features: tuple[str, ...] = ()
    device_timestamp_frequency_hz: float | None = None
    frame_id_modulus: int | None = None


@dataclass(frozen=True)
class CameraConfiguration:
    width: int | None = None
    height: int | None = None
    offset_x: int | None = None
    offset_y: int | None = None
    pixel_format: str | None = None
    exposure_us: float | None = None
    gain: float | None = None
    gain_unit: str | None = None
    frame_rate: float | None = None
    acquisition_mode: str | None = None
    trigger_mode: str | None = None
    trigger_source: str | None = None
    trigger_selector: str | None = None
    trigger_activation: str | None = None


@dataclass(frozen=True)
class FrameMetadata:
    camera_id: str
    acquisition_index: int
    device_frame_id: int | None
    status: str
    host_received_monotonic_ns: int
    width: int
    height: int
    stride: int | None
    payload_bytes: int
    pixel_format: str
    pixel_format_code: int | None = None
    host_capture_monotonic_ns: int | None = None
    device_timestamp: int | None = None
    device_timestamp_unit: str | None = None
    device_timestamp_frequency_hz: float | None = None
    timestamp_validity: str = "host_receipt_only"
    exposure_us: float | None = None
    gain: float | None = None
    gain_unit: str | None = None
    chunks: dict[str, Any] = field(default_factory=dict)

    @property
    def frame_id(self) -> int:
        """Unambiguous session-local acquisition index, distinct from device counter."""
        return self.acquisition_index


@dataclass(frozen=True)
class Frame:
    metadata: FrameMetadata
    payload: bytes

    def __post_init__(self) -> None:
        object.__setattr__(self, "payload", bytes(self.payload))
        if len(self.payload) != self.metadata.payload_bytes:
            raise ValueError("metadata payload_bytes does not match owned payload")


@dataclass(frozen=True)
class AcquisitionStats:
    delivered_frames: int = 0
    complete_frames: int = 0
    incomplete_frames: int = 0
    recorded_frames: int = 0
    enqueued_frames: int = 0
    device_reported_losses: int | None = None
    transport_reported_losses: int | None = None
    transport_buffer_failures: int | None = None
    transport_buffer_underruns: int | None = None
    frame_id_gaps: int = 0
    frame_id_discontinuities: int = 0
    application_drops: int = 0
    deliberately_discarded: int = 0
    retention_evictions: int = 0
    snapshot_failures: int = 0
    preview_skips: int = 0
    queue_high_water_mark: int = 0
    actual_fps: float = 0.0
    recorded_bytes: int = 0
    disk_mb_s: float = 0.0
    integrity: str = "unverifiable"
    errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class SnapshotResult:
    snapshot_id: str
    frame_id: int
    acquisition_mode: str
    policy: str
    request_monotonic_ns: int
    arrival_monotonic_ns: int
    timing_provenance: str = "host_receipt; exposure timing unknown"
    uncertainty_ns: int | None = None
    association_delta_ns: int | None = None
    reference: str | None = None
    files: tuple[str, ...] = ()
    frame: Frame | None = field(default=None, repr=False)
