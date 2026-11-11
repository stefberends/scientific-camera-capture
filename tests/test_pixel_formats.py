from dataclasses import replace

import numpy as np
import pytest
import tifffile

from scientific_camera import CameraConfiguration, Frame, FrameMetadata
from scientific_camera.errors import ConfigurationError, UnsupportedFeatureError
from scientific_camera.pixels.demosaic import demosaic_rggb
from scientific_camera.pixels.export import export_frame
from scientific_camera.pixels.registry import lookup
from scientific_camera.pixels.standard import decode


def frame(payload, name, width, height=1, stride=None):
    return Frame(
        FrameMetadata("fixture", 0, 0, "complete", 123, width, height, stride, len(payload), name),
        payload,
    )


@pytest.mark.parametrize(
    "name,payload,values",
    [
        # Independent, manually specified PFNC LSB fixtures, not produced by test packer.
        ("BayerRG10p", bytes.fromhex("00 fc 0f 40 55"), [0, 1023, 0, 341]),
        ("BayerRG10p", bytes.fromhex("ff 03 a0 ea ff"), [1023, 0, 682, 1023]),
        ("BayerRG12p", bytes.fromhex("00 f0 ff"), [0, 4095]),
        ("BayerRG12p", bytes.fromhex("aa 5a 55"), [2730, 1365]),
    ],
)
def test_standard_independent_fixtures(name, payload, values):
    assert decode(frame(payload, name, len(values))).tolist() == [values]


@pytest.mark.parametrize("bits,name,group", [(10, "BayerRG10p", 4), (12, "BayerRG12p", 2)])
def test_packed_rows_padding_and_roundtrip(bits, name, group):
    rng = np.random.default_rng(42)
    values = rng.integers(0, 1 << bits, (4, group * 5), dtype=np.uint16)
    rows = []
    for row in values:
        packed = sum(int(value) << (i * bits) for i, value in enumerate(row))
        rows.append(packed.to_bytes(len(row) * bits // 8, "little") + b"pad")
    result = decode(frame(b"".join(rows), name, values.shape[1], 4, len(rows[0])))
    np.testing.assert_array_equal(result, values)


def test_unpack_mono16_padding_and_reject_ambiguous_layouts(tmp_path):
    source = frame(b"\x00\x00\xff\xffxx\x34\x12\x78\x56xx", "Mono16", 2, 2, 6)
    assert decode(source).tolist() == [[0, 65535], [0x1234, 0x5678]]
    files = export_frame(source, tmp_path / "samples.tiff", format="tiff")
    assert tifffile.imread(files[0]).tolist() == decode(source).tolist()
    with pytest.raises(ConfigurationError):
        decode(frame(b"short", "Mono16", 5))
    with pytest.raises(UnsupportedFeatureError):
        decode(frame(b"123", "BayerRG10p", 2))
    for name in ("UnknownVendor", "BayerRG10g40IDS", "BayerRG12g24IDS"):
        opaque = frame(b"arbitrary original bytes", name, 2)
        assert not lookup(name).decodable
        files = export_frame(opaque, tmp_path / name)
        assert open(files[0], "rb").read() == opaque.payload
        with pytest.raises(UnsupportedFeatureError):
            decode(opaque)
    with pytest.raises(UnsupportedFeatureError, match="name/code"):
        decode(Frame(replace(source.metadata, pixel_format_code=123), source.payload))


def test_demosaic_orientation_dynamic_range_and_export(tmp_path):
    mosaic = np.empty((6, 8), dtype=np.uint16)
    mosaic[0::2, 0::2], mosaic[0::2, 1::2] = 4095, 1700
    mosaic[1::2, 0::2], mosaic[1::2, 1::2] = 1700, 321
    rgb = demosaic_rggb(mosaic)
    for channel, value in enumerate((4095, 1700, 321)):
        assert np.all(rgb[:, :, channel] == value)
    sample = frame(bytes([240, 100, 240, 100, 100, 20, 100, 20]), "BayerRG8", 4, 2)
    files = export_frame(sample, tmp_path / "rgb.tiff", format="rgb-tiff")
    rgb = tifffile.imread(files[0])
    assert rgb.dtype == np.uint16 and rgb.shape == (2, 4, 3)
    assert np.all(rgb[:, :, 0] == 240)


def test_preview_is_independent(manager, tmp_path):
    import time

    with manager.open("simulated") as camera:
        camera.configure(CameraConfiguration(pixel_format="BayerRG8"))
        with camera.start_stream() as stream, stream.start_recording(tmp_path / "preview.scc"):
            with stream.start_preview(max_dimension=16) as preview:
                deadline = time.monotonic() + 3
                while (
                    preview.last_image is None
                    and preview.error is None
                    and time.monotonic() < deadline
                ):
                    time.sleep(0.005)
                assert preview.error is None
                assert preview.last_image.ndim == 3
                assert max(preview.last_image.shape[:2]) <= 16
