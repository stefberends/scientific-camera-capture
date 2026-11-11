"""IDS grouped Bayer is preserved RAW; exact decoding needs independent golden fixtures."""

from ..errors import UnsupportedFeatureError


def decode_ids_grouped(*_args, **_kwargs):
    raise UnsupportedFeatureError(
        "IDS grouped packing is distinct from PFNC p packing. Conversion is disabled pending "
        "documented layouts plus golden files and provenance from IDS peak or a trusted reference."
    )
