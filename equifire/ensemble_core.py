"""Shared inference features for the ML + physics blender.

Used by the evaluator and serving endpoint so feature order and transforms agree.
Physics fractions describe burned raster area; they are not calibrated probabilities.
"""
import numpy as np
from scipy import ndimage
from .direct import S
from .physcells import FUELS

FEATS = ["ml_logit", "k", "ph_c", "ph_lo", "ph_hi", "ph_c_nb", "ph_hi_nb", "ph_arr_rel", "ph_cover", "dist",
         "log_m", "cal_applied", "cal_conf", "fuel_grass", "fuel_savanna", "fuel_shrub", "fuel_forest",
         "ph_fire_k", "ml_fire_k", "wx_fallback"]
CONF = {"none": 0, "low": 1, "medium": 2, "high": 3}

def _nbmax(a):
    return ndimage.maximum_filter(a, size=3, mode="constant")


def case_rows(P, dist, pc, days):
    """Blender feature rows for every eligible cell (finite ML prob) and horizon. Returns X, idx (k, flat)."""
    meta = pc["meta"]
    fuel = meta.get("fuel") or "none"
    lm = np.log(max(float(meta.get("m", 1.0)), 1e-3))
    Xs, ids = [], []
    for k in range(1, days + 1):
        p = P[k - 1]
        e = np.flatnonzero(np.isfinite(p.ravel()))
        if not len(e):
            continue
        fc, flo, fhi = (pc["frac"][v, k - 1] for v in (1, 0, 2))
        arr = pc["arr_h"]
        rel = np.where(np.isfinite(arr), np.minimum(arr / (24 * k + 12), 5.0), 5.0)
        pk = np.clip(p.ravel()[e], 1e-5, 1 - 1e-5)
        cols = {
            "ml_logit": np.log(pk / (1 - pk)), "k": np.full(len(e), k), "ph_c": fc.ravel()[e], "ph_lo": flo.ravel()[e],
            "ph_hi": fhi.ravel()[e], "ph_c_nb": _nbmax(fc).ravel()[e], "ph_hi_nb": _nbmax(fhi).ravel()[e],
            "ph_arr_rel": rel.ravel()[e], "ph_cover": pc["cover"].ravel()[e], "dist": dist.ravel()[e],
            "log_m": np.full(len(e), lm), "cal_applied": np.full(len(e), float(meta.get("cal_applied", False))),
            "cal_conf": np.full(len(e), CONF.get(meta.get("cal_confidence", "none"), 0)),
            **{f"fuel_{f}": np.full(len(e), float(fuel == f)) for f in FUELS},
            "ph_fire_k": np.full(len(e), np.log1p(float(fc.sum()))),
            "ml_fire_k": np.full(len(e), np.log1p(float(np.nansum(p)))),
            "wx_fallback": np.full(len(e), float(meta.get("weather_fallback", False))),
        }
        Xs.append(np.column_stack([cols[c] for c in FEATS]).astype(np.float32))
        ids.append(np.c_[np.full(len(e), k), e])
    if not Xs:
        return np.zeros((0, len(FEATS)), np.float32), np.zeros((0, 2), np.int64)
    return np.concatenate(Xs), np.concatenate(ids)


def blend_predict(m, rec, days):
    from equifire.direct import S
    P = np.full((days, S * S), np.nan, np.float32)
    if len(rec["X"]):
        p = m.predict_proba(rec["X"])[:, 1]
        P[rec["ids"][:, 0] - 1, rec["ids"][:, 1]] = p
    return P.reshape(days, S, S)


