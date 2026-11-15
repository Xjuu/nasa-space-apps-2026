"""Gridded hourly weather (Open-Meteo, keyless) + Canadian Forest Fire Weather Index (FWI) System.

Sources (free, no key, no account):
  * Forecast API  https://api.open-meteo.com/v1/forecast         past ~92 days .. +16 days (best-match NWP blend)
  * Archive API   https://archive-api.open-meteo.com/v1/archive  1940 .. ~5 days ago (ERA5 / ERA5-Land reanalysis)
  Requests for days older than ARCHIVE_LAG_DAYS go to the archive, the rest to the forecast API (split per call).
  Many points are sent in ONE request (comma-separated latitude/longitude lists).

Units (Open-Meteo defaults, all hourly values, timezone UTC):
  temperature_2m        deg C (2 m air temperature, instantaneous)
  relative_humidity_2m  %     (0-100)
  wind_speed_10m        km/h  (10 m, instantaneous)  -> FWI expects km/h, so no conversion
  wind_direction_10m    deg   meteorological: direction the wind blows FROM, 0 = north, clockwise
  wind_gusts_10m        km/h  (max gust in the preceding hour)
  precipitation         mm    (sum over the preceding hour)

FWI System: Van Wagner (1987) "Development and structure of the Canadian Forest Fire Weather Index System",
equations transcribed from the reference R package `cffdrs` (fine_fuel_moisture_code.r, duff_moisture_code.r,
drought_code.r, initial_spread_index.r, buildup_index.r, fire_weather_index.r, fwi.r), including its latitude
day-length adjustments for DMC/DC. Validated in tests/test_fireweather.py against the cffdrs test tables.

Daily inputs are taken at local NOON (approximated as solar noon, UTC hour = round(12 - lon/15)):
temperature, RH, 10 m wind; rain = 24 h total ending at that noon. Spin-up: starts SPINUP_DAYS before the
requested date from the standard start values FFMC 85, DMC 6, DC 15 (cffdrs defaults). DC has a months-long
memory, so in a long drought a 60-day spin-up still UNDER-estimates DC/BUI (use spinup_days=120+ there).

Rate limits: Open-Meteo free tier ~600 calls/min, 5000/h, 10000/day, shared with equifire/firewx.py. All
requests go through one throttle (>= MIN_INTERVAL s apart, also across processes via a lock file), back off on
HTTP 429, and responses are cached on disk in cache/fireweather/http/ (archive: forever, forecast: 1 h).

    python -m equifire.fireweather fwi 39.5 -121.5            # FWI today
    python -m equifire.fireweather grid 39.5 -121.5 --half-km 10 --hours 24
"""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import requests

from .common import CACHE

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
HOURLY_VARS = ["temperature_2m", "relative_humidity_2m", "wind_speed_10m", "wind_direction_10m",
               "wind_gusts_10m", "precipitation"]
UNITS = {"temperature_2m": "degC", "relative_humidity_2m": "%", "wind_speed_10m": "km/h",
         "wind_direction_10m": "deg_from", "wind_gusts_10m": "km/h", "precipitation": "mm/h"}
ARCHIVE_LAG_DAYS = 6          # archive (ERA5T) lags real time by ~5 days
FORECAST_PAST_DAYS = 92       # forecast API serves at most ~3 months of past data
DIR = CACHE / "fireweather"
HTTP_DIR = DIR / "http"
LOCK = DIR / ".throttle"
MIN_INTERVAL = 1.5            # seconds between Open-Meteo calls (polite; shared limit with firewx.py)
FORECAST_TTL = 3600
SPINUP_DAYS = 60
START = {"ffmc": 85.0, "dmc": 6.0, "dc": 15.0}
UA = {"User-Agent": "equifire-fireweather/1.0"}
_LAST_429 = 0.0


