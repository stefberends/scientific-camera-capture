# SCC Recording v1

A session is a new directory containing `manifest.json`, `frames.jsonl`, `events.jsonl`,
`snapshots.jsonl`, `summary.json`, and `raw/segment_000000.bin` etc. Payloads are concatenated
unchanged, without headers. A frame larger than the segment target occupies its own segment.

Each index line includes all `FrameMetadata` fields plus schema version 1, stream UUID,
segment basename, byte offset, length, and unsigned IEEE CRC32 (`zlib.crc32`, not CRC32C).
`acquisition_index` is the stream-local, strictly increasing identity. It is distinct from
the possibly missing, wrapping, or restarting `device_frame_id`. Offsets are contiguous
within segments. A checksum detects accidental corruption and does not prove authenticity.

The writer writes payload first, then index. Raw is fsynced before the index at configurable
intervals (default 1 second), segment rollover, snapshot publication, and orderly stop.
This is a durability window, not a guarantee against hardware or filesystem failures.
Snapshot/event append is synchronous on its caller thread and fsynced; acquisition never
writes disk data. A failed recording is explicit, retains its completed index prefix, and
has an error in summary/manifest where storage permits writing those files.

Reader validation checks schema/stream identity, ordered indexes, safe segment filenames,
offset continuity, file lengths, and CRC32. It reads incrementally with one payload resident.
It raises on malformed/truncated index rows and never silently hides corruption.

`repair_recording(path)` takes an exclusive advisory lock, verifies the valid index prefix,
backs up the original index, removes invalid trailing records, preserves all original raw
bytes (including unindexed tails), and writes a repair audit/partial summary. It refuses
interior corruption followed by further frame records. Raw payloads lack self-describing
boundaries; unindexed frames cannot be reconstructed safely and are never invented.
Repair cannot prove the original acquisition was complete. An active writer blocks repair.
Auxiliary logs are backed up and repaired too: events retain missing-association reasons,
while references to unrecoverable snapshot frames are removed with an audit. A malformed
trailing auxiliary record may be discarded; interior log corruption is refused.

Manifest fields include identity, configuration readback, intended fps, host architecture/OS,
clock anchor, app version, firmware when known, warnings, and durability policy. Unknown
firmware or source commit is `null`, not guessed. Event and snapshot log details are described
in architecture.md. A failed filesystem may prevent final summary writes: use repair.

No compression, colour correction, or bit-depth reduction occurs in recording.
