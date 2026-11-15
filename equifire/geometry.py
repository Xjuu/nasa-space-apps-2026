"""Viewing geometry and pixel footprint of a MODIS / VIIRS hot-spot detection (EquiFire goal 2).

Two ways to get the scan angle of a detection, recorded in the field ``geom_source``:

* ``"sgp4"``        - propagate the current TLE (aux/tle/*.tle) with SGP4, find the moment in the
                      granule window when the point is perpendicular to the ground track (minimum
                      off-nadir angle) and read satellite position, altitude, slant range, view zenith
                      and scan angle from 3D geometry. Only valid when the detection is within a few
                      days of the TLE epoch (NRT data): SGP4 along-track error grows to hundreds of km
                      after weeks, so for archive data this path is *refused*, not silently used.
* ``"pixel_size"``  - invert the FIRMS ``scan`` / ``track`` pixel size (km) through the instrument
                      footprint-growth model (spherical Earth, nominal altitude). MODIS: along-scan
                      1.0 km at nadir -> 4.8 km at 55 deg (monotone, so we invert ``scan``). VIIRS
                      I-band: along-scan is a saw-tooth because of on-board 3:1 / 2:1 / 1:1 aggregation
                      zones, so we invert the monotone along-track size (0.36 -> 0.78 km in FIRMS).
                      Works for any date; resolution is limited by FIRMS rounding (0.1 km MODIS,
                      0.01 km VIIRS), i.e. +-5-8 deg near nadir, +-1 deg at the edge.
* ``"model"``       - neither TLE nor pixel sizes usable: nominal nadir geometry (scan angle unknown).

Ground-track heading (orientation of the footprint ellipse): from SGP4 velocity when available,
otherwise from a circular sun-synchronous orbit model (inclination, latitude, ascending vs descending
pass inferred from day/night and the satellite's equator-crossing node), corrected for Earth rotation.
"""
from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path

import numpy as np

from .common import ROOT

TLE_DIR = ROOT / "aux" / "tle"
R_E = 6371.0                       # spherical Earth for footprint geometry, km
OMEGA_E = 7.2921159e-5             # rad/s
MAX_TLE_AGE_DAYS = 3.0
WINDOW_MIN = (-3.0, 9.0)           # FIRMS acq_time = granule start; MODIS 5-min / VIIRS 6-min granules


@dataclass(frozen=True)
class Sat:
    key: str
    name: str
    norad: int
    instrument: str
    alt_km: float           # nominal altitude for the pixel-size model
    incl_deg: float
    max_scan_deg: float
    day_ascending: bool     # daytime pass is the ascending node (13:30 orbits) vs descending (Terra 10:30)
    nadir_scan_km: float    # nominal footprint at nadir
    nadir_track_km: float


SATS = {
    "terra": Sat("terra", "Terra", 25994, "MODIS", 705.0, 98.2, 55.0, False, 1.0, 1.0),
    "aqua": Sat("aqua", "Aqua", 27424, "MODIS", 705.0, 98.2, 55.0, True, 1.0, 1.0),
    "snpp": Sat("snpp", "Suomi NPP", 37849, "VIIRS", 833.0, 98.7, 56.28, True, 0.375, 0.375),
    "noaa20": Sat("noaa20", "NOAA-20", 43013, "VIIRS", 833.0, 98.7, 56.28, True, 0.375, 0.375),
    "noaa21": Sat("noaa21", "NOAA-21", 54234, "VIIRS", 833.0, 98.7, 56.28, True, 0.375, 0.375),
}
_ALIASES = {
    "terra": "terra", "t": "terra", "aqua": "aqua", "a": "aqua",
    "n": "snpp", "snpp": "snpp", "suomi npp": "snpp", "suomi-npp": "snpp", "npp": "snpp", "s-npp": "snpp",
    "n20": "noaa20", "1": "noaa20", "noaa20": "noaa20", "noaa-20": "noaa20", "j1": "noaa20", "jpss1": "noaa20",
    "n21": "noaa21", "2": "noaa21", "noaa21": "noaa21", "noaa-21": "noaa21", "j2": "noaa21", "jpss2": "noaa21",
}


