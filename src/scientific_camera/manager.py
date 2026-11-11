"""Backend registry and stable camera selection; no camera access at import time."""

from __future__ import annotations

import logging
import threading
from importlib.metadata import entry_points

from .backend import CameraBackend
from .backends.simulated import SimulatedBackend
from .errors import BackendUnavailableError, CameraInUseError, CameraNotFoundError

_in_use: set[str] = set()
_lock = threading.Lock()
logger = logging.getLogger(__name__)


class CameraManager:
    def __init__(self, backends: list[CameraBackend] | None = None):
        if backends is None:
            from .backends.aravis import AravisBackend

            backends = [SimulatedBackend(), AravisBackend()]
        self.backends = {backend.name: backend for backend in backends}
        self.diagnostics: dict[str, str] = {}

    def register_backend(self, backend: CameraBackend) -> None:
        self.backends[backend.name] = backend

    def load_plugins(self) -> None:
        """Explicitly load installed scientific_camera.backends entry-point factories."""
        for entry in entry_points(group="scientific_camera.backends"):
            self.register_backend(entry.load()())

    def discover(self, backends: list[str] | None = None):
        devices = []
        self.diagnostics.clear()
        for name in backends if backends is not None else self.backends:
            if name not in self.backends:
                raise BackendUnavailableError(f"backend {name!r} is not registered")
            try:
                devices.extend(self.backends[name].discover())
            except BackendUnavailableError as exc:
                self.diagnostics[name] = str(exc)
                logger.info("backend_unavailable", extra={"backend": name, "reason": str(exc)})
        return devices

    def open(self, camera_id: str):
        from .session import CameraSession

        info = next((d for d in self.discover() if d.id == camera_id), None)
        if info is None:
            raise CameraNotFoundError(f"{camera_id!r}; diagnostics: {self.diagnostics}")
        with _lock:
            if camera_id in _in_use:
                raise CameraInUseError(camera_id)
            _in_use.add(camera_id)

        def release():
            with _lock:
                _in_use.discard(camera_id)

        try:
            device = self.backends[info.backend].open(camera_id)
            return CameraSession(device, release)
        except BaseException:
            release()
            raise
