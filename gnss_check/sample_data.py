"""Synthetic demo session.

Everything here is fictional: the names are placeholders and the coordinates
are round numbers in WGS 84 / UTM zone 15N chosen for illustration only. The
observations are simulated from a known "true" antenna position with seeded
random noise, so the expected misclose is known (a few centimetres).
"""

from __future__ import annotations

import math

import numpy as np

from .geodesy import grid_bearing, grid_factors, utm_crs

SAMPLE_ZONE = 15
SAMPLE_HEMISPHERE = "North"

JOB_DETAILS = {
    "job-number": "DEMO-0001",
    "job-description": "Static GNSS antenna position check (synthetic demo data)",
    "client": "Example Client Inc. (fictional)",
    "party-chief": "J. Doe",
    "surveyor": "A. N. Other",
    "site": "Demo Harbor, Berth 0 (fictional)",
    "vessel": "MV Sample Vessel (fictional)",
    "date": "2025-01-01",
    "device-name": "Demo GNSS Receiver #1",
}

INSTRUMENT = {"name": "CP-01 (demo)", "easting": 385000.000, "northing": 3285000.000, "height": 2.500}
BACKSIGHT = {"name": "CP-02 (demo)", "easting": 385120.000, "northing": 3285160.000, "height": 2.815}
INSTRUMENT_HEIGHT = 1.550
BACKSIGHT_READING_DMS = (0, 0, 0.0)

_TRUE_ANTENNA = (385045.000, 3284940.000, 8.735)
_TARGET_OFFSET = 0.120


def _dms(angle: float) -> tuple[int, int, float]:
    angle %= 360.0
    total = round(angle * 36000)  # 0.1" resolution
    d, rem = divmod(total, 36000)
    m, s = divmod(rem, 600)
    return int(d), int(m), s / 10


def gyro_rows(n_epochs: int = 12, seed: int = 2025) -> list[dict]:
    """Simulated total-station observations to the antenna prism plus logged GNSS positions."""
    rng = np.random.default_rng(seed)
    crs = utm_crs(SAMPLE_ZONE, SAMPLE_HEMISPHERE)
    k = grid_factors(INSTRUMENT["easting"], INSTRUMENT["northing"], crs).scale_factor
    e0, n0, h0 = INSTRUMENT["easting"], INSTRUMENT["northing"], INSTRUMENT["height"]
    te, tn, th = _TRUE_ANTENNA

    bs_bearing = grid_bearing(e0, n0, BACKSIGHT["easting"], BACKSIGHT["northing"])
    target_bearing = grid_bearing(e0, n0, te, tn)
    ground_distance = math.hypot(te - e0, tn - n0) / k
    true_vd = th - h0 - INSTRUMENT_HEIGHT - _TARGET_OFFSET

    rows = []
    for i in range(n_epochs):
        direction = target_bearing - bs_bearing + rng.normal(0, 2.0) / 3600
        d, m, s = _dms(direction)
        rows.append(
            {
                "utc_time": f"01:{i * 2:02d}:00",
                "direction_deg": d,
                "direction_min": m,
                "direction_sec": s,
                "obs_distance": round(ground_distance + rng.normal(0, 0.002), 3),
                "obs_v_dist": round(true_vd + rng.normal(0, 0.003), 3),
                "height": _TARGET_OFFSET,
                "dgps_e": round(te + rng.normal(0, 0.015), 3),
                "dgps_n": round(tn + rng.normal(0, 0.015), 3),
                "dgps_h": round(th + rng.normal(0, 0.025), 3),
            }
        )
    return rows
