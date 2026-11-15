"""Next fire-sensor overpasses over a point from TLEs (aux/tle/*.tle) with SGP4.

A pass counts when the point is inside the instrument swath, i.e. the off-nadir scan angle from the
satellite to the point is within the instrument's maximum (MODIS 55 deg, VIIRS 56.3 deg). That is
roughly a ground elevation angle above ~20-25 deg. Pixel size at the best moment of the pass is
estimated from the slant range and view zenith (spherical Earth), e.g. MODIS 1 km at nadir grows to
about 2 x 4.8 km at the swath edge.
"""
from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from sgp4.api import SGP4_ERRORS, Satrec, jday

from equifire.common import ROOT

TLE_DIR = ROOT / "aux" / "tle"
R_EARTH = 6371.0
WGS_A, WGS_F = 6378.137, 1 / 298.257223563
STEP_S = 20


@dataclass(frozen=True)
class Sensor:
    key: str
    name: str
    norad: int
    instrument: str
    nadir_km: float
    max_scan_deg: float


SENSORS = [
    Sensor("terra", "Terra", 25994, "MODIS", 1.0, 55.0),
    Sensor("aqua", "Aqua", 27424, "MODIS", 1.0, 55.0),
    Sensor("snpp", "Suomi NPP", 37849, "VIIRS", 0.375, 56.28),
    Sensor("noaa20", "NOAA-20", 43013, "VIIRS", 0.375, 56.28),
    Sensor("noaa21", "NOAA-21", 54234, "VIIRS", 0.375, 56.28),
]


def read_tles(tle_dir: Path = TLE_DIR) -> dict[int, tuple[str, str]]:
    """NORAD id -> (line1, line2) for every valid TLE found (newest epoch wins)."""
    out: dict[int, tuple[str, str]] = {}
    for f in sorted(tle_dir.glob("*.tle")):
        lines = [ln.rstrip() for ln in f.read_text(errors="replace").splitlines()]
        for a, b in zip(lines, lines[1:]):
            if a.startswith("1 ") and b.startswith("2 ") and len(a) >= 69 and len(b) >= 69:
                try:
                    norad = int(a[2:7])
                    epoch = float(a[18:32])
                except ValueError:
                    continue
                if norad not in out or float(out[norad][0][18:32]) < epoch:
                    out[norad] = (a, b)
    return out


def _gmst(jd_ut1: np.ndarray) -> np.ndarray:
    t = (jd_ut1 - 2451545.0) / 36525.0
    g = 280.46061837 + 360.98564736629 * (jd_ut1 - 2451545.0) + 0.000387933 * t * t - t ** 3 / 38710000.0
    return np.radians(g % 360.0)


def _observer_ecef(lat: float, lon: float) -> tuple[np.ndarray, np.ndarray]:
    la, lo = math.radians(lat), math.radians(lon)
    e2 = WGS_F * (2 - WGS_F)
    n = WGS_A / math.sqrt(1 - e2 * math.sin(la) ** 2)
    pos = np.array([n * math.cos(la) * math.cos(lo), n * math.cos(la) * math.sin(lo), n * (1 - e2) * math.sin(la)])
    up = np.array([math.cos(la) * math.cos(lo), math.cos(la) * math.sin(lo), math.sin(la)])
    return pos, up


def _epoch_dt(sat: Satrec) -> dt.datetime:
    jd = sat.jdsatepoch + sat.jdsatepochF
    return dt.datetime(2000, 1, 1, 12, tzinfo=dt.timezone.utc) + dt.timedelta(days=jd - 2451545.0)


def julian(times: list[dt.datetime]) -> tuple[np.ndarray, np.ndarray]:
    jd, fr = np.array([jday(t.year, t.month, t.day, t.hour, t.minute, t.second + t.microsecond / 1e6)
                       for t in times]).T
    return np.ascontiguousarray(jd), np.ascontiguousarray(fr)