def sat_of(name: str) -> Sat:
    """Satellite from a FIRMS code ('Terra', 'Aqua', 'N', 'N20', '1', 'N21') or a key/name."""
    k = _ALIASES.get(str(name).strip().lower())
    if k is None:
        raise KeyError(f"unknown satellite {name!r}; expected one of {sorted(_ALIASES)}")
    return SATS[k]


# ------------------------------------------------------------------ spherical scan geometry
def view_zenith(theta_deg, alt_km: float):
    """View zenith angle at the ground (deg) for off-nadir scan angle theta."""
    s = (R_E + alt_km) / R_E * np.sin(np.radians(theta_deg))
    return np.degrees(np.arcsin(np.clip(s, -1, 1)))


def slant_range(theta_deg, alt_km: float):
    th = np.radians(np.asarray(theta_deg, float))
    z = np.radians(view_zenith(theta_deg, alt_km))
    beta = z - th                                           # Earth central angle
    with np.errstate(invalid="ignore", divide="ignore"):
        d = np.where(th > 1e-6, R_E * np.sin(beta) / np.sin(np.where(th > 1e-6, th, 1)), alt_km)
    return d


def _scan_growth(theta_deg, alt_km: float):
    """Along-scan pixel size / nadir pixel size (d ground distance / d scan angle, normalised)."""
    th = np.radians(np.asarray(theta_deg, float))
    z = np.radians(view_zenith(theta_deg, alt_km))
    r = R_E + alt_km
    return (r * np.cos(th) / np.cos(z) - R_E) / alt_km


def _track_growth(theta_deg, alt_km: float):
    return slant_range(theta_deg, alt_km) / alt_km


def _viirs_agg(theta_deg):
    t = np.abs(np.asarray(theta_deg, float))
    return np.where(t < 31.59, 3.0, np.where(t < 44.68, 2.0, 1.0))


def pixel_size_model(sat: Sat, theta_deg):
    """(along-scan km, along-track km) predicted by the footprint model at scan angle theta."""
    if sat.instrument == "MODIS":
        return sat.nadir_scan_km * _scan_growth(theta_deg, sat.alt_km), sat.nadir_track_km * _track_growth(theta_deg, sat.alt_km)
    # VIIRS I-band: 3 sub-pixels of ~0.125 km aggregated (3:1, 2:1, 1:1 zones); FIRMS track ~0.36 km nadir
    return (0.125 * _viirs_agg(theta_deg) * _scan_growth(theta_deg, sat.alt_km),
            0.36 * _track_growth(theta_deg, sat.alt_km))


@lru_cache(maxsize=None)
def _inverse_table(key: str):
    sat = SATS[key]
    th = np.linspace(0, sat.max_scan_deg, 2001)
    s, t = pixel_size_model(sat, th)
    return th, (s if sat.instrument == "MODIS" else t)


def scan_angle_from_pixel(sat: Sat, scan_km=None, track_km=None):
    """Invert FIRMS pixel size to |scan angle| (deg). MODIS uses `scan`, VIIRS uses `track`."""
    th, size = _inverse_table(sat.key)
    v = np.asarray(scan_km if sat.instrument == "MODIS" else track_km, float)
    return np.interp(v, size, th)          # clamps to [0, max_scan] outside the model range


