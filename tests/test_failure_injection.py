import time

import pytest

from scientific_camera import CameraConfiguration, CameraManager
from scientific_camera.backends.simulated import SimulatedBackend, SimulationOptions
from scientific_camera.errors import CameraDisconnectedError, DiskWriteError, RecordingOverflowError
from scientific_camera.recording import RecordingReader, WriterOptions, repair_recording


def test_disk_full_mid_frame(manager, tmp_path):
    path = tmp_path / "disk-full.scc"
    with manager.open("simulated") as camera, camera.start_stream() as stream:
        recorder = stream.start_recording(path, options=WriterOptions(fail_after_bytes=3072 + 100))
        deadline = time.monotonic() + 3
        while recorder.error is None and time.monotonic() < deadline:
            time.sleep(0.005)
        with pytest.raises(DiskWriteError):
            recorder.stop()
        assert stream.stats().application_drops >= 1
    report = repair_recording(path)
    assert report["valid_frames"] == 1
    assert report["orphan_raw_bytes_preserved"] == 100
    assert len(list(RecordingReader(path).iter_frames())) == 1


def test_queue_pressure_explicit_failure(manager, tmp_path):
    with manager.open("simulated") as camera:
        camera.configure(CameraConfiguration(frame_rate=1000))
        with camera.start_stream() as stream:
            recorder = stream.start_recording(
                tmp_path / "slow.scc",
                options=WriterOptions(queue_frames=2, write_delay_seconds=0.1),
            )
            deadline = time.monotonic() + 3
            while recorder.error is None and time.monotonic() < deadline:
                time.sleep(0.005)
            with pytest.raises(RecordingOverflowError):
                recorder.stop()
            assert stream.stats().queue_high_water_mark <= 2
            assert stream.stats().application_drops > 0
            assert stream.stats().integrity == "partial"


def test_unplug_is_reported_and_releases_camera():
    manager = CameraManager([SimulatedBackend(SimulationOptions(disconnect_after=2))])
    with manager.open("simulated") as camera:
        with camera.start_stream() as stream:
            with pytest.raises(CameraDisconnectedError):
                for _ in range(5):
                    stream.next_frame()
    with manager.open("simulated"):
        pass


def test_incomplete_missing_and_restarted_ids_are_visible(tmp_path):
    manager = CameraManager(
        [
            SimulatedBackend(
                SimulationOptions(
                    incomplete_every=2,
                    missing_id_every=3,
                    reset_id_at=5,
                    delay_every=3,
                    delay_seconds=0.001,
                    time_scale=0.1,
                )
            )
        ]
    )
    with manager.open("simulated") as camera, camera.start_stream() as stream:
        with stream.start_recording(tmp_path / "faults.scc"):
            for _ in range(10):
                stream.next_frame()
        stats = stream.stats()
        assert stats.incomplete_frames > 0
        assert stats.integrity == "partial"
    frames = list(RecordingReader(tmp_path / "faults.scc").iter_frames())
    assert any(f.metadata.status == "incomplete" for f in frames)
    assert any(f.metadata.device_frame_id is None for f in frames)


def test_recording_byte_budget_overflow(manager, tmp_path):
    with manager.open("simulated") as camera, camera.start_stream() as stream:
        recorder = stream.start_recording(
            tmp_path / "budget.scc", options=WriterOptions(queue_bytes=1)
        )
        stream.next_frame()
        with pytest.raises(RecordingOverflowError):
            recorder.stop()
        assert stream.stats().recorded_frames == 0
