# Project guidance

Make focused changes on feature/fix branches, never directly on main. Inspect the final diff
before a PR; describe behavior, rationale, and actual validation. Do not force push.
Keep the base package dependency-free and native imports lazy. Preserve owned sensor bytes.
Keep acquisition independent of disk I/O. Bound queues and retention, and report overload.
Never infer exposure time from host receipt timestamps. Unknown telemetry stays unknown.
Test failure and crash paths; never claim hardware verification from simulator tests.
Do not copy IDS SDK code or import printer software. Vendor packing needs independent fixtures.
Do not create a remote, push, install system packages, or change USB permissions automatically.

