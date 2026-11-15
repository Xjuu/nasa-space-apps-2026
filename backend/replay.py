"""Honest historical replay: issue a forecast using only what was known at the initialization time, then reveal
what actually happened and score it.

    GET /replay/incidents                  curated demonstration incidents (data/incidents.json) + saved-replay status
    GET /replay/run?incident=<id>          saved/cached replay of a curated incident (computed once, then cached)
    GET /replay/run?lat=&lon=&date=&days=  any archive fire (2024-2025 ML+physics; earlier years ML-only)
    python -m backend.replay precompute    write cache/replay/*.json for every curated incident (offline demo)

No-leakage design (structural, not a promise)
---------------------------------------------
The forecast is issued from an *as-of index*: a copy of the VIIRS/MODIS fire index in which every cell-day later
than the initialization day has been deleted (``asof_index``). The forecaster (equifire ML model, ML+physics blender,
physics calibration) is handed only that object, so it cannot read later detections even by accident. The reveal is
computed afterwards from the full archive. tests: backend/test_replay.py (forecast is byte-identical when all later
observations are removed or tampered with).

Not leak-free (disclosed in every response): the physics component is driven by ERA5 *reanalysis* weather for the
forecast period (perfect-weather replay, not an issued weather forecast), so physics skill is optimistic compared with a
real-time forecast. Models are chosen so that they were trained on years strictly before the incident year.
"""
from __future__ import annotations

import base64
import datetime as dt
import functools
import hashlib
import json
import math
import re
import threading
import time
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import ndimage

from equifire import direct
from equifire.common import CACHE, EPOCH, GRID, MODELS, ROOT, cell_center
from equifire.direct import HIST, LONG, S, W

SCHEMA = "equifire.replay/1"
OBSERVABILITY_SCORING_VERSION = "fixed-cloud-proxy-mask/1"
CACHE_DIR = CACHE / "replay"
INCIDENTS_FILE = ROOT / "data" / "incidents.json"
OBS_CLOUD_THRESHOLD = 80.0      # % ERA5 cloud cover above which an overpass is treated as obscured (proxy)
OVERPASS_LOCAL_H = {"day": 13.5, "night": 1.5}   # VIIRS (S-NPP/NOAA-20/21) local solar crossing times
MIN_PROB_OUT = 0.02             # issued GeoJSON keeps cells with probability >= this or selected
PALETTE = {                     # Okabe-Ito colourblind-safe suggestion for the frontend
    "hit": "#0072B2", "near_hit": "#56B4E9", "miss": "#E69F00", "false_alarm": "#CC79A7",
    "unverifiable": "#999999", "burning_at_init": "#000000"}
CLASS_NAMES = {0: "none", 1: "hit", 2: "miss", 3: "false_alarm", 4: "unverifiable", 5: "near_hit"}

_RUN_LOCK = threading.RLock()


class NoObservations(ValueError):
    pass


# ============================================================== curated incidents
def load_incidents() -> list[dict]:
    return json.loads(INCIDENTS_FILE.read_text())["incidents"]


def get_incident(incident_id: str) -> dict:
    for inc in load_incidents():
        if inc["id"] == incident_id:
            return inc
    raise KeyError(f"unknown incident {incident_id!r}; see GET /replay/incidents")


