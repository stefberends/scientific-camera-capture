"""Conservative prefix repair. No frame boundaries are guessed from orphan raw bytes."""

from __future__ import annotations

import json
import os
import shutil
import uuid
from pathlib import Path

from ..errors import RecordingCorruptError
from .reader import RecordingReader
from .schema import RecordingLock, atomic_json, json_bytes


def _log_rows(path):
    """Tolerate only invalid trailing auxiliary records, never interior corruption."""
    bad = False
    if not path.exists():
        return
    with path.open("rb") as source:
        for line in source:
            try:
                if not line.endswith(b"\n"):
                    raise ValueError("truncated log")
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError("invalid log row")
            except ValueError:
                bad = True
                continue
            if bad:
                raise RecordingCorruptError(f"interior corruption in {path.name}")
            yield row


def _prepare_links(directory, valid, suffix):
    reports = {}
    prepared = []
    for name in ("events.jsonl", "snapshots.jsonl"):
        path = directory / name
        temporary = directory / (name + suffix)
        changed = removed = 0
        with temporary.open("wb") as output:
            for row in _log_rows(path):
                identity = row.get("frame_id")
                if identity is not None and type(identity) is not int:
                    raise RecordingCorruptError(f"invalid frame association in {name}")
                if identity is not None and identity not in valid:
                    if name == "snapshots.jsonl":
                        removed += 1
                        continue
                    row["frame_id"] = None
                    row["missing_association_reason"] = "associated frame was not recoverable"
                    changed += 1
                output.write(json_bytes(row))
            output.flush()
            os.fsync(output.fileno())
        reports[name] = {
            "removed_dangling_snapshot_links": removed,
            "events_unlinked": changed,
            "original_bytes": path.stat().st_size if path.exists() else 0,
            "repaired_bytes": temporary.stat().st_size,
        }
        prepared.append((path, temporary))
    return reports, prepared


def repair_recording(directory: Path | str) -> dict:
    directory = Path(directory)
    lock = RecordingLock(directory)
    temporary = directory / "frames.repaired.tmp"
    suffix = ".repair-" + uuid.uuid4().hex
    try:
        reader = RecordingReader(directory)
        previous = None
        bad = None
        valid_bytes = count = raw_bytes = removed = 0
        referenced = {
            row["frame_id"]
            for name in ("events.jsonl", "snapshots.jsonl")
            for row in _log_rows(directory / name)
            if type(row.get("frame_id")) is int
        }
        valid = set()
        # A corruption with later well-formed index rows is not merely a trailing crash.
        # Refuse destructive prefix repair in that case; preserve all evidence.
        with (directory / "frames.jsonl").open("rb") as source, temporary.open("wb") as target:
            for number, line in enumerate(source, 1):
                if bad:
                    removed += 1
                    try:
                        later = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(later, dict) and "acquisition_index" in later:
                        raise RecordingCorruptError(
                            "interior index corruption; refusing to discard later frames"
                        )
                    continue
                try:
                    if not line.endswith(b"\n"):
                        raise RecordingCorruptError("truncated index line")
                    row = json.loads(line)
                    frame = reader.validate_row(row, previous)
                except (ValueError, RecordingCorruptError) as exc:
                    bad = {"line": number, "reason": str(exc)}
                    removed += 1
                    continue
                target.write(line)
                previous = row
                if row["acquisition_index"] in referenced:
                    valid.add(row["acquisition_index"])
                count += 1
                raw_bytes += len(frame.payload)
                valid_bytes += len(line)
        links, prepared = _prepare_links(directory, valid, suffix)
        backup = "frames.jsonl.backup-" + uuid.uuid4().hex
        shutil.copyfile(directory / "frames.jsonl", directory / backup)
        # Keep original raw segments, including unindexed bytes, for forensic recovery.
        with temporary.open("rb") as source:
            os.fsync(source.fileno())
        os.replace(temporary, directory / "frames.jsonl")
        for original, replacement in prepared:
            if original.exists():
                shutil.copyfile(original, original.with_name(original.name + suffix + ".backup"))
            os.replace(replacement, original)
        orphan_bytes = (
            sum(p.stat().st_size for p in (directory / "raw").glob("segment_*.bin")) - raw_bytes
        )
        report = {
            "schema_version": 1,
            "state": "repaired_partial",
            "valid_frames": count,
            "indexed_bytes": raw_bytes,
            "removed_index_records": removed,
            "first_invalid_record": bad,
            "index_backup": backup,
            "orphan_raw_bytes_preserved": orphan_bytes,
            "unindexed_frames_reconstructed": 0,
            "auxiliary_logs": links,
            "warning": "original acquisition completeness cannot be recovered from raw bytes alone",
        }
        reader.manifest["state"] = "repaired_partial"
        reader.manifest["repair_report"] = "repair-report.json"
        atomic_json(directory / "manifest.json", reader.manifest)
        atomic_json(directory / "repair-report.json", report)
        atomic_json(directory / "summary.json", {**report, "integrity": "partial"})
        return report
    finally:
        temporary.unlink(missing_ok=True)
        for name in ("events.jsonl", "snapshots.jsonl"):
            (directory / (name + suffix)).unlink(missing_ok=True)
        lock.close()
