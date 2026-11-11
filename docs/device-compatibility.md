# Compatibility evidence and physical checklist

| Host / camera | Evidence | Status |
| --- | --- | --- |
| Intel macOS 15.8.1, Python 3.12.3 / simulator | local tests, CLI, build, stress reports | SOFTWARE TESTED |
| Linux Python 3.10 / 3.12 / 3.13 / simulator | unit tests/lint/type checks/build passed in initial GitHub CI; see Actions for complete job results | SOFTWARE TESTED |
| macOS arm64 / simulator | no native arm64 host tested; CI logs actual runner architecture | NOT TESTED HERE |
| Aravis 0.8 GI runtime / either architecture | upstream example/docs reviewed; local GI absent | BINDING EXECUTION NOT TESTED |
| IDS U3-38L1XLS-C Rev. 1.2 / Intel or arm64 macOS | no physical device/native runtime | NOT HARDWARE VERIFIED |
| IDS grouped 10/12-bit conversion | independent golden fixtures absent | DISABLED |
| Another GenICam camera model/manufacturer | no device available | NOT TESTED |

The saved simulator reports do not establish USB bandwidth, reference-camera support,
native arm64 performance, exposure timing, or timestamp reliability. Hardware acceptance
cannot be completed with mocks. The opt-in hardware test is skipped in ordinary CI.

For each physical run, record actual model/revision, serial, firmware, host OS/architecture,
Python/Aravis versions and GI signatures, GenICam XML, USB topology/cable/controller/speed,
pixel format/ROI/payload/stride, exposure/fps, timestamps/counter behavior, USB transfer mode,
buffer limits, SSD/filesystem/free space and all loss/throughput/latency reports.

Run separately on Intel and Apple Silicon before claiming both:

1. Verify matched Aravis/GI/Python installation, method signatures and buffer ownership.
2. Discover by stable ID and query actual features; capture full-resolution native payloads.
3. Run `camera-test` without recording to measure transport independently of storage.
4. Record 30 minutes per selected native format; check every checksum/payload length and retain
   received/written counts, device counters, incompletes, application drops and queue peaks.
5. Issue concurrent snapshots/events; compare exact referenced bytes and measure latency/loss.
6. Test software triggering, idle/free-run transitions, external trigger sources and edges.
7. Compare sync/async USB mode with identical settings; no mode is presumed superior.
8. Unplug/replug during streaming/recording; check errors, cleanup, rediscovery and repair.
9. Obtain independent IDS reference fixtures and prove unpacking/CFA orientation before enabling
   vendor conversion. Repeat functional checks with another manufacturer before broad claims.

```sh
SCICAM_CAMERA_ID='aravis:<discovered-id>' pytest --run-hardware -m hardware
```

This smoke test does not replace the checklist. Aravis supplies normalized nanoseconds,
preserved separately in `chunks.aravis_timestamp_ns`; original device ticks/frequency are
not fabricated. Buffer failures/underruns are not exact missing sensor frame counts. Device
loss telemetry remains unknown, so host counters alone cannot certify zero sensor loss.
