"""Streaming index reader with strict offsets and optional per-frame checksum validation."""

from __future__ import annotations

import json
import zlib
from pathlib import Path

from ..errors import NoMatchingFrameError, RecordingCorruptError
from ..types import Frame
from .schema import metadata_from_row, segment_path


class RecordingReader:
    def __init__(self, directory: Path | str, *, verify_checksums: bool = True):
        self.directory = Path(directory)
        self.verify_checksums = verify_checksums
        try:
            self.manifest = json.loads((self.directory / "manifest.json").read_text())
            if self.manifest["schema_version"] != 1:
                raise ValueError("unsupported schema")
            if not isinstance(self.manifest["stream_id"], str):
                raise ValueError("invalid stream_id")
            if not isinstance(self.manifest["camera"], dict) or not isinstance(
                self.manifest["state"], str
            ):
                raise ValueError("invalid camera/state")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise RecordingCorruptError(f"invalid SCC manifest: {exc}") from exc

    def validate_row(self, row: dict, previous: dict | None = None) -> Frame:
        try:
            if row["schema_version"] != 1 or row["stream_id"] != self.manifest["stream_id"]:
                raise ValueError("schema/stream mismatch")
            metadata = metadata_from_row(row)
            path = segment_path(self.directory, row["segment"])
            offset, length = row["offset"], row["length"]
            if type(offset) is not int or type(length) is not int or offset < 0 or length < 0:
                raise ValueError("invalid offset/length")
            if length != metadata.payload_bytes:
                raise ValueError("payload length mismatch")
            if type(row["crc32"]) is not int or not 0 <= row["crc32"] <= 0xFFFFFFFF:
                raise ValueError("invalid checksum")
            expected_offset = 0
            if previous:
                if metadata.acquisition_index <= previous["acquisition_index"]:
                    raise ValueError("non-monotonic acquisition index")
                if row["segment"] == previous["segment"]:
                    expected_offset = previous["offset"] + previous["length"]
                elif (
                    int(row["segment"].split("_")[1].split(".")[0])
                    != int(previous["segment"].split("_")[1].split(".")[0]) + 1
                ):
                    raise ValueError("non-sequential raw segment")
            elif row["segment"] != "segment_000000.bin":
                raise ValueError("first segment must be zero")
            if offset != expected_offset:
                raise ValueError("non-contiguous payload offset")
            if offset + length > path.stat().st_size:
                raise ValueError("truncated payload")
            with path.open("rb") as raw:
                raw.seek(offset)
                payload = raw.read(length)
            if len(payload) != length:
                raise ValueError("short payload read")
            if self.verify_checksums and zlib.crc32(payload) != row["crc32"]:
                raise ValueError("CRC32 mismatch")
            return Frame(metadata, payload)
        except (OSError, ValueError, KeyError, TypeError, IndexError) as exc:
            raise RecordingCorruptError(f"invalid frame record: {exc}") from exc

    def iter_rows(self):
        previous = None
        try:
            with (self.directory / "frames.jsonl").open("rb") as index:
                for number, line in enumerate(index, 1):
                    try:
                        if not line.endswith(b"\n"):
                            raise ValueError("unterminated index record")
                        row = json.loads(line)
                        frame = self.validate_row(row, previous)
                    except (ValueError, RecordingCorruptError) as exc:
                        raise RecordingCorruptError(f"index line {number}: {exc}") from exc
                    previous = row
                    yield row, frame
        except OSError as exc:
            raise RecordingCorruptError(f"cannot read index: {exc}") from exc

    def iter_frames(
        self, *, start_monotonic_ns: int | None = None, end_monotonic_ns: int | None = None
    ):
        for _, frame in self.iter_rows():
            receipt = frame.metadata.host_received_monotonic_ns
            if start_monotonic_ns is not None and receipt < start_monotonic_ns:
                continue
            if end_monotonic_ns is not None and receipt > end_monotonic_ns:
                continue
            yield frame

    def get_frame(self, frame_id: int) -> Frame:
        for frame in self.iter_frames():
            if frame.metadata.acquisition_index == frame_id:
                return frame
        raise NoMatchingFrameError(f"frame {frame_id} is not indexed")

    def inspect(self) -> dict:
        count = size = 0
        incomplete = 0
        for _, frame in self.iter_rows():
            count += 1
            size += len(frame.payload)
            incomplete += frame.metadata.status != "complete"
        return {
            "schema_version": 1,
            "state": self.manifest["state"],
            "frames": count,
            "bytes": size,
            "incomplete_frames": incomplete,
            "checksum_validation": self.verify_checksums,
            "camera": self.manifest["camera"],
        }
