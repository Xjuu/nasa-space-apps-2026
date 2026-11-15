"""Step 2: build training tables from cell observations.

size task   - MODIS overpass in a cell, matched with a VIIRS overpass of the same cell within
              +-30 min. Target = what VIIRS (375 m) sees: fire footprint area and FRP.
              Lets the model turn coarse MODIS (1 km) data into VIIRS-equivalent fire size,
              which is what harmonizing the 25-year record needs.
spread task - for every cell next to a burning cell on day t (and not burning itself),
              predict whether it burns on day t+1. Built from VIIRS (2012+).
"""
import time

import numpy as np
import pandas as pd

from . import fastcache
from .common import (CACHE, MODIS_SATS, STRIDE_DAY, VIIRS_SATS, KeySet, cell_center, cell_key,
                     day_of_year_features, day_to_year, decode_key)

VIIRS_DAILY = CACHE / "viirs_daily.parquet"  # viirs_daily(load_cells()), keyed on the cells fingerprint
VIIRS_DAILY_VERSION = 1  # bump when _daily_active / viirs_daily logic changes
MATCH_MINUTES = 30
NEIGHBORS = [(dx, dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1) if (dx, dy) != (0, 0)]
RING2 = [(dx, dy) for dx in range(-2, 3) for dy in range(-2, 3) if max(abs(dx), abs(dy)) == 2]


def _daily_active(cells):
    """Any-satellite activity per (day, cell)."""
    g = cells.groupby(["day", "gx", "gy"], observed=True).agg(
        n=("n", "sum"), frp=("frp_sum", "sum"), area=("area_sum", "sum")).reset_index()
    return g


def _neighbor_count(ks, day, gx, gy, offsets):
    c = np.zeros(len(day), np.int16)
    for dx, dy in offsets:
        c += ks.has(cell_key(day, gx + dx, gy + dy))
    return c


def build_size(cells):
    mod = cells[cells["sat"].isin(MODIS_SATS)]
    vi = cells[cells["sat"].isin(VIIRS_SATS)]
    keys = ["day", "gx", "gy", "dn"]
    m = mod.reset_index(drop=True)
    m["mid"] = np.arange(len(m))
    j = m.merge(vi[keys + ["minute", "n", "frp_sum", "area_sum", "sat"]], on=keys, suffixes=("", "_v"))
    j["dt"] = (j["minute"] - j["minute_v"]).abs()
    j = j[j["dt"] <= MATCH_MINUTES].sort_values("dt").drop_duplicates("mid")

    # context from MODIS only, so the model can run on MODIS-era (2000-2011) data too
    mday = _daily_active(mod)
    ks = KeySet(cell_key(mday["day"], mday["gx"], mday["gy"]))
    d, x, y = j["day"].values, j["gx"].values, j["gy"].values
    lat, _ = cell_center(x, y)
    s, c = day_of_year_features(d)
    X = pd.DataFrame({
        "n": j["n"].values, "frp_sum": j["frp_sum"].values, "frp_max": j["frp_max"].values,
        "area_sum": j["area_sum"].values, "scan_mean": j["scan_mean"].values,
        "track_mean": j["track_mean"].values, "bright_max": j["bright_max"].values,
        "conf_mean": j["conf_mean"].values, "dn": j["dn"].values,
        "aqua": (j["sat"].astype(str) == "A").values.astype(np.int8),
        "lat": lat, "doy_sin": s, "doy_cos": c,
        "nbr_active": _neighbor_count(ks, d, x, y, NEIGHBORS),
        "ring2_active": _neighbor_count(ks, d, x, y, RING2),
        "prev_days_active": sum(ks.has(cell_key(d - k, x, y)) for k in (1, 2, 3)).astype(np.int8),
    })
    y_df = pd.DataFrame({"area": j["area_sum_v"].values, "frp": j["frp_sum_v"].values})
    year = day_to_year(d)
    print(f"[size]   {len(mod):,} MODIS overpasses, {len(vi):,} VIIRS overpasses -> "
          f"{len(X):,} matched pairs (|dt| <= {MATCH_MINUTES} min)")
    return X, y_df, year


def build_spread(cells, max_rows=3_000_000, seed=0):
    vi = cells[cells["sat"].isin(VIIRS_SATS)]
    act = _daily_active(vi)
    akeys = cell_key(act["day"], act["gx"], act["gy"])
    ks = KeySet(akeys, frp=act["frp"].values)
    act["prev"] = ks.has(akeys - STRIDE_DAY).astype(np.float32)
    print(f"[spread] {len(act):,} burning cell-days (VIIRS) used as fire fronts")

    years = day_to_year(act["day"].values)
    total_est = len(act) * 4
    frac = min(1.0, max_rows / max(total_est, 1))
    rng = np.random.default_rng(seed)
    parts = []
    for yr in np.unique(years):
        a = act[years == yr]
        g = spread_candidates(a, ks)
        if frac < 1:
            g = g.sample(frac=frac, random_state=int(rng.integers(1 << 31)))
        parts.append(g)
        print(f"[spread] year {yr}: {len(a):,} fronts -> {len(g):,} candidate cells "
              f"(total so far {sum(map(len, parts)):,})", flush=True)
    g = pd.concat(parts, ignore_index=True)

    X, d, x, y = spread_features(g, ks)
    label = ks.has(g["tkey"].values + STRIDE_DAY).astype(np.int8)
    lat, lon = X["lat"].values, X["lon"].values
    meta = pd.DataFrame({"day": d, "lat": lat, "lon": lon})
    print(f"[spread] {len(X):,} candidate cells, {label.mean():.1%} actually caught fire next day")
    return X, label, day_to_year(d), meta


