"""Coordinate reference system handling, grid <-> geographic conversion and grid factors.

A UTM easting/northing pair does **not** identify its zone: every zone reuses
the same 500 000 m false easting, so the zone (and hemisphere) must be supplied
by the user, taken from an EPSG code, or derived from a known latitude/longitude.

Sign convention for grid convergence (gamma) follows PROJ and the US National
Geodetic Survey: gamma is the angle from true (geodetic) north to grid north,
positive when grid north lies east of true north. For a transverse Mercator
projection gamma ~= (lon - lon0) * sin(lat), and::

    grid bearing = true (geodetic) azimuth - gamma

Some agencies (e.g. Geoscience Australia / ICSM) publish gamma with the
opposite sign so that ``grid bearing = azimuth + gamma``; the magnitude is the same.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from pyproj import CRS, Proj, Transformer
from pyproj.exceptions import CRSError

HEMISPHERES = ("North", "South")


class CRSInputError(ValueError):
    """Raised when the user-supplied zone / hemisphere / EPSG code is unusable."""


@dataclass(frozen=True)
class GridFactors:
    latitude: float
    longitude: float
    convergence_deg: float
    scale_factor: float


def utm_zone_from_lonlat(longitude: float, latitude: float) -> tuple[int, str]:
    """Return ``(zone, hemisphere)`` for a WGS84 longitude/latitude.

    Includes the standard UTM exceptions for south-west Norway (zone 32V) and
    Svalbard (zones 31X, 33X, 35X, 37X).
    """
    if not (-80.0 <= latitude <= 84.0):
        raise CRSInputError("UTM is only defined between 80°S and 84°N (use UPS near the poles).")
    if not (-180.0 <= longitude <= 180.0):
        raise CRSInputError("Longitude must be between -180° and 180°.")

    lon = -180.0 if longitude == 180.0 else longitude
    zone = int((lon + 180.0) // 6.0) + 1

    if 56.0 <= latitude < 64.0 and 3.0 <= lon < 12.0:
        zone = 32
    elif 72.0 <= latitude <= 84.0 and 0.0 <= lon < 42.0:
        if lon < 9.0:
            zone = 31
        elif lon < 21.0:
            zone = 33
        elif lon < 33.0:
            zone = 35
        else:
            zone = 37

    hemisphere = "North" if latitude >= 0.0 else "South"
    return zone, hemisphere


def utm_central_meridian(zone: int) -> float:
    return zone * 6.0 - 183.0


def utm_crs(zone, hemisphere) -> CRS:
    """WGS 84 / UTM CRS (EPSG:326zz north, EPSG:327zz south)."""
    try:
        zone = int(zone)
    except (TypeError, ValueError):
        raise CRSInputError("Select a UTM zone (1-60).") from None
    if not 1 <= zone <= 60:
        raise CRSInputError("UTM zone must be between 1 and 60.")
    if hemisphere not in HEMISPHERES:
        raise CRSInputError("Select a hemisphere (North or South).")
    base = 32600 if hemisphere == "North" else 32700
    return CRS.from_epsg(base + zone)


def crs_from_epsg(code) -> CRS:
    """A projected, metre-based CRS from an EPSG code such as 32615, 26915 or 7855."""
    try:
        code = int(str(code).strip().upper().removeprefix("EPSG:"))
    except (TypeError, ValueError):
        raise CRSInputError("Enter a numeric EPSG code, e.g. 32615.") from None
    try:
        crs = CRS.from_epsg(code)
    except CRSError:
        raise CRSInputError(f"EPSG:{code} is not a known coordinate reference system.") from None
    if not crs.is_projected:
        raise CRSInputError(f"EPSG:{code} ({crs.name}) is not a projected (grid) CRS.")
    units = {axis.unit_name for axis in crs.axis_info}
    if units != {"metre"}:
        raise CRSInputError(
            f"EPSG:{code} ({crs.name}) uses {', '.join(sorted(units))}; only metre-based grids are supported."
        )
    return crs


def resolve_crs(mode: str, zone=None, hemisphere=None, epsg=None) -> CRS:
    """Build the working CRS from the UI selection (``mode`` is ``"utm"`` or ``"epsg"``)."""
    if mode == "epsg":
        return crs_from_epsg(epsg)
    return utm_crs(zone, hemisphere)


def _to_geographic(crs: CRS) -> Transformer:
    return Transformer.from_crs(crs, crs.geodetic_crs, always_xy=True)


def grid_to_geographic(easting: float, northing: float, crs: CRS) -> tuple[float, float]:
    """Return ``(latitude, longitude)`` in degrees on the CRS's own geodetic datum."""
    lon, lat = _to_geographic(crs).transform(easting, northing, errcheck=True)
    return lat, lon


def geographic_to_grid(latitude: float, longitude: float, crs: CRS) -> tuple[float, float]:
    """Return ``(easting, northing)`` for a latitude/longitude on the CRS's geodetic datum."""
    e, n = Transformer.from_crs(crs.geodetic_crs, crs, always_xy=True).transform(
        longitude, latitude, errcheck=True
    )
    return e, n


def grid_factors(easting: float, northing: float, crs: CRS) -> GridFactors:
    """Exact grid convergence and point scale factor at a grid coordinate.

    Uses PROJ's analytic projection derivatives, so it is valid for any
    conformal projection (UTM/TM, Lambert conformal conic, ...), not just UTM.
    """
    lat, lon = grid_to_geographic(easting, northing, crs)
    factors = Proj(crs).get_factors(lon, lat, errcheck=True)
    return GridFactors(
        latitude=lat,
        longitude=lon,
        convergence_deg=factors.meridian_convergence,
        scale_factor=factors.meridional_scale,
    )


def grid_bearing(e1: float, n1: float, e2: float, n2: float) -> float:
    """Grid bearing from point 1 to point 2 in degrees [0, 360)."""
    if e1 == e2 and n1 == n2:
        raise ValueError("Instrument and backsight stations have identical coordinates.")
    return math.degrees(math.atan2(e2 - e1, n2 - n1)) % 360.0


def true_azimuth_from_grid_bearing(grid_bearing_deg: float, convergence_deg: float) -> float:
    return (grid_bearing_deg + convergence_deg) % 360.0


def area_of_use_warning(latitude: float, longitude: float, crs: CRS, margin_deg: float = 0.5) -> str | None:
    """Warn if a point falls outside the CRS's published area of use.

    This catches a wrong zone/EPSG code when the station coordinates put the
    site far outside the zone (e.g. |lon - lon0| > 3°). It cannot detect a wrong
    hemisphere from E/N alone; compare against an approximate site lat/lon for that.
    """
    area = crs.area_of_use
    if area is None:
        return None
    west, south, east, north = area.bounds
    if west > east:
        inside_lon = longitude >= west - margin_deg or longitude <= east + margin_deg
    else:
        inside_lon = west - margin_deg <= longitude <= east + margin_deg
    inside_lat = south - margin_deg <= latitude <= north + margin_deg
    if inside_lon and inside_lat:
        return None
    return (
        f"Station position ({latitude:.4f}°, {longitude:.4f}°) is outside the area of use of "
        f"{crs.name} ({west:g}° to {east:g}° E, {south:g}° to {north:g}° N). Check the zone / EPSG code."
    )


def haversine_distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Approximate great-circle distance (spherical Earth), for sanity checks only."""
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def crs_label(crs: CRS) -> str:
    epsg = crs.to_epsg()
    return f"{crs.name} (EPSG:{epsg})" if epsg else crs.name