def cache_path(key: str, objective: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", key)
    return CACHE_DIR / f"{safe}__{objective}.json"


def adhoc_key(lat, lon, date, days) -> str:
    return f"adhoc_{round(float(lat), 3)}_{round(float(lon), 3)}_{date}_d{int(days)}"


# ============================================================== as-of index (the no-leakage mechanism)
def _slice_tiled(t, d_lo, d_hi, x0, x1, y0, y1):
    from equifire.direct import _TILE
    parts = []
    for tx in range(x0 // _TILE, x1 // _TILE + 1):
        for ty in range(y0 // _TILE, y1 // _TILE + 1):
            s = t.seg.get(tx * 100_000 + ty)
            if s is None:
                continue
            a, b = s
            lo, hi = np.searchsorted(t.day[a:b], [d_lo, d_hi + 1])
            if hi > lo:
                parts.append(np.arange(a + lo, a + hi))
    if not parts:
        return np.zeros(0, np.int64)
    i = np.concatenate(parts)
    gx, gy = np.asarray(t.gx[i]).astype(np.int32), np.asarray(t.gy[i]).astype(np.int32)
    return i[(gx >= x0) & (gx <= x1) & (gy >= y0) & (gy <= y1)]


class AsOfSubcell:
    """Wraps the sub-cell (375 m offset) lookup so it can only be queried for days <= the cut-off day."""

    def __init__(self, sub, cutoff_day):
        self._sub, self.cutoff_day = sub, int(cutoff_day)

    def lookup(self, day, gx, gy):
        if len(day) and int(np.max(day)) > self.cutoff_day:
            raise AssertionError("as-of sub-cell index queried after the cut-off day")
        return self._sub.lookup(day, gx, gy)


def asof_index(full, t0: int, gx: int, gy: int, margin: int = 8):
    """A FireIndex that physically contains only cell-days with day <= t0 inside a window around (gx, gy).

    margin: cells beyond the model window (W) kept so the ignition search / physics ring see the same data."""
    r = W + margin
    x0, x1, y0, y1 = gx - r, gx + r, gy - r, gy + r
    d_lo = t0 - LONG - 5

    def tile(t, keys):
        i = _slice_tiled(t, d_lo, t0, x0, x1, y0, y1)
        return direct._Tiled(np.asarray(t.day[i]), np.asarray(t.gx[i]), np.asarray(t.gy[i]),
                             **{k: np.asarray(t.vals[k][i]) for k in keys})

    out = direct.FireIndex.__new__(direct.FireIndex)
    out.v = tile(full.v, list(full.v.vals))
    out.m = tile(full.m, list(full.m.vals)) if getattr(full, "m", None) is not None else None
    out.has_dn = True
    o = np.argsort(out.v.day, kind="stable")
    out.day, out.gx, out.gy = out.v.day[o], out.v.gx[o], out.v.gy[o]
    sub = getattr(full, "sub", None)
    out.sub = AsOfSubcell(sub, t0) if sub is not None else None
    out.asof_day = int(t0)
    for name, t in (("v", out.v), ("m", out.m)):
        if t is not None and len(t) and int(t.day.max()) > t0:
            raise AssertionError(f"as-of index {name} contains rows after the cut-off day")
    return out


# ============================================================== pure scoring (unit-tested on synthetic cells)
_K3 = np.ones((3, 3), bool)


def _dil(a):
    return ndimage.binary_dilation(a, _K3) if a.any() else np.zeros_like(a, bool)


def _ratio(a, b):
    return None if b == 0 else round(float(a) / float(b), 4)


def strict_counts(pred, truth) -> dict:
    p, t = pred.astype(bool), truth.astype(bool)
    hits = int((p & t).sum())
    npred, nobs = int(p.sum()), int(t.sum())
    union = npred + nobs - hits
    return {"predicted_cells": npred, "observed_cells": nobs, "hits": hits, "misses": nobs - hits,
            "false_alarms": npred - hits, "precision": _ratio(hits, npred), "recall": _ratio(hits, nobs),
            "iou": 1.0 if union == 0 else round(hits / union, 4)}


def tolerant_counts(pred, truth) -> dict:
    """Within 1 cell (~5.5 km). within_5km_score is exactly walk_forward._tol (both empty = 1, one empty = 0)."""
    p, t = pred.astype(bool), truth.astype(bool)
    npred, nobs = int(p.sum()), int(t.sum())
    pm = int((p & _dil(t)).sum())      # predicted cells with an observed cell within 1 cell
    om = int((t & _dil(p)).sum())      # observed cells with a predicted cell within 1 cell
    if npred == 0 and nobs == 0:
        score, iou = 1.0, 1.0
    elif npred == 0 or nobs == 0:
        score, iou = 0.0, 0.0
    else:
        score = round((pm + om) / (npred + nobs), 4)
        matched = (pm + om) / 2.0
        iou = round(matched / (npred + nobs - matched), 4)
    return {"predicted_cells": npred, "observed_cells": nobs, "predicted_matched": pm, "observed_matched": om,
            "misses": nobs - om, "false_alarms": npred - pm, "precision": _ratio(pm, npred),
            "recall": _ratio(om, nobs), "iou": iou, "within_5km_score": score}


def observable_scores(pred, truth, observable=None, *, unknown=None, eligible=None) -> dict:
    """Evaluate BOTH sets on a fixed cloud-proxy mask, never selected from fire outcomes.

    Masking precedes tolerant dilation: excluded predictions/truth cannot match an
    included neighbour. The unchanged full-grid scores remain the primary comparison.
    An empty observable domain is unscored, rather than a fabricated perfect score.
    """
    p, t = np.asarray(pred, bool), np.asarray(truth, bool)
    if p.shape != t.shape:
        raise ValueError("prediction and truth shapes differ")
    out = {"version": OBSERVABILITY_SCORING_VERSION, "available": observable is not None, "scored": False,
           "strict": None, "tolerant": None,
           "coverage": {"total_cells": int(p.size), "observable_cells": None,
                        "observable_fraction": None, "unknown_cells": None, "obscured_cells": None,
                        "eligible_cells": int(np.asarray(eligible, bool).sum()) if eligible is not None else None,
                        "observable_eligible_cells": None},
           "excluded": {"predicted_cells": None, "observed_cells": None},
           "mask_source": "ERA5 cloud_cover proxy at nominal local solar overpass hours, not a satellite cloud mask",
           "note": "Same fixed mask for predictions, observations and baseline; both endpoints must be observable "
                   "for tolerant matching. Cloud proxy omits actual swath, scan angle and smoke. Conditional scores "
                   "are not independent validation and may improve or worsen after selection."}
    if observable is None:
        out["reason"] = "Spatial observation mask unavailable; aggregate cloud summaries cannot reconstruct it."
        return out
    mask = np.asarray(observable, bool)
    if mask.shape != p.shape:
        raise ValueError("observation mask shape differs")
    if unknown is not None:
        u = np.asarray(unknown, bool)
        if u.shape != p.shape or np.any(mask & u):
            raise ValueError("unknown mask differs or overlaps observable mask")
        out["coverage"]["unknown_cells"] = int(u.sum())
        out["coverage"]["obscured_cells"] = int((~mask & ~u).sum())
    n = int(mask.sum())
    out["coverage"].update(observable_cells=n, observable_fraction=round(n / p.size, 6) if p.size else None)
    if eligible is not None:
        e = np.asarray(eligible, bool)
        if e.shape != p.shape:
            raise ValueError("eligible mask shape differs")
        out["coverage"]["observable_eligible_cells"] = int((e & mask).sum())
    out["excluded"] = {"predicted_cells": int((p & ~mask).sum()), "observed_cells": int((t & ~mask).sum())}
    if n == 0 or (eligible is not None and out["coverage"]["observable_eligible_cells"] == 0):
        out["reason"] = "No observable cells in the evaluation domain; no conditional score is reported."
        return out
    out.update(scored=True, strict=strict_counts(p & mask, t & mask),
               tolerant=tolerant_counts(p & mask, t & mask), reason=None)
    return out


def score_day(pred, truth, unverifiable=None, *, observable=None, unknown=None, eligible=None) -> dict:
    """Strict and within-1-cell scores for cumulative masks. `unverifiable` marks cells with no usable observation:
    predicted cells there that were not observed are reported as unverifiable instead of false alarms
    ("adjusted" block); the headline numbers keep them as false alarms (conservative)."""
    p, t = pred.astype(bool), truth.astype(bool)
    out = {"strict": strict_counts(p, t), "tolerant": tolerant_counts(p, t), "adjusted": None,
           "observable": observable_scores(p, t, observable, unknown=unknown, eligible=eligible)}
    if unverifiable is not None:
        u = unverifiable.astype(bool)
        drop_s = p & u & ~t
        drop_t = p & u & ~_dil(t)
        out["adjusted"] = {
            "unverifiable_predicted_cells_strict": int(drop_s.sum()),
            "unverifiable_predicted_cells_tolerant": int(drop_t.sum()),
            "strict": strict_counts(p & ~drop_s, t), "tolerant": tolerant_counts(p & ~drop_t, t),
            "note": "predicted-but-unobserved cells with no clear overpass proxy are excluded from the predicted "
                    "set; cloud proxy is ERA5 modelled cloud cover, not the satellite cloud mask"}
    return out


def classify(pred, truth, unverifiable=None):
    """Per-cell codes: strict (0 none,1 hit,2 miss,3 false_alarm,4 unverifiable) and tolerant (adds 5 near_hit)."""
    p, t = pred.astype(bool), truth.astype(bool)
    u = np.zeros_like(p) if unverifiable is None else unverifiable.astype(bool)
    s = np.zeros(p.shape, np.int8)
    s[p & t] = 1
    s[t & ~p] = 2
    s[p & ~t] = 3
    s[p & ~t & u] = 4
    tol = s.copy()
    near_p, near_t = p & ~t & _dil(t), t & ~p & _dil(p)
    tol[near_p | near_t] = 5
    tol[p & ~t & ~_dil(t) & u] = 4
    return s, tol


# ============================================================== observation-quality proxy (cloud)
def observation_quality(clat, clon, t0_date: dt.date, days: int, cx: int, cy: int, fetch=None, n=5,
                        half_deg=W * GRID + 0.05, threshold=OBS_CLOUD_THRESHOLD) -> dict:
    """Per UTC day and 0.5 deg point: was there a clear-enough VIIRS day or night overpass?

    Proxy only: ERA5 hourly cloud_cover (Open-Meteo archive, keyless) at the local solar overpass times
    (01:30 / 13:30); a day is 'insufficient' at a point when BOTH overpass-hour cloud covers are >= threshold.
    Returns arrays aligned to the S x S model window. available=False when the data cannot be fetched."""
    from equifire import fireweather as fw
    lats = np.linspace(clat - half_deg, clat + half_deg, n)
    lons = np.linspace(clon - half_deg / max(math.cos(math.radians(clat)), 0.3),
                       clon + half_deg / max(math.cos(math.radians(clat)), 0.3), n)
    LA, LO = np.meshgrid(lats, lons, indexing="ij")
    out = {"available": False, "source": "ERA5 cloud_cover via Open-Meteo archive (proxy, not a satellite cloud mask)",
           "threshold_pct": threshold, "overpass_local_hours": OVERPASS_LOCAL_H, "grid_points": n * n}
    d1 = t0_date + dt.timedelta(days=1)
    d2 = t0_date + dt.timedelta(days=days)
    try:
        times, data = (fetch or fw.fetch_hourly)(LA.ravel(), LO.ravel(), d1, d2, variables=["cloud_cover"])
        cc = data["cloud_cover"].reshape(n, n, -1)
    except Exception as e:  # network / rate limit: say so, do not guess
        out["error"] = f"{type(e).__name__}: {e}"[:200]
        return out
    t_idx = {np.datetime64(t, "h").astype("int64"): i for i, t in enumerate(times)}

    def at(lon, day_i, local_h):
        h = int(round((local_h - lon / 15.0) % 24))
        base = (np.datetime64((d1 + dt.timedelta(days=day_i)).isoformat(), "h")).astype("int64")
        vals = [cc_row[t_idx[base + h + dh]] for dh in (-1, 0, 1) if (base + h + dh) in t_idx]
        finite = np.asarray(vals, float)
        finite = finite[np.isfinite(finite)]
        return float(finite.mean()) if len(finite) else float("nan")

    per_day_point = np.full((days, n, n), np.nan)
    dayc = np.full((days, n, n), np.nan)
    nightc = np.full((days, n, n), np.nan)
    for k in range(days):
        for i in range(n):
            for j in range(n):
                cc_row = cc[i, j]
                dayc[k, i, j] = at(lons[j], k, OVERPASS_LOCAL_H["day"])
                nightc[k, i, j] = at(lons[j], k, OVERPASS_LOCAL_H["night"])
    clear = np.fmin(dayc, nightc)                     # clearest of the two overpasses (legacy caveat)
    insuff_pt = clear >= threshold
    unknown_pt = ~np.isfinite(clear)
    # nearest sample point for every model-window cell
    xs, ys = np.meshgrid(np.arange(S), np.arange(S), indexing="ij")
    lat_c, lon_c = cell_center(cx - W + xs, cy - W + ys)
    ii = np.abs(lat_c[..., None] - lats).argmin(-1)
    jj = np.abs(lon_c[..., None] - lons).argmin(-1)
    insuff = np.stack([insuff_pt[k][ii, jj] for k in range(days)])
    unknown = np.stack([unknown_pt[k][ii, jj] for k in range(days)])
    # cumulative: a cell is unverifiable by day k when no day 1..k had a clear-enough overpass
    unver = np.cumprod(insuff & ~unknown, axis=0).astype(bool)
    # Strictly separate known clear, known obscured and unknown. One missing
    # pass plus one cloudy pass is unknown, rather than evidence of obscuration.
    clear_pt = ((np.isfinite(dayc) & (dayc < threshold)) |
                (np.isfinite(nightc) & (nightc < threshold)))
    known_obscured_pt = (np.isfinite(dayc) & np.isfinite(nightc) &
                         (dayc >= threshold) & (nightc >= threshold))
    clear_daily = np.stack([clear_pt[k][ii, jj] for k in range(days)])
    unknown_daily = np.stack([(~clear_pt[k] & ~known_obscured_pt[k])[ii, jj] for k in range(days)])
    observable = np.maximum.accumulate(clear_daily, axis=0)
    unknown_cumulative = ~observable & np.maximum.accumulate(unknown_daily, axis=0)
    def cloud_mean(values):
        finite = values[np.isfinite(values)]
        return round(float(finite.mean()), 1) if len(finite) else None
    out.update(available=not bool(unknown.all()), _insuff=insuff, _unverifiable=unver,
               _observable=observable, _unknown=unknown_cumulative,
               spatial_mask=_encode_observation_masks(observable, unknown_cumulative), per_day=[
        {"day": k + 1, "date": (t0_date + dt.timedelta(days=k + 1)).isoformat(),
         "mean_cloud_pct_day_pass": cloud_mean(dayc[k]),
         "mean_cloud_pct_night_pass": cloud_mean(nightc[k]),
         "window_fraction_obscured": round(float(insuff[k].mean()), 3),
         "window_fraction_unverifiable_cumulative": round(float(unver[k].mean()), 3)} for k in range(days)])
    return out


def _encode_observation_masks(observable, unknown):
    def pack(a):
        return [base64.b64encode(np.packbits(day.ravel(), bitorder="little").tobytes()).decode("ascii") for day in a]
    return {"encoding": "packbits-little-base64", "shape": list(observable.shape),
            "observable": pack(observable), "unknown": pack(unknown),
            "protocol": OBSERVABILITY_SCORING_VERSION,
            "selection": "fixed threshold/nominal pass hours; independent of prediction and detected fire outcome"}


def _decode_observation_masks(quality, days):
    info = (quality or {}).get("spatial_mask") or {}
    if (info.get("encoding") != "packbits-little-base64" or info.get("protocol") != OBSERVABILITY_SCORING_VERSION or
            info.get("shape") != [days, S, S]):
        return None, None
    try:
        def unpack(name):
            values = info[name]
            if len(values) != days:
                raise ValueError("mask day count differs")
            rows = []
            for encoded in values:
                raw = base64.b64decode(encoded, validate=True)
                if len(raw) != (S * S + 7) // 8:
                    raise ValueError("mask byte count differs")
                rows.append(np.unpackbits(np.frombuffer(raw, dtype=np.uint8), bitorder="little")[:S*S].reshape(S, S).astype(bool))
            return np.stack(rows)
        observable, unknown = unpack("observable"), unpack("unknown")
        if np.any(observable & unknown):
            raise ValueError("observation masks overlap")
        return observable, unknown
    except (KeyError, TypeError, ValueError):
        return None, None


def _cached_cloud_hourly(lats, lons, start_date, end_date, variables):
    """Read the existing raw ERA5 cache only; never fall through to HTTP."""
    from equifire import fireweather as fw
    params = {"latitude": ",".join(str(round(float(v), 4)) for v in lats),
              "longitude": ",".join(str(round(float(v), 4)) for v in lons),
              "hourly": ",".join(variables), "timezone": "GMT",
              "start_date": start_date.isoformat(), "end_date": end_date.isoformat()}
    key = hashlib.sha1((fw.ARCHIVE_URL + "?" + json.dumps(params, sort_keys=True)).encode()).hexdigest()
    raw = json.loads((fw.HTTP_DIR / f"{key}.json").read_text())
    locations = raw if isinstance(raw, list) else [raw]
    if len(locations) != len(lats):
        raise ValueError("cached cloud grid differs")
    times = np.arange(np.datetime64(start_date.isoformat(), "h"),
                      np.datetime64((end_date + dt.timedelta(days=1)).isoformat(), "h"))
    arrays = {v: np.full((len(lats), len(times)), np.nan, np.float32) for v in variables}
    for i, location in enumerate(locations):
        hourly = location["hourly"]
        indexes = (np.asarray(hourly["time"], dtype="datetime64[h]") - times[0]).astype(int)
        valid = (indexes >= 0) & (indexes < len(times))
        for variable in variables:
            values = np.asarray([np.nan if value is None else value for value in hourly[variable]], np.float32)
            arrays[variable][i, indexes[valid]] = values[valid]
    return times, arrays


def _public(q: dict) -> dict:
    return {k: v for k, v in q.items() if not k.startswith("_")}


# ============================================================== model loading / issuing the forecast
@functools.lru_cache(maxsize=4)
def _ml_only_model(year: int):
    import xgboost as xgb
    f, mf = MODELS / f"wf_{year}.json", MODELS / f"wf_{year}_meta.json"
    if not (f.exists() and mf.exists()):
        raise _unavailable(f"no ML model trained before {year} (models/wf_{year}.json missing)")
    meta = json.loads(mf.read_text())
    if int(meta.get("test_from", year + 1)) > year:
        raise _unavailable("ML model training cut-off is later than the incident year")
    m = xgb.XGBClassifier(n_jobs=2)
    m.load_model(f)
    return m, meta


def _unavailable(msg):
    from . import ensemble
    return ensemble.PredictorUnavailable(msg)


@functools.lru_cache(maxsize=1)
def _weather():
    return direct.Weather()


def _decode_guidance(g, cx, cy, days):
    """ensemble.predict GeoJSON -> arrays [days, S, S]."""
    shape = (days, S, S)
    A = {k: np.full(shape, np.nan, np.float32) for k in ("ml", "comb", "phys")}
    sel = {k: np.zeros(shape, bool) for k in ("ml", "comb")}
    for f in g["features"]:
        p = f["properties"]
        ring = f["geometry"]["coordinates"][0]
        gx, gy = int(round((ring[0][0] + 180) / GRID)), int(round((ring[0][1] + 90) / GRID))
        x, y, k = gx - cx + W, gy - cy + W, int(p["day"]) - 1
        A["ml"][k, x, y], A["comb"][k, x, y] = p["ml_probability"], p["combined_probability"]
        A["phys"][k, x, y] = p["physics_burned_fraction"]
        sel["ml"][k, x, y], sel["comb"][k, x, y] = p["ml_selected"], p["selected"]
    return A, sel


def issue_forecast(lat, lon, date, days, objective, method, asof, *, predict_fn=None, physics_fn=None):
    """Issue the forecast from the as-of index only. Returns a dict with arrays and the public 'issued' section."""
    from . import ensemble
    t = pd.Timestamp(date)
    year = t.year
    day = int((t.to_datetime64().astype("datetime64[D]") - EPOCH).astype(int))
    gx, gy = int(np.floor((lon + 180) / GRID)), int(np.floor((lat + 90) / GRID))
    cx, cy = ensemble.nearest_fire(asof, day, gx, gy)
    clat, clon = map(float, cell_center(cx, cy))
    bundle, why_not = None, None
    if method in ("auto", "ml_physics"):
        try:
            bundle = ensemble.load_bundle(str(MODELS), year)
        except ensemble.PredictorUnavailable as e:
            why_not = str(e)
            if method == "ml_physics":
                raise
    if bundle is not None:
        g = (predict_fn or ensemble.predict)(lat, lon, date, days, objective, fi=asof, bundle=bundle,
                                             **({"physics_fn": physics_fn} if physics_fn else {}))
        arr, sel = _decode_guidance(g, cx, cy, days)
        used = "ml_physics"
        model = g["model"]
        warnings = list(g.get("warnings", []))
        sim_url = g.get("hourly_simulation_url")
        features = g["features"]
        physics_meta = g.get("physics")
    else:
        ml, meta = _ml_only_model(year)
        res = direct.predict_case(ml, asof, day, cx, cy, _weather(), days, list(meta["features"]))
        if res is None:
            raise ensemble.NoIgnition("Insufficient detected ignition for this model")
        P = res[0]
        arr = {"ml": P, "comb": None, "phys": None}
        sel = {"ml": np.stack([direct.select(P[k], direct.rule_for(meta, k + 1)) for k in range(days)]),
               "comb": None}
        used, features, sim_url, physics_meta = "ml", [], None, None
        model = {"ml_model": f"wf_{year}", "ml_training_before_year": meta["test_from"],
                 "inputs": "VIIRS history plus MODIS activity features"}
        warnings = [why_not or "No ML+physics blender fitted before this year: ML-only replay.",
                    "ML-only: physics is not used because no compatible blender exists for this year."]
        for k in range(days):
            for x, y in zip(*np.nonzero(np.isfinite(P[k]))):
                features.append({"type": "Feature", "geometry": _poly(cx + int(x) - W, cy + int(y) - W),
                                 "properties": {"kind": "ml_forecast", "day": k + 1,
                                                "date": (t + pd.Timedelta(days=k + 1)).date().isoformat(),
                                                "ml_probability": round(float(P[k, x, y]), 5),
                                                "ml_selected": bool(sel["ml"][k, x, y]),
                                                "selected": bool(sel["ml"][k, x, y]), "estimate": True}})
    keep = [f for f in features if f["properties"]["selected"] or f["properties"]["ml_selected"]
            or f["properties"].get("combined_probability", f["properties"]["ml_probability"]) >= MIN_PROB_OUT]
    return {"cx": cx, "cy": cy, "clat": clat, "clon": clon, "arr": arr, "sel": sel, "method": used,
            "model": model, "warnings": warnings, "physics": physics_meta, "sim_url": sim_url,
            "geojson": {"type": "FeatureCollection", "features": keep}, "all_cells": len(features),
            "day": day}


def _poly(gx, gy):
    from . import geo
    return geo.cell_polygon(gx, gy)


# ============================================================== reveal
def _eligible(dist, k):
    return (dist >= 1) & (dist <= k)


def build_reveal(full, issued, days, quality=None, date=None) -> dict:
    cx, cy, day = issued["cx"], issued["cy"], issued["day"]
    A, _ = full.cube(day, cx, cy)
    A0 = A[HIST]
    dist = ndimage.distance_transform_cdt(A0 == 0, metric="chessboard").astype(np.float32)
    truth = direct.case_truth(A, dist, A0, days)
    day_max = int(full.v.day.max())
    through = int(max(0, min(days, day_max - day)))
    t0_date = pd.Timestamp(date).date()
    methods = {"ml_physics": issued["sel"]["comb"], "ml": issued["sel"]["ml"]}
    methods = {k: v for k, v in methods.items() if v is not None}
    ring = np.stack([_eligible(dist, k + 1) for k in range(days)])
    unver = quality["_unverifiable"] if quality and quality.get("available") else None
    observable = quality.get("_observable") if quality and quality.get("available") else None
    unknown = quality.get("_unknown") if quality and quality.get("available") else None
    # data availability per day (VIIRS rows in the model window / a +-40 cell region)
    avail = []
    for k in range(1, days + 1):
        win = len(full.v.query(day + k, day + k, cx, cy))
        reg = 0
        for dx in (-40, 0, 40):
            for dy in (-40, 0, 40):
                reg += len(full.v.query(day + k, day + k, cx + dx, cy + dy))
        avail.append({"day": k, "date": (t0_date + dt.timedelta(days=k)).isoformat(),
                      "viirs_cell_days_in_window": int(win), "viirs_cell_days_in_region": int(reg),
                      "archive_has_day": day + k <= day_max})
    per_day, cell_cls = [], {}
    for k in range(1, days + 1):
        row = {"day": k, "date": (t0_date + dt.timedelta(days=k)).isoformat(), "scored": k <= through,
               "observed_new_cells": int(truth[k - 1].sum()) if k <= through else None, "methods": {}}
        if k <= through:
            u = unver[k - 1] if unver is not None else None
            obs = observable[k - 1] if observable is not None else None
            unk = unknown[k - 1] if unknown is not None else None
            for name, masks in methods.items():
                row["methods"][name] = score_day(masks[k - 1], truth[k - 1], u,
                                                 observable=obs, unknown=unk, eligible=ring[k - 1])
                s, tol = classify(masks[k - 1], truth[k - 1], u)
                for x, y in zip(*np.nonzero((s > 0) | (tol > 0))):
                    c = cell_cls.setdefault((int(x), int(y)), {})
                    c.setdefault(name, {"strict": [0] * days, "tolerant": [0] * days})
                    c[name]["strict"][k - 1], c[name]["tolerant"][k - 1] = int(s[x, y]), int(tol[x, y])
            row["methods"]["baseline_ring"] = score_day(ring[k - 1], truth[k - 1], None,
                                                        observable=obs, unknown=unk, eligible=ring[k - 1])
            row["methods"]["baseline_ring"]["description"] = (
                "comparator only: marks EVERY cell within k cells of the burning area as burned")
        per_day.append(row)
    feats = []
    first_obs = np.zeros((S, S), int)
    for k in range(days, 0, -1):
        first_obs[truth[k - 1]] = k if k <= through else 0
    for (x, y), c in sorted(cell_cls.items()):
        gx, gy = cx - W + x, cy - W + y
        lat, lon = cell_center(gx, gy)
        feats.append({"type": "Feature", "geometry": _poly(gx, gy), "properties": {
            "kind": "reveal_cell", "gx": gx, "gy": gy, "lat": round(float(lat), 4), "lon": round(float(lon), 4),
            "observed_first_day": int(first_obs[x, y]) or None,
            "classes": {m: {"strict": [CLASS_NAMES[v] for v in z["strict"]],
                            "tolerant": [CLASS_NAMES[v] for v in z["tolerant"]]} for m, z in c.items()}}})
    start = [{"type": "Feature", "geometry": _poly(cx - W + int(x), cy - W + int(y)),
              "properties": {"kind": "burning_at_init", "day": 0}} for x, y in zip(*np.nonzero(A0 > 0))]
    return {
        "revealed_through_day": through, "archive_last_day_utc": str(EPOCH + np.timedelta64(day_max, "D")),
        "observability_scoring_version": OBSERVABILITY_SCORING_VERSION,
        "per_day": per_day, "data_availability": avail,
        "geojson": {"type": "FeatureCollection", "features": feats},
        "burning_at_init": {"type": "FeatureCollection", "features": start},
        "observation_quality": _public(quality) if quality else {"available": False,
                                                                  "note": "not computed"},
        "class_definitions": {
            "hit": "predicted and observed new burning cell (strict, same 0.05 deg cell)",
            "miss": "observed new burning cell that was not predicted",
            "false_alarm": "predicted cell with no observed burning, in a place/day with a usable overpass proxy",
            "unverifiable": "predicted cell with no observed burning, but no clear overpass proxy (cloud) so absence "
                            "of detection is not evidence of no fire",
            "near_hit": "tolerant view only: not a strict hit but matched with an observed/predicted cell within 1 "
                        "cell (~5.5 km)"},
        "legend_colours_colourblind_safe": PALETTE,
        "truth_definition": ("cumulative cells NOT burning on the init day, detected by VIIRS (NOAA-20/21/S-NPP) on a "
                             "later day <= k and within k cells of the burning area (same as walk_forward case_truth)"),
        "_truth": truth, "_dist": dist, "_A0": A0}


LIMITATIONS = [
    "Physics uses ERA5 reanalysis weather for the forecast period (perfect-weather replay): physics skill is "
    "optimistic vs a real-time forecast that only has weather forecasts. The ML component uses only day-of-init weather.",
    "Fire observations after the end of the init UTC day are removed from the forecaster's input (as-of index); "
    "forecast models were trained on years before the incident year (walk-forward folds).",
    "'Truth' is satellite detection (VIIRS 375 m, 0.05 deg cells), not burned perimeter: absence of detection is not "
    "proof of no fire (clouds, smoke, overpass gaps, fire below detection threshold).",
    "The cloud/overpass caveat is a proxy from ERA5 modelled cloud cover at nominal local overpass times; actual "
    "overpass schedules and swath coverage are not reconstructed. Unverifiable cells are reported separately.",
    "Scores come from single events, not a benchmark: the 100-fire/year walk-forward in forecast.py is the "
    "statistically meaningful measure. Curated incidents were chosen for diversity, so they are illustrative.",
    "Probabilities are uncalibrated and the physics burned fraction is not an ignition probability.",
    "2023 and earlier incidents can only be replayed ML-only (no ML+physics blender exists for them).",
]


def _strip(d):
    return {k: v for k, v in d.items() if not k.startswith("_")}


# ============================================================== orchestration
def run_replay(lat, lon, date, days=3, objective="iou", method="auto", incident=None, *, full=None,
               predict_fn=None, physics_fn=None, quality_fn=None, with_quality=True) -> dict:
    from . import ensemble
    if not 1 <= days <= 3 or objective not in ("iou", "tol") or method not in ("auto", "ml_physics", "ml"):
        raise ValueError("days must be 1..3; objective iou|tol; method auto|ml_physics|ml")
    t = pd.Timestamp(date)
    date = t.date().isoformat()
    day = int((t.to_datetime64().astype("datetime64[D]") - EPOCH).astype(int))
    full = ensemble.archive_index() if full is None else full
    last = int(full.v.day.max())
    if day >= last:
        raise NoObservations(f"The archive ends {EPOCH + np.timedelta64(last, 'D')}; at least one later observed "
                             "day is needed to replay (use /predict for real forecasts)")
    t_start = time.perf_counter()
    gx, gy = int(np.floor((lon + 180) / GRID)), int(np.floor((lat + 90) / GRID))
    with _RUN_LOCK:
        asof = asof_index(full, day, gx, gy)
        issued = issue_forecast(lat, lon, date, days, objective, method, asof, predict_fn=predict_fn,
                                physics_fn=physics_fn)
        t_issue = time.perf_counter() - t_start
        quality = None
        if with_quality:
            try:
                quality = (quality_fn or observation_quality)(issued["clat"], issued["clon"], t.date(), days,
                                                              issued["cx"], issued["cy"])
            except Exception as e:  # never fail the replay for the caveat layer
                quality = {"available": False, "error": f"{type(e).__name__}: {e}"[:200]}
        reveal = build_reveal(full, issued, days, quality, date)
    meta_ml = issued["model"]
    method_txt = {
        "ml_physics": "XGBoost direct multi-horizon ML (equifire.direct) blended with Rothermel/minimum-travel-time "
                      "physics (backend.ensemble.predict); 'ml' rows score the same ML component alone.",
        "ml": "XGBoost direct multi-horizon ML only (equifire.direct, walk-forward model wf_<year>)."}[issued["method"]]
    issue_cutoff = f"{date}T23:59:59Z"
    out = {
        "schema": SCHEMA, "kind": "historical_replay", "incident": incident,
        "query": {"lat": lat, "lon": lon, "date": date, "days": days, "objective": objective, "method": method},
        "init": {"date": date, "information_cutoff_utc": issue_cutoff,
                 "semantics": "forecast uses fire detections through the end of this UTC day and nothing later"},
        "centre": {"lat": issued["clat"], "lon": issued["clon"], "gx": issued["cx"], "gy": issued["cy"]},
        "method": {"used": issued["method"], "description": method_txt, "model": meta_ml,
                   "objective": objective, "grid_degrees": GRID,
                   "scoring": "cumulative cells newly burning by day k inside the 41x41-cell window; strict = same "
                              "0.05 deg cell; tolerant = within 1 cell (~5.5 km), same as walk_forward._tol",
                   "physics": issued["physics"]},
        "leakage_audit": {
            "fire_observations": f"as-of index: all cell-days after {date} removed before forecasting "
                                 f"(max day in forecaster input = {date})",
            "models": f"trained on years < {t.year} (walk-forward fold)",
            "weather": ("physics uses ERA5 reanalysis for the forecast period (perfect-weather); ML uses init-day "
                        "weather only" if issued["method"] == "ml_physics" else
                        "ML uses init-day weather only; no physics in this variant"),
            "truth_used_in_forecast": False, "reveal_computed_after_forecast": True},
        "issued": {"geojson": issued["geojson"], "cells_total": issued["all_cells"],
                   "min_probability_kept": MIN_PROB_OUT,
                   "per_day_selected": {m: [int(issued["sel"][m][k].sum()) for k in range(days)]
                                        for m in ("comb", "ml") if issued["sel"][m] is not None},
                   "hourly_simulation_url": issued["sim_url"],
                   "hourly_simulation_note": "same machinery run from the init time; recomputed live, uses the "
                                             "full archive and reanalysis weather" if issued["sim_url"] else None,
                   "warnings": issued["warnings"], "methods_available": [
                       m for m, ok in (("ml_physics", issued["sel"]["comb"] is not None), ("ml", True)) if ok]},
        "reveal": _strip(build_reveal_public(reveal)),
        "limitations": LIMITATIONS,
        "generated_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "timings_s": {"issue": round(t_issue, 2), "total": round(time.perf_counter() - t_start, 2)},
        "cached": False,
    }
    return out


def build_reveal_public(r):
    return {k: v for k, v in r.items() if not k.startswith("_")}


def summarize(res: dict) -> dict:
    """Compact scores for listings: per method, per day: strict IoU / within-5 km."""
    out = {}
    for row in res["reveal"]["per_day"]:
        if not row["scored"]:
            continue
        for m, s in row["methods"].items():
            out.setdefault(m, []).append({"day": row["day"], "strict_iou": s["strict"]["iou"],
                                          "strict_precision": s["strict"]["precision"],
                                          "strict_recall": s["strict"]["recall"],
                                          "within_5km": s["tolerant"]["within_5km_score"],
                                          "observed_new_cells": row["observed_new_cells"]})
    return out


def add_cached_observability(res):
    """Augment old saved results in memory, without changing their forecast or disk file.

    Public reveal classes retain every selected prediction and observed cell, so
    they reconstruct the scoring sets. Old caches saved only aggregate cloud
    statistics: recover a spatial mask ONLY from a matching existing raw ERA5
    HTTP cache, or report it unavailable. No network, model or archive load here.
    """
    reveal = res["reveal"]
    if reveal.get("observability_scoring_version") == OBSERVABILITY_SCORING_VERSION:
        return res
    rows = reveal.get("per_day", [])
    days = len(rows)
    quality = reveal.get("observation_quality") or {}
    observable, unknown = _decode_observation_masks(quality, days)
    if observable is None and quality.get("available"):
        c = res.get("centre") or {}
        try:
            recovered = observation_quality(c["lat"], c["lon"], dt.date.fromisoformat(res["init"]["date"]),
                                            days, c["gx"], c["gy"], fetch=_cached_cloud_hourly)
            if recovered.get("available"):
                observable, unknown = recovered.get("_observable"), recovered.get("_unknown")
                quality = {**quality, "spatial_mask": recovered["spatial_mask"],
                           "spatial_mask_recovery": "existing local raw ERA5 HTTP cache; no new request"}
                reveal["observation_quality"] = quality
        except (KeyError, ValueError, TypeError):
            pass
    cx, cy = res["centre"]["gx"], res["centre"]["gy"]
    truth = np.zeros((days, S, S), bool)
    predictions = {name: np.zeros_like(truth) for row in rows for name in row.get("methods", {}) if name != "baseline_ring"}
    for feature in (reveal.get("geojson") or {}).get("features", []):
        p = feature.get("properties") or {}
        x, y = p.get("gx", -1) - (cx - W), p.get("gy", -1) - (cy - W)
        if not (0 <= x < S and 0 <= y < S):
            continue
        first = p.get("observed_first_day")
        if first and 1 <= first <= days:
            truth[first - 1:, x, y] = True
        for name, classes in p.get("classes", {}).items():
            if name not in predictions:
                continue
            for k, cls in enumerate(classes.get("strict", [])[:days]):
                predictions[name][k, x, y] = cls in ("hit", "false_alarm", "unverifiable")
    initial = np.zeros((S, S), bool)
    for feature in (reveal.get("burning_at_init") or {}).get("features", []):
        coords = feature["geometry"]["coordinates"][0]
        gx = int(round((min(p[0] for p in coords) + 180) / GRID))
        gy = int(round((min(p[1] for p in coords) + 90) / GRID))
        x, y = gx - (cx - W), gy - (cy - W)
        if 0 <= x < S and 0 <= y < S:
            initial[x, y] = True
    dist = ndimage.distance_transform_cdt(~initial, metric="chessboard").astype(np.float32)
    for k, row in enumerate(rows):
        if not row.get("scored"):
            continue
        eligible = _eligible(dist, k + 1)
        for name, score in row.get("methods", {}).items():
            p = eligible if name == "baseline_ring" else predictions.get(name, np.zeros_like(truth))[k]
            # Refuse conditional numbers if a damaged/old schema cannot exactly
            # reproduce the unchanged full scores. Never silently reinterpret it.
            matches = (strict_counts(p, truth[k]) == score.get("strict") and
                       tolerant_counts(p, truth[k]) == score.get("tolerant"))
            obs = observable[k] if matches and observable is not None else None
            unk = unknown[k] if matches and unknown is not None else None
            score["observable"] = observable_scores(p, truth[k], obs, unknown=unk, eligible=eligible)
            if not matches:
                score["observable"]["reason"] = "Saved cell sets do not reproduce full scores; conditional score unavailable."
    reveal["observability_scoring_version"] = OBSERVABILITY_SCORING_VERSION
    return res


def get_replay(*, incident=None, lat=None, lon=None, date=None, days=3, objective="iou", method="auto",
               cached_only=False, refresh=False, **kw) -> dict:
    inc = None
    if incident:
        inc = get_incident(incident)
        lat, lon, date, days = inc["lat"], inc["lon"], inc["date"], inc.get("days", days)
        key = inc["id"]
    else:
        if lat is None or lon is None or not date:
            raise ValueError("give incident=<id> or lat, lon and date")
        key = adhoc_key(lat, lon, date, days)
    path = cache_path(key, objective)
    if path.exists() and not refresh:
        res = json.loads(path.read_text())
        add_cached_observability(res)
        res["cached"] = True
        return res
    if cached_only:
        raise FileNotFoundError(f"no saved replay for {key!r} objective={objective}; run without cached_only or "
                                "`python -m backend.replay precompute`")
    res = run_replay(lat, lon, date, days, objective, method, incident=inc, **kw)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(res, separators=(",", ":")))
    tmp.replace(path)
    return res


# ============================================================== availability check for curated incidents
def check_availability(inc: dict, full=None, days=3) -> dict:
    from . import ensemble
    full = ensemble.archive_index() if full is None else full
    t = pd.Timestamp(inc["date"])
    day = int((t.to_datetime64().astype("datetime64[D]") - EPOCH).astype(int))
    gx, gy = int(np.floor((inc["lon"] + 180) / GRID)), int(np.floor((inc["lat"] + 90) / GRID))
    try:
        cx, cy = ensemble.nearest_fire(full, day, gx, gy)
    except ensemble.NoIgnition as e:
        return {"ok": False, "reason": str(e)}
    A, _ = full.cube(day, cx, cy)
    A0 = A[HIST]
    dist = ndimage.distance_transform_cdt(A0 == 0, metric="chessboard").astype(np.float32)
    truth = direct.case_truth(A, dist, A0, days)
    after = [int((A[HIST + k] > 0).sum()) for k in range(1, days + 1)]
    try:
        ensemble.load_bundle(str(MODELS), t.year)
        mode = "ml_physics"
    except ensemble.PredictorUnavailable:
        mode = "ml" if (MODELS / f"wf_{t.year}.json").exists() else "none"
    ok = bool(A0.sum() > 0 and all(a > 0 for a in after) and truth[-1].sum() > 0)
    return {"ok": ok, "init_day_burning_cells": int(A0.sum()), "viirs_cells_active_days_after": after,
            "observed_new_cells_cumulative": [int(m.sum()) for m in truth],
            "archive_last_day_utc": str(EPOCH + np.timedelta64(int(full.v.day.max()), "D")),
            "centre": {"lat": round(float(cell_center(cx, cy)[0]), 4), "lon": round(float(cell_center(cx, cy)[1]), 4)},
            "replay_mode_available": mode,
            "note": "init day present, >= 3 following days with VIIRS cell-days in the window, observed new cells > 0"}


def list_incidents() -> dict:
    out = []
    for inc in load_incidents():
        row = dict(inc)
        saved = {}
        for obj in ("iou", "tol"):
            p = cache_path(inc["id"], obj)
            if p.exists():
                try:
                    r = json.loads(p.read_text())
                    saved[obj] = {"generated_utc": r.get("generated_utc"), "method_used": r["method"]["used"],
                                  "scores": summarize(r)}
                except Exception:
                    saved[obj] = {"error": "unreadable cache file"}
        row["saved_replays"] = saved
        out.append(row)
    return {"schema": SCHEMA, "incidents": out, "legend_colours_colourblind_safe": PALETTE,
            "notes": ["ML+physics replays need an incident year >= 2024 (blender fitted on the previous year); "
                      "earlier years are ML-only.",
                      "Pass cached_only=1 to /replay/run for instant offline results from cache/replay/."]}


# ============================================================== FastAPI router
def make_router():
    from fastapi import APIRouter, HTTPException, Query
    r = APIRouter(prefix="/replay", tags=["replay"])

    @r.get("/incidents")
    def incidents() -> dict:
        """Curated demonstration incidents with data-availability checks and saved-replay scores."""
        return list_incidents()

    @r.get("/run")
    def run(incident: str | None = Query(None, description="curated incident id (GET /replay/incidents)"),
            lat: float | None = Query(None, ge=-90, le=90), lon: float | None = Query(None, ge=-180, le=180),
            date: str | None = Query(None, description="YYYY-MM-DD (UTC): init day; information through its end"),
            days: int = Query(3, ge=1, le=3),
            objective: str = Query("iou", description="iou = exact cells; tol = one-cell tolerance rule"),
            method: str = Query("auto", description="auto | ml_physics | ml"),
            cached_only: bool = Query(False, description="serve only the saved replay (offline demo)"),
            refresh: bool = Query(False, description="recompute and overwrite the saved replay")) -> dict:
        from . import ensemble
        try:
            return get_replay(incident=incident, lat=lat, lon=lon, date=date, days=days, objective=objective,
                              method=method, cached_only=cached_only, refresh=refresh)
        except KeyError as e:
            raise HTTPException(404, str(e.args[0])) from None
        except FileNotFoundError as e:
            raise HTTPException(404, str(e)) from None
        except (ensemble.NoIgnition, NoObservations) as e:
            raise HTTPException(404, str(e)) from None
        except ensemble.PredictorUnavailable as e:
            raise HTTPException(503, str(e)) from None
        except ValueError as e:
            raise HTTPException(400, str(e)) from None

    return r


try:  # importable without fastapi for scripts/tests
    router = make_router()
except ImportError:  # pragma: no cover
    router = None


# ============================================================== CLI
def precompute(ids=None, objectives=("iou", "tol"), refresh=True, days=3):
    from . import ensemble
    full = ensemble.archive_index()
    rows = []
    for inc in load_incidents():
        if ids and inc["id"] not in ids:
            continue
        for obj in objectives:
            t = time.time()
            try:
                res = get_replay(incident=inc["id"], objective=obj, refresh=refresh)
                rows.append((inc["id"], obj, res["method"]["used"], summarize(res), time.time() - t))
                print(f"[replay] {inc['id']} {obj}: {res['method']['used']} in {time.time() - t:.0f}s", flush=True)
            except Exception as e:
                print(f"[replay] {inc['id']} {obj}: FAILED {type(e).__name__}: {e}", flush=True)
    return rows


def update_availability():
    from . import ensemble
    data = json.loads(INCIDENTS_FILE.read_text())
    full = ensemble.archive_index()
    for inc in data["incidents"]:
        inc["availability"] = check_availability(inc, full, inc.get("days", 3))
    INCIDENTS_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    return data


def observability_report():
    """Offline audit of already saved forecasts; curated/duplicate cases are not a benchmark."""
    started = time.perf_counter()
    cases = []
    for path in sorted(CACHE_DIR.glob("*.json")):
        raw = path.read_bytes()
        saved = json.loads(raw)
        original_issued = json.dumps(saved.get("issued"), sort_keys=True)
        full_scores = {row["day"]: {name: {key: value[key] for key in ("strict", "tolerant", "adjusted")}
                                   for name, value in row.get("methods", {}).items()}
                       for row in saved["reveal"]["per_day"]}
        add_cached_observability(saved)
        if json.dumps(saved.get("issued"), sort_keys=True) != original_issued or path.read_bytes() != raw:
            raise AssertionError("offline scoring must not alter issued forecasts or cache files")
        rows = []
        for row in saved["reveal"]["per_day"]:
            for name, value in row.get("methods", {}).items():
                if {key: value[key] for key in ("strict", "tolerant", "adjusted")} != full_scores[row["day"]][name]:
                    raise AssertionError("offline scoring must not alter full/legacy scores")
            rows.append({"day": row["day"], "date": row["date"], "scored": row["scored"],
                         "methods": row.get("methods", {})})
        cases.append({"file": path.name, "sha256": hashlib.sha256(raw).hexdigest(),
                      "query": saved["query"], "methods": saved["issued"].get("methods_available"), "per_day": rows})
    return {"version": OBSERVABILITY_SCORING_VERSION, "generated_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
            "protocol": "Fixed80% ERA5 cloud-cover proxy threshold at nominal01:30/13:30 local solar passes; "
                        "same cumulative mask for prediction/truth/ring. No outcome-conditioned exception. "
                        "Unknown and obscured excluded symmetrically before tolerant matching.",
            "source": "saved replay masks + existing raw local ERA5 HTTP cache, no network or new forecast",
            "limits": ["Curated/adhoc single-event diagnostics, not an independent representative benchmark.",
                       "IOU/tol objective files can repeat the same incident; do not treat them as independent events.",
                       "Modelled cloud proxy does not reconstruct real swath, scan angle, smoke or detection sensitivity.",
                       "Full and conditional scores are both shown; selecting clearer cells may improve or worsen scores.",
                       "A conditional score is absent when no spatial mask or no observable eligible cell is available."],
            "cache_files": len(cases), "elapsed_s": round(time.perf_counter() - started, 4), "cases": cases}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cmd", choices=["precompute", "availability", "scores", "observability"])
    ap.add_argument("--ids", nargs="*")
    ap.add_argument("--objectives", nargs="*", default=["iou", "tol"])
    ap.add_argument("--output", type=Path, default=ROOT / "reports" / "observability_scoring.json")
    a = ap.parse_args()
    if a.cmd == "availability":
        for i in update_availability()["incidents"]:
            print(i["id"], json.dumps(i["availability"]))
    elif a.cmd == "precompute":
        for r in precompute(a.ids, tuple(a.objectives)):
            print(json.dumps(r[:4]))
    elif a.cmd == "observability":
        report = observability_report()
        a.output.parent.mkdir(parents=True, exist_ok=True)
        a.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
        print(f"[observability] {report['cache_files']} cached files in {report['elapsed_s']}s -> {a.output}")
    else:
        print(json.dumps(list_incidents()["incidents"][0].get("saved_replays"), indent=1))
