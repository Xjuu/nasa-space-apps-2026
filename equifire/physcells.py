"""Physics simulator (backend/physics_spread.py) rasterised onto the ML 0.05 deg (~5.5 km) case grid.

    from equifire.physcells import physics_cells
    pc = physics_cells(fi, (t0, cx, cy), days=3)

For one archive fire case of the direct ML model (start day t0, window centre cell cx, cy, window
41 x 41 cells = direct.S) this runs the Rothermel / minimum-travel-time physics exactly as GET /simulate
does with ignition=archive (read-only use of backend.physics_spread; nothing is edited there):

  * ignition  = every VIIRS-burning 0.05 deg cell of day t0 within `radius_km` of the case centre
                (cell centres; own builder, same semantics as ps.ignition_archive -- see _WindowState)
  * start     = t0 12:00 UTC (archive convention of /simulate); run length 24 * days + 12 h, so "by day k"
                means "by the end of UTC day t0 + k", the same day boundary as the ML truth (case_truth)
  * weather   = Open-Meteo archive hourly, single point at the ignition centroid (ps.fetch_weather), cached
                in backend/.cache/meteo_*; uncached calls go through the shared cross-process throttle
                cache/fireweather/.throttle (>= 1.5 s apart) used by equifire.fireweather / firewx
  * terrain   = flat by default (elev=False) to save elevation API calls; elev=True uses ps.dem_sampler
  * calibration (calibration=True): ps.calibrate on the fire's own previous 1-3 days of archive cells
                (pixel measure 'cell005', only days < t0 ... t0 noon), multiplier m applied to every run
  * envelope  = two extra runs with the /simulate envelope definition (low = wind x0.8 & RH +10,
                high = wind x1.2 & RH -10), each mapped independently to ML cells.

Returned per-ML-cell features (arrays indexed [x, y] like direct.FireIndex.cube, x = gx - cx + W):
  frac[v, k-1]  fraction of the ML cell's raster cells burned by the end of day k, v = 0 low, 1 central, 2 high
  arr_h         earliest central arrival time in the cell (hours after t0 12:00 UTC; inf = not reached)
  cover         fraction of the ML cell that lies inside the physics raster domain (0 = physics blind there)
  meta          m, calibration summary, fuel class, ignition cells, domain size, runtime, errors
Cached in cache/physcells/<t0>_<cx>_<cy>_d<days>_c<cal>_r<radius>_e<elev>.npz.
"""
from __future__ import annotations

import json
import os
import threading
import math
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

from .common import CACHE, GRID, cell_center
from .direct import S, W

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DIR = CACHE / "physcells"
FUELS = ["grass", "savanna", "shrub", "forest"]
VERSION = 2


def throttled_getter(url, params, ttl):
    """ps._get_json with the shared Open-Meteo throttle in front of every uncached call."""
    from backend import physics_spread as ps
    key = url + "?" + "&".join(f"{k}={params[k]}" for k in sorted(params))
    hit = ps._cache_get(key, ttl)
    if hit is not None:
        return hit, True
    from .fireweather import _throttle
    _throttle()
    return ps._get_json(url, params, ttl)


class _WindowState:
    """Burning VIIRS 0.05 deg cells of the case window from the memory-mapped fire index.

    Own ignition / history builders (same semantics as ps.ignition_archive / ps.load_history('archive')):
    physics_spread._archive_cells unpacks equifire.common.cell_center (which returns (lat, lon)) as (lon, lat);
    reported in COORDINATION.md. These use the correct order."""

    def __init__(self, fi, cx, cy):
        self.fi, self.cx, self.cy = fi, cx, cy

    def cells(self, day):
        i = self.fi.v.query(int(day), int(day), self.cx, self.cy)
        lat, lon = cell_center(np.asarray(self.fi.v.gx[i], np.int64), np.asarray(self.fi.v.gy[i], np.int64))
        return np.asarray(lat, float), np.asarray(lon, float)

    def ignition(self, lat, lon, day, radius_km):
        from backend import physics_spread as ps
        la, lo = self.cells(day)
        sel = ps._km(lat, lon, la, lo) <= radius_km if len(la) else np.zeros(0, bool)
        if not sel.any():
            return None
        return {"mode": "archive", "lats": la[sel], "lons": lo[sel], "estimate": True, "n_cells": int(sel.sum()),
                "radius_km": radius_km, "day": int(day), "pixel_m": 0,
                "note": "burning VIIRS 0.05 deg cells of that day (EquiFire archive); ignition at each cell CENTRE"}

    def history(self, day0, days_max=3):
        from backend.data import date_of
        rows = []
        for k in range(0, days_max + 2):
            la, lo = self.cells(day0 - k)
            if len(la):
                rows.append(pd.DataFrame({"lat": la, "lon": lo, "day": day0 - k}))
        if not rows:
            return None
        df = pd.concat(rows, ignore_index=True)
        df["t"] = pd.to_datetime([date_of(int(d)) for d in df["day"]]) + pd.Timedelta(hours=12)
        return df[["lat", "lon", "t"]]


