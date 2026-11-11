"""Deterministic camera using the real acquisition and recording contracts."""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass

from ..capabilities import negotiate
from ..errors import CameraDisconnectedError, ConfigurationError, UnsupportedFeatureError
from ..types import (
    CameraCapabilities,
    CameraConfiguration,
    CameraInfo,
    Frame,
    FrameMetadata,
    NumericRange,
)

INFO = CameraInfo(
    "simulated",
    "simulated",
    "Scientific Camera",
    "Deterministic simulator",
    "SIM-0001",
    "simulated",
    "Simulated Mono/Bayer camera",
)


@dataclass(frozen=True)
class SimulationOptions:
    """Faults are deterministic, indexed from zero; delays/periods are seconds."""

    incomplete_every: int = 0
    skip_id_every: int = 0
    missing_id_every: int = 0
    reset_id_at: int | None = None
    frame_id_modulus: int = 65536
    initial_frame_id: int = 0
    disconnect_after: int | None = None
    delay_every: int = 0
    delay_seconds: float = 0.0
    time_scale: float = 1.0
    software_trigger_supported: bool = True
    timestamps: bool = True

    def __post_init__(self) -> None:
        if not math.isfinite(self.time_scale) or not math.isfinite(self.delay_seconds):
            raise ConfigurationError("simulation timing must be finite")
        if self.frame_id_modulus < 2 or self.time_scale <= 0 or self.delay_seconds < 0:
            raise ConfigurationError("invalid simulation clock/counter options")
        if (
            min(self.incomplete_every, self.skip_id_every, self.missing_id_every, self.delay_every)
            < 0
        ):
            raise ConfigurationError("fault periods must be nonnegative")


