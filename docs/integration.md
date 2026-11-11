# Library integration

Install this standalone package in the consumer's environment. There is no dependency on
Prusa Direct Drive, printer movement, load cells, Qt, ROS, or proprietary IDS binaries.
See `examples/record_and_snap.py`, `software_trigger.py` and `external_events.py` for executable
examples. All configure an actually advertised format or retain the default supported format.

```python
from pathlib import Path
from scientific_camera import CameraManager, CameraConfiguration, RecordingReader

with CameraManager().open("simulated") as camera:
    camera.configure(CameraConfiguration(pixel_format=camera.capabilities().pixel_formats[0]))
    with camera.start_stream() as stream:
        with stream.start_recording(Path("run.scc")):
            shot = stream.snapshot()
            stream.mark_event("touch_contact", {"run_uuid": "...", "point_uuid": "..."})
        print(shot.frame_id, stream.stats())

for frame in RecordingReader("run.scc").iter_frames():
    assert len(frame.payload) == frame.metadata.payload_bytes
```

Paths must be new. Capture APIs are synchronous and may block until their documented timeout;
call them from consumer worker threads if the consumer has a GUI. Concurrent snapshot/event
callers do not block acquisition on disk operations. Raw payloads returned to callers are
owned immutable bytes. Caller retention is outside the library's bounded queue budget.

An external controller can provide `time.monotonic_ns()` timestamps to `mark_event` and
nearest selection when using the same host monotonic clock. Another machine's monotonic
timestamps are not interchangeable without measured clock mapping. Frame arrival is not
exposure time. Preserve association uncertainty and missing-frame reasons in experiment logs.
Use stream snapshots during free run and idle `capture_single` only for independent acquisition.

Future printer adapters belong in the consumer repository, wrapping the stable API. A camera
failure should be caught and reported by that consumer; this library sends no motion commands.
Legacy `snap/flush/set_exposure/set_gain/close` compatibility belongs in that adapter.

Backends implement `CameraBackend`/`CameraDevice` protocols. Register an object with
`manager.register_backend()` or explicitly `load_plugins()` from entry-point factories under
`scientific_camera.backends`. Unknown/vendor bytes remain recordable without any converter.
Public API and thread/clock semantics are detailed in architecture.md. Version 0.1 is pre-1.0;
API/schema changes require review and semantic versioning.
