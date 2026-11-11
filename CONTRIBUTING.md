# Contributing

Use focused branches and small commits. Install with `pip install -e '.[dev]'` in a venv.
Run `pytest`, `ruff check .`, `mypy src`, and `python -m build` before a PR.
Keep hardware tests opt-in and include device, firmware, host, and transport provenance.
Do not report simulator runs as camera performance measurements. Describe the final diff,
why it is needed, and the actual checks executed.

