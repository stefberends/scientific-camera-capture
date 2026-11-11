import json

import pytest

from scientific_camera.errors import RecordingCorruptError
from scientific_camera.recording import RecordingReader, repair_recording


def make_recording(manager, path, count=4):
    with manager.open("simulated") as camera, camera.start_stream() as stream:
        with stream.start_recording(path):
            for _ in range(count):
                stream.next_frame()


def test_interrupted_index_repair(manager, tmp_path):
    path = tmp_path / "partial.scc"
    make_recording(manager, path)
    before = list(RecordingReader(path).iter_frames())
    with (path / "frames.jsonl").open("ab") as output:
        output.write(b'{"acquisition_index":')
    with (path / "raw/segment_000000.bin").open("ab") as output:
        output.write(b"unindexed partial payload")
    with pytest.raises(RecordingCorruptError):
        list(RecordingReader(path).iter_frames())
    report = repair_recording(path)
    assert report["removed_index_records"] == 1
    assert report["unindexed_frames_reconstructed"] == 0
    assert report["orphan_raw_bytes_preserved"] > 0
    assert list(RecordingReader(path).iter_frames()) == before
    assert RecordingReader(path).manifest["state"] == "repaired_partial"


def test_truncated_last_payload_and_interior_corruption(manager, tmp_path):
    path = tmp_path / "truncated.scc"
    make_recording(manager, path)
    before = list(RecordingReader(path).iter_frames())
    segment = path / "raw/segment_000000.bin"
    with segment.open("r+b") as raw:
        raw.truncate(segment.stat().st_size - 7)
    report = repair_recording(path)
    assert report["valid_frames"] == len(before) - 1
    assert list(RecordingReader(path).iter_frames()) == before[:-1]
    with segment.open("r+b") as raw:
        raw.write(b"corrupt")
    with pytest.raises(RecordingCorruptError, match="interior"):
        repair_recording(path)


def test_active_repair_refused(manager, tmp_path):
    path = tmp_path / "live.scc"
    with manager.open("simulated") as camera, camera.start_stream() as stream:
        with stream.start_recording(path):
            with pytest.raises(RecordingCorruptError, match="active"):
                repair_recording(path)


def test_reject_path_traversal(manager, tmp_path):
    path = tmp_path / "unsafe.scc"
    make_recording(manager, path)
    lines = (path / "frames.jsonl").read_text().splitlines()
    row = json.loads(lines[0])
    row["segment"] = "../../elsewhere"
    (path / "frames.jsonl").write_text(json.dumps(row) + "\n")
    with pytest.raises(RecordingCorruptError):
        list(RecordingReader(path).iter_frames())


def test_actual_process_death_mid_payload_and_index(tmp_path):
    import subprocess
    import sys

    path = tmp_path / "process-crash.scc"
    script = f"""
import os
from pathlib import Path
from scientific_camera import CameraManager
from scientific_camera.recording import WriterOptions
camera = CameraManager().open("simulated")
stream = camera.start_stream()
recorder = stream.start_recording(Path({str(path)!r}), options=WriterOptions(fsync_interval_seconds=0))
for _ in range(3):
    stream.next_frame()
with recorder._io_lock:
    recorder._segment.write(b"partial unindexed payload")
    os.fsync(recorder._segment.fileno())
    recorder._index.write(b'{{"partial_index":')
    os.fsync(recorder._index.fileno())
    os._exit(17)
"""
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 17, result.stderr
    report = repair_recording(path)
    frames = list(RecordingReader(path).iter_frames())
    assert len(frames) == report["valid_frames"] >= 2
    assert report["removed_index_records"] == 1
    assert report["orphan_raw_bytes_preserved"] == len(b"partial unindexed payload")
    assert report["unindexed_frames_reconstructed"] == 0


def test_repair_removes_dangling_snapshots_but_preserves_events(manager, tmp_path):
    path = tmp_path / "dangling.scc"
    make_recording(manager, path)
    before = list(RecordingReader(path).iter_frames())
    lost = before[-1].metadata.frame_id
    (path / "events.jsonl").write_text(
        json.dumps({"event_id": "e", "name": "contact", "frame_id": lost}) + "\n"
    )
    (path / "snapshots.jsonl").write_text(
        json.dumps({"snapshot_id": "s", "frame_id": lost}) + "\n" + '{"partial":'
    )
    segment = path / "raw/segment_000000.bin"
    with segment.open("r+b") as output:
        output.truncate(segment.stat().st_size - 1)
    report = repair_recording(path)
    assert report["auxiliary_logs"]["snapshots.jsonl"]["removed_dangling_snapshot_links"] == 1
    assert not (path / "snapshots.jsonl").read_text()
    event = json.loads((path / "events.jsonl").read_text())
    assert event["event_id"] == "e" and event["frame_id"] is None
    assert "not recoverable" in event["missing_association_reason"]
