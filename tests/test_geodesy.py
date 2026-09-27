import math

import pytest
from pyproj import CRS

from gnss_check.angles import decimal_to_dms
from gnss_check.app import compute_geometry
from gnss_check.geodesy import (
    CRSInputError,
    area_of_use_warning,
    crs_from_epsg,
    geographic_to_grid,
    grid_bearing,
    grid_factors,
    grid_to_geographic,
    resolve_crs,
    true_azimuth_from_grid_bearing,
    utm_central_meridian,
    utm_crs,
    utm_zone_from_lonlat,
)


def dms(d, m, s, sign=1):
    return sign * (d + m / 60 + s / 3600)


# --------------------------------------------------------------------------
# Independent reference implementation: Krueger n-series to 6th order
# (Karney 2011, "Transverse Mercator with an accuracy of a few nanometers";
# also the method in the GDA2020 Technical Manual). Accurate to < 1 mm / 1e-9
# rad within a UTM zone, so it is a genuine cross-check of PROJ.
# --------------------------------------------------------------------------
WGS84_A, WGS84_F = 6378137.0, 1 / 298.257223563
GRS80_F = 1 / 298.257222101


def krueger_forward(lat, lon, lon0, k0=0.9996, a=WGS84_A, f=WGS84_F, false_e=500000.0, false_n=0.0):
    """Return (easting, northing, convergence_deg, point_scale)."""
    e = math.sqrt(f * (2 - f))
    n = f / (2 - f)
    A = a / (1 + n) * (1 + n**2 / 4 + n**4 / 64 + n**6 / 256)
    alpha = [
        n / 2 - 2 * n**2 / 3 + 5 * n**3 / 16 + 41 * n**4 / 180 - 127 * n**5 / 288 + 7891 * n**6 / 37800,
        13 * n**2 / 48 - 3 * n**3 / 5 + 557 * n**4 / 1440 + 281 * n**5 / 630 - 1983433 * n**6 / 1935360,
        61 * n**3 / 240 - 103 * n**4 / 140 + 15061 * n**5 / 26880 + 167603 * n**6 / 181440,
        49561 * n**4 / 161280 - 179 * n**5 / 168 + 6601661 * n**6 / 7257600,
        34729 * n**5 / 80640 - 3418889 * n**6 / 1995840,
        212378941 * n**6 / 319334400,
    ]
    phi, lam = math.radians(lat), math.radians(lon - lon0)
    tau = math.tan(phi)
    sigma = math.sinh(e * math.atanh(e * tau / math.sqrt(1 + tau**2)))
    tau_p = tau * math.sqrt(1 + sigma**2) - sigma * math.sqrt(1 + tau**2)
    xi_p = math.atan2(tau_p, math.cos(lam))
    eta_p = math.asinh(math.sin(lam) / math.sqrt(tau_p**2 + math.cos(lam) ** 2))

    xi, eta, p, q = xi_p, eta_p, 1.0, 0.0
    for j, a_j in enumerate(alpha, start=1):
        xi += a_j * math.sin(2 * j * xi_p) * math.cosh(2 * j * eta_p)
        eta += a_j * math.cos(2 * j * xi_p) * math.sinh(2 * j * eta_p)
        p += 2 * j * a_j * math.cos(2 * j * xi_p) * math.cosh(2 * j * eta_p)
        q += 2 * j * a_j * math.sin(2 * j * xi_p) * math.sinh(2 * j * eta_p)

    gamma = math.atan(tau_p / math.sqrt(1 + tau_p**2) * math.tan(lam)) + math.atan2(q, p)
    k = (
        k0
        * math.sqrt(1 - e**2 * math.sin(phi) ** 2)
        * math.sqrt(1 + tau**2)
        / math.sqrt(tau_p**2 + math.cos(lam) ** 2)
        * (A / a)
        * math.hypot(p, q)
    )
    return false_e + k0 * A * eta, false_n + k0 * A * xi, math.degrees(gamma), k


# ------------------------------------------------------------------ zone logic


@pytest.mark.parametrize(
    "lon, lat, expected",
    [
        (-94.19, 29.69, (15, "North")),  # US Gulf coast
        (-122.42, 37.77, (10, "North")),  # San Francisco
        (-74.01, 40.71, (18, "North")),  # New York
        (-149.90, 61.22, (6, "North")),  # Anchorage
        (151.21, -33.86, (56, "South")),  # Sydney
        (144.42, -37.95, (55, "South")),
        (3.0, 0.0, (31, "North")),
        (-180.0, 10.0, (1, "North")),
        (180.0, 10.0, (1, "North")),
        (179.99, -10.0, (60, "South")),
        (5.0, 60.0, (32, "North")),  # SW Norway exception (would be 31)
        (5.0, 55.0, (31, "North")),
        (8.0, 78.0, (31, "North")),  # Svalbard exceptions
        (10.0, 78.0, (33, "North")),
        (25.0, 78.0, (35, "North")),
        (40.0, 78.0, (37, "North")),
    ],
)
def test_utm_zone_from_lonlat(lon, lat, expected):
    assert utm_zone_from_lonlat(lon, lat) == expected