# ============================================================== HTTP (throttled + cached)
def _throttle():
    """Cross-process: hold an flock on LOCK, wait until MIN_INTERVAL passed since the last stamped call."""
    DIR.mkdir(parents=True, exist_ok=True)
    with open(LOCK, "a+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.seek(0)
        try:
            last = float(f.read().strip() or 0)
        except ValueError:
            last = 0.0
        wait = last + MIN_INTERVAL - time.time()
        if wait > 0:
            time.sleep(wait)
        f.seek(0)
        f.truncate()
        f.write(str(time.time()))
        fcntl.flock(f, fcntl.LOCK_UN)


def _get_json(url: str, params: dict, ttl: float | None, retries: int = 5):
    """GET with disk cache. ttl=None -> cache forever."""
    key = hashlib.sha1((url + "?" + json.dumps(params, sort_keys=True)).encode()).hexdigest()
    path = HTTP_DIR / f"{key}.json"
    if path.exists() and (ttl is None or time.time() - path.stat().st_mtime < ttl):
        return json.loads(path.read_text())
    global _LAST_429
    if time.time() - _LAST_429 < 60:
        raise RuntimeError("Open-Meteo rate-limit cooldown; retry later")
    err = None
    for a in range(retries):
        _throttle()
        try:
            r = requests.get(url, params=params, headers=UA, timeout=20 if retries == 1 else 60)
        except requests.RequestException as e:
            err = e
            if a + 1 >= retries:
                break
            time.sleep(5 * (a + 1))
            continue
        if r.status_code == 429:
            _LAST_429 = time.time()
            err = RuntimeError("429 Too Many Requests")
            if a + 1 >= retries:
                break
            wait = float(r.headers.get("Retry-After") or 30 * 2 ** a)
            print(f"[fireweather] 429 from Open-Meteo, backing off {wait:.0f}s", flush=True)
            time.sleep(min(wait, 300))
            continue
        if r.status_code >= 500:
            err = RuntimeError(f"HTTP {r.status_code}")
            if a + 1 >= retries:
                break
            time.sleep(5 * (a + 1))
            continue
        if r.status_code != 200:
            raise RuntimeError(f"Open-Meteo HTTP {r.status_code}: {r.text[:300]}")
        data = r.json()
        HTTP_DIR.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data))
        tmp.replace(path)
        return data
    raise RuntimeError(f"Open-Meteo request failed after {retries} tries: {err}")


def fetch_hourly(lats, lons, start_date: dt.date, end_date: dt.date, variables=HOURLY_VARS,
                 today: dt.date | None = None, retries=5):
    """Hourly series for many points in one call per API (archive part + forecast part).

    Returns (times: np.ndarray[datetime64[h]] (T,), data: {var: float array (P, T)}) with NaN for gaps."""
    lats = [round(float(v), 4) for v in np.atleast_1d(lats)]
    lons = [round(float(v), 4) for v in np.atleast_1d(lons)]
    today = today or dt.datetime.now(dt.timezone.utc).date()
    cut = today - dt.timedelta(days=ARCHIVE_LAG_DAYS)            # first day served by the forecast API
    parts = []
    if start_date < cut:
        parts.append((ARCHIVE_URL, start_date, min(end_date, cut - dt.timedelta(days=1)), None))
    if end_date >= cut:
        s = max(start_date, cut)
        parts.append((FORECAST_URL, s, end_date, FORECAST_TTL))
    base = {"latitude": ",".join(map(str, lats)), "longitude": ",".join(map(str, lons)),
            "hourly": ",".join(variables), "timezone": "GMT"}
    t_all = np.arange(np.datetime64(start_date.isoformat(), "h"),
                      np.datetime64((end_date + dt.timedelta(days=1)).isoformat(), "h"))
    out = {v: np.full((len(lats), len(t_all)), np.nan, dtype=np.float32) for v in variables}
    def fill(url, s, e, ttl, only_nan=False):
        p = dict(base, start_date=s.isoformat(), end_date=e.isoformat())
        res = _get_json(url, p, ttl, retries=retries)
        res = res if isinstance(res, list) else [res]
        for i, loc in enumerate(res):
            h = loc["hourly"]
            t = np.array(h["time"], dtype="datetime64[h]")
            idx = (t - t_all[0]).astype(int)
            ok = (idx >= 0) & (idx < len(t_all))
            for v in variables:
                vals = np.array([np.nan if x is None else x for x in h[v]], dtype=np.float32)
                tgt = out[v][i]
                if only_nan:
                    m = ok.copy()
                    m[ok] = np.isnan(tgt[idx[ok]])
                    tgt[idx[m]] = vals[m]
                else:
                    tgt[idx[ok]] = vals[ok]

    for url, s, e, ttl in parts:
        fill(url, s, e, ttl)
    # the archive can lag more than ARCHIVE_LAG_DAYS: fill its trailing NaN days from the forecast API
    if parts and parts[0][0] == ARCHIVE_URL:
        nan_t = np.isnan(out[variables[0]]).any(axis=0)
        oldest = today - dt.timedelta(days=FORECAST_PAST_DAYS - 1)
        if nan_t.any():
            first = dt.date.fromisoformat(str(t_all[np.argmax(nan_t)].astype("datetime64[D]")))
            s = max(first, oldest)
            e = min(end_date, cut - dt.timedelta(days=1))
            if s <= e:
                fill(FORECAST_URL, s, e, FORECAST_TTL, only_nan=True)
    return t_all, out


