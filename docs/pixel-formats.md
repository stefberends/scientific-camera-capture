# Pixel formats and scientific export

`pixels.registry.lookup(name)` returns a named layout; unknown names are opaque RAW.
Identifiers are checked when supplied. Packing is never inferred from nominal bit depth.

| Format | Decode/export | Layout |
| --- | --- | --- |
| Mono8 | uint8 mosaic TIFF | 1 byte/sample |
| Mono16 | uint16 TIFF | little endian, 2 bytes/sample |
| BayerRG8 | uint8 RGGB mosaic, optional uint16 RGB TIFF | 1 byte/sample |
| BayerRG10p | uint16 RGGB mosaic, optional uint16 RGB TIFF | PFNC lsb-packed, 4 samples/5 bytes |
| BayerRG12p | uint16 RGGB mosaic, optional uint16 RGB TIFF | PFNC lsb-packed, 2 samples/3 bytes |
| BayerRG10g40IDS / BayerRG12g24IDS | original RAW only | distinct vendor grouped formats |
| Unknown | original RAW only | no assumptions |

The standard `p` layouts follow [EMVA PFNC 2.4](https://www.emva.org/wp-content/uploads/GenICam_PFNC_2_4.pdf)
and [Teledyne's primary packing reference](https://www.teledynevisionsolutions.com/en-gb/support/support-center/technical-guidance/iis/understanding-pixel-formats/).
These are distinct from older `*Packed` layouts. The first byte is the first sample's low
8 bits; successive sample bits are contiguous, least significant first. Tests use independently
specified byte vectors and a separate integer-bitstream test packer with padding/row boundaries.
Only complete groups are accepted: widths divisible by 4 for 10p and by 2 for 12p. Partial
tail groups are deliberately rejected. Explicit per-row padding is supported. Chunk-bearing
transport payloads and trailing image padding need verified image extraction before export.

`export_frame(frame, path, format='raw'|'tiff'|'rgb-tiff')` never changes the recording.
RAW+JSON supports every format, even incomplete payloads. TIFF rejects incomplete frames,
unknown layout, mismatched PFNC code, or inconsistent payload/stride. Install `[export]` for
NumPy and tifffile. Mosaic TIFF uses grayscale samples with a JSON sidecar containing CFA,
bit depth, units, source metadata and representation. It does not claim interoperable TIFF
CFA tags, and must not be mistaken for finished RGB. Original 10/12-bit sample values remain
0..1023/4095 in uint16, with no automatic scaling or truncation.

RGB TIFF is explicitly uint16 in original sample units. Conversion uses simple bilinear
RGGB interpolation with symmetric boundaries. Tests verify channel orientation, constant
planes, dynamic range and TIFF round trips; they do **not** establish camera colour calibration.
White balance, gamma, black level and colour matrix are all absent and documented in the
sidecar. No hidden colour corrections occur. Use the raw mosaic for quantitative work.
Vendor scientific colour output is not claimed.

`stream.start_preview(max_dimension=256)` uses a separate best-effort worker, downsampling
in CFA-preserving 2x2 blocks before demosaic. `last_image` is a disposable preview; `error`
reports unsupported conversion. Preview skips are separate stats. Recording never waits
for preview. TIFF export runs only when explicitly requested.

IDS grouped conversion is disabled. Enabling it requires exact vendor packing specifications,
tail/row fixtures, independent IDS peak/trusted golden files with provenance, sample-by-sample
comparison and debayer orientation tests. No guessed decoder is supplied.

Dependency licenses: original project MIT; separately installed Aravis LGPL-2.1-or-later;
PyGObject LGPL-2.1-or-later; NumPy BSD-3-Clause (binary distributions may bundle additional
licensed libraries); tifffile BSD-3-Clause. No native dependency source/binary is vendored.
Review the exact distributions' notices before release or binary redistribution.
