"""Independent exposure when idle; never silently substitutes unsupported triggering."""

from scientific_camera import CameraManager
from scientific_camera.errors import UnsupportedFeatureError

with CameraManager().open("simulated") as camera:
    try:
        shot = camera.capture_single(trigger="software")
    except UnsupportedFeatureError as exc:
        raise SystemExit(f"Camera does not support the requested software trigger: {exc}") from exc
    print(shot.frame_id, shot.acquisition_mode, shot.timing_provenance)
