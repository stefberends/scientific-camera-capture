"""Discover by stable identity without requiring any native SDK."""

from scientific_camera import CameraManager

manager = CameraManager()
for info in manager.discover():
    print(info.id, info.display_label)
print("Optional backend diagnostics:", manager.diagnostics)
