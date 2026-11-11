import json
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from scientific_camera import CameraConfiguration, CameraManager
from scientific_camera.backends.simulated import SimulatedBackend, SimulationOptions
from scientific_camera.errors import (
    CaptureTimeoutError,
    LifecycleError,
    NoMatchingFrameError,
    UnsupportedFeatureError,
)
from scientific_camera.recording import RecordingReader


def test_concurrent_recording_snapshots_are_exact_references(manager, tmp_path):
    path = tmp_path / "snapshots.scc"
    with manager.open("simulated") as camera, camera.start_stream() as stream:
        with stream.start_recording(path):
            with ThreadPoolExecutor(max_workers=6) as pool:
                shots = list(pool.map(lambda _: stream.snapshot(), range(6)))
            event = stream.mark_event("contact", {"force_g": 5})
    reader = RecordingReader(path)
    for shot in shots:
        assert shot.reference and not shot.files
        assert reader.get_frame(shot.frame_id) == shot.frame
        assert shot.acquisition_mode == "stream_selection"
        assert shot.uncertainty_ns is None
    links = [json.loads(line) for line in (path / "snapshots.jsonl").read_text().splitlines()]
    assert len(links) == 6
    assert len({link["snapshot_id"] for link in links}) == 6
    assert event["name"] == "contact"
    assert json.loads((path / "events.jsonl").read_text())["event_id"] == event["event_id"]


def test_nearest_window_and_raw_save_before_after_recording(manager, tmp_path):
    with manager.open("simulated") as camera:
        with camera.start_stream(snapshot_directory=tmp_path / "shots") as stream:
            first = stream.snapshot()
            assert first.files
            assert open(first.files[0], "rb").read() == first.frame.payload
            event_time = first.arrival_monotonic_ns
            nearest = stream.snapshot(policy="nearest", event_monotonic_ns=event_time, save=False)
            assert nearest.frame_id == first.frame_id and nearest.association_delta_ns == 0
            with pytest.raises(NoMatchingFrameError):
                stream.snapshot(policy="nearest", event_monotonic_ns=0, window_ns=1)
            with stream.start_recording(tmp_path / "middle.scc"):
                stream.snapshot()
            final = stream.snapshot()
            assert final.files and not final.reference
            event = stream.mark_event("old", {}, event_monotonic_ns=0)
            assert event["frame_id"] is None and event["missing_association_reason"]
            assert len(list((tmp_path / "shots/events").glob("*.json"))) == 1


def test_software_single_arms_and_restores(manager):
    with manager.open("simulated") as camera:
        initial = camera.configuration
        shot = camera.capture_single(trigger="software")
        assert shot.acquisition_mode == "software_trigger"
        assert shot.frame.metadata.frame_id == 0
        assert camera.configuration == initial
        with camera.start_stream():
            with pytest.raises(LifecycleError):
                camera.capture_single()


def test_unsupported_trigger_and_timeout(tmp_path):
    manager = CameraManager([SimulatedBackend(SimulationOptions(software_trigger_supported=False))])
    with manager.open("simulated") as camera:
        with pytest.raises(UnsupportedFeatureError):
            camera.capture_single(trigger="software")
        camera.configure(CameraConfiguration(trigger_mode="On"))
        with camera.start_stream(snapshot_directory=tmp_path) as stream:
            with pytest.raises(CaptureTimeoutError):
                stream.snapshot(timeout=0.02)


def test_snapshot_stop_race_does_not_deadlock(manager, tmp_path):
    with manager.open("simulated") as camera:
        camera.configure(CameraConfiguration(frame_rate=100))
        with camera.start_stream() as stream:
            recorder = stream.start_recording(tmp_path / "race.scc")
            with ThreadPoolExecutor(max_workers=2) as pool:
                future = pool.submit(stream.snapshot)
                # Wait for a selected/pinned frame, then close recording concurrently.
                deadline = time.monotonic() + 2
                while (
                    not recorder._link_users and not future.done() and time.monotonic() < deadline
                ):
                    time.sleep(0.001)
                recorder.stop()
                shot = future.result(timeout=2)
            assert RecordingReader(tmp_path / "race.scc").get_frame(shot.frame_id) == shot.frame