# ============================================================== weather grid
@dataclass
class WeatherGrid:
    """Hourly weather on an n x n lat/lon grid. arrays[var] has shape (T, n_lat, n_lon). Units: see UNITS."""
    lats: np.ndarray          # ascending (n,)
    lons: np.ndarray          # ascending (n,)
    times: np.ndarray         # datetime64[h] (T,), UTC
    arrays: dict = field(default_factory=dict)
    units: dict = field(default_factory=lambda: dict(UNITS))

    def interp(self, lat: float, lon: float, t) -> dict:
        """Bilinear in space, linear in time. Wind direction is interpolated through u/v components.
        t: datetime / np.datetime64 / ISO string (UTC). Points outside the grid are clamped to its edge."""
        tt = np.datetime64(t, "s") if not isinstance(t, dt.datetime) else np.datetime64(t.replace(tzinfo=None), "s")
        ft = (tt - self.times[0].astype("datetime64[s]")).astype(float) / 3600.0
        ft = float(np.clip(ft, 0, len(self.times) - 1))
        t0 = int(np.floor(ft)); t1 = min(t0 + 1, len(self.times) - 1); wt = ft - t0

        def frac(arr, v):
            f = np.interp(v, arr, np.arange(len(arr)))
            i0 = int(min(np.floor(f), len(arr) - 2)) if len(arr) > 1 else 0
            return i0, f - i0

        i, wi = frac(self.lats, lat)
        j, wj = frac(self.lons, lon)

        def bil(a):  # a (T, n, n)
            def sp(k):
                if a.shape[1] == 1:
                    return a[k, 0, 0]
                return ((1 - wi) * (1 - wj) * a[k, i, j] + (1 - wi) * wj * a[k, i, j + 1]
                        + wi * (1 - wj) * a[k, i + 1, j] + wi * wj * a[k, i + 1, j + 1])
            return float((1 - wt) * sp(t0) + wt * sp(t1))

        out = {}
        for v, a in self.arrays.items():
            if v == "wind_direction_10m":
                continue
            out[v] = bil(a)
        if "wind_direction_10m" in self.arrays and "wind_speed_10m" in self.arrays:
            rad = np.deg2rad(self.arrays["wind_direction_10m"])
            s = self.arrays["wind_speed_10m"]
            u, v = -s * np.sin(rad), -s * np.cos(rad)       # components the air moves TOWARD (east, north)
            uu, vv = bil(u), bil(v)
            out["wind_direction_10m"] = float((np.rad2deg(np.arctan2(-uu, -vv)) + 360) % 360)
            out["wind_u_kmh"], out["wind_v_kmh"] = uu, vv
        return out

    def at_hour(self, t) -> dict:
        """All grid arrays for the hour nearest to t: {var: (n, n)}."""
        k = int(np.argmin(np.abs(self.times - np.datetime64(t, "h"))))
        return {v: a[k] for v, a in self.arrays.items()}


