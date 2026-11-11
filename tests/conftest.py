import pytest

from scientific_camera import CameraManager
from scientific_camera.backends.simulated import SimulatedBackend


@pytest.fixture
def manager():
    return CameraManager([SimulatedBackend()])


def pytest_addoption(parser):
    parser.addoption("--run-hardware", action="store_true", default=False)


def pytest_collection_modifyitems(config, items):
    if not config.getoption("--run-hardware"):
        skip = pytest.mark.skip(reason="hardware requires --run-hardware")
        for item in items:
            if "hardware" in item.keywords:
                item.add_marker(skip)
