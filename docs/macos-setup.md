# macOS setup (Intel and Apple Silicon)

Hardware acquisition is **NOT HARDWARE VERIFIED**. This checkout was developed on an Intel
macOS host with Python 3.12; Aravis/PyGObject were absent. Native installation steps below
are based on upstream packaging docs and must be validated on the target machine. No tested
GI path or macOS device compatibility is claimed. Linux remains feasible; Windows is not a
supported native adapter target (POSIX advisory ownership locks are used).

Hardware-free setup, verified with no GI/native SDK:

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
pytest
scicam discover --backend simulated --json
scicam record --camera simulated --seconds 2 --output example.scc
```

Check `uname -m`, `python3 --version`, `python3 -c 'import platform; print(platform.machine())'`,
`command -v brew` and `brew --prefix`. All native libraries and Python must have compatible
architectures. Avoid mixing Rosetta Intel Python with native arm64 Homebrew libraries.
Do not assume `/opt/homebrew` or `/usr/local`; discover the prefix.

The [official Homebrew formula](https://formulae.brew.sh/formula/aravis) documents
`brew install aravis`. PyGObject is separately installed for the **same Python** that runs
scicam; Homebrew offers `pygobject3`. Consult [PyGObject's installation guide](https://pygobject.gnome.org/getting_started.html)
for the supported interpreter/environment combination. A venv made from the matched Homebrew
Python using `--system-site-packages` may expose its installed PyGObject. Verify rather than
assuming that an arbitrary python.org or system Python can see it:

```sh
python -c 'import gi; gi.require_version("Aravis", "0.8"); from gi.repository import Aravis; print(Aravis.Camera.create_stream.__doc__)'
scicam doctor --json
```

The `[aravis]` extra requests PyGObject but does not install the native Aravis library or
Homebrew. Installing the base package never attempts a native SDK install.
Do not guess GI_TYPELIB_PATH or DYLD_LIBRARY_PATH. Diagnose the actual typelib/dylib locations
with Homebrew and `scicam doctor` before making any environment changes. Doctor reports
native import errors, architecture, interpreter, discovered libraries/tools, prefix, installed
Aravis version and GI method signatures when import succeeds. Use `doctor --require-aravis`
for a nonzero exit when the optional backend is unavailable. `doctor --usb` additionally
collects macOS USB topology with `system_profiler`; reported bus speed is not measured payload throughput.

Homebrew supplies `arv-tool-0.8` and `arv-camera-test-0.8`. Consult each `--help` for installed
version syntax. If those see the device but Python cannot import GI, fix the Python/library
environment first. If neither sees the camera, inspect device access, cable and USB bus.
Linux udev instructions do **not** apply to macOS. No privileged commands or USB permission
changes are performed by this project.

```sh
scicam discover --backend aravis --json
scicam inspect --camera 'aravis:<exact-discovered-id>' --json --genicam-xml features.xml
scicam camera-test --camera 'aravis:<exact-discovered-id>' --seconds 10
```

Use a native SuperSpeed connection, suitable cable, and sufficient SSD bandwidth/capacity.
Avoid slow hubs. Four transport buffers are allocated by default, with a 128 MiB pool cap.
Use `--transport-buffers`, `--transport-memory-mb`, `--usb-mode sync|async` to experiment.
The mode is applied only to a USB device exposing the API; unsupported modes fail explicitly.
Native buffer allocation happens after configuration. The adapter locks TLParamsLocked where
writable, copies owned bytes before requeue, and stops/deletes the pool on shutdown.

API sources: [upstream Python example, 0.8.36](https://github.com/AravisProject/aravis/blob/0.8.36/tests/python/arv-camera-test.py),
[stable camera API](https://aravisproject.github.io/aravis/aravis-stable/class.Camera.html),
[buffer data ownership](https://aravisproject.github.io/aravis/aravis-stable/method.Buffer.get_data.html),
[USB transfer modes](https://aravisproject.github.io/aravis/aravis-stable/usb.html).
The stable docs currently describe 0.8.37 while Homebrew lists 0.8.36: local signatures
must still be checked. No API mock test is evidence of a native binding/hardware pass.