class SimulatedDevice:
    def __init__(self, options: SimulationOptions | None = None):
        self.info = INFO
        self.options = options or SimulationOptions()
        self.configuration = CameraConfiguration(
            width=64,
            height=48,
            offset_x=0,
            offset_y=0,
            pixel_format="Mono8",
            exposure_us=1000.0,
            gain=0.0,
            gain_unit="dB",
            frame_rate=30.0,
            acquisition_mode="Continuous",
            trigger_mode="Off",
            trigger_source="Software",
            trigger_selector="FrameStart",
            trigger_activation="RisingEdge",
        )
        self._condition = threading.Condition()
        self._running = False
        self._closed = False
        self._index = 0
        self._counter = self.options.initial_frame_id
        self._tokens = 0
        self._next = 0.0
        self._losses = 0

    def capabilities(self) -> CameraCapabilities:
        return CameraCapabilities(
            pixel_formats=("Mono8", "Mono16", "BayerRG8", "BayerRG10p", "BayerRG12p"),
            width=NumericRange(2, 4096, 2, "pixels"),
            height=NumericRange(2, 4096, 2, "pixels"),
            offset_x=NumericRange(0, 4094, 2, "pixels"),
            offset_y=NumericRange(0, 4094, 2, "pixels"),
            frame_rate=NumericRange(0.1, 10000, None, "Hz"),
            exposure_us=NumericRange(1, 1_000_000, 1, "us"),
            gain=NumericRange(0, 24, 0.1, "dB"),
            trigger_modes=("Off", "On"),
            trigger_sources=("Software",) if self.options.software_trigger_supported else (),
            writable_features=(
                "Width",
                "Height",
                "OffsetX",
                "OffsetY",
                "PixelFormat",
                "ExposureTime",
                "Gain",
                "AcquisitionFrameRate",
                "TriggerMode",
                "TriggerSource",
                "TriggerSelector",
                "TriggerActivation",
            ),
            readonly_features=("PayloadSize", "DeviceSerialNumber"),
            device_timestamp_frequency_hz=1_000_000 if self.options.timestamps else None,
            frame_id_modulus=self.options.frame_id_modulus,
        )

    def configure(self, config: CameraConfiguration) -> CameraConfiguration:
        if self._running or self._closed:
            raise ConfigurationError("configure requires an open, idle camera")
        candidate = negotiate(self.configuration, config, self.capabilities())
        assert candidate.width is not None and candidate.height is not None
        assert candidate.offset_x is not None and candidate.offset_y is not None
        assert candidate.pixel_format is not None
        from ..pixels.registry import lookup

        lookup(candidate.pixel_format).row_bytes(candidate.width)
        if candidate.acquisition_mode != "Continuous":
            raise UnsupportedFeatureError("simulator supports Continuous acquisition")
        if (
            candidate.trigger_selector != "FrameStart"
            or candidate.trigger_activation != "RisingEdge"
        ):
            raise UnsupportedFeatureError("unsupported simulated trigger selector/edge")
        if (
            candidate.width + candidate.offset_x > 4096
            or candidate.height + candidate.offset_y > 4096
        ):
            raise ConfigurationError("ROI exceeds simulated sensor")
        self.configuration = candidate
        return candidate

    def read_feature(self, name: str):
        mapping = {
            "Width": "width",
            "Height": "height",
            "OffsetX": "offset_x",
            "OffsetY": "offset_y",
            "PixelFormat": "pixel_format",
            "ExposureTime": "exposure_us",
            "Gain": "gain",
            "AcquisitionFrameRate": "frame_rate",
            "TriggerMode": "trigger_mode",
            "TriggerSource": "trigger_source",
            "TriggerSelector": "trigger_selector",
            "TriggerActivation": "trigger_activation",
            "AcquisitionMode": "acquisition_mode",
        }
        if name == "PayloadSize":
            c = self.configuration
            assert c.width is not None and c.height is not None and c.pixel_format is not None
            from ..pixels.registry import lookup

            return lookup(c.pixel_format).row_bytes(c.width) * c.height
        if name == "DeviceSerialNumber":
            return self.info.serial
        if name not in mapping:
            raise UnsupportedFeatureError(f"unknown simulated feature {name}")
        return getattr(self.configuration, mapping[name])

    def start(self) -> None:
        if self._closed:
            raise CameraDisconnectedError("simulator closed")
        with self._condition:
            self._index = 0
            self._counter = self.options.initial_frame_id
            self._tokens = 0
            self._losses = 0
            self._next = time.monotonic()
            self._running = True

    def software_trigger(self) -> None:
        if not self.options.software_trigger_supported:
            raise UnsupportedFeatureError("software trigger unavailable")
        with self._condition:
            if not self._running or self.configuration.trigger_mode != "On":
                raise ConfigurationError("software trigger requires an armed stream")
            self._tokens += 1
            self._condition.notify_all()

    def receive(self, timeout: float) -> Frame | None:
        deadline = time.monotonic() + timeout
        with self._condition:
            while self._running:
                now = time.monotonic()
                armed = self.configuration.trigger_mode == "On"
                if (armed and self._tokens) or (not armed and now >= self._next):
                    break
                wait = deadline - now
                if wait <= 0:
                    return None
                self._condition.wait(wait if armed else min(wait, max(0, self._next - now)))
            if not self._running:
                return None
            if (
                self.options.disconnect_after is not None
                and self._index >= self.options.disconnect_after
            ):
                raise CameraDisconnectedError("injected simulator unplug")
            if armed:
                self._tokens -= 1
            i = self._index
            self._index += 1
            assert self.configuration.frame_rate is not None
            interval = self.options.time_scale / self.configuration.frame_rate
            self._next = max(self._next + interval, time.monotonic())
            if self.options.delay_every and (i + 1) % self.options.delay_every == 0:
                self._next += self.options.delay_seconds
            if self.options.skip_id_every and i and i % self.options.skip_id_every == 0:
                self._counter += 1
                self._losses += 1
            if self.options.reset_id_at == i:
                self._counter = 0
            device_id: int | None = self._counter % self.options.frame_id_modulus
            self._counter += 1
            if self.options.missing_id_every and (i + 1) % self.options.missing_id_every == 0:
                device_id = None
        c = self.configuration
        assert c.width is not None and c.height is not None and c.pixel_format is not None
        assert c.frame_rate is not None
        # Known linear pixel pattern; immutable bytes survive subsequent backend reuse.
        block = bytes((n + i) % 256 for n in range(256))
        count = c.width * c.height
        if c.pixel_format == "Mono16":
            block = b"".join(((n + i) % 65536).to_bytes(2, "little") for n in range(256))
            count *= 2
        if c.pixel_format in ("BayerRG10p", "BayerRG12p"):
            bits, samples = (10, 4) if c.pixel_format == "BayerRG10p" else (12, 2)
            block = sum(((i + n) % (1 << bits)) << (n * bits) for n in range(samples)).to_bytes(
                samples * bits // 8, "little"
            )
            count = c.width * c.height * bits // 8
        payload = (block * ((count + len(block) - 1) // len(block)))[:count]
        receipt = time.monotonic_ns()
        incomplete = self.options.incomplete_every and (i + 1) % self.options.incomplete_every == 0
        return Frame(
            FrameMetadata(
                self.info.id,
                i,
                device_id,
                "incomplete" if incomplete else "complete",
                receipt,
                c.width,
                c.height,
                len(payload) // c.height,
                len(payload),
                c.pixel_format,
                device_timestamp=round(i * 1_000_000 / c.frame_rate)
                if self.options.timestamps
                else None,
                device_timestamp_unit="ticks" if self.options.timestamps else None,
                device_timestamp_frequency_hz=1_000_000 if self.options.timestamps else None,
                timestamp_validity="simulated_device_clock"
                if self.options.timestamps
                else "host_receipt_only",
                exposure_us=c.exposure_us,
                gain=c.gain,
                gain_unit=c.gain_unit,
            ),
            payload,
        )

    def statistics(self) -> dict[str, int | None]:
        return {"device_reported_losses": self._losses, "transport_reported_losses": 0}

    def stop(self) -> None:
        with self._condition:
            self._running = False
            self._condition.notify_all()

    def close(self) -> None:
        self.stop()
        self._closed = True


class SimulatedBackend:
    name = "simulated"

    def __init__(self, options: SimulationOptions | None = None):
        self.options = options

    def discover(self) -> list[CameraInfo]:
        return [INFO]

    def open(self, camera_id: str) -> SimulatedDevice:
        from ..errors import CameraNotFoundError

        if camera_id != INFO.id:
            raise CameraNotFoundError(camera_id)
        return SimulatedDevice(self.options)
