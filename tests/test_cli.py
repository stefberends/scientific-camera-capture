import json
import os
import subprocess
import sys

import pytest


def run(*args, expected=0):
    result = subprocess.run(
        [sys.executable, "-m", "scientific_camera.cli", *map(str, args)],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == expected, (result.stdout, result.stderr)
    return result


@pytest.mark.parametrize(
    "command",
    [
        "doctor",
        "discover",
        "inspect",
        "record",
        "snapshot",
        "export",
        "inspect-recording",
        "repair",
        "benchmark",
        "camera-test",
    ],
)
def test_subcommand_help(command):
    assert "usage:" in run(command, "--help").stdout


def test_cli_end_to_end_and_json(tmp_path):
    devices = json.loads(run("discover", "--backend", "simulated", "--json").stdout)
    assert devices["devices"][0]["id"] == "simulated"
    info = json.loads(run("inspect", "--camera", "simulated", "--json").stdout)
    assert "Mono8" in info["capabilities"]["pixel_formats"]
    path = tmp_path / "cli.scc"
    report = json.loads(run("record", "--output", path, "--seconds", "0.15", "--json").stdout)
    assert report["stats"]["recorded_frames"] > 0
    inspected = json.loads(run("inspect-recording", path, "--json").stdout)
    assert inspected["frames"] == report["stats"]["recorded_frames"]
    from scientific_camera.recording import RecordingReader

    identity = next(RecordingReader(path).iter_frames()).metadata.frame_id
    exported = json.loads(
        run(
            "export",
            "--input",
            path,
            "--frame",
            identity,
            "--format",
            "tiff",
            "--output",
            tmp_path / "frame.tiff",
            "--json",
        ).stdout
    )
    assert os.path.exists(exported["files"][0])
    shot = json.loads(
        run("snapshot", "--trigger", "software", "--output", tmp_path / "single", "--json").stdout
    )
    assert shot["acquisition_mode"] == "software_trigger"
    with (path / "frames.jsonl").open("ab") as output:
        output.write(b'{"partial":')
    repaired = json.loads(run("repair", path, "--json").stdout)
    assert repaired["removed_index_records"] == 1
    benchmark = json.loads(run("benchmark", "--seconds", "0.1", "--json").stdout)
    assert benchmark["retained_output"] is None


def test_failures_are_nonzero_and_structured(tmp_path):
    failure = run("inspect", "--camera", "missing", "--json", expected=1)
    assert json.loads(failure.stderr)["error"] == "CameraNotFoundError"
    failure = run(
        "record",
        "--pixel-format",
        "fiction",
        "--seconds",
        "0.1",
        "--output",
        tmp_path / "invalid.scc",
        expected=1,
    )
    assert json.loads(failure.stderr)["error"] == "UnsupportedFeatureError"
    run("record", "--seconds", "nan", "--output", tmp_path / "invalid.scc", expected=2)


def test_base_import_and_capture_without_third_party_packages():
    from pathlib import Path

    source = Path(__file__).parents[1] / "src"
    script = f"""import sys; sys.path.insert(0, {str(source)!r})
from scientific_camera import CameraManager
with CameraManager().open("simulated") as camera:
    assert camera.capture_single().frame.payload
assert "gi" not in sys.modules and "numpy" not in sys.modules
"""
    result = subprocess.run(
        [sys.executable, "-S", "-c", script], capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0, result.stderr


def test_record_duration_excludes_slow_writer_setup(monkeypatch, tmp_path, capsys):
    import time

    from scientific_camera.cli import main
    from scientific_camera.session import AcquisitionSession

    original = AcquisitionSession.start_recording

    def delayed_setup(self, *args, **kwargs):
        time.sleep(0.2)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(AcquisitionSession, "start_recording", delayed_setup)
    assert (
        main(["record", "--output", str(tmp_path / "slow-setup.scc"), "--seconds", "0.1", "--json"])
        == 0
    )
    report = json.loads(capsys.readouterr().out)
    assert report["stats"]["recorded_frames"] > 0
    assert report["duration_seconds"] >= 0.1
