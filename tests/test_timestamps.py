from scientific_camera import CameraConfiguration, CameraManager
from scientific_camera.backends.simulated import SimulatedBackend, SimulationOptions
from scientific_camera.timestamps import anchor, anchored_utc


def test_anchor_does_not_invent_device_sync():
    value = anchor()
    assert anchored_utc(value, value["host_monotonic_ns"]) == value["host_utc"]


def test_wrap_gaps_and_timestamp_provenance():
    manager = CameraManager(
        [
            SimulatedBackend(
                SimulationOptions(
                    frame_id_modulus=16, initial_frame_id=14, skip_id_every=4, timestamps=False
                )
            )
        ]
    )
    with manager.open("simulated") as camera:
        camera.configure(CameraConfiguration(frame_rate=200))
        with camera.start_stream() as stream:
            frames = [stream.next_frame()]
            for _ in range(7):
                frames.append(stream.next_frame(after_index=frames[-1].metadata.frame_id))
            assert stream.stats().frame_id_gaps > 0
            assert frames[0].metadata.device_timestamp is None
            assert frames[0].metadata.timestamp_validity == "host_receipt_only"
            assert frames[0].metadata.host_capture_monotonic_ns is None