def grid_points(lat, lon, half_km, n=5):
    dlat = half_km / 111.32
    dlon = half_km / (111.32 * max(np.cos(np.deg2rad(lat)), 0.05))
    return np.linspace(lat - dlat, lat + dlat, n), np.linspace(lon - dlon, lon + dlon, n)


def get_weather_grid(lat: float, lon: float, half_km: float, start_utc, hours: int, n: int = 5,
                     variables=HOURLY_VARS, retries=5) -> WeatherGrid:
    """Hourly weather on an n x n grid covering [lat +- half_km] x [lon +- half_km], hours from start_utc.

    One Open-Meteo request per API part (n*n points in one call). Open-Meteo model resolution is ~1-11 km
    (forecast best-match) / ~9-31 km (ERA5-Land/ERA5), so n > ~5 adds little for small areas."""
    st = np.datetime64(start_utc if not isinstance(start_utc, dt.datetime)
                       else start_utc.replace(tzinfo=None), "h")
    en = st + np.timedelta64(int(hours) - 1, "h")
    la, lo = grid_points(lat, lon, half_km, n)
    LA, LO = np.meshgrid(la, lo, indexing="ij")
    sd = dt.date.fromisoformat(str(st.astype("datetime64[D]")))
    ed = dt.date.fromisoformat(str(en.astype("datetime64[D]")))
    t, data = fetch_hourly(LA.ravel(), LO.ravel(), sd, ed, variables, retries=retries)
    sel = (t >= st) & (t <= en)
    arrays = {v: data[v][:, sel].T.reshape(sel.sum(), n, n) for v in variables}
    return WeatherGrid(la, lo, t[sel], arrays)


# ============================================================== FWI equations (cffdrs)
FFMC_COEFFICIENT = 250.0 * 59.5 / 101.0
_ELL01 = np.array([6.5, 7.5, 9, 12.8, 13.9, 13.9, 12.4, 10.9, 9.4, 8, 7, 6])
_ELL02 = np.array([7.9, 8.4, 8.9, 9.5, 9.9, 10.2, 10.1, 9.7, 9.1, 8.6, 8.1, 7.8])
_ELL03 = np.array([10.1, 9.6, 9.1, 8.5, 8.1, 7.8, 7.9, 8.3, 8.9, 9.4, 9.9, 10.2])
_ELL04 = np.array([11.5, 10.5, 9.2, 7.9, 6.8, 6.2, 6.5, 7.4, 8.7, 10, 11.2, 11.8])
_FL01 = np.array([-1.6, -1.6, -1.6, 0.9, 3.8, 5.8, 6.4, 5, 2.4, 0.4, -1.6, -1.6])
_FL02 = np.array([6.4, 5, 2.4, 0.4, -1.6, -1.6, -1.6, -1.6, -1.6, 0.9, 3.8, 5.8])


def ffmc_calc(ffmc_yda, temp, rh, ws, prec):
    """Fine Fuel Moisture Code. temp degC, rh %, ws km/h, prec mm (24 h)."""
    ffmc_yda, temp, rh, ws, prec = np.broadcast_arrays(*[np.asarray(a, float) for a in (ffmc_yda, temp, rh, ws, prec)])
    with np.errstate(all="ignore"):
        wmo = FFMC_COEFFICIENT * (101 - ffmc_yda) / (59.5 + ffmc_yda)
        ra = np.where(prec > 0.5, prec - 0.5, prec)
        add = 42.5 * ra * np.exp(-100 / (251 - wmo)) * (1 - np.exp(-6.93 / ra))
        wet = np.where(wmo > 150, wmo + 0.0015 * (wmo - 150) ** 2 * np.sqrt(ra) + add, wmo + add)
        wmo = np.where(prec > 0.5, wet, wmo)
        wmo = np.where(wmo > 250, 250, wmo)
        ed = 0.942 * rh ** 0.679 + 11 * np.exp((rh - 100) / 10) + 0.18 * (21.1 - temp) * (1 - 1 / np.exp(rh * 0.115))
        ew = 0.618 * rh ** 0.753 + 10 * np.exp((rh - 100) / 10) + 0.18 * (21.1 - temp) * (1 - 1 / np.exp(rh * 0.115))
        wetting = (wmo < ed) & (wmo < ew)
        z = np.where(wetting, 0.424 * (1 - ((100 - rh) / 100) ** 1.7) + 0.0694 * np.sqrt(ws) * (1 - ((100 - rh) / 100) ** 8), 0)
        x = z * 0.581 * np.exp(0.0365 * temp)
        wm = np.where(wetting, ew - (ew - wmo) / 10 ** x, wmo)
        z = np.where(wmo > ed, 0.424 * (1 - (rh / 100) ** 1.7) + 0.0694 * np.sqrt(ws) * (1 - (rh / 100) ** 8), z)
        x = z * 0.581 * np.exp(0.0365 * temp)
        wm = np.where(wmo > ed, ed + (wmo - ed) / 10 ** x, wm)
        f = 59.5 * (250 - wm) / (FFMC_COEFFICIENT + wm)
    return np.clip(f, 0, 101)


