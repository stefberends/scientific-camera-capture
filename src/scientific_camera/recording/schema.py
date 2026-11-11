"""SCC v1 serialization and safe paths. CRC is IEEE CRC32, not CRC32C."""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, fields
from pathlib import Path

from ..errors import RecordingCorruptError
from ..types import FrameMetadata

SCHEMA_VERSION = 1


def json_bytes(value) -> bytes:
    return (json.dumps(value, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")


def atomic_json(path: Path, value) -> None:
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("wb") as output:
        output.write(json_bytes(value))
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, path)
    # Persist directory entry changes on POSIX filesystems supporting directory fsync.
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def metadata_from_row(row: dict) -> FrameMetadata:
    try:
        result = FrameMetadata(**{f.name: row[f.name] for f in fields(FrameMetadata)})
        for name in (
            "acquisition_index",
            "host_received_monotonic_ns",
            "payload_bytes",
            "width",
            "height",
        ):
            value = getattr(result, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"invalid {name}")
        if (
            not result.width
            or not result.height
            or (not result.payload_bytes and result.status == "complete")
        ):
            raise ValueError("empty frame")
        if (
            not isinstance(result.camera_id, str)
            or not isinstance(result.pixel_format, str)
            or result.status not in ("complete", "incomplete")
            or not isinstance(result.chunks, dict)
        ):
            raise ValueError("invalid frame identity/status/chunks")
        if result.stride is not None and (type(result.stride) is not int or result.stride < 0):
            raise ValueError("invalid stride")
        return result
    except (KeyError, TypeError, ValueError) as exc:
        raise RecordingCorruptError(f"invalid frame metadata: {exc}") from exc


def frame_row(
    metadata: FrameMetadata, stream_id: str, segment: str, offset: int, checksum: int
) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "stream_id": stream_id,
        **asdict(metadata),
        "segment": segment,
        "offset": offset,
        "length": metadata.payload_bytes,
        "crc32": checksum,
    }


def segment_path(directory: Path, name: str) -> Path:
    if not isinstance(name, str) or not re.fullmatch(r"segment_[0-9]{6,}\.bin", name):
        raise RecordingCorruptError("invalid segment filename")
    raw = directory / "raw"
    candidate = raw / name
    if raw.is_symlink() or candidate.is_symlink():
        raise RecordingCorruptError("symlinked raw segments are not permitted")
    return candidate


class RecordingLock:
    """Advisory POSIX lock, released by process death. Shared by writer and repair."""

    def __init__(self, directory: Path):
        import fcntl

        self.file = (directory / ".writer.lock").open("a+b")
        try:
            fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.file.close()
            raise RecordingCorruptError("recording is active or already being repaired") from exc

    def close(self):
        self.file.close()
