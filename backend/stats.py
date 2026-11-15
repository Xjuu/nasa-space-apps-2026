"""Area-of-interest fire statistics for the burning calendar and charts (GET/POST /stats).

Aggregates the in-memory daily 0.05 deg table (State.daily) over the cells whose centre lies in the
area (bbox, optionally clipped by a GeoJSON polygon), per ISO year x ISO week and per sensor:

- viirs_cell_days / viirs_detections   (VIIRS, from 2012)
- modis_cell_days / modis_detections   (MODIS, from 2008: the longest consistent record)
- any_cell_days                        (cell-days seen burning by either sensor)
- frp_equiv_mw                         (sum over cell-days of the mean VIIRS-equivalent FRP per
                                        overpass, MODIS harmonized by the size model)

The baseline for a week of the selected year uses the same week of every other year whose week lies
fully inside that sensor's data range (leave-one-year-out). z = (x - mean) / max(std, sqrt(max(mean, 1)))
as in /anomaly. A ~10x10 deg box answers in well under a second on the full table.
"""
from __future__ import annotations

import math
import threading

import numpy as np
import pandas as pd

from equifire.common import EPOCH, GRID

METRICS = {
    "viirs_cell_days": ("viirs", "VIIRS burning 0.05-deg cell-days"),
    "viirs_detections": ("viirs", "VIIRS fire detections (pixels)"),
    "modis_cell_days": ("modis", "MODIS burning 0.05-deg cell-days"),
    "modis_detections": ("modis", "MODIS fire detections (pixels)"),
    "any_cell_days": ("any", "burning 0.05-deg cell-days, MODIS or VIIRS"),
    "frp_equiv_mw": ("any", "fire radiative power, VIIRS-equivalent (MODIS harmonized), MW summed over cell-days"),
}
ERA_MATCHED = ("any_cell_days", "frp_equiv_mw")
NW = 53
MAX_SPAN_DEG = 40.0
MAX_VERTICES = 2000

_lock = threading.Lock()
_cache: dict = {"state": None}


class StatsError(ValueError):
    pass


# ------------------------------------------------------------------ per-State lookup tables
def _tables(st) -> dict:
    """ISO year/week per day and per-sensor week coverage, built once per State."""
    with _lock:
        if _cache.get("state") is st:
            return _cache
        days = np.arange(st.day_min, st.day_max + 1)
        iso = pd.DatetimeIndex(EPOCH + days.astype("timedelta64[D]")).isocalendar()
        iy, iw = iso["year"].values.astype(np.int32), iso["week"].values.astype(np.int32)
        cal_years = (EPOCH + days.astype("timedelta64[D]")).astype("datetime64[Y]").astype(int) + 1970
        years = list(range(int(cal_years.min()), int(cal_years.max()) + 1))
        y0 = years[0]
        yi = iy - y0
        keep = (yi >= 0) & (yi < len(years))  # ISO week 1 of next year / week 52 of the year before: drop
        m = st.daily["n_m"].values > 0
        dd = st.daily["day"].values
        modis_lo, modis_hi = (int(dd[m].min()), int(dd[m].max())) if m.any() else (0, -1)
        ranges = {"any": (st.day_min, st.day_max), "modis": (modis_lo, modis_hi),
                  "viirs": (st.viirs_day_min, st.viirs_day_max) if st.viirs_rows else (0, -1)}
        cov = {}
        for s, (lo, hi) in ranges.items():
            ok = keep & (days >= lo) & (days <= hi)
            c = np.bincount(yi[ok] * (NW + 1) + iw[ok], minlength=len(years) * (NW + 1))
            cov[s] = c.reshape(len(years), NW + 1)[:, 1:]
        _cache.clear()
        _cache.update(state=st, iy=yi, iw=iw, keep=keep, years=years, cov=cov, ranges=ranges)
        return _cache


