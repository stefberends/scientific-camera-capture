from scientific_camera import CameraConfiguration


def test_owned_bytes_and_sequence(manager):
    with manager.open("simulated") as camera:
        camera.configure(CameraConfiguration(frame_rate=200))
        with camera.start_stream(retention_frames=2) as stream:
            first = stream.next_frame()
            original = bytes(first.payload)
            last = first
            for _ in range(5):
                last = stream.next_frame(after_index=last.metadata.frame_id)
            assert last.metadata.frame_id > first.metadata.frame_id
            assert original == first.payload
            assert first.payload[:4] == bytes((n + first.metadata.frame_id) % 256 for n in range(4))
            assert stream.stats().retention_evictions > 0


def test_packed_simulator_uses_same_recording_and_decode_path(manager, tmp_path):
    from scientific_camera.pixels.standard import decode
    from scientific_camera.recording import RecordingReader

    with manager.open("simulated") as camera:
        camera.configure(CameraConfiguration(pixel_format="BayerRG12p", width=20, height=8))
        with camera.start_stream() as stream, stream.start_recording(tmp_path / "packed.scc"):
            shot = stream.snapshot()
            assert len(shot.frame.payload) == 20 * 8 * 12 // 8
            assert decode(shot.frame).shape == (8, 20)
    assert RecordingReader(tmp_path / "packed.scc").get_frame(shot.frame_id) == shot.frame


def test_mutable_transport_bytes_are_copied():
    from scientific_camera import Frame, FrameMetadata

    transport = bytearray(b"sensor pixels")
    meta = FrameMetadata(
        "reuse", 0, 0, "complete", 1, len(transport), 1, len(transport), len(transport), "Mono8"
    )
    frame = Frame(meta, transport)
    transport[:] = b"x" * len(transport)
    assert frame.payload == b"sensor pixels"
