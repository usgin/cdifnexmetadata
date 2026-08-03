"""Normalising XDI header values before they become concept values.

XDI headers are free text, and producers write things the dictionary does
not allow: a temperature as `room temperature`, a value and unit run
together as `10K`, a datetime in half a dozen shapes, an edge energy with
no unit at all. A reader that passes these through emits metadata that
says something different from what it means -- `room temperature` is not
a temperature a machine can compare, and a bare `7112` is not an energy.

These four functions are ported from `api/cdi.py` in the RML pipeline,
where they were worked out against the same 55-file corpus. They live
here rather than in the crosswalk because a crosswalk row says *which
concept a header means*, not *what its value should be read as*.

Each returns the value unchanged when it does not recognise the input.
Guessing is worse than leaving a validator to report it.
"""
from __future__ import annotations

import re
from datetime import datetime

#: What a qualitative temperature is taken to mean. 295 K is the
#: convention the XAS community uses for "room temperature"; the point is
#: less the number than that it is stated once, here, rather than assumed
#: differently by every consumer.
ROOM_TEMPERATURE_K = "295.0 K"

QUALITATIVE_TEMPERATURES = {
    "room temperature": ROOM_TEMPERATURE_K,
    "room temp": ROOM_TEMPERATURE_K,
    "roomtemperature": ROOM_TEMPERATURE_K,
    "rt": ROOM_TEMPERATURE_K,
    "ambient": ROOM_TEMPERATURE_K,
    "ambient temperature": ROOM_TEMPERATURE_K,
}

#: A numeric temperature and its unit, however they are spaced. Anchored,
#: so anything with trailing text ("10 K (nominal)") falls through
#: untouched rather than being silently truncated to the part that parsed.
_TEMPERATURE_VALUE_UNIT = re.compile(
    r"^(?P<value>[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)"
    r"\s*(?P<unit>[KkCc])$"
)

#: A number and nothing else -- including "7112." with a trailing point,
#: which real files write. A value that already carries a unit, or that
#: has already been flagged, does not match, so this is safe to run
#: repeatedly.
_BARE_NUMBER = re.compile(r"^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$")

UNITS_NOT_REPORTED = "units not reported"

#: Non-ISO datetime formats accepted, tried in order after
#: `fromisoformat` fails.
_DATETIME_FALLBACK_FORMATS = (
    "%Y/%m/%d %H:%M:%S",
    "%Y/%m/%d %H:%M",
    "%Y/%m/%dT%H:%M:%S",
    "%m/%d/%Y %H:%M:%S",
    "%m/%d/%Y %H:%M",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%dT%H:%M",
    "%Y-%m-%d",
    "%Y%m%d",
    "%Y%m%dT%H%M%S",
)

#: Header keys each normaliser applies to, lower-cased for matching.
DATETIME_KEYS = {"scan.start_time", "scan.end_time"}
TEMPERATURE_KEYS = {"sample.temperature"}
ENERGY_KEYS = {"scan.edge_energy", "scanparameters.e0"}


def normalize_datetime(value: str) -> str | None:
    """A raw XDI datetime as ISO 8601, or None if unparseable.

    None rather than a guess: the caller keeps the original string and
    lets validation report it. A spec-noncompliant timestamp should not
    fail the conversion.
    """
    s = (value or "").strip()
    if not s:
        return None
    try:
        return datetime.fromisoformat(s).isoformat()
    except ValueError:
        pass
    for fmt in _DATETIME_FALLBACK_FORMATS:
        try:
            return datetime.strptime(s, fmt).isoformat()
        except ValueError:
            continue
    return None


def normalize_temperature(value: str) -> tuple[str, str | None]:
    """Return (value, note).

    A recognised qualitative temperature becomes a float and a unit, plus
    a note recording what the file actually said -- the phrase is the
    evidence, and replacing it silently with a number nobody measured
    would be a worse record than the one we started with.

    `10K` becomes `10 K`. That carries no note: only the spacing changes,
    and nothing is asserted that the file did not already say.

    Anything else is returned unchanged, including a numeric value this
    has no business rewriting and an unrecognised phrase, which is better
    reported by validation than guessed at.
    """
    raw = (value or "").strip()
    key = re.sub(r"[\s_]+", " ", raw).strip().lower().rstrip(".")
    if key in QUALITATIVE_TEMPERATURES:
        return QUALITATIVE_TEMPERATURES[key], f'temperature reported as "{raw}"'

    match = _TEMPERATURE_VALUE_UNIT.match(raw)
    if match:
        return f"{match.group('value')} {match.group('unit').upper()}", None

    return raw, None


def normalize_energy(value: str) -> str:
    """Flag an energy that arrives without a unit.

    Says so in the value rather than inventing eV. Which unit a bare
    number is in is exactly what the file failed to record, and an
    absorption edge energy in the wrong unit is not a near miss.
    """
    raw = (value or "").strip()
    if _BARE_NUMBER.match(raw):
        return f"{raw} {UNITS_NOT_REPORTED}"
    return raw


def normalize(key: str, value: str) -> tuple[str, str | None]:
    """Normalise one header value by its key. Returns (value, note)."""
    k = (key or "").lower()
    if k in DATETIME_KEYS:
        iso = normalize_datetime(value)
        return (iso if iso is not None else value), None
    if k in TEMPERATURE_KEYS:
        return normalize_temperature(value)
    if k in ENERGY_KEYS:
        return normalize_energy(value), None
    return value, None
