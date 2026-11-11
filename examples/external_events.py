"""Printer-agnostic event association using an external host monotonic timestamp."""

import time
from pathlib import Path

from scientific_camera import CameraManager
from scientific_camera.errors import NoMatchingFrameError

with CameraManager().open("simulated") as camera, camera.start_stream() as stream:
    with stream.start_recording(Path("events_001.scc")):
        stream.next_frame()
        timestamp = time.monotonic_ns()
        event = stream.mark_event(
            "touch_contact", {"run_uuid": "example", "point_uuid": "p1"}, timestamp
        )
        try:
            shot = stream.snapshot(
                policy="nearest", event_monotonic_ns=timestamp, window_ns=100_000_000
            )
            print(event["event_id"], shot.reference, shot.association_delta_ns)
        except NoMatchingFrameError:
            print("Event retained without a sufficiently close host-receipt frame")
