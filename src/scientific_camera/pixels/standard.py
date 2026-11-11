"""PFNC standard unpacking (not the older *Packed or IDS grouped layouts).

Bit layouts: EMVA PFNC 2.4, lsb-packed monochrome; packing repeats per complete group.
Partial groups/tails are rejected; explicit per-row padding is supported.
"""

from ..errors import (
    BackendUnavailableError,
    ConfigurationError,
    IncompleteFrameError,
    UnsupportedFeatureError,
)
from ..types import Frame
from .registry import lookup


def decode(frame: Frame):
    try:
        import numpy as np
    except ImportError as exc:
        raise BackendUnavailableError(
            "install scientific-camera-capture[export] for pixel conversion"
        ) from exc
    meta = frame.metadata
    if meta.status != "complete":
        raise IncompleteFrameError("incomplete frames cannot be scientifically decoded")
    spec = lookup(meta.pixel_format)
    if not spec.decodable:
        raise UnsupportedFeatureError(f"no verified converter for {meta.pixel_format}; use RAW")
    if meta.pixel_format_code is not None and meta.pixel_format_code != spec.code:
        raise UnsupportedFeatureError("pixel name/code mismatch; refusing conversion")
    active = spec.row_bytes(meta.width)
    padding = meta.chunks.get("image_padding", [0, 0])
    if meta.chunks.get("raw_payload_includes_chunks") or padding[1]:
        raise UnsupportedFeatureError(
            "chunk payload/trailing image padding requires verified image extraction"
        )
    stride = meta.stride if meta.stride is not None else active + padding[0]
    if stride < active or len(frame.payload) != stride * meta.height:
        raise ConfigurationError(
            "payload length/stride inconsistent with explicitly registered layout"
        )
    rows = (
        np.frombuffer(frame.payload, dtype=np.uint8).reshape(meta.height, stride)[:, :active].copy()
    )
    if spec.packing == "u8":
        return rows.reshape(meta.height, meta.width)
    if spec.packing == "u16le":
        return rows.view("<u2").reshape(meta.height, meta.width).copy()
    if spec.packing == "p10lsb":
        groups = rows.reshape(meta.height, -1, 5).astype(np.uint16)
        b0, b1, b2, b3, b4 = (groups[:, :, i] for i in range(5))
        out = np.empty((meta.height, meta.width // 4, 4), dtype=np.uint16)
        out[:, :, 0] = b0 | ((b1 & 3) << 8)
        out[:, :, 1] = (b1 >> 2) | ((b2 & 15) << 6)
        out[:, :, 2] = (b2 >> 4) | ((b3 & 63) << 4)
        out[:, :, 3] = (b3 >> 6) | (b4 << 2)
        return out.reshape(meta.height, meta.width)
    if spec.packing == "p12lsb":
        groups = rows.reshape(meta.height, -1, 3).astype(np.uint16)
        b0, b1, b2 = (groups[:, :, i] for i in range(3))
        out = np.empty((meta.height, meta.width // 2, 2), dtype=np.uint16)
        out[:, :, 0] = b0 | ((b1 & 15) << 8)
        out[:, :, 1] = (b1 >> 4) | (b2 << 4)
        return out.reshape(meta.height, meta.width)
    raise UnsupportedFeatureError(f"no converter for registered packing {spec.packing}")
