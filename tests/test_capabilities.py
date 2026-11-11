import pytest

from scientific_camera import CameraConfiguration
from scientific_camera.errors import ConfigurationError, UnsupportedFeatureError


def test_configuration_and_readback(manager):
    with manager.open("simulated") as camera:
        assert camera.capabilities().pixel_formats[0] == "Mono8"
        actual = camera.configure(CameraConfiguration(width=80, pixel_format="Mono16"))
        assert actual.width == camera.read_feature("Width") == 80
        assert camera.read_feature("PayloadSize") == 80 * 48 * 2


@pytest.mark.parametrize(
    "config,error",
    [
        (CameraConfiguration(width=3), ConfigurationError),
        (CameraConfiguration(width=0), ConfigurationError),
        (CameraConfiguration(width=4096, offset_x=2), ConfigurationError),
        (CameraConfiguration(frame_rate=float("nan")), ConfigurationError),
        (CameraConfiguration(pixel_format="fake"), UnsupportedFeatureError),
        (CameraConfiguration(gain=2, gain_unit="unknown"), ConfigurationError),
    ],
)
def test_reject_invalid_configuration(manager, config, error):
    with manager.open("simulated") as camera:
        before = camera.configuration
        with pytest.raises(error):
            camera.configure(config)
        assert before == camera.configuration
