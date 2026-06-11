from __future__ import annotations


def parse_timecode(value: str | float | int | None) -> float | None:
    if value is None:
        return None
    if isinstance(value, int | float):
        return float(value)

    text = value.strip()
    if not text:
        raise ValueError("time value cannot be empty")
    if ":" not in text:
        return float(text)

    parts = text.split(":")
    if len(parts) not in {2, 3}:
        raise ValueError(f"invalid time value: {value!r}")

    try:
        numbers = [float(part) for part in parts]
    except ValueError as exc:
        raise ValueError(f"invalid time value: {value!r}") from exc

    if any(number < 0 for number in numbers):
        raise ValueError("time values must be >= 0")

    if len(numbers) == 2:
        minutes, seconds = numbers
        if seconds >= 60:
            raise ValueError("seconds must be less than 60 in MM:SS time values")
        return minutes * 60 + seconds

    hours, minutes, seconds = numbers
    if minutes >= 60 or seconds >= 60:
        raise ValueError("minutes and seconds must be less than 60 in HH:MM:SS time values")
    return hours * 3600 + minutes * 60 + seconds

