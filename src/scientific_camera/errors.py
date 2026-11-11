"""Typed operational failures. Failed SCC sessions remain inspectable/recoverable."""


class CameraError(RuntimeError):
    """Base public error."""


class BackendUnavailableError(CameraError):
    pass


class CameraNotFoundError(CameraError):
    pass


class CameraInUseError(CameraError):
    pass


class UnsupportedFeatureError(CameraError):
    pass


class ConfigurationError(CameraError):
    pass


class CameraDisconnectedError(CameraError):
    pass


class CaptureTimeoutError(CameraError):
    pass


class IncompleteFrameError(CameraError):
    pass


class RecordingOverflowError(CameraError):
    pass


class DiskWriteError(CameraError):
    pass


class RecordingCorruptError(CameraError):
    pass


class NoMatchingFrameError(CameraError):
    pass


class LifecycleError(CameraError):
    pass