def _month_idx(mon):
    return np.asarray(mon, int) - 1


def dmc_calc(dmc_yda, temp, rh, prec, lat, mon, lat_adjust=True):
    """Duff Moisture Code (day-length factor by latitude band and month)."""
    dmc_yda, temp, rh, prec, lat, mon = np.broadcast_arrays(*[np.asarray(a, float) for a in (dmc_yda, temp, rh, prec, lat, mon)])
    m = _month_idx(mon)
    ell = _ELL01[m]
    if lat_adjust:
        ell = np.where((lat <= 30) & (lat > 10), _ELL02[m], ell)
        ell = np.where((lat <= -10) & (lat > -30), _ELL03[m], ell)
        ell = np.where((lat <= -30) & (lat >= -90), _ELL04[m], ell)
        ell = np.where((lat <= 10) & (lat > -10), 9.0, ell)
    temp = np.where(temp < -1.1, -1.1, temp)
    rk = 1.894 * (temp + 1.1) * (100 - rh) * ell * 1e-4
    with np.errstate(all="ignore"):
        rw = 0.92 * prec - 1.27
        wmi = 20 + 280 / np.exp(0.023 * dmc_yda)
        b = np.where(dmc_yda <= 33, 100 / (0.5 + 0.3 * dmc_yda),
                     np.where(dmc_yda <= 65, 14 - 1.3 * np.log(dmc_yda), 6.2 * np.log(dmc_yda) - 17.2))
        wmr = wmi + 1000 * rw / (48.77 + b * rw)
        pr = np.where(prec <= 1.5, dmc_yda, 43.43 * (5.6348 - np.log(wmr - 20)))
    pr = np.where(pr < 0, 0, pr)
    d = pr + rk
    return np.where(d < 0, 0, d)


def dc_calc(dc_yda, temp, rh, prec, lat, mon, lat_adjust=True):
    """Drought Code (day-length adjustment; rh unused, kept for the cffdrs signature)."""
    dc_yda, temp, prec, lat, mon = np.broadcast_arrays(*[np.asarray(a, float) for a in (dc_yda, temp, prec, lat, mon)])
    m = _month_idx(mon)
    temp = np.where(temp < -2.8, -2.8, temp)
    pe = (0.36 * (temp + 2.8) + _FL01[m]) / 2
    if lat_adjust:
        pe = np.where(lat <= -20, (0.36 * (temp + 2.8) + _FL02[m]) / 2, pe)
        pe = np.where((lat > -20) & (lat <= 20), (0.36 * (temp + 2.8) + 1.4) / 2, pe)
    pe = np.where(pe < 0, 0, pe)
    with np.errstate(all="ignore"):
        rw = 0.83 * prec - 1.27
        smi = 800 * np.exp(-dc_yda / 400)
        dr0 = dc_yda - 400 * np.log(1 + 3.937 * rw / smi)
    dr0 = np.where(dr0 < 0, 0, dr0)
    dr = np.where(prec <= 2.8, dc_yda, dr0)
    d = dr + pe
    return np.where(d < 0, 0, d)


