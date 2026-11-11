"""Physical opt-in smoke check, never selected from ordinary CI."""

import os

import pytest

from scientific_camera import CameraManager

pytestmark = pytest.mark.hardware


def test_discover_and_receive_owned_frame():
    camera_id = os.environ.get("SCICAM_CAMERA_ID")
    if not camera_id:
        pytest.skip("set SCICAM_CAMERA_ID to a discovered stable aravis:... ID")
    assert camera_id.startswith("aravis:")
    with CameraManager().open(camera_id) as camera:
        assert camera.capabilities().pixel_formats
        with camera.start_stream() as stream:
            frame = stream.next_frame(timeout=10)
            assert isinstance(frame.payload, bytes)
            assert len(frame.payload) == frame.metadata.payload_bytes