# ------------------------------------------------------------------ geometry
def _rings(geom: dict) -> list[list[np.ndarray]]:
    """GeoJSON (Feature, FeatureCollection, Polygon, MultiPolygon) -> list of polygons as [outer, *holes]."""
    if not isinstance(geom, dict):
        raise StatsError("geometry must be a GeoJSON object")
    t = geom.get("type")
    if t == "FeatureCollection":
        return [p for f in geom.get("features") or [] for p in _rings(f)]
    if t == "Feature":
        return _rings(geom.get("geometry") or {})
    coords = geom.get("coordinates")
    if t == "Polygon":
        polys = [coords]
    elif t == "MultiPolygon":
        polys = coords
    else:
        raise StatsError(f"unsupported geometry type {t!r}: use Polygon or MultiPolygon")
    out, n = [], 0
    try:
        for poly in polys:
            rings = [np.asarray(r, float)[:, :2] for r in poly]
            if not rings or any(len(r) < 3 for r in rings):
                raise StatsError("each polygon ring needs at least 3 points")
            n += sum(len(r) for r in rings)
            out.append(rings)
    except (TypeError, ValueError, IndexError) as e:
        if isinstance(e, StatsError):
            raise
        raise StatsError("bad polygon coordinates: expected [[[lon, lat], ...]]") from None
    if not out:
        raise StatsError("empty geometry")
    if n > MAX_VERTICES:
        raise StatsError(f"polygon has {n} vertices; at most {MAX_VERTICES} allowed")
    if not all(np.isfinite(r).all() for p in out for r in p):
        raise StatsError("polygon coordinates must be finite")
    return out


def _in_ring(x: np.ndarray, y: np.ndarray, ring: np.ndarray) -> np.ndarray:
    inside = np.zeros(len(x), bool)
    xs, ys = ring[:, 0], ring[:, 1]
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi, xj, yj = xs[i], ys[i], xs[j], ys[j]
        if yi != yj:
            cross = (yi > y) != (yj > y)
            xint = xi + (y - yi) * (xj - xi) / (yj - yi)
            inside ^= cross & (x < xint)
        j = i
    return inside


def _in_polygons(x: np.ndarray, y: np.ndarray, polys) -> np.ndarray:
    m = np.zeros(len(x), bool)
    for rings in polys:
        p = _in_ring(x, y, rings[0])
        for hole in rings[1:]:
            p &= ~_in_ring(x, y, hole)
        m |= p
    return m


def polygon_bbox(polys) -> tuple[float, float, float, float]:
    pts = np.concatenate([p[0] for p in polys])
    return float(pts[:, 0].min()), float(pts[:, 1].min()), float(pts[:, 0].max()), float(pts[:, 1].max())


# ------------------------------------------------------------------ core
def _cell_range(lo: float, hi: float, off: float) -> tuple[int, int]:
    """Cells whose centre lies in [lo, hi]; at least the cell holding the midpoint."""
    a = math.ceil((lo + off) / GRID - 0.5)
    b = math.floor((hi + off) / GRID - 0.5)
    if a > b:
        a = b = int(math.floor(((lo + hi) / 2 + off) / GRID))
    return a, b


def _r(v, nd=2):
    return None if v is None or not math.isfinite(v) else round(float(v), nd)