def isi_calc(ffmc, ws, fbp_mod=False):
    """Initial Spread Index (fbp_mod: FBP-system wind function above 40 km/h)."""
    ffmc, ws = np.broadcast_arrays(np.asarray(ffmc, float), np.asarray(ws, float))
    with np.errstate(all="ignore"):
        fm = FFMC_COEFFICIENT * (101 - ffmc) / (59.5 + ffmc)
        fw = np.where((ws >= 40) & bool(fbp_mod), 12 * (1 - np.exp(-0.0818 * (ws - 28))), np.exp(0.05039 * ws))
        ff = 91.9 * np.exp(-0.1386 * fm) * (1 + fm ** 5.31 / 49300000)
    return 0.208 * fw * ff


def bui_calc(dmc, dc):
    """Buildup Index."""
    dmc, dc = np.broadcast_arrays(np.asarray(dmc, float), np.asarray(dc, float))
    with np.errstate(all="ignore"):
        bui1 = np.where((dmc == 0) & (dc == 0), 0, 0.8 * dc * dmc / (dmc + 0.4 * dc))
        p = np.where(dmc == 0, 0, (dmc - bui1) / dmc)
        cc = 0.92 + (0.0114 * dmc) ** 1.7
    bui0 = dmc - cc * p
    bui0 = np.where(bui0 < 0, 0, bui0)
    return np.where(bui1 < dmc, bui0, bui1)


def fwi_calc(isi, bui):
    """Fire Weather Index."""
    isi, bui = np.broadcast_arrays(np.asarray(isi, float), np.asarray(bui, float))
    with np.errstate(all="ignore"):
        bb = np.where(bui > 80, 0.1 * isi * (1000 / (25 + 108.64 / np.exp(0.023 * bui))),
                      0.1 * isi * (0.626 * bui ** 0.809 + 2))
        return np.where(bb <= 1, bb, np.exp(2.72 * (0.434 * np.log(bb)) ** 0.647))


def dsr_calc(fwi):
    """Daily Severity Rating."""
    return 0.0272 * np.asarray(fwi, float) ** 1.77


def fwi_series(temp, rh, ws, prec, mon, lat, init=None, lat_adjust=True) -> dict:
    """Run the daily FWI system over a sequence of noon observations (like cffdrs::fwi for one station).
    Returns dict of arrays ffmc, dmc, dc, isi, bui, fwi, dsr."""
    init = dict(START, **(init or {}))
    f, d, c = init["ffmc"], init["dmc"], init["dc"]
    out = {k: [] for k in ("ffmc", "dmc", "dc", "isi", "bui", "fwi", "dsr")}
    for T, H, W, P, M in zip(temp, rh, ws, prec, mon):
        H = 99.9999 if H >= 100 else H
        P = max(float(P), 0.0)
        W = max(float(W), 0.0)
        H = max(float(H), 0.0)
        f = float(ffmc_calc(f, T, H, W, P))
        d = float(dmc_calc(d, T, H, P, lat, M, lat_adjust))
        c = float(dc_calc(c, T, H, P, lat, M, lat_adjust))
        i = float(isi_calc(f, W))
        b = float(bui_calc(d, c))
        w = float(fwi_calc(i, b))
        for k, v in zip(out, (f, d, c, i, b, w, float(dsr_calc(w)))):
            out[k].append(v)
    return {k: np.array(v) for k, v in out.items()}


# ============================================================== FWI from Open-Meteo
def noon_hour_utc(lon: float) -> int:
    """UTC hour of local solar noon (approximation of local standard noon used by the FWI system)."""
    return int(round(12 - lon / 15.0)) % 24