def geometry(sat: Satrec, lat: float, lon: float, jd: np.ndarray, fr: np.ndarray):
    """Elevation (deg), scan angle (deg), slant range (km), altitude (km) per time, NaN on SGP4 error."""
    err, r, _ = sat.sgp4_array(jd, fr)
    g = _gmst(jd + fr)
    c, s = np.cos(g), np.sin(g)
    x = c * r[:, 0] + s * r[:, 1]                  # TEME -> ECEF (polar motion ignored)
    y = -s * r[:, 0] + c * r[:, 1]
    sat_ecef = np.c_[x, y, r[:, 2]]
    obs, up = _observer_ecef(lat, lon)
    rho = sat_ecef - obs
    rng = np.linalg.norm(rho, axis=1)
    elev = np.degrees(np.arcsin(np.clip(rho @ up / rng, -1, 1)))
    to_obs = obs - sat_ecef
    to_ctr = -sat_ecef
    cosang = np.einsum("ij,ij->i", to_obs, to_ctr) / (rng * np.linalg.norm(to_ctr, axis=1))
    scan = np.degrees(np.arccos(np.clip(cosang, -1, 1)))
    alt = np.linalg.norm(sat_ecef, axis=1) - np.linalg.norm(obs)
    bad = err != 0
    for a in (elev, scan, rng, alt):
        a[bad] = np.nan
    return elev, scan, rng, alt, err


def pixel_km(sensor: Sensor, elev_deg: float, rng_km: float, alt_km: float) -> tuple[float, float]:
    """(along-track, along-scan) pixel size at this geometry."""
    along_track = sensor.nadir_km * rng_km / alt_km
    zen = math.radians(90 - elev_deg)
    return along_track, along_track / max(math.cos(zen), 0.05)


def passes(lat: float, lon: float, start: dt.datetime, hours: float, tles: dict | None = None,
           sensors: list[Sensor] = SENSORS) -> list[dict]:
    tles = read_tles() if tles is None else tles
    n = int(hours * 3600 / STEP_S) + 1
    times = [start + dt.timedelta(seconds=STEP_S * i) for i in range(n)]
    jd, fr = julian(times)
    out = []
    for sn in sensors:
        info = {"satellite": sn.name, "key": sn.key, "norad": sn.norad, "instrument": sn.instrument,
                "max_scan_deg": sn.max_scan_deg, "passes": []}
        if sn.norad not in tles:
            info["error"] = f"no valid TLE for NORAD {sn.norad} in {TLE_DIR}"
            out.append(info)
            continue
        sat = Satrec.twoline2rv(*tles[sn.norad])
        ep = _epoch_dt(sat)
        info["tle_epoch"] = ep.isoformat()
        info["tle_age_days"] = round((start - ep).total_seconds() / 86400, 2)
        elev, scan, rng, alt, err = geometry(sat, lat, lon, jd, fr)
        if (err != 0).all():
            info["error"] = SGP4_ERRORS.get(int(err[0]), "sgp4 error")
            out.append(info)
            continue
        # in swath and on the visible side of the Earth
        inside = (np.nan_to_num(scan, nan=180) <= sn.max_scan_deg) & (np.nan_to_num(elev, nan=-90) > 0)
        idx = np.flatnonzero(np.diff(np.r_[0, inside.astype(np.int8), 0]))
        for a, b in zip(idx[::2], idx[1::2]):           # [a, b) runs inside the swath
            i = a + int(np.nanargmin(scan[a:b]))
            t_best = times[i]
            at, asc = pixel_km(sn, float(elev[i]), float(rng[i]), float(alt[i]))
            solar_h = (t_best.hour + t_best.minute / 60 + lon / 15) % 24
            info["passes"].append({
                "start": times[a].isoformat(), "end": times[b - 1].isoformat(), "best": t_best.isoformat(),
                "minutes_until": round((t_best - start).total_seconds() / 60, 1),
                "max_elevation_deg": round(float(elev[i]), 1), "min_scan_deg": round(float(scan[i]), 1),
                "pixel_km": [round(at, 2), round(asc, 2)],
                "local_solar_hour": round(solar_h, 1), "day_pass": 6 <= solar_h < 18,
                "in_progress": bool(a == 0),
            })
        out.append(info)
    return out
