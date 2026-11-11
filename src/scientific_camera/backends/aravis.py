"""Lazy Aravis 0.8 GI adapter; native binding/hardware verification remains outstanding.

API basis: upstream 0.8.36 Python camera example and official stable 0.8 API documentation.
Borrowed transport bytes are copied before requeue. No disk work occurs during receive.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
import threading
import time
from dataclasses import fields
from pathlib import Path

from ..capabilities import negotiate
from ..errors import (
    BackendUnavailableError,
    CameraDisconnectedError,
    CameraInUseError,
    CameraNotFoundError,
    ConfigurationError,
    UnsupportedFeatureError,
)
from ..types import (
    CameraCapabilities,
    CameraConfiguration,
    CameraInfo,
    Frame,
    FrameMetadata,
    NumericRange,
)

FEATURES = {
    "width": "Width",
    "height": "Height",
    "offset_x": "OffsetX",
    "offset_y": "OffsetY",
    "pixel_format": "PixelFormat",
    "exposure_us": "ExposureTime",
    "gain": "Gain",
    "frame_rate": "AcquisitionFrameRate",
    "acquisition_mode": "AcquisitionMode",
    "trigger_mode": "TriggerMode",
    "trigger_source": "TriggerSource",
    "trigger_selector": "TriggerSelector",
    "trigger_activation": "TriggerActivation",
}


def load_aravis():
    try:
        import gi

        gi.require_version("Aravis", "0.8")
        from gi.repository import Aravis

        return Aravis
    except (ImportError, ValueError, OSError) as exc:
        raise BackendUnavailableError(
            f"Aravis 0.8 / PyGObject unavailable: {exc}. See docs/macos-setup.md"
        ) from exc


class DeviceLock:
    """Cooperative cross-process ownership on POSIX; lock files are never unlinked."""

    def __init__(self, camera_id: str):
        import fcntl

        key = hashlib.sha256(camera_id.encode()).hexdigest()
        path = Path(tempfile.gettempdir()) / f"scicam-{os.getuid()}-{key}.lock"
        descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        self.file = os.fdopen(descriptor, "a+b")
        try:
            fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.file.close()
            raise CameraInUseError(camera_id) from exc

    def close(self):
        self.file.close()


class AravisBackend:
    name = "aravis"

    def __init__(
        self,
        *,
        buffer_count: int = 4,
        max_pool_bytes: int = 128 * 1024 * 1024,
        usb_mode: str | None = None,
    ):
        if buffer_count < 2 or max_pool_bytes < 1 or usb_mode not in (None, "sync", "async"):
            raise ConfigurationError("invalid Aravis buffer pool or USB mode")
        self.buffer_count, self.max_pool_bytes, self.usb_mode = (
            buffer_count,
            max_pool_bytes,
            usb_mode,
        )

    def discover(self) -> list[CameraInfo]:
        api = load_aravis()
        try:
            api.update_device_list()
            result = []
            for index in range(api.get_n_devices()):
                identity = api.get_device_id(index)
                result.append(
                    CameraInfo(
                        "aravis:" + identity,
                        "aravis",
                        api.get_device_vendor(index),
                        api.get_device_model(index),
                        api.get_device_serial_nbr(index),
                        {"USB3Vision": "usb3vision", "GigEVision": "gigevision"}.get(
                            api.get_device_protocol(index), "unknown"
                        ),
                        f"{api.get_device_model(index)} ({identity})",
                    )
                )
            return result
        except Exception as exc:
            raise BackendUnavailableError(f"Aravis discovery failed: {exc}") from exc

    def open(self, camera_id: str) -> AravisDevice:
        info = next((info for info in self.discover() if info.id == camera_id), None)
        if not info:
            raise CameraNotFoundError(camera_id)
        lock = DeviceLock(camera_id)
        try:
            return AravisDevice(load_aravis(), info, self, lock)
        except BaseException:
            lock.close()
            raise


class AravisDevice:
    def __init__(self, api, info: CameraInfo, backend: AravisBackend, lock: DeviceLock):
        self.api, self.info, self.backend, self._lock = api, info, backend, lock
        self.camera = api.Camera.new(info.id.removeprefix("aravis:"))
        if self.camera is None:
            raise CameraNotFoundError(info.id)
        self.device = self.camera.get_device()
        self._disconnected = threading.Event()
        self._signal = self.device.connect("control-lost", lambda *_: self._disconnected.set())
        self._stream = None
        self._closed = False
        self._index = 0
        self._transport_stats = {
            "device_reported_losses": None,
            "transport_reported_losses": None,
            "transport_buffer_failures": None,
            "transport_buffer_underruns": None,
        }
        self.configuration = self._readback()

    def _available(self, name: str) -> bool:
        return bool(self.device.is_feature_available(name))

    def _access(self, name: str) -> str:
        if not self._available(name):
            return "unavailable"
        if not hasattr(self.device, "get_feature_access_mode"):
            return "unknown"
        access = self.device.get_feature_access_mode(name)
        return {
            self.api.GcAccessMode.RW: "rw",
            self.api.GcAccessMode.RO: "ro",
            self.api.GcAccessMode.WO: "wo",
        }.get(access, "unavailable")

    def read_feature(self, name: str):
        if not self._available(name):
            raise UnsupportedFeatureError(f"GenICam node {name} unavailable")
        node = self.device.get_feature(name)
        if isinstance(node, (self.api.GcEnumeration, self.api.GcString)):
            return self.device.get_string_feature_value(name)
        if isinstance(node, self.api.GcInteger):
            return self.device.get_integer_feature_value(name)
        if isinstance(node, self.api.GcFloat):
            return self.device.get_float_feature_value(name)
        if isinstance(node, self.api.GcBoolean):
            return self.device.get_boolean_feature_value(name)
        raise UnsupportedFeatureError(f"node {name} is not a readable scalar")

    def genicam_xml(self) -> bytes:
        xml = self.device.get_genicam_xml()
        if isinstance(xml, str):
            return xml.encode()
        if isinstance(xml, (bytes, bytearray)):
            return bytes(xml)
        raise BackendUnavailableError(f"unverified get_genicam_xml GI return type: {type(xml)}")

    def _readback(self) -> CameraConfiguration:
        values = {
            field: self.read_feature(node)
            for field, node in FEATURES.items()
            if self._available(node) and self._access(node) != "wo"
        }
        if self._available("Gain"):
            values["gain_unit"] = self.device.get_feature("Gain").get_unit() or None
        return CameraConfiguration(**values)

    def _range(self, name: str, unit: str | None = None) -> NumericRange | None:
        if not self._available(name):
            return None
        node = self.device.get_feature(name)
        if isinstance(node, self.api.GcInteger):
            low, high = self.device.get_integer_feature_bounds(name)
            step = self.device.get_integer_feature_increment(name)
        elif isinstance(node, self.api.GcFloat):
            low, high = self.device.get_float_feature_bounds(name)
            step = self.device.get_float_feature_increment(name)
        else:
            return None
        return NumericRange(low, high, step if step > 0 else None, unit or node.get_unit() or None)

    def _choices(self, name: str) -> tuple[str, ...]:
        if not self._available(name):
            return ()
        values = self.camera.dup_available_enumerations_as_strings(name)
        if not isinstance(values, (list, tuple)) or not all(isinstance(v, str) for v in values):
            raise BackendUnavailableError(
                f"unverified GI enumeration return for {name}: {type(values)}"
            )
        return tuple(values)

    def capabilities(self) -> CameraCapabilities:
        names = (
            *FEATURES.values(),
            "PayloadSize",
            "DeviceSerialNumber",
            "DeviceFirmwareVersion",
            "TLParamsLocked",
            "TriggerSoftware",
        )
        access = {name: self._access(name) for name in names}
        return CameraCapabilities(
            pixel_formats=self._choices("PixelFormat"),
            width=self._range("Width", "pixels"),
            height=self._range("Height", "pixels"),
            offset_x=self._range("OffsetX", "pixels"),
            offset_y=self._range("OffsetY", "pixels"),
            frame_rate=self._range("AcquisitionFrameRate", "Hz"),
            exposure_us=self._range("ExposureTime", "us"),
            gain=self._range("Gain"),
            trigger_modes=self._choices("TriggerMode"),
            trigger_sources=self._choices("TriggerSource"),
            writable_features=tuple(n for n, a in access.items() if a in ("rw", "wo")),
            readonly_features=tuple(n for n, a in access.items() if a == "ro"),
        )

    def configure(self, config: CameraConfiguration) -> CameraConfiguration:
        if self._stream or self._closed:
            raise ConfigurationError("native configuration requires an open idle camera")
        requested = {
            f.name: getattr(config, f.name)
            for f in fields(config)
            if getattr(config, f.name) is not None
            and getattr(config, f.name) != getattr(self.configuration, f.name)
        }
        negotiate(self.configuration, CameraConfiguration(**requested), self.capabilities())
        for field, value in requested.items():
            if field == "gain_unit":
                continue
            name = FEATURES[field]
            if self._access(name) not in ("rw", "wo"):
                raise UnsupportedFeatureError(f"{name} is not writable")
            if isinstance(value, str) and value not in self._choices(name):
                raise UnsupportedFeatureError(f"{name}={value} is not advertised")
        try:
            ordered = sorted(
                requested,
                key=lambda field: (
                    0
                    if field in ("width", "height")
                    and requested[field] < getattr(self.configuration, field)
                    else 1
                    if field in ("offset_x", "offset_y")
                    else 2
                ),
            )
            for field in ordered:
                if field == "gain_unit":
                    continue
                name, value = FEATURES[field], requested[field]
                if field in ("width", "height", "offset_x", "offset_y"):
                    self.device.set_integer_feature_value(name, value)
                elif field in ("gain", "frame_rate", "exposure_us"):
                    self.device.set_float_feature_value(name, value)
                else:
                    self.device.set_string_feature_value(name, value)
        except Exception as exc:
            self.configuration = self._readback()
            raise ConfigurationError(
                f"GenICam configuration partially applied; inspect readback: {exc}"
            ) from exc
        self.configuration = self._readback()
        return self.configuration

    def _transport_lock(self, value: int):
        if self._access("TLParamsLocked") in ("rw", "wo"):
            self.device.set_integer_feature_value("TLParamsLocked", value)

    def start(self):
        if self._closed or self._disconnected.is_set():
            raise CameraDisconnectedError(self.info.id)
        if (
            self.configuration.width is None
            or self.configuration.height is None
            or self.configuration.pixel_format is None
        ):
            raise UnsupportedFeatureError("camera has no readable frame geometry/pixel format")
        payload = self.camera.get_payload()
        if payload <= 0 or payload * self.backend.buffer_count > self.backend.max_pool_bytes:
            raise ConfigurationError(
                "transport buffers exceed memory budget; adjust pool count/limit"
            )
        if self.backend.usb_mode:
            if not self.camera.is_uv_device() or not hasattr(self.camera, "uv_set_usb_mode"):
                raise UnsupportedFeatureError("USB mode unavailable for this camera/build")
            self.camera.uv_set_usb_mode(
                self.api.UvUsbMode.SYNC
                if self.backend.usb_mode == "sync"
                else self.api.UvUsbMode.ASYNC
            )
        try:
            self._transport_lock(1)
            self._stream = self.camera.create_stream(None, None)
            if self._stream is None:
                raise BackendUnavailableError("Aravis returned no stream")
            for _ in range(self.backend.buffer_count):
                self._stream.push_buffer(self.api.Buffer.new_allocate(payload))
            self._index = 0
            self.camera.start_acquisition()
        except BaseException:
            self.stop()
            raise

    def receive(self, timeout: float) -> Frame | None:
        if self._disconnected.is_set():
            raise CameraDisconnectedError(f"control lost: {self.info.id}")
        stream = self._stream
        if stream is None:
            return None
        buffer = stream.timeout_pop_buffer(round(timeout * 1_000_000))  # Aravis uses microseconds.
        receipt = time.monotonic_ns()
        if buffer is None:
            return None
        try:
            payload = buffer.get_data()
            if not isinstance(payload, (bytes, bytearray, memoryview, list)):
                raise BackendUnavailableError(f"unverified GI buffer data type {type(payload)}")
            owned = bytes(payload)
            success = buffer.get_status() == self.api.BufferStatus.SUCCESS
            config = self.configuration
            chunks = {
                "aravis_timestamp_ns": buffer.get_timestamp(),
                "original_device_ticks_available": False,
                "buffer_status": str(buffer.get_status()),
            }
            if success:
                chunks["raw_payload_includes_chunks"] = buffer.has_chunks()
                chunks["image_padding"] = list(buffer.get_image_padding())
            metadata = FrameMetadata(
                camera_id=self.info.id,
                acquisition_index=self._index,
                device_frame_id=buffer.get_frame_id(),
                status="complete" if success else "incomplete",
                host_received_monotonic_ns=receipt,
                width=config.width,
                height=config.height,
                stride=None,
                payload_bytes=len(owned),
                pixel_format=config.pixel_format,
                pixel_format_code=buffer.get_image_pixel_format() if success else None,
                timestamp_validity="aravis_normalized_ns_device_reliability_unverified",
                exposure_us=config.exposure_us,
                gain=config.gain,
                gain_unit=config.gain_unit,
                chunks=chunks,
            )
            self._index += 1
            return Frame(metadata, owned)
        finally:
            stream.push_buffer(buffer)

    def software_trigger(self):
        if not self.camera.is_software_trigger_supported():
            raise UnsupportedFeatureError("camera does not advertise software trigger")
        self.camera.software_trigger()

    def statistics(self) -> dict[str, int | None]:
        stream = self._stream
        if stream:
            _, failures, underruns = stream.get_statistics()
            self._transport_stats.update(
                transport_buffer_failures=failures, transport_buffer_underruns=underruns
            )
        return dict(self._transport_stats)

    def stop(self):
        if self._stream is None:
            self._transport_lock(0)
            return
        stream = self._stream
        try:
            self.camera.stop_acquisition()
        finally:
            self.statistics()
            try:
                stream.stop_thread(True)
            finally:
                self._stream = None
                self._transport_lock(0)

    def close(self):
        if self._closed:
            return
        try:
            self.stop()
        finally:
            self.device.disconnect(self._signal)
            self.device = None
            self.camera = None
            self._closed = True
            self._lock.close()
