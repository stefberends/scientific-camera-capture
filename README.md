# Scientific Camera Capture

Standalone, printer-independent Python library and `scicam` CLI. Original sensor bytes
are preserved. The core has no third-party dependencies; Aravis and TIFF export are optional.
Version 0.1.0 is a pre-1.0 software MVP, not a hardware compatibility certification.

| Component | Status |
| --- | --- |
| Simulator / SCC recording / snapshots / events | Tested locally |
| PFNC Mono8/16, BayerRG8/10p/12p, TIFF, preview | Tested with synthetic fixtures |
| Aravis on macOS Intel / Apple Silicon | NOT HARDWARE VERIFIED |
| IDS U3-38L1XLS-C / 18.2 fps | NOT TESTED |
| IDS grouped Bayer conversion | Disabled pending independent fixtures |

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
pytest
scicam discover --backend simulated --json
scicam inspect --camera simulated --json
scicam record --camera simulated --seconds 2 --output experiment_001.scc
scicam snapshot --camera simulated --trigger software --output sample_001
scicam inspect-recording experiment_001.scc
scicam benchmark --camera simulated --width 640 --height 480 --fps 100 --seconds 2
```

Output directories/images must be new. Use `scicam --help` and each subcommand's `--help`.
`pip install -e .` installs the dependency-free base; `[export]` adds NumPy/tifffile,
and `[aravis]` requests PyGObject separately from the native Aravis installation.

```python
from pathlib import Path
from scientific_camera import CameraManager, CameraConfiguration, RecordingReader

with CameraManager().open("simulated") as camera:
    camera.configure(CameraConfiguration(pixel_format=camera.capabilities().pixel_formats[0]))
    with camera.start_stream() as stream:
        with stream.start_recording(Path("run.scc")):
            shot = stream.snapshot(policy="next")
            stream.mark_event("probe_contact", {"force_g": 5.0, "point": 3})
        print(shot.frame_id, stream.stats())

for frame in RecordingReader("run.scc").iter_frames():
    print(frame.metadata.acquisition_index, len(frame.payload))
```

Stream snapshots select existing frames and never command another exposure. Independent
software-triggered capture requires an idle, capable camera. Receipt timestamps are host
monotonic nanoseconds, not exposure start; unknown telemetry/uncertainty stays unknown.
The library owns immutable bytes, bounds recording/retention, and fails explicitly on overload.
SCC v1 stores untouched payload segments, a checksummed index and durable event/snapshot logs.
Repair retains a verified prefix, preserves backups/raw tails and never invents missing frames.

Read [architecture/API](docs/architecture.md), [recording format](docs/recording-format.md),
[macOS setup](docs/macos-setup.md), [pixel formats](docs/pixel-formats.md),
[performance evidence](docs/performance.md), [compatibility checklist](docs/device-compatibility.md),
[integration](docs/integration.md), [troubleshooting](docs/troubleshooting.md), and
[implementation status](docs/implementation-status.md).

Original code is MIT licensed. Aravis is a separately installed LGPL-2.1-or-later dependency.
No printer repository, proprietary SDK binaries, remote repository, upload or system permission
changes are involved. Hardware acceptance, 30-minute recording, both native Mac architectures,
and independent IDS grouped decoding require physical validation before any v1.0 claim.

