"""High-confidence 1 km fire-spread alerts from raw VIIRS 375 m detections.

Grid: 0.01 deg (~1.1 km) cells, per UTC day, union of S-NPP and NOAA-20 (type == 0).

Definitions (used everywhere in this module):
  * burning(t, c): at least one detection in cell c on day t.
  * fire case / "fire-day": (day t0, 0.5-deg tile) with >= 5 burning cells in the tile.
  * candidate: core-tile cell NOT burning on t0 with Chebyshev distance <= 3 cells to a
    cell burning on t0.
  * new fire (horizon h): cell burning on some day in t0+1..t0+h and NOT burning on t0.
  * label / alert hit: the candidate or any of its 8 neighbours has new fire (h days).
  * precision = alerts that are hits / alerts.
  * recall = new-fire cells in the core tile lying within +-1 cell of some alert
    / all new-fire cells in the core tile (including those far from today's fire,
    which no candidate can reach -> honest upper bound < 100%).
Features use days <= t0 only.
"""
from __future__ import annotations

import json
import os
from datetime import date as _date, datetime
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import ndimage

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data_country"
CACHE = ROOT / "cache" / "alerts1km"
MODELS = ROOT / "models"
COUNTRIES = ["Brazil", "Australia", "India", "United_States", "Canada"]
TILE = 50          # 0.5 deg tiles
PAD = 12           # padding cells around the core window (ring r=10 + label r=1)
HIST = 30          # dense history days
HMAX = 3
RINGS = (1, 2, 3, 5, 10)
EPOCH = np.datetime64("1970-01-01")

FEATURES = (
    ["dist", "dual_sat", "lat", "doy_sin", "doy_cos"]
    + [f"cnt_r{r}" for r in RINGS] + [f"frp_r{r}" for r in RINGS]
    + ["cnt1_r1", "cnt1_r3", "cnt2_r3", "new0_r3", "new0_r5",
       "burn7", "burn7_days", "burn7_r1", "burn30", "burn30_r1", "since_last365",
       "align_glob", "growth_glob", "align_loc", "growth_loc",
       "size0", "size1", "size2", "size_ratio", "frp0", "frp1", "frp_ratio",
       "hi_frac_r3", "lo_frac_r3", "night_frac_r3", "win_night", "persist7", "frp_max_r1"]
)


EXTRA_FEATURES = ["lon", "dist_km", "dist_prev_km", "dist_front_km", "nearest_frp",
                  "nearest_hi_frac", "nearest_night_frac", "active7_r3", "persist_r3",
                  "local_growth_ratio", "local_frp_trend", "burn365_days", "burn365_r3",
                  "front_r3", "new7_r3", "dist_change_km"]


# ----------------------------------------------------------------------------- data
def _csv_paths(country: str, year: int):
    out = []
    for sat in ("viirs-snpp", "viirs-jpss1"):
        p = DATA / sat / str(year) / f"{sat}_{year}_{country}.csv"
        if p.exists():
            out.append(p)
    return out


def aggregate_detections(df: pd.DataFrame) -> dict:
    """Raw detections -> per (day, cell) records (sorted by tile, day)."""
    if "type" in df.columns:
        df = df[df["type"] == 0]
    lat = df["latitude"].to_numpy(np.float64)
    lon = df["longitude"].to_numpy(np.float64)
    iy = np.floor((lat + 90.0) * 100.0).astype(np.int32)
    ix = np.floor((lon + 180.0) * 100.0).astype(np.int32)
    day = ((pd.to_datetime(df["acq_date"]).to_numpy().astype("datetime64[D]") - EPOCH)
           .astype(np.int32))
    conf = df["confidence"].astype(str).str[0].str.lower().to_numpy()
    night = (df["daynight"].astype(str).to_numpy() == "N").astype(np.int16)
    frp = pd.to_numeric(df["frp"], errors="coerce").fillna(0).to_numpy(np.float32)
    a = pd.DataFrame({"day": day, "iy": iy, "ix": ix, "frp": frp,
                      "hi": (conf == "h").astype(np.int16), "lo": (conf == "l").astype(np.int16),
                      "night": night, "n": np.ones(len(day), np.int16)})
    g = a.groupby(["day", "iy", "ix"], sort=False).agg(
        n=("n", "sum"), frp=("frp", "sum"), frpmax=("frp", "max"), hi=("hi", "sum"),
        lo=("lo", "sum"), night=("night", "sum")).reset_index()
    rec = {k: g[k].to_numpy() for k in g.columns}
    return _sort_recs(rec)


