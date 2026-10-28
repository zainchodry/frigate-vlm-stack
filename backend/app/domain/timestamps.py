from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def parse_timestamp(value: Any) -> datetime | None:
    """Parse a source timestamp and return an aware UTC datetime.

    Numeric values are treated as Unix seconds, with millisecond values
    accepted when their magnitude is clearly larger than a seconds epoch.
    Naive ISO values are interpreted as UTC because source systems emit UTC
    timestamps for event data.
    """
    if value is None or value == "":
        return None

    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, bool):
        raise ValueError("boolean is not a timestamp")
    elif isinstance(value, (int, float)):
        seconds = float(value)
        if abs(seconds) > 100_000_000_000:
            seconds /= 1000
        return datetime.fromtimestamp(seconds, tz=UTC)
    elif isinstance(value, str):
        text = value.strip()
        try:
            numeric = float(text)
        except ValueError:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        else:
            if abs(numeric) > 100_000_000_000:
                numeric /= 1000
            return datetime.fromtimestamp(numeric, tz=UTC)
    else:
        raise TypeError(f"unsupported timestamp type: {type(value).__name__}")

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
