import pytest

from scientific_camera import CameraConfiguration, CameraManager
from scientific_camera.errors import (
    CameraInUseError,
    CameraNotFoundError,
    ConfigurationError,
    LifecycleError,
)


def test_lifecycle_and_exclusive_open(manager):
    camera = manager.open("simulated")
    with pytest.raises(CameraInUseError):
        manager.open("simulated")
    stream = camera.start_stream()
    with pytest.raises(LifecycleError):
        camera.start_stream()
    with pytest.raises(ConfigurationError):
        camera.configure(CameraConfiguration(width=80))
    stream.stop()
    stream.stop()
    with camera.start_stream() as stream:
        assert stream.next_frame().payload
    camera.close()
    camera.close()
    with manager.open("simulated"):
        pass
    with pytest.raises(LifecycleError):
        camera.start_stream()


def test_discovery_without_native_sdk():
    manager = CameraManager()
    assert "simulated" in [d.id for d in manager.discover()]
    with pytest.raises(CameraNotFoundError):
        manager.open("nonexistent")


def test_recorder_start_does_not_invert_camera_close_lock(manager, tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    with manager.open("simulated") as camera, camera.start_stream() as stream:
        with ThreadPoolExecutor(max_workers=1) as pool:
            # close owns this camera lock before waiting on the stream lifecycle lock.
            # Recorder initialization must not acquire it in the reverse order.
            with camera._lock:
                future = pool.submit(stream.start_recording, tmp_path / "locking.scc")
                recorder = future.result(timeout=2)
            recorder.stop()
