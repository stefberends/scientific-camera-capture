# Troubleshooting

- Backend unavailable: run `scicam doctor --json`. Verify interpreter/architecture and native
  library/typelib installation. Simulation remains usable without GI or a native SDK.
- Camera missing/in use: use the full discovered stable ID and release other sessions/apps.
  Cooperative POSIX temp lock files persist, but locks release on close or process death.
  Do not delete active lock files. Native control ownership also applies to other applications.
- Unsupported/read-only feature: inspect capabilities/readback and configure while idle.
  Trigger availability, gain units and formats are camera-specific. Native partial configuration
  failures expose readback; inspect it before retrying.
- RecordingOverflowError: capture exceeded the bounded writer queue. The recording fails
  explicitly and stats count application drops. Reduce rate, improve storage, or increase the
  measured memory budget. A larger queue cannot fix sustained storage underperformance.
- DiskWriteError: retain the SCC directory, restore storage availability, then run `repair`.
  Missing summaries do not imply empty data. Completed indexed frames may be recoverable.
- Corrupt index/checksum: reader raises. Repair verifies the valid prefix, keeps backups/raw
  bytes, and never guesses missing frames. Interior corruption needs manual investigation.
  Events survive missing associations; dangling snapshot references are audited/removed.
- Timeout/no match: check trigger pulses, exposure/fps, retention limits and clock/window.
  Nearest searches retained complete frames using host receipt time. Oversized frames remain
  recordable but are unavailable to ring consumers if they exceed the retention budget.
- Unknown pixel layout: export RAW+JSON. Do not rename IDS grouped data to PFNC `p`.
  Chunk extraction, uncommon padding and vendor conversion require independent validation.
- Preview error: inspect `preview.error`; conversion is optional and independent of recording.
  Preview and interpolated RGB are not camera colour calibration.

Stop/close are idempotent and worker joins are bounded. A native call or OS disk write that
never returns cannot safely be cancelled by a Python thread; a reported shutdown timeout
requires process termination followed by repair. No system packages, USB permissions,
printer movement or uploads are performed automatically.
