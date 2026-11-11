"""scicam CLI; all durations are seconds and MB/s uses decimal megabytes."""

from __future__ import annotations

import argparse
import ctypes.util
import json
import math
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from . import __version__
from .backends.aravis import AravisBackend, load_aravis
from .backends.simulated import SimulatedBackend
from .errors import BackendUnavailableError, CameraError, ConfigurationError
from .manager import CameraManager
from .recording import RecordingReader, WriterOptions, repair_recording
from .types import CameraConfiguration


def positive(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError("must be finite and positive")
    return parsed


def doctor(*, usb: bool = False) -> dict:
    report: dict[str, Any] = {
        "version": __version__,
        "python": sys.version,
        "python_executable": sys.executable,
        "architecture": platform.machine(),
        "os": platform.platform(),
        "brew": shutil.which("brew"),
        "brew_prefix": None,
        "aravis_library": ctypes.util.find_library("aravis-0.8"),
        "GI_TYPELIB_PATH": os.environ.get("GI_TYPELIB_PATH"),
        "DYLD_LIBRARY_PATH": os.environ.get("DYLD_LIBRARY_PATH"),
        "arv_tool": shutil.which("arv-tool-0.8"),
        "arv_camera_test": shutil.which("arv-camera-test-0.8"),
        "usb_speed": "unknown; request --usb to collect OS USB topology",
        "hardware_validation": "NOT TESTED",
    }
    if report["brew"]:
        try:
            result = subprocess.run(
                [report["brew"], "--prefix"], capture_output=True, text=True, timeout=5
            )
            report["brew_prefix"] = (
                result.stdout.strip() if result.returncode == 0 else result.stderr.strip()
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            report["brew_diagnostic"] = str(exc)
    try:
        api = load_aravis()
        report["aravis_version"] = ".".join(
            str(f()) for f in (api.get_major_version, api.get_minor_version, api.get_micro_version)
        )
        report["binding_signatures"] = {
            name: str(method.__doc__)
            for name, method in (
                ("Camera.create_stream", api.Camera.create_stream),
                ("Buffer.get_data", api.Buffer.get_data),
                (
                    "Camera.dup_available_enumerations_as_strings",
                    api.Camera.dup_available_enumerations_as_strings,
                ),
            )
        }
        report["aravis_devices"] = [asdict(d) for d in AravisBackend().discover()]
        report["aravis_available"] = True
    except (BackendUnavailableError, AttributeError) as exc:
        report["aravis_available"] = False
        report["aravis_diagnostic"] = str(exc)
    if usb and platform.system() == "Darwin":
        try:
            result = subprocess.run(
                ["system_profiler", "SPUSBDataType", "-json"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            report["usb_topology"] = (
                json.loads(result.stdout) if result.returncode == 0 else result.stderr
            )
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            report["usb_topology_error"] = str(exc)
    return report


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="scicam", description="Loss-aware scientific RAW capture")
    root.add_argument("--version", action="version", version=__version__)
    commands = root.add_subparsers(dest="command", required=True)
    diagnose = commands.add_parser(
        "doctor", help="diagnose optional native imports and host architecture"
    )
    diagnose.add_argument("--usb", action="store_true")
    diagnose.add_argument("--require-aravis", action="store_true")
    diagnose.add_argument("--json", action="store_true")
    discover = commands.add_parser("discover", help="list stable camera IDs")
    discover.add_argument("--backend", action="append", choices=["simulated", "aravis"])
    discover.add_argument("--json", action="store_true")
    for command in ("inspect", "record", "snapshot", "benchmark", "camera-test"):
        item = commands.add_parser(
            command,
            help={
                "inspect": "inspect capabilities and current configuration",
                "record": "record native RAW to SCC",
                "snapshot": "capture one frame while idle",
                "benchmark": "measure acquisition and raw writes",
                "camera-test": "receive frames without disk recording (hardware is opt-in by ID)",
            }[command],
        )
        item.add_argument("--camera", default="simulated")
        item.add_argument("--json", action="store_true")
        item.add_argument("--usb-mode", choices=["sync", "async"])
        item.add_argument("--transport-buffers", type=int, default=4)
        item.add_argument("--transport-memory-mb", type=positive, default=128)
        if command == "inspect":
            item.add_argument("--genicam-xml", type=Path)
            continue
        for name in ("width", "height"):
            item.add_argument("--" + name, type=int)
        item.add_argument("--pixel-format")
        item.add_argument("--fps", type=positive)
        item.add_argument("--exposure-us", type=positive)
        if command == "snapshot":
            item.add_argument("--mode", choices=["single"], default="single")
            item.add_argument("--trigger", choices=["software", "free_run"], default="free_run")
            item.add_argument("--output", type=Path, required=True)
            item.add_argument("--timeout", type=positive, default=5)
            continue
        item.add_argument("--seconds", type=positive, default=5)
        item.add_argument("--output", type=Path, required=command == "record")
        item.add_argument("--report", type=Path, help="write JSON and text performance reports")
        item.add_argument("--queue-frames", type=int, default=16)
        item.add_argument(
            "--memory-mb", type=positive, default=128, help="recording queue budget, MiB"
        )
        item.add_argument("--retention-mb", type=positive, default=64)
        item.add_argument("--segment-mb", type=positive, default=1024)
    inspect = commands.add_parser(
        "inspect-recording", help="verify indexed lengths, order and checksums"
    )
    inspect.add_argument("input", type=Path)
    inspect.add_argument("--json", action="store_true")
    repair = commands.add_parser(
        "repair", help="repair invalid trailing records with an audit and backup"
    )
    repair.add_argument("input", type=Path)
    repair.add_argument("--json", action="store_true")
    export = commands.add_parser("export", help="export exact RAW or a supported scientific TIFF")
    export.add_argument("--input", type=Path, required=True)
    export.add_argument("--frame", type=int, required=True, help="stream acquisition index")
    export.add_argument("--format", choices=["raw", "tiff", "rgb-tiff"], default="raw")
    export.add_argument("--output", type=Path, required=True)
    export.add_argument("--json", action="store_true")
    return root


def _manager(args) -> CameraManager:
    return CameraManager(
        [
            SimulatedBackend(),
            AravisBackend(
                buffer_count=args.transport_buffers,
                max_pool_bytes=int(args.transport_memory_mb * 1024 * 1024),
                usb_mode=args.usb_mode,
            ),
        ]
    )


def _run_capture(args, camera) -> dict:
    temporary = tempfile.TemporaryDirectory(prefix="scicam-benchmark-") if not args.output else None
    destination = args.output
    if destination is None:
        assert temporary is not None
        destination = Path(temporary.name) / "benchmark.scc"
    cpu_start = time.process_time()
    started = time.monotonic()
    windows = []
    previous_time, previous_count = started, 0
    try:
        with camera.start_stream(retention_bytes=int(args.retention_mb * 1024 * 1024)) as stream:
            recorder = None
            if args.command != "camera-test":
                recorder = stream.start_recording(
                    destination,
                    options=WriterOptions(
                        queue_frames=args.queue_frames,
                        queue_bytes=int(args.memory_mb * 1024 * 1024),
                        segment_bytes=int(args.segment_mb * 1024 * 1024),
                    ),
                )
            while time.monotonic() - started < args.seconds:
                if stream._error:
                    raise stream._error
                if recorder and recorder.error:
                    raise recorder.error
                time.sleep(min(0.02, max(0, args.seconds - (time.monotonic() - started))))
                now = time.monotonic()
                if now - previous_time >= 1:
                    count = stream.stats().delivered_frames
                    windows.append(
                        {
                            "elapsed_seconds": now - started,
                            "received_fps": (count - previous_count) / (now - previous_time),
                        }
                    )
                    previous_count, previous_time = count, now
            if recorder:
                recorder.stop()
            stream.stop()
            stats = stream.stats()
        elapsed = time.monotonic() - started
        checked = None
        check_seconds = None
        if recorder:
            verify_start = time.monotonic()
            checked = RecordingReader(destination).inspect()
            check_seconds = time.monotonic() - verify_start
            if checked["frames"] != recorder.summary["recorded_frames"]:
                raise ConfigurationError("recording summary/index count mismatch")
        import resource

        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        report = {
            "schema_version": 1,
            "backend": camera.info.backend,
            "camera": asdict(camera.info),
            "configuration": asdict(camera.configuration),
            "hardware_verified": False,
            "host": {
                "os": platform.platform(),
                "architecture": platform.machine(),
                "python": sys.version,
            },
            "duration_seconds": elapsed,
            "windows": windows,
            "stats": asdict(stats),
            "nominal_fps": camera.configuration.frame_rate,
            "cpu_seconds": time.process_time() - cpu_start,
            "peak_rss_bytes": peak if platform.system() == "Darwin" else peak * 1024,
            "image_export_seconds": None,
            "recording_validation": checked,
            "readback_seconds": check_seconds,
            "recording": recorder.summary if recorder else None,
            "usb_mode": args.usb_mode or "Aravis build default",
            "retained_output": str(args.output) if args.output else None,
        }
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.with_suffix(".json").write_text(json.dumps(report, indent=2) + "\n")
            args.report.with_suffix(".txt").write_text(
                f"{camera.info.backend}: {stats.delivered_frames} received, {stats.recorded_frames} written\n"
                f"{stats.actual_fps:.2f} received fps; {stats.disk_mb_s:.2f} MB/s; {stats.integrity}\n"
                "This is an observation, not hardware certification. See JSON for losses and clocks.\n"
            )
        return report
    finally:
        if temporary:
            temporary.cleanup()


def dispatch(args):
    if args.command == "doctor":
        report = doctor(usb=args.usb)
        if args.require_aravis and not report["aravis_available"]:
            raise BackendUnavailableError(report["aravis_diagnostic"])
        return report
    if args.command == "discover":
        manager = CameraManager()
        devices = manager.discover(args.backend)
        if args.backend and all(b in manager.diagnostics for b in args.backend):
            raise BackendUnavailableError(str(manager.diagnostics))
        return {"devices": [asdict(d) for d in devices], "diagnostics": manager.diagnostics}
    if args.command == "inspect-recording":
        return RecordingReader(args.input).inspect()
    if args.command == "repair":
        return repair_recording(args.input)
    if args.command == "export":
        from .pixels.export import export_frame

        return {
            "files": export_frame(
                RecordingReader(args.input).get_frame(args.frame), args.output, format=args.format
            )
        }
    with _manager(args).open(args.camera) as camera:
        if args.command == "inspect":
            if args.genicam_xml:
                if not hasattr(camera.device, "genicam_xml"):
                    raise ConfigurationError("backend has no GenICam XML")
                args.genicam_xml.write_bytes(camera.device.genicam_xml())
            return {
                "camera": asdict(camera.info),
                "capabilities": asdict(camera.capabilities()),
                "configuration": asdict(camera.configuration),
            }
        camera.configure(
            CameraConfiguration(
                width=args.width,
                height=args.height,
                pixel_format=args.pixel_format,
                frame_rate=args.fps,
                exposure_us=args.exposure_us,
            )
        )
        if args.command == "snapshot":
            from .snapshots import snapshot_row

            return snapshot_row(
                camera.capture_single(
                    args.trigger, save=True, output=args.output, timeout=args.timeout
                ),
                None,
            )
        return _run_capture(args, camera)


def main(argv: list[str] | None = None) -> int:
    root = parser()
    args = root.parse_args(argv)
    try:
        result = dispatch(args)
        print(json.dumps(result, indent=None if args.json else 2, allow_nan=False))
        return 0
    except (CameraError, OSError, ValueError) as exc:
        print(json.dumps({"error": type(exc).__name__, "message": str(exc)}), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("capture interrupted; inspect recording state before reuse", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
