"""Repeatable simulator writes plus concurrent snapshot latency and checksum readback.

python benchmarks/capture_stress.py --seconds 3 --width 4056 --height 3040 \
    --fps 18.2 --pixel-format BayerRG12p --report report.json
Temporary raw data is deleted after verification; reports can be retained.
"""

import argparse
import json
import platform
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path

from scientific_camera import CameraConfiguration, CameraManager, RecordingReader


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=480)
    parser.add_argument("--fps", type=float, default=100)
    parser.add_argument("--seconds", type=float, default=2)
    parser.add_argument("--pixel-format", default="Mono8")
    parser.add_argument("--snapshots", type=int, default=8)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if args.seconds <= 0 or args.snapshots < 0:
        parser.error("positive duration and nonnegative snapshot count required")
    latencies = []
    with tempfile.TemporaryDirectory(prefix="scicam-stress-") as temporary:
        directory = Path(temporary) / "stress.scc"
        with CameraManager().open("simulated") as camera:
            camera.configure(
                CameraConfiguration(
                    width=args.width,
                    height=args.height,
                    frame_rate=args.fps,
                    pixel_format=args.pixel_format,
                )
            )
            started = time.monotonic()
            with camera.start_stream() as stream:
                recorder = stream.start_recording(directory)

                def shot(_):
                    begin = time.monotonic()
                    result = stream.snapshot(timeout=10)
                    return result, time.monotonic() - begin

                with ThreadPoolExecutor(max_workers=4) as pool:
                    futures = [pool.submit(shot, n) for n in range(args.snapshots)]
                    while time.monotonic() - started < args.seconds:
                        if recorder.error:
                            raise recorder.error
                        time.sleep(0.01)
                    results = [future.result(timeout=10) for future in futures]
                    latencies = [latency for _, latency in results]
                recorder.stop()
                stream.stop()
                stats = stream.stats()
            reader = RecordingReader(directory)
            inspection = reader.inspect()
            assert inspection["frames"] == stats.recorded_frames
            for snapshot, _ in results:
                assert reader.get_frame(snapshot.frame_id) == snapshot.frame
            report = {
                "hardware_verified": False,
                "host": platform.platform(),
                "configuration": asdict(camera.configuration),
                "stats": asdict(stats),
                "recording": recorder.summary,
                "readback": inspection,
                "snapshot_latency_seconds": latencies,
                "max_snapshot_latency_seconds": max(latencies) if latencies else None,
            }
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
