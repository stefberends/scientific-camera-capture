"""Run in a scratch directory; experiment_001.scc must not already exist."""

from pathlib import Path

from scientific_camera import CameraConfiguration, CameraManager
from scientific_camera.errors import UnsupportedFeatureError

with CameraManager().open("simulated") as camera:
    formats = camera.capabilities().pixel_formats
    if not formats:
        raise UnsupportedFeatureError("camera did not advertise pixel formats")
    # To request a particular format, first check e.g. 'BayerRG12p' in formats.
    try:
        camera.configure(CameraConfiguration(pixel_format=formats[0]))
    except UnsupportedFeatureError as exc:
        raise SystemExit(f"Select another advertised format from {formats}: {exc}") from exc
    with camera.start_stream() as stream:
        with stream.start_recording(Path("experiment_001.scc")):
            shot = stream.snapshot(policy="next", save=True)
            stream.mark_event("probe_contact", {"force_g": 5.0, "point": 3})
            print("Exact acquisition index/reference:", shot.frame_id, shot.reference)
        print(stream.stats())