@pytest.mark.parametrize("lat", [-80.5, 84.5])
def test_utm_zone_out_of_range(lat):
    with pytest.raises(CRSInputError):
        utm_zone_from_lonlat(0, lat)


def test_easting_does_not_encode_zone():
    """Regression for the original ``int((easting // 1e6) % 60) + 1`` zone guess.

    The same grid coordinate is a valid point in every zone; only the chosen
    zone decides the longitude. The old formula returned zone 1 for every
    valid easting (< 1 000 000 m), i.e. longitudes near 177°W.
    """
    e, n = 271_000.0, 3_294_000.0
    assert int((e // 1_000_000) % 60) + 1 == 1

    lat15, lon15 = grid_to_geographic(e, n, utm_crs(15, "North"))
    lat1, lon1 = grid_to_geographic(e, n, utm_crs(1, "North"))
    assert lat15 == pytest.approx(lat1, abs=1e-12)
    assert lon15 - lon1 == pytest.approx(14 * 6, abs=1e-9)
    assert -96 < lon15 < -90


def test_utm_crs_epsg_codes():
    assert utm_crs(15, "North").to_epsg() == 32615
    assert utm_crs(56, "South").to_epsg() == 32756
    for bad_zone in (0, 61, None, "x"):
        with pytest.raises(CRSInputError):
            utm_crs(bad_zone, "North")
    with pytest.raises(CRSInputError):
        utm_crs(15, None)


def test_crs_from_epsg_validation():
    assert crs_from_epsg("EPSG:26915").to_epsg() == 26915
    assert crs_from_epsg(" 7855 ").to_epsg() == 7855
    for bad in ("4326", "2278", "abc", "999999"):  # geographic, US survey feet, junk, unknown
        with pytest.raises(CRSInputError):
            crs_from_epsg(bad)
    assert resolve_crs("epsg", epsg="32615") == resolve_crs("utm", 15, "North")


# ------------------------------------------------------------------ published / exact reference points


def test_origin_of_zone_31():
    """(0°, 3°E) is the true origin of zone 31: E = 500 000, N = 0 (north) / 10 000 000 (south)."""
    assert geographic_to_grid(0.0, 3.0, utm_crs(31, "North")) == pytest.approx((500_000.0, 0.0), abs=1e-6)
    assert geographic_to_grid(0.0, 3.0, utm_crs(31, "South")) == pytest.approx((500_000.0, 10_000_000.0), abs=1e-6)


def test_snyder_transverse_mercator_example():
    """USGS Professional Paper 1395 (Snyder 1987), ellipsoidal TM worked example, Clarke 1866.

    lat 40°30'N, lon 73°30'W, lon0 75°W, k0 0.9996 -> x = 127 106.5 m, y = 4 484 124.4 m, k = 0.9997989.
    """
    crs = CRS.from_proj4("+proj=tmerc +lat_0=0 +lon_0=-75 +k=0.9996 +x_0=0 +y_0=0 +ellps=clrk66 +units=m")
    x, y = geographic_to_grid(40.5, -73.5, crs)
    assert x == pytest.approx(127_106.5, abs=0.05)
    assert y == pytest.approx(4_484_124.4, abs=0.05)
    assert grid_factors(x, y, crs).scale_factor == pytest.approx(0.9997989, abs=5e-8)


FLINDERS_PEAK = (dms(37, 57, 3.7203, -1), dms(144, 25, 29.5244))


def test_flinders_peak_mga2020():
    """Geodetic test point "Flinders Peak" from the Australian GDA technical manuals (MGA zone 55).

    Published grid coordinates: E 273 741.2966, N 5 796 489.7769. Convergence and
    scale are checked against the independent Krueger series (GRS80).
    """
    lat, lon = FLINDERS_PEAK
    crs = crs_from_epsg(7855)
    e, n = geographic_to_grid(lat, lon, crs)
    assert e == pytest.approx(273_741.2966, abs=0.001)
    assert n == pytest.approx(5_796_489.7769, abs=0.001)

    back_lat, back_lon = grid_to_geographic(e, n, crs)
    assert back_lat == pytest.approx(lat, abs=1e-9)
    assert back_lon == pytest.approx(lon, abs=1e-9)

    factors = grid_factors(e, n, crs)
    _, _, gamma_ref, k_ref = krueger_forward(lat, lon, 147.0, f=GRS80_F, false_n=10_000_000.0)
    assert factors.convergence_deg == pytest.approx(gamma_ref, abs=1e-8)
    assert factors.scale_factor == pytest.approx(k_ref, abs=1e-10)
    # PROJ/NGS sign convention; Australian publications quote the same value as -1°35'03.65".
    assert decimal_to_dms(factors.convergence_deg, signed=True) == "+001° 35' 03.65\""
    assert factors.scale_factor == pytest.approx(1.00023056, abs=1e-8)


SITES = [
    (29.69, -94.19),
    (37.77, -122.42),
    (61.22, -149.90),
    (-33.86, 151.21),
    (-31.95, 115.86),
    (51.92, 4.48),
    (1.29, 103.85),
    (-54.80, -68.30),
    (70.0, 25.9),
]


@pytest.mark.parametrize("lat, lon", SITES)
def test_utm_against_independent_krueger_series(lat, lon):
    zone, hemisphere = utm_zone_from_lonlat(lon, lat)
    crs = utm_crs(zone, hemisphere)
    false_n = 0.0 if hemisphere == "North" else 10_000_000.0
    e_ref, n_ref, gamma_ref, k_ref = krueger_forward(lat, lon, utm_central_meridian(zone), false_n=false_n)

    e, n = geographic_to_grid(lat, lon, crs)
    assert (e, n) == pytest.approx((e_ref, n_ref), abs=1e-3)

    factors = grid_factors(e, n, crs)
    assert factors.latitude == pytest.approx(lat, abs=1e-9)
    assert factors.longitude == pytest.approx(lon, abs=1e-9)
    assert factors.convergence_deg == pytest.approx(gamma_ref, abs=1e-8)  # < 0.00004"
    assert factors.scale_factor == pytest.approx(k_ref, abs=1e-10)


def test_convergence_invariants_and_sign():
    crs = utm_crs(15, "North")
    cm_e, cm_n = geographic_to_grid(30.0, -93.0, crs)
    f = grid_factors(cm_e, cm_n, crs)
    assert cm_e == pytest.approx(500_000.0, abs=1e-6)
    assert f.convergence_deg == pytest.approx(0.0, abs=1e-12)
    assert f.scale_factor == pytest.approx(0.9996, abs=1e-10)

    assert grid_factors(*geographic_to_grid(0.0, -91.0, crs), crs).convergence_deg == pytest.approx(0.0, abs=1e-9)
    assert grid_factors(*geographic_to_grid(30.0, -91.0, crs), crs).convergence_deg > 0  # N, east of CM
    assert grid_factors(*geographic_to_grid(30.0, -95.0, crs), crs).convergence_deg < 0  # N, west of CM
    south = utm_crs(56, "South")
    assert grid_factors(*geographic_to_grid(-33.0, 154.0, south), south).convergence_deg < 0  # S, east of CM


def test_lambert_conformal_conic_state_plane_convergence():
    """EPSG:32140 NAD83 / Texas South Central (metres): gamma = n * (lon - lon0) exactly for LCC."""
    crs = crs_from_epsg(32140)
    phi1, phi2, a, f = math.radians(30 + 17 / 60), math.radians(28 + 23 / 60), 6378137.0, 1 / 298.257222101
    e = math.sqrt(f * (2 - f))

    def m(phi):
        return math.cos(phi) / math.sqrt(1 - e**2 * math.sin(phi) ** 2)

    def t(phi):
        return math.tan(math.pi / 4 - phi / 2) / ((1 - e * math.sin(phi)) / (1 + e * math.sin(phi))) ** (e / 2)

    cone = (math.log(m(phi1)) - math.log(m(phi2))) / (math.log(t(phi1)) - math.log(t(phi2)))
    lat, lon = 29.76, -95.37  # Houston area
    factors = grid_factors(*geographic_to_grid(lat, lon, crs), crs)
    assert factors.convergence_deg == pytest.approx(cone * (lon - (-99.0)), abs=1e-9)


# ------------------------------------------------------------------ bearings and sanity checks


def test_grid_bearing_quadrants():
    assert grid_bearing(0, 0, 0, 10) == pytest.approx(0.0)
    assert grid_bearing(0, 0, 10, 0) == pytest.approx(90.0)
    assert grid_bearing(0, 0, 0, -10) == pytest.approx(180.0)
    assert grid_bearing(0, 0, -10, 0) == pytest.approx(270.0)
    assert grid_bearing(0, 0, 10, 10) == pytest.approx(45.0)
    with pytest.raises(ValueError):
        grid_bearing(1, 1, 1, 1)


def test_true_azimuth_from_grid_bearing():
    assert true_azimuth_from_grid_bearing(359.5, 1.0) == pytest.approx(0.5)
    assert true_azimuth_from_grid_bearing(10.0, -0.5) == pytest.approx(9.5)


def test_area_of_use_warning_for_out_of_zone_point():
    crs = utm_crs(15, "North")
    assert area_of_use_warning(29.7, -94.2, crs) is None
    assert area_of_use_warning(29.7, -80.0, crs) is not None


def test_compute_geometry_flags_wrong_zone_via_approximate_position():
    e, n = geographic_to_grid(29.69, -94.19, utm_crs(15, "North"))
    good = compute_geometry("utm", 15, "North", None, e, n, e + 120, n + 160, 29.7, -94.2)
    assert good.ok and not good.warnings
    wrong = compute_geometry("utm", 14, "North", None, e, n, e + 120, n + 160, 29.7, -94.2)
    assert wrong.ok and any("km from the approximate site position" in w for w in wrong.warnings)
    missing = compute_geometry("utm", None, "North", None, e, n, e + 120, n + 160)
    assert not missing.ok and "zone" in missing.error
