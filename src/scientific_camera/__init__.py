"""Scientific camera capture. Native hardware libraries are optional."""

from .manager import CameraManager
from .pixels.export import export_frame
from .recording import RecordingReader, WriterOptions, repair_recording
from .types import (
    AcquisitionStats,
    CameraCapabilities,
    CameraConfiguration,
    CameraInfo,
    Frame,
    FrameMetadata,
    NumericRange,
    SnapshotResult,
)

__version__ = "0.1.0"
__all__ = [
    "CameraInfo",
    "CameraCapabilities",
    "CameraConfiguration",
    "Frame",
    "FrameMetadata",
    "AcquisitionStats",
    "SnapshotResult",
    "NumericRange",
    "CameraManager",
    "RecordingReader",
    "WriterOptions",
    "repair_recording",
    "export_frame",
]