def compute(st, box: tuple[float, float, float, float], polygon=None, year: int | None = None,
            metric: str = "viirs_cell_days", z_threshold: float = 2.0) -> dict:
    if metric not in METRICS:
        raise StatsError(f"metric must be one of {', '.join(METRICS)}")
    x0, y0, x1, y1 = box
    if x1 - x0 > MAX_SPAN_DEG or y1 - y0 > MAX_SPAN_DEG:
        raise StatsError(f"area too large: at most {MAX_SPAN_DEG:.0f} x {MAX_SPAN_DEG:.0f} degrees")
    T = _tables(st)
    years, ny = T["years"], len(T["years"])
    gx0, gx1 = _cell_range(x0, x1, 180.0)
    gy0, gy1 = _cell_range(y0, y1, 90.0)

    d = st.daily
    gx, gy = d["gx"].values, d["gy"].values
    idx = np.flatnonzero((gx >= gx0) & (gx <= gx1) & (gy >= gy0) & (gy <= gy1))
    n_cells = (gx1 - gx0 + 1) * (gy1 - gy0 + 1)
    if polygon is not None:
        cx = gx[idx].astype(np.float64) * GRID - 180 + GRID / 2
        cy = gy[idx].astype(np.float64) * GRID - 90 + GRID / 2
        idx = idx[_in_polygons(cx, cy, polygon)]
        gxs = np.arange(gx0, gx1 + 1) * GRID - 180 + GRID / 2
        gys = np.arange(gy0, gy1 + 1) * GRID - 90 + GRID / 2
        if n_cells <= 4_000_000:
            mx, my = np.meshgrid(gxs, gys)
            n_cells = int(_in_polygons(mx.ravel(), my.ravel(), polygon).sum())
    lat_mid = math.radians((y0 + y1) / 2)
    area_km2 = n_cells * (GRID * 111.32) ** 2 * math.cos(lat_mid)

    di = d["day"].values[idx].astype(np.int64) - st.day_min
    keep = T["keep"][di]
    idx, di = idx[keep], di[keep]
    key = T["iy"][di] * (NW + 1) + T["iw"][di]
    n_v = d["n_v"].values[idx]
    n_m = d["n_m"].values[idx]
    ov = d["ov_v"].values[idx].astype(np.float32) + d["ov_m"].values[idx]
    frp = (d["frp_v"].values[idx] + d["frp_mh"].values[idx]) / np.maximum(ov, 1)
    size = ny * (NW + 1)

    def grid(weights=None, mask=None):
        k = key if mask is None else key[mask]
        w = None if weights is None else (weights if mask is None else weights[mask])
        return np.bincount(k, weights=w, minlength=size).reshape(ny, NW + 1)[:, 1:]

    vm, mm = n_v > 0, n_m > 0
    raw = {
        "viirs_cell_days": grid(mask=vm),
        "viirs_detections": grid(n_v.astype(np.float64)),
        "modis_cell_days": grid(mask=mm),
        "modis_detections": grid(n_m.astype(np.float64)),
        "any_cell_days": grid(),
        "frp_equiv_mw": grid(frp.astype(np.float64)),
    }
    cov = T["cov"]

    def masked(name):
        a = raw[name].astype(float)
        a[cov[METRICS[name][0]] == 0] = np.nan
        return a

    calendar = {k: [[_r(v, 1) for v in row] for row in masked(k)] for k in raw}

    per_year = []
    for i, y in enumerate(years):
        r = {"year": y}
        for k in raw:
            c = cov[METRICS[k][0]][i]
            r[k] = _r(raw[k][i].sum(), 1) if c.sum() else None
        for s in ("viirs", "modis"):
            r[f"{s}_coverage_days"] = int(cov[s][i].sum())
        per_year.append(r)

    # ---- selected year vs leave-one-year-out baseline
    sensor = METRICS[metric][0]
    c = cov[sensor]
    vals = raw[metric].astype(float)
    have = [i for i in range(ny) if c[i].sum() > 0]
    if not have:
        raise StatsError(f"no {sensor.upper()} data in the archive")
    if year is None:
        year = years[have[-1]]
    if year not in years or c[years.index(year)].sum() == 0:
        raise StatsError(f"year {year} has no {sensor.upper()} data; available: "
                         f"{years[have[0]]}..{years[have[-1]]}")
    yi = years.index(year)
    others = [i for i in have if i != yi]
    # any_cell_days / frp_equiv_mw mix sensors: pre-VIIRS years (MODIS only) are not comparable with VIIRS-era
    # years, so their baseline uses only years of the same sensor era (before vs from the VIIRS start year).
    baseline_scope = "all_years"
    vlo, vhi = T["ranges"]["viirs"]
    if metric in ERA_MATCHED and vhi >= vlo:
        vy = int((EPOCH + np.timedelta64(int(vlo), "D")).astype("datetime64[Y]").astype(int)) + 1970
        same = [i for i in others if (years[i] >= vy) == (year >= vy)]
        if len(same) >= 2:
            others, baseline_scope = same, "same_sensor_era"
    weeks = []
    for w in range(NW):
        base = [vals[i, w] for i in others if c[i, w] == 7]
        x = vals[yi, w] if c[yi, w] > 0 else None
        e = {"week": w + 1, "value": _r(x, 2) if x is not None else None, "partial": bool(0 < c[yi, w] < 7),
             "mean": None, "std": None, "z": None, "flag": None, "n_years": len(base)}
        if len(base) >= 2:
            b = np.array(base)
            mean, sd = float(b.mean()), float(b.std(ddof=1))
            e.update(mean=_r(mean), std=_r(sd))
            if x is not None:
                z = (x - mean) / max(sd, math.sqrt(max(mean, 1.0)))
                e["z"] = _r(z)
                if z >= z_threshold and x > 0:
                    e["flag"] = "high"
                elif z <= -z_threshold:
                    e["flag"] = "low"
        weeks.append(e)

    # ---- key figures over the weeks of the selected year that have data
    pw = [w for w in range(NW) if c[yi, w] > 0]
    total = float(vals[yi, pw].sum())
    base_years = [i for i in others if all(c[i, w] >= c[yi, w] for w in pw)]  # same weeks, as covered
    base_tot = np.array([vals[i, pw].sum() for i in base_years], float)
    key_fig = {"year": year, "metric": metric, "value": _r(total, 1), "weeks_with_data": len(pw),
               "baseline_years": [years[i] for i in base_years], "baseline_scope": baseline_scope,
               "baseline_mean": None, "baseline_std": None, "pct_vs_mean": None, "z": None,
               "anomalous_weeks": [e["week"] for e in weeks if e["flag"] == "high"],
               "low_weeks": [e["week"] for e in weeks if e["flag"] == "low"],
               "peak_week": int(np.argmax(np.nan_to_num(vals[yi]))) + 1 if total > 0 else None}
    if len(base_tot) >= 2:
        mean, sd = float(base_tot.mean()), float(base_tot.std(ddof=1))
        z = (total - mean) / max(sd, math.sqrt(max(mean, 1.0)))
        key_fig.update(baseline_mean=_r(mean, 1), baseline_std=_r(sd, 1), z=_r(z),
                       pct_vs_mean=_r((total / mean - 1) * 100, 1) if mean > 0 else None)
        if total == 0 and mean == 0:
            label = "no fire activity"
        elif z >= z_threshold:
            label = "anomaly: above normal"
        elif z <= -z_threshold:
            label = "unusually low"
        else:
            label = "routine: within normal range"
    else:
        label = "not enough years for a baseline"
    key_fig["label"] = label
    ranges = {s: [str(EPOCH + np.timedelta64(int(lo), "D")), str(EPOCH + np.timedelta64(int(hi), "D"))]
              if hi >= lo else None for s, (lo, hi) in T["ranges"].items()}
    return {
        "aoi": {"bbox": [x0, y0, x1, y1], "polygon": polygon is not None, "cells_0p05deg": int(n_cells),
                "area_km2": round(area_km2, 1)},
        "metric": metric, "metric_label": METRICS[metric][1], "metrics": {k: v[1] for k, v in METRICS.items()},
        "week_system": "ISO 8601 weeks 1-53 (week 53 only in long years)",
        "years": years, "sensor_ranges": ranges, "z_threshold": z_threshold,
        "calendar": calendar, "per_year": per_year,
        "selected": {"year": year, "weeks": weeks, "key": key_fig},
        "cell_days_total": int(len(idx)),
        "note": None if len(idx) else "no fire detections in the archive for this area (it may lie outside "
                                      "the downloaded regions, or never burned)",
    }