def _flat(LA, LO):
    return np.zeros(np.shape(LA))


def _variant_wx(wx, wind, drh):
    w = wx.copy()
    w["wind_kmh"] = w["wind_kmh"] * wind
    if "gust_kmh" in w:
        w["gust_kmh"] = w["gust_kmh"] * wind
    w["rh"] = (w["rh"] + drh).clip(1, 100)
    return w


def _arrival(frames):
    """Per-raster-cell arrival hour from keep_masks frames (first frame where burned)."""
    m0, g = frames[0]["_mask"]
    T = np.full(m0.shape, np.inf)
    for fr in frames:
        m, _ = fr["_mask"]
        T[np.isinf(T) & m] = fr["t_hours"]
    return T, g


def _to_cells(T, g, cx, cy, days, end_offset=12):
    """Raster arrival times -> ML-cell (count, burned-by-day-k counts, min arrival)."""
    lons, lats = g.centre_lonlat()           # lon per column j, lat per row i
    gxs = np.floor((lons + 180) / GRID).astype(np.int64) - cx + W
    gys = np.floor((lats + 90) / GRID).astype(np.int64) - cy + W
    X = np.broadcast_to(gxs[None, :], T.shape)
    Y = np.broadcast_to(gys[:, None], T.shape)
    ok = (X >= 0) & (X < S) & (Y >= 0) & (Y < S)
    flat = (X * S + Y)[ok]
    t = T[ok]
    n = np.bincount(flat, minlength=S * S).astype(np.float32)
    burned = np.stack([np.bincount(flat[t <= 24 * k + end_offset + 1e-6], minlength=S * S)
                       for k in range(1, days + 1)])
    arr = np.full(S * S, np.inf)
    np.minimum.at(arr, flat, t)
    return n.reshape(S, S), burned.reshape(days, S, S).astype(np.float32), arr.reshape(S, S)


def _cell_area_raster(g, cx, cy):
    """Number of raster cells that would fill one ML cell at this latitude (for the cover fraction)."""
    lat = float(cell_center(cx, cy)[0])
    ml = (GRID * 111_320) ** 2 * math.cos(math.radians(lat))
    return ml / g.cell ** 2