def _sort_recs(rec):
    tile = (rec["iy"] // TILE).astype(np.int64) * 100000 + rec["ix"] // TILE
    o = np.lexsort((rec["day"], tile))
    rec = {k: v[o] for k, v in rec.items()}
    rec["tile"] = tile[o]
    return rec


def load_country_year(country: str, year: int) -> dict | None:
    CACHE.mkdir(parents=True, exist_ok=True)
    cp = CACHE / f"{country}_{year}.npz"
    if cp.exists():
        z = np.load(cp)
        return {k: z[k] for k in z.files}
    paths = _csv_paths(country, year)
    if not paths:
        return None
    cols = ["latitude", "longitude", "acq_date", "confidence", "frp", "daynight", "type"]
    dfs = [pd.read_csv(p, usecols=lambda c: c in cols, low_memory=False) for p in paths]
    df = pd.concat(dfs, ignore_index=True)
    del dfs
    rec = aggregate_detections(df)
    del df
    np.savez(cp, **rec)
    return rec


def concat_recs(recs):
    recs = [r for r in recs if r is not None]
    out = {k: np.concatenate([r[k] for r in recs]) for k in recs[0] if k != "tile"}
    return _sort_recs(out)


class RecIndex:
    """Tile -> slice index for fast window extraction."""

    def __init__(self, rec):
        self.rec = rec
        t = rec["tile"]
        u, s = np.unique(t, return_index=True)
        e = np.append(s[1:], len(t))
        self.idx = {int(a): (int(b), int(c)) for a, b, c in zip(u, s, e)}

    def window(self, d0, d1, y0, y1, x0, x1):
        parts = []
        for ty in range(y0 // TILE, (y1 - 1) // TILE + 1):
            for tx in range(x0 // TILE, (x1 - 1) // TILE + 1):
                sl = self.idx.get(ty * 100000 + tx)
                if sl is None:
                    continue
                s, e = sl
                dd = self.rec["day"][s:e]
                a, b = np.searchsorted(dd, d0, "left"), np.searchsorted(dd, d1, "right")
                if b > a:
                    parts.append(np.arange(s + a, s + b))
        if not parts:
            return None
        ii = np.concatenate(parts)
        r = {k: self.rec[k][ii] for k in ("day", "iy", "ix", "n", "frp", "frpmax", "hi", "lo", "night")}
        m = (r["iy"] >= y0) & (r["iy"] < y1) & (r["ix"] >= x0) & (r["ix"] < x1)
        return {k: v[m] for k, v in r.items()}


def within_radius(query, truth, y0, x0, radius_km):
    """Match cell centres by great-circle distance, including across tile edges."""
    from scipy.spatial import cKDTree
    if not len(query) or not len(truth):
        return np.zeros(len(query), dtype=bool)

    def sphere(points):
        points = np.asarray(points, dtype=np.float64)
        lat = np.deg2rad((points[:, 0] + y0 + 0.5) / 100 - 90)
        lon = np.deg2rad((points[:, 1] + x0 + 0.5) / 100 - 180)
        return np.column_stack((np.cos(lat) * np.cos(lon),
                                np.cos(lat) * np.sin(lon), np.sin(lat)))

    chord = 2 * np.sin(radius_km / (2 * 6371.0088))
    distance, _ = cKDTree(sphere(truth)).query(sphere(query), k=1)
    return distance <= chord + 1e-12


# ----------------------------------------------------------------------------- features
def _box(a, r):
    """Sum over a (2r+1)^2 box (zero padded)."""
    k = 2 * r + 1
    return ndimage.uniform_filter(a.astype(np.float32), size=k, mode="constant") * (k * k)


def _bmax(a, r):
    return ndimage.maximum_filter(a, size=2 * r + 1, mode="constant")


def featurize(index: RecIndex, t0: int, y0: int, x0: int, ny: int, nx: int,
              dual_sat: float, with_labels=True, radius_km=None, weather=None, enhanced=False):
    """Features for candidates in core [y0,y0+ny) x [x0,x0+nx) on day t0.

    Returns dict(X, cy, cx, labels{h}, newcells{h}) or None if no fire today."""
    Y0, X0 = y0 - PAD, x0 - PAD
    H, W = ny + 2 * PAD, nx + 2 * PAD
    r = index.window(t0 - 365, t0 + (HMAX if with_labels else 0), Y0, Y0 + H, X0, X0 + W)
    if r is None:
        return None
    ly, lx, dd = r["iy"] - Y0, r["ix"] - X0, r["day"] - t0
    # dense burning stack for t0-HIST .. t0+HMAX
    B = np.zeros((HIST + 1 + HMAX, H, W), np.uint8)
    m = dd >= -HIST
    B[dd[m] + HIST, ly[m], lx[m]] = 1
    B0, B1, B2 = B[HIST], B[HIST - 1], B[HIST - 2]
    if B0[PAD:PAD + ny, PAD:PAD + nx].sum() == 0:
        return None
    m0 = dd == 0
    F0 = np.zeros((H, W), np.float32); F0[ly[m0], lx[m0]] = r["frp"][m0]
    FM = np.zeros((H, W), np.float32); FM[ly[m0], lx[m0]] = r["frpmax"][m0]
    N0 = np.zeros((H, W), np.float32); N0[ly[m0], lx[m0]] = r["n"][m0]
    HI = np.zeros((H, W), np.float32); HI[ly[m0], lx[m0]] = r["hi"][m0]
    LO = np.zeros((H, W), np.float32); LO[ly[m0], lx[m0]] = r["lo"][m0]
    NI = np.zeros((H, W), np.float32); NI[ly[m0], lx[m0]] = r["night"][m0]
    m1 = dd == -1
    F1tot = float(r["frp"][m1].sum())
    # days since last burn within 365 (strictly before t0)
    L = np.full((H, W), 366, np.int32)
    mp = dd < 0
    if mp.any():
        np.minimum.at(L, (ly[mp], lx[mp]), -dd[mp])

    dist = ndimage.distance_transform_edt(B0 == 0).astype(np.float32)
    cheb3 = _bmax(B0, 3).astype(bool)
    cand = cheb3 & (B0 == 0)
    core = np.zeros((H, W), bool); core[PAD:PAD + ny, PAD:PAD + nx] = True
    cand &= core
    cy, cx = np.nonzero(cand)
    if len(cy) == 0:
        return None

    f = {}
    f["dist"] = dist[cy, cx]
    f["dual_sat"] = np.full(len(cy), dual_sat, np.float32)
    lat = (cy + Y0 + 0.5) / 100.0 - 90.0
    f["lat"] = lat.astype(np.float32)
    doy = (np.datetime64(int(t0), "D") - np.datetime64(int(t0), "D").astype("datetime64[Y]")).astype(int)
    f["doy_sin"] = np.full(len(cy), np.sin(2 * np.pi * doy / 365.25), np.float32)
    f["doy_cos"] = np.full(len(cy), np.cos(2 * np.pi * doy / 365.25), np.float32)
    for rr in RINGS:
        f[f"cnt_r{rr}"] = _box(B0, rr)[cy, cx]
        f[f"frp_r{rr}"] = _box(F0, rr)[cy, cx]
    f["cnt1_r1"] = _box(B1, 1)[cy, cx]
    f["cnt1_r3"] = _box(B1, 3)[cy, cx]
    f["cnt2_r3"] = _box(B2, 3)[cy, cx]
    new0 = (B0 == 1) & (B1 == 0) & (B2 == 0)
    f["new0_r3"] = _box(new0, 3)[cy, cx]
    f["new0_r5"] = _box(new0, 5)[cy, cx]
    b7 = B[HIST - 7:HIST].sum(0)
    b30 = B[0:HIST].max(0)
    f["burn7"] = (b7 > 0)[cy, cx].astype(np.float32)
    f["burn7_days"] = b7[cy, cx].astype(np.float32)
    f["burn7_r1"] = _box(b7 > 0, 1)[cy, cx]
    f["burn30"] = b30[cy, cx].astype(np.float32)
    f["burn30_r1"] = _box(b30, 1)[cy, cx]
    f["since_last365"] = L[cy, cx].astype(np.float32)

    gy, gx = np.mgrid[0:H, 0:W].astype(np.float32)
    # global growth direction: centroid(t0) - centroid(t0-1)
    s0, s1 = B0.sum(), B1.sum()
    c0 = np.array([(gy * B0).sum() / s0, (gx * B0).sum() / s0])
    if s1 > 0:
        v = c0 - np.array([(gy * B1).sum() / s1, (gx * B1).sum() / s1])
    else:
        v = np.zeros(2)
    u = np.stack([cy - c0[0], cx - c0[1]], 1)
    nv = np.linalg.norm(v); nu = np.linalg.norm(u, axis=1) + 1e-6
    f["align_glob"] = ((u @ v) / (nu * nv) if nv > 0 else np.zeros(len(cy))).astype(np.float32)
    f["growth_glob"] = np.full(len(cy), nv, np.float32)
    # local growth (r=5 box moments)
    R = 5
    n0 = _box(B0, R); n1 = _box(B1, R)
    my0 = _box(B0 * gy, R) / np.maximum(n0, 1); mx0 = _box(B0 * gx, R) / np.maximum(n0, 1)
    my1 = _box(B1 * gy, R) / np.maximum(n1, 1); mx1 = _box(B1 * gx, R) / np.maximum(n1, 1)
    ok = (n0 > 0) & (n1 > 0)
    vy = np.where(ok, my0 - my1, 0)[cy, cx]; vx = np.where(ok, mx0 - mx1, 0)[cy, cx]
    uy = cy - my0[cy, cx]; ux = cx - mx0[cy, cx]
    nvl = np.hypot(vy, vx); nul = np.hypot(uy, ux) + 1e-6
    f["align_loc"] = np.where(nvl > 0, (uy * vy + ux * vx) / (nul * nvl + 1e-6), 0).astype(np.float32)
    f["growth_loc"] = nvl.astype(np.float32)

    size2 = float(B2.sum())
    f["size0"] = np.full(len(cy), s0, np.float32)
    f["size1"] = np.full(len(cy), s1, np.float32)
    f["size2"] = np.full(len(cy), size2, np.float32)
    f["size_ratio"] = np.full(len(cy), np.log1p(s0) - np.log1p(s1), np.float32)
    f0t = float(F0.sum())
    f["frp0"] = np.full(len(cy), f0t, np.float32)
    f["frp1"] = np.full(len(cy), F1tot, np.float32)
    f["frp_ratio"] = np.full(len(cy), np.log1p(f0t) - np.log1p(F1tot), np.float32)
    n3 = np.maximum(_box(N0, 3), 1)
    f["hi_frac_r3"] = (_box(HI, 3) / n3)[cy, cx]
    f["lo_frac_r3"] = (_box(LO, 3) / n3)[cy, cx]
    f["night_frac_r3"] = (_box(NI, 3) / n3)[cy, cx]
    f["win_night"] = np.full(len(cy), float(NI.sum() > 0), np.float32)
    f["persist7"] = np.full(len(cy), float((B[HIST - 7:HIST + 1].reshape(8, -1).max(1) > 0).sum()), np.float32)
    f["frp_max_r1"] = _bmax(FM, 1)[cy, cx]

    X = np.stack([np.asarray(f[k], np.float32) for k in FEATURES], 1)
    if enhanced:
        # Distances in kilometres, correcting east-west scale for latitude.
        spacing = (1.11195, 1.11195 * max(.01, float(np.cos(np.deg2rad(lat.mean())))))
        distance, nearest = ndimage.distance_transform_edt(B0 == 0, sampling=spacing,
                                                           return_indices=True)
        def distance_to(mask):
            if not mask.any():
                return np.full(len(cy), np.nan, np.float32)
            return ndimage.distance_transform_edt(~mask.astype(bool), sampling=spacing)[cy, cx]
        previous = distance_to(B1)
        front = (B0 > 0) & (b7 == 0)
        fy, fx = nearest[0, cy, cx], nearest[1, cy, cx]
        F1 = np.zeros((H, W), np.float32)
        F1[ly[m1], lx[m1]] = r["frp"][m1]
        past_count = np.zeros((H, W), np.float32)
        np.add.at(past_count, (ly[mp], lx[mp]), 1)
        border = (B0 > 0) & (_box(B0, 1) < 8.5)
        extras = [
            (cx + X0 + .5) / 100 - 180, distance[cy, cx], previous, distance_to(front),
            F0[fy, fx], HI[fy, fx] / np.maximum(N0[fy, fx], 1),
            NI[fy, fx] / np.maximum(N0[fy, fx], 1), _box(b7 > 0, 3)[cy, cx],
            _box((B0 > 0) & (B1 > 0), 3)[cy, cx],
            (np.log1p(_box(B0, 3).clip(0)) - np.log1p(_box(B1, 3).clip(0)))[cy, cx],
            (np.log1p(_box(F0, 3).clip(0)) - np.log1p(_box(F1, 3).clip(0)))[cy, cx],
            past_count[cy, cx], _box(past_count, 3)[cy, cx],
            _box(border, 3)[cy, cx], _box(front, 3)[cy, cx], previous - distance[cy, cx]]
        X = np.column_stack((X, *extras)).astype(np.float32)
    if weather is not None:
        _, nearest = ndimage.distance_transform_edt(B0 == 0, return_indices=True)
        north = (cy - nearest[0, cy, cx]) * 1.11195
        east = (cx - nearest[1, cy, cx]) * 1.11195 * np.cos(np.deg2rad(lat))
        lon = (cx + X0 + .5) / 100 - 180
        X = np.column_stack((X, weather.features(t0, lat, lon, north, east)))
    out = {"X": X, "cy": (cy - PAD).astype(np.int16), "cx": (cx - PAD).astype(np.int16),
           "y0": y0, "x0": x0, "t0": t0}
    if with_labels:
        labels, newcells = {}, {}
        for h in (1, 3):
            N = (B[HIST + 1:HIST + 1 + h].max(0) == 1) & (B0 == 0)
            if radius_km is None:  # compatibility with previously trained models
                labels[h] = _bmax(N.astype(np.uint8), 1)[cy, cx].astype(np.uint8)
            else:
                labels[h] = within_radius(np.column_stack((cy, cx)),
                                          np.column_stack(np.nonzero(N)), Y0, X0,
                                          radius_km).astype(np.uint8)
            ny_, nx_ = np.nonzero(N & core)
            newcells[h] = np.stack([ny_ - PAD, nx_ - PAD], 1).astype(np.int16)
        out["labels"] = labels
        out["newcells"] = newcells
    return out


# ----------------------------------------------------------------------------- cases
def build_cases(country: str, year: int, n_cases: int, max_cand: int | None, seed: int = 0,
                keep_maps: bool = False, radius_km=None, weather=None, enhanced=False):
    """Sample fire cases for (country, year); returns dict of arrays (+ per-case maps)."""
    cur = load_country_year(country, year)
    if cur is None or len(cur["day"]) == 0:
        return None
    prev = load_country_year(country, year - 1) if year - 1 >= 2012 else None
    rec = concat_recs([prev, cur]) if prev is not None else cur
    index = RecIndex(rec)
    # candidate fire-days: (day, tile) with >= 5 burning cells, t0 in `year`, t0+3 in year
    end = (np.datetime64(f"{year}-12-31") - EPOCH).astype(int) - HMAX
    start = (np.datetime64(f"{year}-01-01") - EPOCH).astype(int)
    m = (cur["day"] >= start) & (cur["day"] <= end)
    key = cur["day"][m].astype(np.int64) * 10**10 + cur["tile"][m]
    u, c = np.unique(key, return_counts=True)
    u = u[c >= 5]
    if len(u) == 0:
        return None
    import zlib
    rng = np.random.default_rng(zlib.crc32(f"{country}|{year}|{seed}".encode()))
    pick = rng.choice(len(u), size=min(n_cases, len(u)), replace=False)
    dual = 1.0 if year >= 2018 else 0.0
    Xs, ys1, ys3, cid, maps, weights = [], [], [], [], [], []
    for k, j in enumerate(pick):
        t0 = int(u[j] // 10**10); tile = int(u[j] % 10**10)
        ty, tx = tile // 100000, tile % 100000
        o = featurize(index, t0, ty * TILE, tx * TILE, TILE, TILE, dual, radius_km=radius_km, weather=weather, enhanced=enhanced)
        if o is None:
            continue
        n = len(o["X"])
        sel = np.arange(n)
        if max_cand is not None and n > max_cand:
            sel = rng.choice(n, max_cand, replace=False)
        Xs.append(o["X"][sel]); ys1.append(o["labels"][1][sel]); ys3.append(o["labels"][3][sel])
        weights.append(np.full(len(sel), n / len(sel), np.float32))
        cid.append(np.full(len(sel), k, np.int32))
        if keep_maps:
            maps.append({"cy": o["cy"], "cx": o["cx"], "new1": o["newcells"][1],
                         "new3": o["newcells"][3], "y1": o["labels"][1],
                         "y3": o["labels"][3], "case": k, "y0": o["y0"],
                         "x0": o["x0"], "radius_km": radius_km})
    if not Xs:
        return None
    return {"X": np.concatenate(Xs), "y1": np.concatenate(ys1), "y3": np.concatenate(ys3),
            "weights": np.concatenate(weights), "case": np.concatenate(cid), "maps": maps, "country": country, "year": year}


# ----------------------------------------------------------------------------- eval
def eval_alerts(maps, alert_masks):
    """maps: list of per-case dicts; alert_masks: list of bool arrays (per candidate)."""
    out = {}
    for h in (1, 3):
        n_alert = hits = n_new = covered = 0
        for mp, am in zip(maps, alert_masks):
            new = mp[f"new{h}"]
            n_new += len(new)
            if am.sum() == 0:
                continue
            ay, ax = mp["cy"][am].astype(np.int32), mp["cx"][am].astype(np.int32)
            n_alert += len(ay)
            A = np.zeros((TILE + 2, TILE + 2), np.uint8)
            A[ay + 1, ax + 1] = 1
            Ad = ndimage.maximum_filter(A, size=3, mode="constant")
            if len(new):
                if mp.get("radius_km") is None:
                    covered += int(Ad[new[:, 0] + 1, new[:, 1] + 1].sum())
                else:
                    covered += int(within_radius(new, np.column_stack((ay, ax)),
                                                 mp["y0"], mp["x0"], mp["radius_km"]).sum())
            hits += int(mp[f"y{h}"][am].sum())
        out[h] = {"alerts": n_alert, "hits": hits,
                  "precision": hits / n_alert if n_alert else float("nan"),
                  "new_cells": n_new, "covered": covered,
                  "recall": covered / n_new if n_new else float("nan"),
                  "fire_days": len(maps), "alerts_per_fire_day": n_alert / max(len(maps), 1)}
    return out


def feature_window(index, t0, y0, x0, ny, nx, dual_sat, weather=None):
    """Enhanced inference uses exactly the same fixed core tiles as training."""
    parts = []
    for ty in range(y0 // TILE, (y0 + ny - 1) // TILE + 1):
        for tx in range(x0 // TILE, (x0 + nx - 1) // TILE + 1):
            o = featurize(index, t0, ty * TILE, tx * TILE, TILE, TILE,
                          dual_sat, with_labels=False, weather=weather, enhanced=True)
            if o is None:
                continue
            gy = o["cy"].astype(np.int32) + o["y0"]
            gx = o["cx"].astype(np.int32) + o["x0"]
            keep = (gy >= y0) & (gy < y0 + ny) & (gx >= x0) & (gx < x0 + nx)
            if keep.any():
                parts.append((o["X"][keep], gy[keep] - y0, gx[keep] - x0))
    if not parts:
        return None
    return {"X": np.concatenate([p[0] for p in parts]),
            "cy": np.concatenate([p[1] for p in parts]),
            "cx": np.concatenate([p[2] for p in parts]), "y0": y0, "x0": x0}


# ----------------------------------------------------------------------------- inference
_MODEL_CACHE = {}


def _load_model(h, model_dir=None):
    if h not in (1, 3):
        raise ValueError("supported horizons are 1 and 3 days")
    model_dir = Path(model_dir or MODELS).resolve()
    key = (str(model_dir), h)
    if key not in _MODEL_CACHE:
        import xgboost as xgb
        meta = json.loads((model_dir / "alerts1km_meta.json").read_text())
        b = xgb.Booster()
        b.load_model(str(model_dir / ("alerts1km.json" if h == 1 else "alerts1km_h3.json")))
        _MODEL_CACHE[key] = (b, meta)
    return _MODEL_CACHE[key]


def predict_alerts(lat: float, lon: float, date, h: int = 1, threshold: float | None = None,
                   detections: pd.DataFrame | None = None, half: int = 50,
                   countries=COUNTRIES, model_dir=None, weather=None):
    """Alerts around (lat, lon) for day `date` (detections up to and including `date`).

    detections: optional raw VIIRS DataFrame (FIRMS columns); otherwise read from
    data_country (cached). Returns list of (lat, lon, prob) for cells with prob >= threshold
    in a (2*half+1)^2-cell window centred on the point. Cell coordinates are cell centres.
    """
    import xgboost as xgb
    if half < 1 or half > 1000:
        raise ValueError("half must be between 1 and 1000 cells")
    booster, meta = _load_model(h, model_dir)
    thr = float(threshold if threshold is not None else meta["models"][str(h)]["threshold"])
    d = pd.Timestamp(date).date() if not isinstance(date, _date) else date
    t0 = int((np.datetime64(d.isoformat()) - EPOCH).astype(int))
    if detections is not None:
        rec = aggregate_detections(detections)
    else:
        rs = []
        extent = max(1., (half + PAD + TILE + 1) / 100)
        for c in countries:
            for y in (d.year - 1, d.year):
                rr = load_country_year(c, y)
                if rr is None:
                    continue
                ok = ((np.abs((rr["iy"] + 0.5) / 100 - 90 - lat) < extent)
                      & (np.abs((rr["ix"] + 0.5) / 100 - 180 - lon) < extent))
                if ok.any():
                    rs.append({k: v[ok] for k, v in rr.items() if k != "tile"})
        if not rs:
            return []
        rec = _sort_recs({k: np.concatenate([r[k] for r in rs]) for k in rs[0]})
    rec = {k: v for k, v in rec.items()}
    m = rec["day"] <= t0
    rec = {k: v[m] for k, v in rec.items()}
    if len(rec["day"]) == 0:
        return []
    index = RecIndex(rec)
    cy0 = int(np.floor((lat + 90) * 100)); cx0 = int(np.floor((lon + 180) * 100))
    if any(f.startswith("wx_") for f in meta["features"]):
        if weather is None:
            from .weather import AlertWeather
            weather = AlertWeather(weather_csv=meta.get("weather_csv"))
    else:
        weather = None
    if meta.get("feature_version", 1) >= 2:
        o = feature_window(index, t0, cy0 - half, cx0 - half, 2 * half + 1, 2 * half + 1,
                           1.0 if d.year >= 2018 else 0.0, weather=weather)
    else:
        o = featurize(index, t0, cy0 - half, cx0 - half, 2 * half + 1, 2 * half + 1,
                      1.0 if d.year >= 2018 else 0.0, with_labels=False, weather=weather)
    if o is None:
        return []
    p = booster.predict(xgb.DMatrix(o["X"], feature_names=meta["features"]))
    keep = p >= thr
    gy = o["cy"][keep].astype(np.int32) + o["y0"]; gx = o["cx"][keep].astype(np.int32) + o["x0"]
    return [(round(float((a + 0.5) / 100 - 90), 4), round(float((b + 0.5) / 100 - 180), 4), float(q))
            for a, b, q in zip(gy, gx, p[keep])]
