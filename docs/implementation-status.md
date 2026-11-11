# Implementation status — 2026-11-11

This is a standalone 0.1.0 software MVP on `feat/initial-implementation`, published privately
on GitHub with draft PR #1. Main contains the approved MIT license bootstrap. No printer
project was modified. The original specification's
hardware acceptance criteria remain separate from software validation.

| Phase | Components / primary files | Actual checkpoint evidence | Remaining |
| --- | --- | --- | --- |
| 0 | pyproject, CI, AGENTS, README, types/errors/backend/capabilities/timestamps | dependency-free import passed | native environments unavailable |
| 1 | manager/session/metrics, simulated backend, lifecycle/configuration tests | 10 tests passed, Ruff passed | hardware frame ownership untested |
| 2 | recording schema/writer/reader/repair, format docs, failure tests | 19 tests passed; 200 frames/61.44 MB written in ~2 s | physical disk interruption differs from injected failures |
| 3 | snapshots/events/single trigger, architecture docs, tests | 26 tests passed including concurrent requests/stop races | physical trigger timing unknown |
| 4 | lazy Aravis adapter, doctor/camera-test, native docs, opt-in hardware test | 26 tests passed, 1 hardware test skipped; doctor correctly reports missing GI/library | local GI signatures and every physical test NOT TESTED |
| 5 | standard registry/unpackers, raw/TIFF export, preview, pixel docs/tests | 35 tests passed, 1 hardware test skipped | IDS decoder disabled pending fixtures; colour calibration absent |
| 6 | full-resolution simulator benchmark and concurrent snapshots, saved reports | exact checksum/reference readback; performance.md reports measurements and overload limitation | physical high-speed tuning, sync/async comparison, second camera BLOCKED by equipment/runtime |
| 7 | CLI tests, actual process crash, repair link audit, examples, packaging, typed marker, docs | final local checks described below | v1.0 release/API freeze deferred until hardware acceptance |

Final local result: **56 passed, 1 hardware test skipped** (8.81 seconds). Ruff lint/format,
Mypy (25 source files), editable installation, source/wheel builds, an isolated dependency-free
wheel capture, all four examples, full-resolution throughput and concurrent snapshot stress
passed. The wheel typing marker and source-distribution docs/tests were inspected. CI is
configured for Linux and macOS across Python 3.10/3.12/3.13; remote CI was not run in this
local checkpoint. Remote results are now tracked in
[GitHub Actions](https://github.com/stefberends/scientific-camera-capture/actions).
The capture timer now starts after writer setup; a regression test injects slow setup to
ensure a short requested capture still records frames. Runner architecture is reported by
CI, not inferred from its OS label. Native arm64
and Intel camera validation remain outstanding. See device-compatibility.md for precise tests.

Important decisions: dependency-free core; immutable owned raw bytes; bounded queues/ring;
nonblocking acquisition admission; raw-first/checksummed SCC index; conservative prefix repair
with backups; durable snapshot references rather than duplicate raw while recording;
host-receipt timing with unknown exposure uncertainty; lazy documented Aravis APIs; explicit
format converters with no guessed vendor decoder; no GUI/native optimization without evidence.

All requested initial module/example/test/doc paths are present. Additional MANIFEST.in and
py.typed package the documentation/test fixtures and expose installed type information;
saved benchmark reports and this status file provide reviewable validation evidence.

Next step: install/verify a matched native Aravis/GI environment and attach a physical camera,
then execute the compatibility checklist. Obtain independently sourced IDS grouped fixtures
before enabling vendor decoding. This project does not claim reference-camera acceptance or
completed v1.0 hardware hardening from simulator results.
