"""Angle helpers: degrees-minutes-seconds (DMS) parsing/formatting and normalisation."""

from __future__ import annotations

import math


def dms_to_decimal(degrees, minutes, seconds) -> float | None:
    """Convert a non-negative DMS direction reading to decimal degrees.

    Returns ``None`` if any part is missing or out of range. Directions read
    from a total station are always in [0, 360), so negative input is rejected.
    """
    if None in (degrees, minutes, seconds):
        return None
    try:
        d, m, s = float(degrees), float(minutes), float(seconds)
    except (TypeError, ValueError):
        return None
    if not (0 <= d < 360 and 0 <= m < 60 and 0 <= s < 60):
        return None
    return d + m / 60 + s / 3600


def decimal_to_dms(
    decimal_degrees: float | None, seconds_decimals: int = 2, signed: bool = False, wrap360: bool = False
) -> str:
    """Format decimal degrees as ``DDD° MM' SS.ss"``.

    The sign is applied to the whole angle (``-001° 35' 03.65"``), and rounding
    carries into minutes/degrees so seconds never read ``60.00``. Use
    ``signed=True`` to always show a leading ``+``/``-`` (e.g. for convergence)
    and ``wrap360=True`` for bearings so ``359.999999`` reads ``000°``.
    """
    if decimal_degrees is None or not math.isfinite(decimal_degrees):
        return "N/A"
    sign = "-" if decimal_degrees < 0 else ("+" if signed else "")
    scale = 10**seconds_decimals
    total = round(abs(decimal_degrees) * 3600 * scale)
    if total == 0:
        sign = "+" if signed else ""
    if wrap360:
        total %= 360 * 3600 * scale
    degrees, rem = divmod(total, 3600 * scale)
    minutes, sec_scaled = divmod(rem, 60 * scale)
    seconds = sec_scaled / scale
    width = 3 + seconds_decimals if seconds_decimals else 2
    return f"{sign}{degrees:03d}° {minutes:02d}' {seconds:0{width}.{seconds_decimals}f}\""


def normalize_0_360(angle: float) -> float:
    """Normalise an angle to [0, 360)."""
    result = angle % 360.0
    return 0.0 if result == 360.0 else result


def normalize_pm180(angle: float) -> float:
    """Normalise an angle to (-180, 180]."""
    result = normalize_0_360(angle)
    return result - 360.0 if result > 180.0 else result
