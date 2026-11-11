"""Capability-driven configuration validation; unknown does not imply support."""

import math
from dataclasses import fields, replace

from .errors import ConfigurationError, UnsupportedFeatureError
from .types import CameraCapabilities, CameraConfiguration


def negotiate(
    current: CameraConfiguration, requested: CameraConfiguration, caps: CameraCapabilities
) -> CameraConfiguration:
    for item in fields(requested):
        value = getattr(requested, item.name)
        if value is None:
            continue
        bound = getattr(caps, item.name, None)
        if item.name in (
            "width",
            "height",
            "offset_x",
            "offset_y",
            "frame_rate",
            "exposure_us",
            "gain",
        ):
            if bound is None:
                raise UnsupportedFeatureError(f"{item.name} is unavailable")
            if not math.isfinite(value) or not bound.minimum <= value <= bound.maximum:
                raise ConfigurationError(f"{item.name} outside {bound}")
            if item.name in ("width", "height", "offset_x", "offset_y") and int(value) != value:
                raise ConfigurationError(f"{item.name} must be an integer")
            if bound.increment:
                steps = (value - bound.minimum) / bound.increment
                if not math.isclose(steps, round(steps), abs_tol=1e-6):
                    raise ConfigurationError(f"{item.name} violates increment {bound.increment}")
        choices = {
            "pixel_format": caps.pixel_formats,
            "trigger_mode": caps.trigger_modes,
            "trigger_source": caps.trigger_sources,
        }.get(item.name)
        if choices is not None and value not in choices:
            raise UnsupportedFeatureError(f"{item.name}={value!r} is not supported")
        if item.name == "gain_unit" and value != (caps.gain.unit if caps.gain else None):
            raise ConfigurationError("gain units do not match camera readback")
    return replace(
        current,
        **{
            f.name: getattr(requested, f.name)
            for f in fields(requested)
            if getattr(requested, f.name) is not None
        },
    )