def physics_cells(fi, case, days=3, calibration=True, radius_km=60.0, elev=False, getter=None, use_cache=True,
                  envelope=True, verbose=False, start=None, history=None):
    t0, cx, cy = (int(v) for v in case)
    tag = f"{t0}_{cx}_{cy}_d{days}_c{int(bool(calibration))}_r{int(radius_km)}_e{int(bool(elev))}_v{VERSION}"
    if start is not None or history is not None:
        use_cache = False  # partial-day feeds change within the day; never reuse archive ignition/weather
    if not envelope:
        tag += "_noenv"
    f = DIR / f"{tag}.npz"
    if use_cache and f.exists():
        z = np.load(f, allow_pickle=False)
        return {"frac": z["frac"], "arr_h": z["arr_h"], "cover": z["cover"], "meta": json.loads(str(z["meta"]))}
    from backend import physics_spread as ps
    from backend.data import date_of
    tt = time.time()
    getter = getter or throttled_getter
    state = _WindowState(fi, cx, cy)
    clat0, clon0 = (float(v) for v in cell_center(cx, cy))
    start = ps._parse_start(start) if start is not None else pd.Timestamp(date_of(t0)) + pd.Timedelta(hours=12)
    end_offset = 24 - start.hour
    hours = 24 * days + end_offset
    meta = {"t0": t0, "cx": cx, "cy": cy, "days": days, "start_utc": start.isoformat() + "Z", "hours": hours,
            "radius_km": radius_km, "terrain": "Open-Meteo DEM" if elev else "flat (assumed)", "errors": []}
    ign = state.ignition(clat0, clon0, t0, radius_km)
    empty = {"frac": np.zeros((3, days, S, S), np.float32), "arr_h": np.full((S, S), np.inf),
             "cover": np.zeros((S, S), np.float32)}
    if ign is None:
        meta["errors"].append("no burning cell within radius at t0")
        meta.update(m=1.0, cal_applied=False, cal_confidence="none", fuel="none", ok=False)
        return {**empty, "meta": meta}
    clat, clon = float(np.mean(ign["lats"])), float(np.mean(ign["lons"]))
    t_now = start
    D, t_c0, hist, pixel = 0, None, None, "cell005"
    if calibration:
        hist = history if history is not None else state.history(t0)
        if hist is not None:
            ha = ps.associate(hist[pd.to_datetime(hist["t"]) <= t_now], ign["lats"], ign["lons"])
            if not ha.empty:
                D, t_c0 = ps.history_window(ha, t_now)
    w_start = t_c0 if t_c0 is not None else start
    span = int(math.ceil((start + pd.Timedelta(hours=hours) - w_start).total_seconds() / 3600))
    wx, wx_info = ps.fetch_weather(clat, clon, w_start, span, getter=getter)
    meta["weather_fallback"] = bool(wx_info.get("fallback"))
    meta["weather_sources"] = wx_info.get("sources", [])
    meta["weather_time_step_minutes"] = 60
    meta["errors"] += wx_info.get("errors", [])
    ef = (ps.dem_sampler(clat, clon, getter=getter) if elev else None) or _flat
    cal = {"applied": False, "m": 1.0, "confidence": "none",
           "reason": "calibration off" if not calibration else "no usable 1-3 day history"}
    if calibration and t_c0 is not None:
        try:
            cal = ps.calibrate(ign["lats"], ign["lons"], t_now, hist, wx=wx, elev_fn=ef, getter=getter, pixel=pixel)
        except Exception as e:  # never let calibration kill the run
            meta["errors"].append(f"calibration: {type(e).__name__}: {str(e)[:120]}")
    m = float(cal.get("m", 1.0))
    variants = [(1, 1.0, 0.0)] + ([(0, 0.8, 10.0), (2, 1.2, -10.0)] if envelope else [])
    frac = np.zeros((3, days, S, S), np.float32)
    arr = cover = None
    fuel = None
    edge = False
    for vi, wind, drh in variants:
        w = wx if vi == 1 else _variant_wx(wx, wind, drh)
        r = ps.simulate(clat, clon, start, hours, 180, ignition=ign, wx=w, wx_info=wx_info, getter=getter,
                        elev_fn=ef, envelope=False, frames=False, keep_masks=True, ros_mult=m)
        T, g = _arrival(r["frames"])
        n, burned, a = _to_cells(T, g, cx, cy, days, end_offset)
        frac[vi] = np.where(n > 0, burned / np.maximum(n, 1), 0)
        if vi == 1:
            arr = a
            cover = np.clip(n / _cell_area_raster(g, cx, cy), 0, 1).astype(np.float32)
            fuel = r["fuel"]["class"]
            edge = bool(r["summary"]["reached_domain_edge"])
            meta.update(cell_m=r["grid"]["cell_m"], grid_n=r["grid"]["size"], half_km=r["grid"]["half_width_km"],
                        burned_km2=r["summary"]["burned_km2"], initial_km2=r["summary"]["initial_km2"])
    if not envelope:
        frac[0] = frac[2] = frac[1]
    # low <= central <= high, like /simulate's clipped envelope
    frac[0] = np.minimum(frac[0], frac[1])
    frac[2] = np.maximum(frac[2], frac[1])
    meta.update(m=m, cal_applied=bool(cal.get("applied")), cal_confidence=cal.get("confidence", "none"),
                cal_days=int(cal.get("days_used", 0) or 0), cal_reason=cal.get("reason"), fuel=fuel,
                n_ignition_cells=int(ign["n_cells"]), reached_edge=edge, ok=True,
                runtime_s=round(time.time() - tt, 2))
    out = {"frac": frac, "arr_h": arr, "cover": cover, "meta": meta}
    if use_cache:
        DIR.mkdir(parents=True, exist_ok=True)
        tmp = f.with_name(f.stem + f".{os.getpid()}.{threading.get_ident()}.tmp.npz")
        np.savez_compressed(tmp, frac=frac, arr_h=arr, cover=cover, meta=json.dumps(meta, default=str))
        tmp.replace(f)
    if verbose:
        print(f"[physcells] {tag}: m={m:.3g} fuel={fuel} {meta['runtime_s']}s", flush=True)
    return out