# ------------------------------------------------------------------ ground-track heading (no TLE)
def heading_model(lat_deg, sat: Sat, ascending):
    """Azimuth of the ground track (deg from north, clockwise) for a circular orbit, Earth-rotation corrected."""
    lat = np.radians(np.asarray(lat_deg, float))
    inc = math.radians(sat.incl_deg)
    s = np.clip(math.cos(inc) / np.maximum(np.cos(lat), 1e-3), -1, 1)
    az = np.arcsin(s)                                      # ascending: slightly west of north for i > 90
    az = np.where(np.asarray(ascending, bool), az, np.pi - az)
    period = 2 * math.pi * math.sqrt((R_E + sat.alt_km) ** 3 / 398600.4418)
    v = 2 * math.pi * R_E / period                         # ground speed, km/s
    ve = v * np.sin(az) - OMEGA_E * R_E * np.cos(lat)      # Earth turns east under the satellite
    vn = v * np.cos(az)
    return np.degrees(np.arctan2(ve, vn)) % 360


def ascending_from_daynight(sat: Sat, daynight):
    """FIRMS daynight 'D'/'N' -> ascending pass? (Terra: day = descending; Aqua/VIIRS: day = ascending)."""
    day = np.asarray(daynight).astype(str) == "D"
    return day if sat.day_ascending else ~day


# ------------------------------------------------------------------ SGP4 path
def read_tles(tle_dir: Path = TLE_DIR) -> dict[int, tuple[str, str]]:
    out: dict[int, tuple[str, str]] = {}
    for f in sorted(tle_dir.glob("*.tle")):
        lines = [ln.rstrip() for ln in f.read_text(errors="replace").splitlines()]
        for a, b in zip(lines, lines[1:]):
            if a.startswith("1 ") and b.startswith("2 ") and len(a) >= 69 and len(b) >= 69:
                try:
                    norad, epoch = int(a[2:7]), float(a[18:32])
                except ValueError:
                    continue
                if norad not in out or float(out[norad][0][18:32]) < epoch:
                    out[norad] = (a, b)
    return out


@lru_cache(maxsize=None)
def _satrec(norad: int):
    from sgp4.api import Satrec
    tles = read_tles()
    if norad not in tles:
        return None
    return Satrec.twoline2rv(*tles[norad])


def tle_epoch(sat: Sat) -> dt.datetime | None:
    rec = _satrec(sat.norad)
    if rec is None:
        return None
    jd = rec.jdsatepoch + rec.jdsatepochF
    return dt.datetime(2000, 1, 1, 12, tzinfo=dt.timezone.utc) + dt.timedelta(days=jd - 2451545.0)


def _gmst(jd_ut1):
    t = (jd_ut1 - 2451545.0) / 36525.0
    g = 280.46061837 + 360.98564736629 * (jd_ut1 - 2451545.0) + 0.000387933 * t * t - t ** 3 / 38710000.0
    return np.radians(g % 360.0)


def _ecef_point(lat, lon):
    """Spherical-Earth ECEF of a ground point (km) - consistent with the footprint model."""
    la, lo = math.radians(lat), math.radians(lon)
    return R_E * np.array([math.cos(la) * math.cos(lo), math.cos(la) * math.sin(lo), math.sin(la)])


def _utc(t: dt.datetime) -> dt.datetime:
    return t.replace(tzinfo=dt.timezone.utc) if t.tzinfo is None else t.astimezone(dt.timezone.utc)


