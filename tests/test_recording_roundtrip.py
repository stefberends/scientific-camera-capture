import json

from scientific_camera import CameraConfiguration
from scientific_camera.recording import RecordingReader, WriterOptions


def test_exact_bytes_metadata_and_segments(manager, tmp_path):
    path = tmp_path / "roundtrip.scc"
    with manager.open("simulated") as camera:
        camera.configure(CameraConfiguration(frame_rate=100))
        with camera.start_stream() as stream:
            with stream.start_recording(path, options=WriterOptions(segment_bytes=7000)):
                observed = [stream.next_frame()]
                for _ in range(9):
                    observed.append(stream.next_frame(after_index=observed[-1].metadata.frame_id))
            stats = stream.stats()
    frames = list(RecordingReader(path).iter_frames())
    by_id = {f.metadata.frame_id: f for f in frames}
    for frame in observed:
        assert by_id[frame.metadata.frame_id] == frame
    assert len(frames) == stats.recorded_frames
    assert len(list((path / "raw").glob("*.bin"))) > 1
    rows = [json.loads(line) for line in (path / "frames.jsonl").read_text().splitlines()]
    for row in rows:
        assert row["offset"] in (0, 3072)
    assert json.loads((path / "manifest.json").read_text())["state"] == "complete"


def test_refuses_overwrite(manager, tmp_path):
    import pytest

    with manager.open("simulated") as camera, camera.start_stream() as stream:
        path = tmp_path / "existing.scc"
        path.mkdir()
        with pytest.raises(FileExistsError):
            stream.start_recording(path)
