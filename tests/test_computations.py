import math

import pytest

from gnss_check import sample_data
from gnss_check.angles import dms_to_decimal
from gnss_check.computations import horizontal_check, parse_utc_time, summarise, vertical_check
from gnss_check.geodesy import grid_bearing, grid_factors, utm_crs


def _row(time="01:00:00", deg=None, mnt=0, sec=0.0, dist=None, **extra):
    return {"utc_time": time, "direction_deg": deg, "direction_min": mnt, "direction_sec": sec,
            "obs_distance": dist, **extra}


def test_parse_utc_time():
    assert parse_utc_time("01:02:03") == "01:02:03"
    assert parse_utc_time(" 23:59:59.5 ") == "23:59:59.5"
    for bad in ("24:00:00", "1:2", "aa:bb:cc", None, ""):
        assert parse_utc_time(bad) is None


def test_horizontal_check_exact_geometry():
    # Backsight due north, reading 0°; a direction of 90° must land due east of the station.
    rows = [
        _row(deg=90, dist=100.0, dgps_e=1100.0, dgps_n=2000.0),
        _row(time="01:00:10", deg=180, dist=50.0, dgps_e=1000.0, dgps_n=1950.0),
        _row(time="01:00:20", deg=45, dist=math.sqrt(2) * 10, dgps_e=1010.03, dgps_n=2010.04),
    ]
    result = horizontal_check(1000.0, 2000.0, 0.0, 0.0, rows)
    assert result.successful_rows == 3 and result.failed_rows == 0
    assert result.rows[0]["Calc Easting"] == pytest.approx(1100.0)
    assert result.rows[0]["Calc Northing"] == pytest.approx(2000.0)
    assert result.rows[0]["Linear Misclose (m)"] == pytest.approx(0.0, abs=1e-9)
    assert result.rows[1]["Grid Bearing (DMS)"] == "180° 00' 00.00\""
    assert result.rows[2]["Linear Misclose (m)"] == pytest.approx(0.05)
    assert result.linear.maximum == pytest.approx(0.05)


def test_horizontal_check_orientation_uses_backsight_reading():
    # Backsight bearing 30°, backsight read as 350°: a reading of 20° is 30° clockwise of the backsight.
    result = horizontal_check(0.0, 0.0, 30.0, 350.0, [_row(deg=20, dist=10.0)])
    assert result.rows[0]["Grid Bearing (DMS)"] == "060° 00' 00.00\""
    assert result.rows[0]["Calc Easting"] == pytest.approx(10 * math.sin(math.radians(60)))


def test_horizontal_check_scale_factor_and_row_counting():
    rows = [_row(deg=0, dist=100.0)] + [{"utc_time": "", "direction_deg": None} for _ in range(5)]
    rows.append(_row(time="99:00:00", deg=0, dist=10.0))
    rows.append(_row(deg=0, dist=-1.0))
    result = horizontal_check(0.0, 0.0, 0.0, 0.0, rows, scale_factor=0.9996)
    assert result.observed_rows == 3  # blank rows are not counted as observations
    assert result.successful_rows == 1 and result.failed_rows == 2 and len(result.errors) == 2
    assert result.rows[0]["Calc Northing"] == pytest.approx(99.96)


def test_vertical_check():
    rows = [
        {"utc_time": "01:00:00", "obs_v_dist": 4.5, "height": 0.12, "dgps_h": 8.70},
        {"utc_time": "01:00:10", "obs_v_dist": 4.5, "height": None, "dgps_h": None},
        {"utc_time": "", "obs_v_dist": None, "height": 0.1, "dgps_h": 1.0},
    ]
    result = vertical_check(2.5, 1.55, rows)
    assert len(result.rows) == 2
    assert result.rows[0]["Calc Height (m)"] == pytest.approx(2.5 + 1.55 + 4.5 + 0.12)
    assert result.rows[0]["dH (m)"] == pytest.approx(8.67 - 8.70)
    assert result.rows[1]["dH (m)"] is None
    assert result.misclose.count == 1


def test_summarise():
    s = summarise([0.01, -0.03, 0.02])
    assert s.count == 3 and s.mean == pytest.approx(0.0) and s.maximum == pytest.approx(0.03)
    assert s.rms == pytest.approx(math.sqrt((0.0001 + 0.0009 + 0.0004) / 3))
    assert summarise([]).count == 0


def test_sample_session_closes_within_noise():
    inst, bs = sample_data.INSTRUMENT, sample_data.BACKSIGHT
    crs = utm_crs(sample_data.SAMPLE_ZONE, sample_data.SAMPLE_HEMISPHERE)
    k = grid_factors(inst["easting"], inst["northing"], crs).scale_factor
    bearing = grid_bearing(inst["easting"], inst["northing"], bs["easting"], bs["northing"])
    reading = dms_to_decimal(*sample_data.BACKSIGHT_READING_DMS)
    rows = sample_data.gyro_rows()

    reduced = horizontal_check(inst["easting"], inst["northing"], bearing, reading, rows, scale_factor=k)
    assert reduced.successful_rows == len(rows)
    assert reduced.linear.mean < 0.04

    unreduced = horizontal_check(inst["easting"], inst["northing"], bearing, reading, rows)
    assert unreduced.linear.mean > reduced.linear.mean  # ~1.8 cm scale bias over ~83 m at k = 0.99976

    vertical = vertical_check(inst["height"], sample_data.INSTRUMENT_HEIGHT, rows)
    assert abs(vertical.misclose.mean) < 0.03