def sgp4_geometry(sat: Sat, lat: float, lon: float, t: dt.datetime, step_s: float = 1.0) -> dict | None:
    """Geometry at the moment of minimum off-nadir angle inside the granule window around t, or None."""
    from sgp4.api import jday
    rec = _satrec(sat.norad)
    if rec is None:
        return None
    t = _utc(t)
    secs = np.arange(WINDOW_MIN[0] * 60, WINDOW_MIN[1] * 60 + step_s, step_s)
    jd0, fr0 = jday(t.year, t.month, t.day, t.hour, t.minute, t.second + t.microsecond / 1e6)
    jd = np.full(secs.size, jd0)
    fr = fr0 + secs / 86400.0
    err, r, _ = rec.sgp4_array(jd, fr)
    if (err != 0).all():
        return None
    g = _gmst(jd + fr)
    c, s = np.cos(g), np.sin(g)
    sat_ecef = np.c_[c * r[:, 0] + s * r[:, 1], -s * r[:, 0] + c * r[:, 1], r[:, 2]]   # TEME -> ECEF
    obs = _ecef_point(lat, lon)
    los = obs - sat_ecef
    rng = np.linalg.norm(los, axis=1)
    rs = np.linalg.norm(sat_ecef, axis=1)
    scan = np.degrees(np.arccos(np.clip(np.einsum("ij,ij->i", los, -sat_ecef) / (rng * rs), -1, 1)))
    up = obs / R_E
    elev = np.degrees(np.arcsin(np.clip(-(los @ up) / rng, -1, 1)))
    scan[(err != 0) | (elev <= 0)] = np.inf
    i = int(np.argmin(scan))
    if not np.isfinite(scan[i]) or scan[i] > sat.max_scan_deg + 2 or i in (0, secs.size - 1):
        return {"in_swath": False, "min_scan_deg": float(scan[i]) if np.isfinite(scan[i]) else None}
    p = sat_ecef[i]
    sub_lat = math.degrees(math.asin(p[2] / rs[i]))
    sub_lon = math.degrees(math.atan2(p[1], p[0]))
    # heading from neighbouring sub-satellite points
    a, b = sat_ecef[i - 1] / rs[i - 1], sat_ecef[i + 1] / rs[i + 1]
    la1, lo1 = math.asin(a[2]), math.atan2(a[1], a[0])
    la2, lo2 = math.asin(b[2]), math.atan2(b[1], b[0])
    y = math.sin(lo2 - lo1) * math.cos(la2)
    x = math.cos(la1) * math.sin(la2) - math.sin(la1) * math.cos(la2) * math.cos(lo2 - lo1)
    heading = math.degrees(math.atan2(y, x)) % 360
    when = t + dt.timedelta(seconds=float(secs[i]))
    return {
        "in_swath": True, "time": when.isoformat(),
        "sat_ecef_km": [round(float(v), 3) for v in p],
        "sub_lat": sub_lat, "sub_lon": sub_lon,
        "altitude_km": float(rs[i] - R_E),
        "slant_range_km": float(rng[i]),
        "scan_deg": float(scan[i]),
        "view_zenith_deg": float(90 - elev[i]),
        "heading_deg": heading,
        "ascending": bool(b[2] > a[2]),
    }


