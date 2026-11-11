"""Scientific RAW or TIFF exports with explicit representation and processing metadata."""

from dataclasses import asdict
from pathlib import Path

from ..errors import BackendUnavailableError, UnsupportedFeatureError
from ..recording.schema import atomic_json
from ..snapshots import save_raw
from ..types import Frame
from .demosaic import demosaic_rggb
from .registry import lookup
from .standard import decode


def export_frame(frame: Frame, output: Path | str, *, format: str = "raw") -> tuple[str, ...]:
    output = Path(output)
    if format == "raw":
        return save_raw(frame, output)
    if format not in ("tiff", "rgb-tiff"):
        raise UnsupportedFeatureError(f"unknown export format {format}")
    data = decode(frame)
    spec = lookup(frame.metadata.pixel_format)
    representation = "raw Bayer mosaic" if spec.cfa else "raw monochrome"
    if format == "rgb-tiff":
        if spec.cfa != "RGGB":
            raise UnsupportedFeatureError("RGB export requires supported Bayer RGGB input")
        data = demosaic_rggb(data)
        representation = (
            "linear interpolated RGB, uint16 in original sensor sample units; not colour calibrated"
        )
    try:
        import tifffile
    except ImportError as exc:
        raise BackendUnavailableError("install scientific-camera-capture[export] for TIFF") from exc
    output.parent.mkdir(parents=True, exist_ok=True)
    sidecar = output.with_suffix(output.suffix + ".json")
    if output.exists() or sidecar.exists():
        raise FileExistsError(output)
    description = {
        "source": asdict(frame.metadata),
        "representation": representation,
        "cfa": spec.cfa,
        "source_bit_depth": spec.bit_depth,
        "output_dtype": str(data.dtype),
        "white_balance": None,
        "gamma": None,
        "black_level": None,
        "colour_matrix": None,
        "interpolation": "bilinear RGGB, symmetric boundary" if format == "rgb-tiff" else None,
        "calibration": "not performed",
        "source_payload_modified": False,
    }
    # Deliberately use a JSON sidecar instead of claiming an interoperable TIFF CFA tag profile.
    with output.open("xb") as target:
        tifffile.imwrite(
            target,
            data,
            photometric="rgb" if format == "rgb-tiff" else "minisblack",
            metadata=description,
        )
    atomic_json(sidecar, description)
    return str(output.absolute()), str(sidecar.absolute())