def spread_candidates(fronts, ks):
    """Cells adjacent to burning `fronts` (day, gx, gy, frp, n, area, prev) that are not burning
    themselves on that day, with aggregated neighbour stats. `ks` = all known burning cell keys."""
    src = []
    for dx, dy in NEIGHBORS:
        src.append(pd.DataFrame({
            "tkey": cell_key(fronts["day"].values, fronts["gx"].values + dx, fronts["gy"].values + dy),
            "frp": fronts["frp"].values, "n": fronts["n"].values, "area": fronts["area"].values,
            "prev": fronts["prev"].values, "dx": np.int8(dx), "dy": np.int8(dy),
        }))
    s = pd.concat(src, ignore_index=True)
    s = s[~ks.has(s["tkey"].values)]  # target must not burn yet
    s["wx"] = s["dx"] * s["frp"]
    s["wy"] = s["dy"] * s["frp"]
    return s.groupby("tkey").agg(
        nbr_n=("frp", "size"), nbr_frp_sum=("frp", "sum"), nbr_frp_max=("frp", "max"),
        nbr_det=("n", "sum"), nbr_area=("area", "sum"), nbr_prev_frac=("prev", "mean"),
        wx=("wx", "sum"), wy=("wy", "sum")).reset_index()


def spread_features(g, ks):
    """Model features for candidate cells `g` (output of spread_candidates)."""
    tk = g["tkey"].values
    d, x, y = decode_key(tk)
    lat, lon = cell_center(x, y)
    s_, c_ = day_of_year_features(d)
    norm = g["nbr_frp_sum"].values + 1e-6
    X = pd.DataFrame({
        "nbr_n": g["nbr_n"].values, "nbr_frp_sum": g["nbr_frp_sum"].values,
        "nbr_frp_max": g["nbr_frp_max"].values, "nbr_det": g["nbr_det"].values,
        "nbr_area": g["nbr_area"].values, "nbr_prev_frac": g["nbr_prev_frac"].values,
        "push_x": g["wx"].values / norm, "push_y": g["wy"].values / norm,
        "ring2_active": _neighbor_count(ks, d, x, y, RING2),
        "burnt_last7": sum(ks.has(cell_key(d - k, x, y)) for k in range(1, 8)).astype(np.int8),
        "lat": lat, "lon": lon, "doy_sin": s_, "doy_cos": c_,
    })
    return X, d, x, y


def viirs_daily(cells):
    """Burning cell-days from VIIRS with the per-cell stats used as fire fronts.

    When `cells` is the unmodified frame returned by load_cells(), the result is served from (or
    saved to) cache/viirs_daily.parquet; any other frame is computed directly."""
    fp = fastcache.fingerprint_of(cells)
    if fp is not None:
        meta = fastcache.read_meta(VIIRS_DAILY)
        if meta is not None and meta.get("fingerprint") == fp and meta.get("version") == VIIRS_DAILY_VERSION:
            return fastcache.read_frame(VIIRS_DAILY)
    act = _daily_active(cells[cells["sat"].isin(VIIRS_SATS)])
    if fp is not None:
        fastcache.write_frame(act, VIIRS_DAILY, {"fingerprint": fp, "version": VIIRS_DAILY_VERSION,
                                                 "rows": len(act)})
    return act


def load_viirs_daily():
    """viirs_daily(load_cells()) without loading the cell table when the cache is fresh.

    Returns the VIIRS burning cell-days frame: columns day, gx, gy, n (int32), frp, area
    (float32), one row per (day, cell), sorted by (day, gx, gy), RangeIndex (~20M rows, ~0.5 GB).
    Reads only cache/viirs_daily.parquet (~0.3 s) if it matches the current cache/cells/*.parquet
    files; otherwise falls back to load_cells() + viirs_daily(), which also refreshes the cache.
    Use it in servers/APIs that need fire fronts but not raw overpasses:

        from equifire.datasets import load_viirs_daily
        act = load_viirs_daily()
    """
    from .collect import cells_fingerprint
    t0 = time.time()
    fp = cells_fingerprint()
    meta = fastcache.read_meta(VIIRS_DAILY)
    if meta is not None and meta.get("fingerprint") == fp and meta.get("version") == VIIRS_DAILY_VERSION:
        act = fastcache.read_frame(VIIRS_DAILY)
        print(f"[collect] loaded {VIIRS_DAILY.name} -> {len(act):,} VIIRS burning cell-days "
              f"({time.time() - t0:.1f}s)")
        return act
    from .collect import load_cells
    return viirs_daily(load_cells())