# ------------------------------------------------------------------ public API
def detection_geometry(lat: float, lon: float, time: dt.datetime, sat: str | Sat,
                       scan_km: float | None = None, track_km: float | None = None,
                       daynight: str | None = None, max_tle_age_days: float = MAX_TLE_AGE_DAYS) -> dict:
    """View geometry + footprint ellipse of one detection. See module doc for `geom_source`."""
    s = sat if isinstance(sat, Sat) else sat_of(sat)
    time = _utc(time)
    out: dict = {"satellite": s.name, "instrument": s.instrument, "norad": s.norad}
    ep = tle_epoch(s)
    age = None if ep is None else (time - ep).total_seconds() / 86400
    out["tle_epoch"] = ep.isoformat() if ep else None
    out["tle_age_days"] = None if age is None else round(age, 2)
    g = None
    if age is not None and abs(age) <= max_tle_age_days:
        g = sgp4_geometry(s, lat, lon, time)
    if g and g.get("in_swath"):
        theta, vza, rng, alt, heading = g["scan_deg"], g["view_zenith_deg"], g["slant_range_km"], g["altitude_km"], g["heading_deg"]
        out.update(geom_source="sgp4", sat_position=g)
        # footprint model at the actual SGP4 altitude (pixel angular size fixed -> nadir size ~ altitude)
        k = alt / s.alt_km
        s_alt = replace(s, alt_km=alt, nadir_scan_km=s.nadir_scan_km * k, nadir_track_km=s.nadir_track_km * k)
        ps, pt = (float(v) * (k if s.instrument == "VIIRS" else 1.0) for v in pixel_size_model(s_alt, theta))
    else:
        if g is not None and not g.get("in_swath"):
            out["sgp4_note"] = (f"point not inside the {s.instrument} swath in the granule window "
                                f"(min off-nadir {g.get('min_scan_deg')} deg) - detection time/TLE mismatch")
        elif age is not None:
            out["sgp4_note"] = f"TLE epoch {abs(age):.1f} days from detection (> {max_tle_age_days}); SGP4 not trusted"
        if (s.instrument == "MODIS" and scan_km) or (s.instrument == "VIIRS" and track_km):
            theta = float(scan_angle_from_pixel(s, scan_km, track_km))
            out["geom_source"] = "pixel_size"
        else:
            theta = 0.0
            out["geom_source"] = "model"
            out["note"] = "no usable TLE and no FIRMS scan/track: nadir geometry assumed"
        vza, rng, alt = float(view_zenith(theta, s.alt_km)), float(slant_range(theta, s.alt_km)), s.alt_km
        asc = None if daynight is None else bool(ascending_from_daynight(s, daynight))
        heading = float(heading_model(lat, s, True if asc is None else asc))
        if asc is None:
            out["heading_note"] = "pass direction unknown (no daynight): ascending assumed; axis orientation +-180 deg is the same"
        ps, pt = (float(v) for v in pixel_size_model(s, theta))
    # FIRMS pixel sizes are the instrument team's own footprint values: prefer them for the ellipse
    if scan_km and track_km:
        ps, pt, fp_src = float(scan_km), float(track_km), "firms_scan_track"
    else:
        fp_src = "model"
    out.update(
        scan_angle_deg=round(theta, 2), view_zenith_deg=round(vza, 2),
        slant_range_km=round(rng, 1), altitude_km=round(alt, 1),
        footprint={
            "along_scan_km": round(ps, 3), "along_track_km": round(pt, 3),
            "semi_axis_scan_km": round(ps / 2, 3), "semi_axis_track_km": round(pt / 2, 3),
            "track_azimuth_deg": round(heading % 360, 1),
            "scan_azimuth_deg": round((heading + 90) % 360, 1),
            "area_km2": round(math.pi * ps * pt / 4, 3), "source": fp_src,
        },
    )
    return out


def add_geometry(df, sat_col: str = "satellite"):
    """Vectorised pixel-size geometry for a FIRMS frame (archive path). Adds scan_deg, vza_deg,
    along_scan_km, along_track_km, heading_deg, geom_source. Rows of unknown satellite get NaN."""
    import pandas as pd
    n = len(df)
    scan = np.full(n, np.nan)
    head = np.full(n, np.nan)
    vza = np.full(n, np.nan)
    keys = pd.Series(df[sat_col].astype(str).str.strip().str.lower().map(_ALIASES).to_numpy(), index=df.index)
    for k, idx in df.groupby(keys.values).indices.items():
        s = SATS[k]
        sub = df.iloc[idx]
        th = scan_angle_from_pixel(s, sub["scan"].to_numpy(float), sub["track"].to_numpy(float))
        scan[idx] = th
        vza[idx] = view_zenith(th, s.alt_km)
        asc = ascending_from_daynight(s, sub["daynight"].astype(str).to_numpy()) if "daynight" in sub else np.ones(len(idx), bool)
        head[idx] = heading_model(sub["latitude"].to_numpy(float), s, asc)
    out = df.assign(scan_deg=scan.astype(np.float32), vza_deg=vza.astype(np.float32), heading_deg=head.astype(np.float32),
                    along_scan_km=df["scan"].astype(np.float32), along_track_km=df["track"].astype(np.float32),
                    sat_key=pd.Categorical(keys.values),
                    geom_source=pd.Categorical(np.where(np.isnan(scan), "none", "pixel_size")))
    return out
