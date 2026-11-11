# Architecture and public API (0.1)

The base import uses only the standard library. `CameraManager` registers the simulator and
the lazy Aravis adapter. `discover(backends=[...])` returns stable identities and preserves
optional backend errors in `manager.diagnostics`. Explicit `load_plugins()` loads factories
from the `scientific_camera.backends` Python entry-point group. No printer code is imported.

`manager.open(id)` returns one context-managed `CameraSession`. Configure while idle using
`CameraConfiguration` (`None` means unchanged); capabilities specify ranges, increments,
units and available modes. Unknown native features are unavailable, not assumed supported.
`camera.configuration` contains readback. `camera.read_feature(name)` is explicit node access.

`camera.start_stream()` owns a backend receive thread. Buffers are copied before transport
requeue. Recording uses a separate bounded worker queue; queue admission never waits for disk.
`retention_frames` (16) and `retention_bytes` (64 MiB) bound the selection ring. Oversized
frames can still be recorded but are not retained for `next_frame`/nearest selection.
`next_frame(after_index=..., timeout=5)` is a blocking convenience for consumers; slow
consumers can miss evicted frames, unlike the loss-aware recording path.

`stream.start_recording(path, options=WriterOptions(...))` requires a new directory.
Defaults are 16 queued frames **including in-flight data**, 128 MiB, 1 GiB segments.
Queue pressure or storage errors fail recording explicitly. The stream may continue for
diagnostics; `recorder.stop()` raises its typed error. Always stop recording before closing
the camera, preferably using context managers. Shutdown has bounded joins; a stuck operating
system write cannot be forcefully cancelled safely by Python and requires process recovery.

`stream.snapshot(policy='next', save=True, timeout=5, output=None)` waits for the next
complete existing-stream frame. Concurrent requests pending for the same frame select that
same frame with different snapshot UUIDs. Incomplete frames remain counted and indexed.
The acquisition worker pins selected owned bytes until requesting callers finish. Up to
`max_snapshot_requests` (8) may be outstanding, bounding additional payload retention by
that count times the largest selected payload; account for this in memory budgeting.
Nearest selection requires `event_monotonic_ns` and checks `window_ns` (100 ms default)
against **host receipt**, with deterministic earlier-index tie breaking. It cannot prove
exposure timing. Unknown uncertainty is `None`, never zero.

With recording active, snapshots publish durable references to existing indexed frames;
they wait for raw/index fsync on the caller thread. Recorder shutdown waits for pinned link
users so concurrent stop cannot invalidate a selected reference. Outside recording (or with
an explicit output), snapshots write raw+JSON to `snapshot_directory` (`./snapshots` default).
`save=False` returns the owned frame without disk I/O. Caller-held returned frames are outside
the library's memory budget. Request callers never run on acquisition thread.

`camera.capture_single(trigger='software'|'free_run', save=False, output=None)` requires
an idle camera. Software mode is armed before executing a real software trigger, requires
advertised support, and restores configuration afterward. A free-run single capture means
selecting one newly received frame, not precision exposure synchronization.

`stream.mark_event(name, payload, event_monotonic_ns=None)` saves JSON-compatible payloads
independent of image arrival. During recording it appends to `events.jsonl`; outside recording
it persists individual JSON files under `snapshot_directory/events`. Associations outside
the retained receipt-time window are explicitly missing. This operation performs caller-thread
I/O and can block on storage. It never triggers an exposure. Events preceding recording
retain their timestamps but do not link to frames outside that recording.

Camera lifecycle/configuration calls are serialized. Snapshots, events, stats and recorder
stop are safe from independent threads. Receive, writer and snapshot paths avoid holding the
stream condition over disk operations. Stop/close are idempotent. Errors remain observable.

`Frame.payload` is immutable owned `bytes`; the metadata documents format, dimensions,
stride (or unknown), receipt clock, raw device timestamp origin/units, and settings readback.
UTC is a wall-clock label anchored to host monotonic time. The clocks are not synchronized.
Rolling shutter produces different row exposure times. `host_capture_monotonic_ns` stays
unknown without measured exposure timing. Device counter gaps are warnings; only device or
transport telemetry is labelled reported loss. Stats integrity is verified/partial/unverifiable
for the observation window, not a scientific certification of sensor timing or colour.

Public stable candidates: `CameraManager`, configuration/capability/info/range/frame/metadata/
stats/snapshot dataclasses, `RecordingReader`, `WriterOptions`, `repair_recording`, and
`export_frame`. Typed exceptions live in `scientific_camera.errors`. The package follows
semantic versioning and is intentionally pre-1.0 until hardware validation and API review.