def daily_noon_weather(lat, lon, start: dt.date, end: dt.date, retries=5):
    """Noon temp/RH/wind and 24 h rain (noon-to-noon) for each day start..end from hourly Open-Meteo data."""
    h = noon_hour_utc(lon)
    off = int(round(12 - lon / 15.0)) - h          # day shift if solar noon falls on the previous/next UTC day
    t, data = fetch_hourly([lat], [lon], start - dt.timedelta(days=2), end + dt.timedelta(days=1),
                           ["temperature_2m", "relative_humidity_2m", "wind_speed_10m", "precipitation"], retries=retries)
    days = [start + dt.timedelta(days=k) for k in range((end - start).days + 1)]
    rows = []
    for day in days:
        tn = np.datetime64(day.isoformat(), "h") + np.timedelta64(h + 24 * (off // 24), "h")
        k = int((tn - t[0]).astype(int))
        T, H, W = (float(data[v][0, k]) for v in ("temperature_2m", "relative_humidity_2m", "wind_speed_10m"))
        rain = data["precipitation"][0, k - 23:k + 1]     # hours ending at noon (each value = preceding hour)
        rows.append({"date": day.isoformat(), "noon_utc": str(tn), "temp": T, "rh": H, "ws": W,
                     "prec": float(np.nansum(rain)), "missing": bool(np.isnan([T, H, W]).any())})
    return rows


def fwi_for(lat: float, lon: float, date=None, spinup_days: int = SPINUP_DAYS, init=None, retries=5) -> dict:
    """FWI system for (lat, lon) on `date` (UTC date, default today), spun up from `spinup_days` before.

    Returns {'lat','lon','date','ffmc','dmc','dc','isi','bui','fwi','dsr','danger','series':[daily dicts],
             'source', 'spinup_days', 'units'}. Missing noon values (rare gaps) are filled by persistence."""
    date = dt.date.fromisoformat(str(date)) if date is not None else dt.datetime.now(dt.timezone.utc).date()
    start = date - dt.timedelta(days=spinup_days)
    rows = daily_noon_weather(lat, lon, start, date, retries=retries)
    last = None
    for r in rows:                                   # persistence fill for gaps
        if r["missing"]:
            if last is None:
                continue
            for k in ("temp", "rh", "ws"):
                if np.isnan(r[k]):
                    r[k] = last[k]
        last = r
    rows = [r for r in rows if not np.isnan([r["temp"], r["rh"], r["ws"]]).any()]
    s = fwi_series([r["temp"] for r in rows], [r["rh"] for r in rows], [r["ws"] for r in rows],
                   [r["prec"] for r in rows], [int(r["date"][5:7]) for r in rows], lat, init=init)
    series = [dict(r, **{k: round(float(s[k][i]), 2) for k in s}) for i, r in enumerate(rows)]
    cur = series[-1]
    return {"lat": lat, "lon": lon, "date": cur["date"], **{k: cur[k] for k in s},
            "danger": danger_class(cur["fwi"]), "series": series, "spinup_days": spinup_days,
            "source": "Open-Meteo (archive ERA5 + forecast best-match), noon LST approx = solar noon",
            "units": {"temp": "degC", "rh": "%", "ws": "km/h", "prec": "mm/24h"}}


def danger_class(fwi: float) -> str:
    """Canadian (CWFIS) FWI danger classes: <5 low, 5-10 moderate, 10-20 high, 20-30 very high, >=30 extreme."""
    return ("low" if fwi < 5 else "moderate" if fwi < 10 else "high" if fwi < 20
            else "very high" if fwi < 30 else "extreme")


def _main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("fwi"); a.add_argument("lat", type=float); a.add_argument("lon", type=float)
    a.add_argument("--date"); a.add_argument("--spinup", type=int, default=SPINUP_DAYS)
    g = sub.add_parser("grid"); g.add_argument("lat", type=float); g.add_argument("lon", type=float)
    g.add_argument("--half-km", type=float, default=10); g.add_argument("--hours", type=int, default=24)
    g.add_argument("--start"); g.add_argument("--n", type=int, default=5)
    args = ap.parse_args()
    if args.cmd == "fwi":
        r = fwi_for(args.lat, args.lon, args.date, args.spinup)
        print(json.dumps({k: v for k, v in r.items() if k != "series"}, indent=1))
        for row in r["series"][-5:]:
            print(row)
    else:
        st = args.start or dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H")
        wg = get_weather_grid(args.lat, args.lon, args.half_km, st, args.hours, args.n)
        print(wg.times[0], wg.times[-1], {v: a.shape for v, a in wg.arrays.items()})
        print(wg.interp(args.lat, args.lon, wg.times[len(wg.times) // 2]))


if __name__ == "__main__":
    _main()
