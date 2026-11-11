"""Wall/monotonic anchors label events; they do not synchronize device clocks."""

from datetime import datetime, timedelta, timezone
from time import monotonic_ns


def anchor() -> dict[str, str | int]:
    return {
        "host_monotonic_ns": monotonic_ns(),
        "host_utc": datetime.now(timezone.utc).isoformat(),
        "uncertainty_ns": "unknown",
    }


def anchored_utc(clock_anchor: dict, timestamp_ns: int) -> str:
    utc = datetime.fromisoformat(clock_anchor["host_utc"])
    return (
        utc + timedelta(microseconds=(timestamp_ns - clock_anchor["host_monotonic_ns"]) / 1000)
    ).isoformat()
