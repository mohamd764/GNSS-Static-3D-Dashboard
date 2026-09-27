"""Horizontal and vertical position-check computations (UI independent)."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .angles import decimal_to_dms, dms_to_decimal, normalize_0_360


def to_float(value) -> float | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def parse_utc_time(value) -> str | None:
    """Validate ``hh:mm:ss`` (seconds may be decimal); return the normalised string or ``None``."""
    text = (value or "").strip() if isinstance(value, str) else ""
    parts = text.split(":")
    if len(parts) != 3:
        return None
    try:
        hh, mm, ss = int(parts[0]), int(parts[1]), float(parts[2])
    except ValueError:
        return None
    if not (0 <= hh < 24 and 0 <= mm < 60 and 0 <= ss < 60):
        return None
    return text


def _is_blank_row(row: dict, keys) -> bool:
    return all(row.get(k) in (None, "") for k in keys)


@dataclass
class Summary:
    count: int
    mean: float | None
    std: float | None
    rms: float | None
    maximum: float | None


def summarise(values: list[float]) -> Summary:
    if not values:
        return Summary(0, None, None, None, None)
    arr = np.asarray(values, dtype=float)
    return Summary(
        count=len(arr),
        mean=float(arr.mean()),
        std=float(arr.std(ddof=1)) if len(arr) > 1 else 0.0,
        rms=float(np.sqrt(np.mean(arr**2))),
        maximum=float(np.abs(arr).max()),
    )


@dataclass
class HorizontalResult:
    rows: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    observed_rows: int = 0
    successful_rows: int = 0
    linear: Summary | None = None
    delta_e: Summary | None = None
    delta_n: Summary | None = None

    @property
    def failed_rows(self) -> int:
        return self.observed_rows - self.successful_rows


HORIZONTAL_KEYS = ("utc_time", "direction_deg", "direction_min", "direction_sec", "obs_distance")


def horizontal_check(
    e1: float,
    n1: float,
    backsight_grid_bearing: float,
    backsight_reading: float,
    observations: list[dict],
    scale_factor: float = 1.0,
) -> HorizontalResult:
    """Radiate each observed direction/distance from the instrument station.

    ``bearing = backsight_grid_bearing + (direction - backsight_reading)``;
    the horizontal distance is multiplied by ``scale_factor`` (1.0 = distances
    are already grid distances) before computing the target E/N, which is then
    compared with the GNSS (DGPS) position logged at the same epoch.
    """
    result = HorizontalResult()
    miscloses, des, dns = [], [], []

    for index, row in enumerate(observations, start=1):
        if _is_blank_row(row, HORIZONTAL_KEYS):
            continue
        result.observed_rows += 1

        time_val = parse_utc_time(row.get("utc_time"))
        if time_val is None:
            result.errors.append(f"Row {index}: invalid UTC time (expected hh:mm:ss).")
            continue
        direction = dms_to_decimal(row.get("direction_deg"), row.get("direction_min"), row.get("direction_sec"))
        if direction is None:
            result.errors.append(f"Row {index}: invalid direction (DMS).")
            continue
        distance = to_float(row.get("obs_distance"))
        if distance is None or distance <= 0:
            result.errors.append(f"Row {index}: horizontal distance must be a positive number.")
            continue

        bearing = normalize_0_360(backsight_grid_bearing + (direction - backsight_reading))
        grid_distance = distance * scale_factor
        rad = math.radians(bearing)
        calc_e = e1 + grid_distance * math.sin(rad)
        calc_n = n1 + grid_distance * math.cos(rad)

        dgps_e, dgps_n = to_float(row.get("dgps_e")), to_float(row.get("dgps_n"))
        d_e = d_n = misclose = None
        if dgps_e is not None and dgps_n is not None:
            d_e, d_n = calc_e - dgps_e, calc_n - dgps_n
            misclose = math.hypot(d_e, d_n)
            miscloses.append(misclose)
            des.append(d_e)
            dns.append(d_n)

        result.rows.append(
            {
                "UTC Time": time_val,
                "Grid Bearing (DMS)": decimal_to_dms(bearing, wrap360=True),
                "Obs Dist (m)": distance,
                "Grid Dist (m)": grid_distance,
                "Calc Easting": calc_e,
                "Calc Northing": calc_n,
                "GNSS Easting": dgps_e,
                "GNSS Northing": dgps_n,
                "dE (m)": d_e,
                "dN (m)": d_n,
                "Linear Misclose (m)": misclose,
            }
        )
        result.successful_rows += 1

    result.linear = summarise(miscloses)
    result.delta_e = summarise(des)
    result.delta_n = summarise(dns)
    return result


@dataclass
class VerticalResult:
    rows: list[dict] = field(default_factory=list)
    misclose: Summary | None = None


def vertical_check(station_height: float, instrument_height: float, observations: list[dict]) -> VerticalResult:
    """``H_target = H_station + HI + observed vertical distance + target offset``.

    The target offset (``height`` column) is the signed vertical offset from the
    prism centre to the GNSS antenna reference point; blank means 0.
    """
    result = VerticalResult()
    miscloses = []
    for row in observations:
        obs_v = to_float(row.get("obs_v_dist"))
        if obs_v is None:
            continue
        offset = to_float(row.get("height")) or 0.0
        calc_h = station_height + instrument_height + obs_v + offset
        dgps_h = to_float(row.get("dgps_h"))
        misclose = calc_h - dgps_h if dgps_h is not None else None
        if misclose is not None:
            miscloses.append(misclose)
        result.rows.append(
            {
                "UTC Time": (row.get("utc_time") or "").strip(),
                "Station Height (m)": station_height,
                "Instrument Height (m)": instrument_height,
                "Obs Vertical Dist (m)": obs_v,
                "Target Offset (m)": offset,
                "Calc Height (m)": calc_h,
                "GNSS Height (m)": dgps_h,
                "dH (m)": misclose,
            }
        )
    result.misclose = summarise(miscloses)
    return result
