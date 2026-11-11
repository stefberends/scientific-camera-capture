"""Explicit PFNC layout registry. Unknown/vendor formats remain opaque RAW."""

from dataclasses import dataclass

from ..errors import UnsupportedFeatureError


@dataclass(frozen=True)
class PixelFormat:
    name: str
    code: int | None
    bit_depth: int | None
    packing: str
    cfa: str | None = None
    decodable: bool = False
    group_pixels: int = 1
    group_bytes: int | None = None

    def row_bytes(self, width: int) -> int:
        if not self.decodable or self.group_bytes is None or width % self.group_pixels:
            raise UnsupportedFeatureError(f"unverified width/layout for {self.name}: {width}")
        return width // self.group_pixels * self.group_bytes


_formats = {
    item.name: item
    for item in (
        PixelFormat("Mono8", 0x01080001, 8, "u8", decodable=True, group_bytes=1),
        PixelFormat("Mono16", 0x01100007, 16, "u16le", decodable=True, group_bytes=2),
        PixelFormat("BayerRG8", 0x01080009, 8, "u8", "RGGB", True, group_bytes=1),
        PixelFormat("BayerRG10p", 0x010A0058, 10, "p10lsb", "RGGB", True, 4, 5),
        PixelFormat("BayerRG12p", 0x010C0059, 12, "p12lsb", "RGGB", True, 2, 3),
        PixelFormat("BayerRG10g40IDS", None, 10, "vendor_grouped_lsb", "RGGB"),
        PixelFormat("BayerRG12g24IDS", None, 12, "vendor_grouped_lsb", "RGGB"),
    )
}


def lookup(name: str) -> PixelFormat:
    return _formats.get(name, PixelFormat(name, None, None, "opaque_unknown"))


def register(pixel_format: PixelFormat):
    if pixel_format.name in _formats:
        raise ValueError(f"format {pixel_format.name} already registered")
    _formats[pixel_format.name] = pixel_format
