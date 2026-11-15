"""MODIS -> VIIRS-equivalent reconstruction of the pre-VIIRS archive (2000 - 2012-01-19).

VIIRS (375 m, 2012+) sees many more / smaller fires than MODIS (1 km, 2000+). To make the old
archive comparable, a cell-day model learned on the overlap years (both sensors fly) answers, from
MODIS information alone:
  (a) would VIIRS have detected fire in this 0.05 deg cell on day d?       (classifier, p_viirs)
  (b) if so, how many VIIRS detections, how much FRP, what footprint area? (regressors, log1p)

Candidate cells for day d: every cell within R_CAND cells (Chebyshev) of a MODIS-active cell on
day d, d-1 or d+1 (VIIRS often sees fires MODIS just misses next to MODIS fires). VIIRS cells far
from any MODIS fire cannot be reconstructed; they count as misses in every metric.

Features (MODIS only, Terra "T" + Aqua "A"): the cell's own day-d detections (count per satellite,
FRP sum / max, footprint, scan / track, brightness, confidence, day / night), neighbourhood counts /
detections / FRP in 3x3, 5x5, 9x9, the cell's MODIS activity on d-3..d-1 and d+1 (a reconstruction
of the past may look one day ahead), neighbourhood activity on d-1 / d+1, burn history of the cell
(MODIS fire days in the last 30 / 365 days, days since the last one), lat / lon, day of year, and
`viirs_sats` = number of VIIRS satellites to emulate (1 = S-NPP only, 2 = S-NPP + NOAA-20). In
overlap years >= 2018 every candidate is used twice: S-NPP-only truth with viirs_sats=1 and all-VIIRS
truth with viirs_sats=2, so the model learns both. Pre-2012 output is built with viirs_sats=1,
which matches the real record it is joined to (S-NPP only until 2018-04).

Truth is VIIRS only. Honest evaluation: train years TRAIN_YEARS, validation year VAL_YEARS (early
stopping + threshold), held-out test years TEST_YEARS, never seen during fitting or tuning.

    .venv/bin/python harmonize.py train      # -> models/harmonize_{cls,n,frp,area}.json, models/harmonize_meta.json
    .venv/bin/python harmonize.py calibrate  # ratio-of-totals correction of n / FRP / area (validation year)
    .venv/bin/python harmonize.py eval       # held-out metrics vs baselines -> models/harmonize_metrics.json
    .venv/bin/python harmonize.py apply      # -> cache/viirs_equiv_daily.parquet

    from equifire.harmonize import load_harmonized_daily
    act = load_harmonized_daily(2008)        # reconstructed before 2012-01-20, real VIIRS after
"""
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from .common import CACHE, DATA_DIRS, EPOCH, GRID, MODELS, NY, SAT_NORMALIZE, cell_center, cell_key, \
    day_of_year_features, decode_key

CELLS = CACHE / "cells"
OUT = CACHE / "viirs_equiv_daily.parquet"
META_F = MODELS / "harmonize_meta.json"
METRICS_F = MODELS / "harmonize_metrics.json"
MODEL_FILES = {t: MODELS / f"harmonize_{t}.json" for t in ("cls", "n", "frp", "area")}
VIIRS_START = int((np.datetime64("2012-01-20") - EPOCH).astype(int))   # first S-NPP FIRMS day
N20_START = int((np.datetime64("2018-04-01") - EPOCH).astype(int))     # first NOAA-20 FIRMS day
TRAIN_YEARS = list(range(2012, 2020))
VAL_YEARS = [2020]
TEST_YEARS = list(range(2021, 2026))
R_CAND = 2
_DK = 1 << 14  # day slots in the cell-major key (days since 2000 < 16384)

FEATURES = ["m_n", "m_nT", "m_nA", "m_frp", "m_frpmax", "m_area", "m_scan", "m_track", "m_bright", "m_conf",
            "m_nday", "m_nnight", "dist",
            "b1_act", "b1_n", "b1_frp", "b2_act", "b2_n", "b2_frp", "b4_act", "b4_n", "b4_frp",
            "own_l1", "own_l2", "own_l3", "own_p1", "b1_l1", "b2_l1", "b1_p1", "b2_p1", "b2_l13",
            "h30", "h365", "since_last", "lat", "lon", "doy_sin", "doy_cos", "viirs_sats"]


# ----------------------------------------------------------------------------- data access
def _sensor_files(prefix, year):
    return sorted(CELLS.glob(f"{prefix}_{year}_*.parquet"))


def _aggregate_csv_mem(csv):
    """Same aggregation as collect.aggregate_file, in memory (does not write cache/cells)."""
    from .collect import VIIRS_CONF
    df = pd.read_csv(csv, dtype={"acq_time": str, "satellite": str, "confidence": str, "daynight": str})
    if "type" in df:
        df = df[df["type"] == 0]
    viirs = "bright_ti4" in df
    conf = df["confidence"].str.lower().map(VIIRS_CONF) if viirs else \
        pd.to_numeric(df["confidence"], errors="coerce") / 100
    t = df["acq_time"].str.zfill(4)
    out = pd.DataFrame({
        "day": (pd.to_datetime(df["acq_date"]).values.astype("datetime64[D]") - EPOCH).astype(np.int32),
        "gx": np.floor((df["longitude"].values + 180) / GRID).astype(np.int32),
        "gy": np.floor((df["latitude"].values + 90) / GRID).astype(np.int32),
        "sat": df["satellite"].map(SAT_NORMALIZE).fillna("?").values,
        "dn": (df["daynight"] == "D").astype(np.int8).values,
        "frp": df["frp"].astype(np.float32).values, "area": (df["scan"] * df["track"]).astype(np.float32).values,
        "scan": df["scan"].astype(np.float32).values, "track": df["track"].astype(np.float32).values,
        "bright": (df["bright_ti4"] if viirs else df["brightness"]).astype(np.float32).values,
        "conf": conf.astype(np.float32).values,
        "minute": (t.str[:2].astype(int) * 60 + t.str[2:].astype(int)).astype(np.float32).values,
    })
    g = out.groupby(["day", "gx", "gy", "sat", "dn"], sort=False).agg(
        n=("frp", "size"), frp_sum=("frp", "sum"), frp_max=("frp", "max"), area_sum=("area", "sum"),
        scan_mean=("scan", "mean"), track_mean=("track", "mean"), bright_max=("bright", "max"),
        conf_mean=("conf", "mean"), minute=("minute", "mean")).reset_index()
    return g


def read_cells(prefix, years, countries=None):
    """Cell observations of one sensor prefix ('modis', 'viirs-snpp', 'viirs-jpss1') for `years`.
    Reads cache/cells/<prefix>_<year>_<country>.parquet; MODIS CSVs that are downloaded but not yet
    collected are aggregated in memory."""
    parts = []
    for y in years:
        files = {p.stem: p for p in _sensor_files(prefix, y)}
        if prefix == "modis":
            for d in DATA_DIRS:
                for csv in (d / prefix / str(y)).glob("*.csv") if (d / prefix / str(y)).exists() else []:
                    if csv.stem not in files or files[csv.stem].stat().st_mtime < csv.stat().st_mtime:
                        files[csv.stem] = csv
        for stem, p in sorted(files.items()):
            if countries and not any(stem.endswith("_" + c) for c in countries):
                continue
            parts.append(_aggregate_csv_mem(p) if p.suffix == ".csv" else pq.read_table(p).to_pandas())
    if not parts:
        return None
    df = pd.concat(parts, ignore_index=True)
    # same detection in two neighbouring countries' files
    return df.drop_duplicates(["day", "gx", "gy", "sat", "dn", "minute"], ignore_index=True)


def viirs_countries(years):
    """Countries whose VIIRS (S-NPP) cell files are cached for every one of `years`: the region where
    VIIRS truth exists. MODIS CSVs of other countries may already be downloaded; evaluating there would
    count every reconstructed cell as a false positive."""
    sets = [{p.stem.split(f"_{y}_", 1)[1] for p in _sensor_files("viirs-snpp", y)} for y in years]
    return sorted(set.intersection(*sets)) if sets else []


def modis_daily(c):
    """Per (day, cell) MODIS stats, sorted by cell_key."""
    c = c[c["sat"].isin(["T", "A"])]
    k = cell_key(c["day"].values, c["gx"].values, c["gy"].values)
    u, inv = np.unique(k, return_inverse=True)
    n = c["n"].values.astype(np.float64)
    bc = lambda w: np.bincount(inv, w, len(u))
    nn = bc(n)
    out = {"key": u, "n": nn, "nT": bc(n * (c["sat"].values == "T")), "nA": bc(n * (c["sat"].values == "A")),
           "frp": bc(c["frp_sum"].values), "area": bc(c["area_sum"].values),
           "scan": bc(c["scan_mean"].values * n) / nn, "track": bc(c["track_mean"].values * n) / nn,
           "conf": bc(c["conf_mean"].values * n) / nn,
           "nday": bc(n * c["dn"].values), "nnight": bc(n * (1 - c["dn"].values))}
    fm = np.zeros(len(u))
    np.maximum.at(fm, inv, c["frp_max"].values.astype(np.float64))
    bm = np.zeros(len(u))
    np.maximum.at(bm, inv, c["bright_max"].values.astype(np.float64))
    out["frpmax"], out["bright"] = fm, bm
    return out


def viirs_truth(years, countries=None, snpp_only=False):
    """VIIRS burning cell-days: sorted keys and (n, frp, area) sums over all passes of the day."""
    parts = [read_cells("viirs-snpp", years, countries)]
    if not snpp_only:
        parts.append(read_cells("viirs-jpss1", years, countries))
    parts = [p for p in parts if p is not None]
    if not parts:
        return np.zeros(0, np.int64), {}
    c = pd.concat(parts, ignore_index=True)
    k = cell_key(c["day"].values, c["gx"].values, c["gy"].values)
    u, inv = np.unique(k, return_inverse=True)
    return u, {"n": np.bincount(inv, c["n"].values, len(u)), "frp": np.bincount(inv, c["frp_sum"].values, len(u)),
               "area": np.bincount(inv, c["area_sum"].values, len(u))}


class Sparse:
    """Active cell-days with prefix sums: O(log n) box sums and per-cell day-range counts."""

    def __init__(self, md):
        self.k = md["key"]
        self.cs = {v: np.r_[0.0, np.cumsum(md[v])] for v in ("n", "frp")}
        self.cs["act"] = np.arange(len(self.k) + 1, dtype=np.float64)
        d, x, y = decode_key(self.k)
        ck = (x * NY + y) * _DK + d
        self.ck = np.sort(ck)

    def box(self, d, x, y, r):
        out = {v: np.zeros(len(d)) for v in self.cs}
        for dx in range(-r, r + 1):
            lo = np.searchsorted(self.k, cell_key(d, x + dx, y - r), "left")
            hi = np.searchsorted(self.k, cell_key(d, x + dx, y + r), "right")
            for v in out:
                out[v] += self.cs[v][hi] - self.cs[v][lo]
        return out

    def cell_days(self, d, x, y, a, b):
        """Number of active days of the cell in [d+a, d+b]."""
        base = (x.astype(np.int64) * NY + y) * _DK + d
        return (np.searchsorted(self.ck, base + b, "right") - np.searchsorted(self.ck, base + a, "left"))

    def since_last(self, d, x, y, cap=999):
        base = (x.astype(np.int64) * NY + y) * _DK + d
        i = np.searchsorted(self.ck, base - 1, "right") - 1  # last key <= d-1
        prev = self.ck[np.clip(i, 0, None)]
        ok = (i >= 0) & (prev // _DK == base // _DK)
        return np.where(ok, np.minimum(d - prev % _DK, cap), cap)


def candidates(md, d_lo, d_hi):
    """Candidate cell keys for days d_lo..d_hi: within R_CAND of MODIS fire on day d-1, d or d+1."""
    d, x, y = decode_key(md["key"])
    m = (d >= d_lo - 1) & (d <= d_hi + 1)
    d, x, y = d[m], x[m], y[m]
    ks = []
    for dd in (-1, 0, 1):
        for dx in range(-R_CAND, R_CAND + 1):
            for dy in range(-R_CAND, R_CAND + 1):
                ks.append(cell_key(d + dd, x + dx, y + dy))
    k = np.unique(np.concatenate(ks))
    kd = decode_key(k)[0]
    return k[(kd >= d_lo) & (kd <= d_hi)]


def features(keys, md, sp, viirs_sats):
    d, x, y = decode_key(keys)
    i = np.searchsorted(md["key"], keys)
    i = np.clip(i, 0, len(md["key"]) - 1)
    own = md["key"][i] == keys
    f = {}
    for name, v in (("n", "n"), ("nT", "nT"), ("nA", "nA"), ("frp", "frp"), ("frpmax", "frpmax"),
                    ("area", "area"), ("scan", "scan"), ("track", "track"), ("bright", "bright"),
                    ("conf", "conf"), ("nday", "nday"), ("nnight", "nnight")):
        f["m_" + name] = np.where(own, md[v][i], 0.0 if name not in ("scan", "track", "bright", "conf") else np.nan)
    b = {r: sp.box(d, x, y, r) for r in (1, 2, 4)}
    for r in (1, 2, 4):
        for v in ("act", "n", "frp"):
            f[f"b{r}_{v}"] = b[r][v]
    f["dist"] = np.select([own, b[1]["act"] > 0, b[2]["act"] > 0, b[4]["act"] > 0], [0, 1, 2, 3], 5)
    for lag in (1, 2, 3):
        f[f"own_l{lag}"] = sp.cell_days(d, x, y, -lag, -lag)
    f["own_p1"] = sp.cell_days(d, x, y, 1, 1)
    f["b1_l1"], f["b2_l1"] = sp.box(d - 1, x, y, 1)["act"], sp.box(d - 1, x, y, 2)["act"]
    f["b1_p1"], f["b2_p1"] = sp.box(d + 1, x, y, 1)["act"], sp.box(d + 1, x, y, 2)["act"]
    f["b2_l13"] = f["b2_l1"] + sp.box(d - 2, x, y, 2)["act"] + sp.box(d - 3, x, y, 2)["act"]
    f["h30"] = sp.cell_days(d, x, y, -30, -4)
    f["h365"] = sp.cell_days(d, x, y, -365, -4)
    f["since_last"] = sp.since_last(d, x, y)
    lat, lon = cell_center(x, y)
    f["lat"], f["lon"] = lat, lon
    f["doy_sin"], f["doy_cos"] = day_of_year_features(d)
    f["viirs_sats"] = np.broadcast_to(np.asarray(viirs_sats, np.float64), d.shape)
    return np.column_stack([np.asarray(f[c], np.float32) for c in FEATURES])


def _lookup(tk, tv, keys):
    i = np.clip(np.searchsorted(tk, keys), 0, max(len(tk) - 1, 0))
    hit = (tk[i] == keys) if len(tk) else np.zeros(len(keys), bool)
    return hit, {v: np.where(hit, tv[v][i], 0.0) for v in tv}


class YearData:
    """MODIS (years y-1..y+1, for history / d+1) and VIIRS truth of year y."""

    def __init__(self, year, countries=None, truth=True):
        t = time.time()
        c = read_cells("modis", [year - 1, year, year + 1], countries)
        self.year = year
        self.md = modis_daily(c) if c is not None else None
        del c
        self.sp = Sparse(self.md) if self.md is not None else None
        if truth:
            self.vk, self.vv = viirs_truth([year], countries)
            self.sk, self.sv = viirs_truth([year], countries, snpp_only=True)
        n = 0 if self.md is None else len(self.md["key"])
        print(f"[harmonize] {year}: {n:,} MODIS cell-days (y-1..y+1) loaded ({time.time() - t:.0f}s)", flush=True)

    def chunks(self, step=31):
        if self.md is None:
            return
        lo = int((np.datetime64(f"{self.year}-01-01") - EPOCH).astype(int))
        hi = int((np.datetime64(f"{self.year + 1}-01-01") - EPOCH).astype(int)) - 1
        for a in range(lo, hi + 1, step):
            b = min(a + step - 1, hi)
            yield a, b, candidates(self.md, a, b)


def sats_for(day):
    return np.where(np.asarray(day) >= N20_START, 2, 1)


# ----------------------------------------------------------------------------- training
def _rows(years, frac, rng, countries):
    Xs, ys, Rs, Ds = [], [], [], []
    for yr in years:
        yd = YearData(yr, countries)
        for a, b, k in yd.chunks():
            k = k[decode_key(k)[0] >= VIIRS_START]
            if frac < 1:
                k = k[rng.random(len(k)) < frac]
            if not len(k):
                continue
            kd = decode_key(k)[0]
            variants = [(yd.vk, yd.vv, sats_for(kd), np.ones(len(k), bool), False)]
            if (kd >= N20_START).any():  # also learn the S-NPP-only view of the N20 era
                variants.append((yd.sk, yd.sv, np.ones(len(k)), kd >= N20_START, True))
            for tk, tv, sats, sel, dup in variants:
                kk = k[sel]
                X = features(kk, yd.md, yd.sp, sats[sel])
                hit, v = _lookup(tk, tv, kk)
                Xs.append(X)
                ys.append(hit.astype(np.float32))
                Rs.append(np.column_stack([v["n"], v["frp"], v["area"]]).astype(np.float32))
                Ds.append(np.full(len(kk), dup))
        print(f"[harmonize] {yr}: rows so far {sum(map(len, ys)):,}", flush=True)
        del yd
    return np.concatenate(Xs), np.concatenate(ys), np.concatenate(Rs), np.concatenate(Ds)


def _xgb(kind, nthread):
    import xgboost as xgb
    common = dict(n_estimators=2000, tree_method="hist", learning_rate=0.08, max_depth=8, min_child_weight=5,
                  subsample=0.8, colsample_bytree=0.8, early_stopping_rounds=50, n_jobs=nthread, max_bin=256)
    if kind == "cls":
        return xgb.XGBClassifier(eval_metric="logloss", **common)
    return xgb.XGBRegressor(eval_metric="rmse", **common)


def train(frac=0.3, val_frac=0.3, countries=None, nthread=8, seed=0):
    t0 = time.time()
    rng = np.random.default_rng(seed)
    countries = countries or viirs_countries(TRAIN_YEARS + VAL_YEARS)
    Xt, yt, Rt, _ = _rows(TRAIN_YEARS, frac, rng, countries)
    Xv, yv, Rv, Dv = _rows(VAL_YEARS, val_frac, rng, countries)
    print(f"[harmonize] train rows {len(yt):,} ({yt.mean():.1%} VIIRS-positive), val {len(yv):,} | "
          f"{Xt.nbytes / 1e9:.2f} GB", flush=True)
    MODELS.mkdir(exist_ok=True)
    cls = _xgb("cls", nthread)
    cls.fit(Xt, yt, eval_set=[(Xv, yv)], verbose=200)
    cls.save_model(MODEL_FILES["cls"])
    from sklearn.metrics import roc_auc_score
    pv = cls.predict_proba(Xv)[:, 1]
    auc = float(roc_auc_score(yv, pv))
    print(f"[harmonize] classifier: val AUC {auc:.4f}, {cls.best_iteration + 1} trees", flush=True)
    reg_info = {}
    pt, pvm = yt > 0, yv > 0
    for j, t in enumerate(("n", "frp", "area")):
        r = _xgb("reg", nthread)
        r.fit(Xt[pt], np.log1p(Rt[pt, j]), eval_set=[(Xv[pvm], np.log1p(Rv[pvm, j]))], verbose=0)
        r.save_model(MODEL_FILES[t])
        reg_info[t] = int(r.best_iteration + 1)
        print(f"[harmonize] regressor {t}: {reg_info[t]} trees", flush=True)
    # linear-calibration baseline (MODIS cells where VIIRS also burns): VIIRS = a * MODIS + b
    both = pt & (Xt[:, FEATURES.index("m_n")] > 0) & (Xt[:, FEATURES.index("viirs_sats")] == 2)
    lin = {}
    for t, col, j in (("frp", "m_frp", 1), ("n", "m_n", 0), ("area", "m_area", 2)):
        a, b = np.polyfit(Xt[both, FEATURES.index(col)].astype(float), Rt[both, j].astype(float), 1)
        lin[t] = [float(a), float(b)]
    # threshold: maximise IoU on the validation year (all-VIIRS truth); candidates were sampled at val_frac,
    # VIIRS cell-days outside the candidate set are added (scaled) as misses
    vy = VAL_YEARS[0]
    vk, _ = viirs_truth([vy], countries)
    vk = vk[decode_key(vk)[0] >= VIIRS_START]
    sel_all = ~Dv  # all-VIIRS view only
    out_missed = max(len(vk) * val_frac - yv[sel_all].sum(), 0)
    thr_grid = np.round(np.arange(0.05, 0.951, 0.025), 3)
    stats = []
    for th in thr_grid:
        pr = pv[sel_all] >= th
        tp = float((pr & (yv[sel_all] > 0)).sum())
        fp = float((pr & (yv[sel_all] == 0)).sum())
        fn = float(((~pr) & (yv[sel_all] > 0)).sum()) + out_missed
        stats.append((th, tp / (tp + fp + fn), (tp + fp) / (tp + fn)))
    best = max(stats, key=lambda s: s[1])
    count_match = min(stats, key=lambda s: abs(s[2] - 1))
    print(f"[harmonize] threshold (max val IoU) {best[0]:.3f}: IoU {best[1]:.3f}, pred/true cells {best[2]:.2f}; "
          f"count-matching threshold would be {count_match[0]:.3f} (IoU {count_match[1]:.3f})", flush=True)
    meta = {"features": FEATURES, "train_years": TRAIN_YEARS, "val_years": VAL_YEARS, "test_years": TEST_YEARS,
            "r_cand": R_CAND, "train_frac": frac, "val_frac": val_frac, "countries": countries,
            "n_train_rows": int(len(yt)), "n_val_rows": int(len(yv)), "val_auc": auc,
            "cls_trees": int(cls.best_iteration + 1), "reg_trees": reg_info,
            "threshold": float(best[0]), "threshold_rule": "max IoU of VIIRS-active cell-days on validation year",
            "val_iou_at_threshold": float(best[1]), "val_count_ratio_at_threshold": float(best[2]),
            "count_matching_threshold": float(count_match[0]), "linear_baseline": lin,
            "threshold_curve": [[float(a), float(b), float(c)] for a, b, c in stats],
            "train_seconds": round(time.time() - t0)}
    META_F.write_text(json.dumps(meta, indent=1))
    print(f"[harmonize] saved models/harmonize_*.json ({time.time() - t0:.0f}s)")
    return meta


def load_models(nthread=8):
    import xgboost as xgb
    if not META_F.exists():
        raise SystemExit("no harmonize model; run: .venv/bin/python harmonize.py train")
    meta = json.loads(META_F.read_text())
    ms = {}
    for t, f in MODEL_FILES.items():
        m = xgb.XGBClassifier() if t == "cls" else xgb.XGBRegressor()
        m.load_model(f)
        bst = m.get_booster()
        bst.set_param({"nthread": nthread})
        ms[t] = bst
    return ms, meta


def predict(ms, X, scale=None):
    """p_viirs and expected VIIRS n / FRP / area given fire. `scale` (meta['reg_scale'], from calibrate())
    turns the log-space regression (median-like) into totals that match VIIRS on validation cells."""
    p = ms["cls"].inplace_predict(X)
    out = {"p": p}
    for t in ("n", "frp", "area"):
        out[t] = np.expm1(np.maximum(ms[t].inplace_predict(X), 0)) * (scale or {}).get(t, 1.0)
    return out


def calibrate(frac=0.05, countries=None, seed=3):
    """Ratio-of-totals correction for the log1p regressors (log-space fits underestimate sums), fitted on
    VIIRS-positive candidate cells of the validation year only. Stored as meta['reg_scale']."""
    ms, meta = load_models()
    countries = countries or viirs_countries(VAL_YEARS)
    X, y, R, D = _rows(VAL_YEARS, frac, np.random.default_rng(seed), countries)
    m = (y > 0) & ~D
    P = predict(ms, X[m])
    meta["reg_scale"] = {t: float(R[m, j].sum() / max(P[t].sum(), 1e-9)) for j, t in enumerate(("n", "frp", "area"))}
    META_F.write_text(json.dumps(meta, indent=1))
    print(f"[harmonize] regression scale (validation {VAL_YEARS}): {meta['reg_scale']}")
    return meta["reg_scale"]


# ----------------------------------------------------------------------------- evaluation
class _Acc:
    def __init__(self):
        self.tp = self.fp = self.fn = 0
        self.frp_p = self.frp_t = self.n_p = self.n_t = 0.0
        self.e_frp, self.e_n, self.e_area = [], [], []

    def add(self, pred, truth_hit, pf, pn, pa, tf, tn, ta):
        tp = pred & truth_hit
        self.tp += int(tp.sum())
        self.fp += int((pred & ~truth_hit).sum())
        self.fn += int((~pred & truth_hit).sum())
        self.frp_p += float(pf[pred].sum())
        self.n_p += float(pn[pred].sum())
        self.e_frp.append(np.c_[np.log1p(pf[tp]), np.log1p(tf[tp])])
        self.e_n.append(np.c_[np.log1p(pn[tp]), np.log1p(tn[tp])])
        self.e_area.append(np.c_[np.log1p(pa[tp] * 1e3), np.log1p(ta[tp] * 1e3)])

    def result(self, total_true, true_frp, true_n):
        fn = max(total_true - self.tp, 0)  # includes VIIRS cells outside the candidate set
        r = {"tp": self.tp, "fp": self.fp, "fn": int(fn),
             "precision": self.tp / max(self.tp + self.fp, 1), "recall": self.tp / max(total_true, 1),
             "iou": self.tp / max(self.tp + self.fp + fn, 1),
             "pred_cells_over_true": (self.tp + self.fp) / max(total_true, 1),
             "total_frp_ratio": self.frp_p / max(true_frp, 1e-9), "total_n_ratio": self.n_p / max(true_n, 1e-9)}
        for name, e in (("frp", self.e_frp), ("n", self.e_n), ("area", self.e_area)):
            e = np.concatenate(e) if e else np.zeros((0, 2))
            if len(e):
                d = e[:, 0] - e[:, 1]
                r[f"{name}_log1p_mae_on_tp"] = float(np.abs(d).mean())
                r[f"{name}_log1p_bias_on_tp"] = float(d.mean())
                r[f"{name}_corr_log_on_tp"] = float(np.corrcoef(e[:, 0], e[:, 1])[0, 1]) if len(e) > 2 else None
                r[f"{name}_median_ratio_on_tp"] = float(np.median(np.expm1(e[:, 0]) / np.maximum(np.expm1(e[:, 1]), 1e-9)))
        return r


def evaluate(years=None, countries=None, frac=0.25, seed=1):
    """Held-out metrics on a random `frac` of candidate cell-days (VIIRS totals scaled by frac, so
    precision / recall / IoU / ratios are unbiased estimates of the full-data values)."""
    ms, meta = load_models()
    rng = np.random.default_rng(seed)
    years = years or meta["test_years"]
    countries = countries or viirs_countries(years)
    print(f"[harmonize] evaluating on {', '.join(countries)}", flush=True)
    th, lin = meta["threshold"], meta["linear_baseline"]
    res = {"threshold": th, "years": years, "by_view": {}}
    views = ("all_viirs", "snpp_only")
    accs = {v: {m: _Acc() for m in ("model", "modis_raw", "modis_linear", "model_count_thr")} for v in views}
    tot = {v: {"cells": 0.0, "frp": 0.0, "n": 0.0} for v in views}
    for yr in years:
        yd = YearData(yr, countries)
        for view in views:
            tk, tv = (yd.vk, yd.vv) if view == "all_viirs" else (yd.sk, yd.sv)
            if len(tk):
                m = decode_key(tk)[0] >= VIIRS_START
                tot[view]["cells"] += m.sum() * frac
                tot[view]["frp"] += float(tv["frp"][m].sum()) * frac
                tot[view]["n"] += float(tv["n"][m].sum()) * frac
        for a, b, k in yd.chunks():
            k = k[decode_key(k)[0] >= VIIRS_START]
            k = k[rng.random(len(k)) < frac]
            kd = decode_key(k)[0]
            for view in views:
                tk, tv = (yd.vk, yd.vv) if view == "all_viirs" else (yd.sk, yd.sv)
                sats = sats_for(kd) if view == "all_viirs" else np.ones(len(k))
                X = features(k, yd.md, yd.sp, sats)
                P = predict(ms, X, meta.get("reg_scale"))
                hit, v = _lookup(tk, tv, k)
                mn, mf, ma = (X[:, FEATURES.index(c)].astype(float) for c in ("m_n", "m_frp", "m_area"))
                act = mn > 0
                A = accs[view]
                A["model"].add(P["p"] >= th, hit, P["frp"], P["n"], P["area"], v["frp"], v["n"], v["area"])
                A["model_count_thr"].add(P["p"] >= meta["count_matching_threshold"], hit, P["frp"], P["n"],
                                         P["area"], v["frp"], v["n"], v["area"])
                A["modis_raw"].add(act, hit, mf, mn, ma, v["frp"], v["n"], v["area"])
                lf = np.maximum(lin["frp"][0] * mf + lin["frp"][1], 0)
                ln = np.maximum(lin["n"][0] * mn + lin["n"][1], 1)
                la = np.maximum(lin["area"][0] * ma + lin["area"][1], 0)
                A["modis_linear"].add(act, hit, lf, ln, la, v["frp"], v["n"], v["area"])
        del yd
    for view in views:
        T = tot[view]
        res["by_view"][view] = {"true_viirs_cell_days": int(round(T["cells"] / frac)), "eval_frac": frac,
                                **{m: a.result(T["cells"], T["frp"], T["n"]) for m, a in accs[view].items()}}
        print(f"[harmonize] held-out {years[0]}-{years[-1]}, truth = {view}:")
        for mth, r in res["by_view"][view].items():
            if isinstance(r, dict):
                print(f"   {mth:>16}: precision {r['precision']:.3f} recall {r['recall']:.3f} IoU {r['iou']:.3f} "
                      f"cells pred/true {r['pred_cells_over_true']:.2f} | FRP total ratio {r['total_frp_ratio']:.2f}, "
                      f"log-MAE {r.get('frp_log1p_mae_on_tp', float('nan')):.3f} | count log-MAE "
                      f"{r.get('n_log1p_mae_on_tp', float('nan')):.3f}", flush=True)
    meta_all = json.loads(META_F.read_text())
    out = {"meta": {k: v for k, v in meta_all.items() if k != "threshold_curve"}, "test": res}
    if METRICS_F.exists():
        old = json.loads(METRICS_F.read_text())
        out = {**old, **out}
    METRICS_F.write_text(json.dumps(out, indent=1))
    return res


# ----------------------------------------------------------------------------- apply
def _apply_year(yr, countries, viirs_sats, th, nthread=4):
    ms, meta = load_models(nthread)
    yd = YearData(yr, countries, truth=False)
    parts = []
    for a, b, k in yd.chunks():
        if not len(k):  # e.g. 2000-01..10: Terra not flying yet
            continue
        kd = decode_key(k)[0]
        sats = sats_for(kd) if viirs_sats is None else np.full(len(k), viirs_sats)
        P = predict(ms, features(k, yd.md, yd.sp, sats), meta.get("reg_scale"))
        m = P["p"] >= th
        d, x, y = decode_key(k[m])
        parts.append(pd.DataFrame({
            "day": d.astype(np.int32), "gx": x.astype(np.int32), "gy": y.astype(np.int32),
            "n": np.maximum(np.rint(P["n"][m]), 1).astype(np.int32), "frp": P["frp"][m].astype(np.float32),
            "area": P["area"][m].astype(np.float32), "p_viirs": P["p"][m].astype(np.float32)}))
    df = pd.concat(parts, ignore_index=True) if parts else None
    print(f"[harmonize] {yr}: {0 if df is None else len(df):,} VIIRS-equivalent cell-days", flush=True)
    return df


def apply(years, countries=None, viirs_sats=None, out=OUT, keep_p=None, workers=1):
    """Reconstruct VIIRS-equivalent cell-days for `years`. viirs_sats None = 1 before NOAA-20, 2 after.
    Rows with p_viirs >= threshold are written (keep_p overrides). Years already in `out` but not in
    `years` are kept."""
    _, meta = load_models()
    th = meta["threshold"] if keep_p is None else keep_p
    t = time.time()
    if workers > 1:
        from concurrent.futures import ProcessPoolExecutor
        with ProcessPoolExecutor(workers) as ex:
            parts = list(ex.map(_apply_year, years, [countries] * len(years), [viirs_sats] * len(years),
                                [th] * len(years), [max(1, 8 // workers)] * len(years)))
    else:
        parts = [_apply_year(y, countries, viirs_sats, th, 8) for y in years]
    parts = [p for p in parts if p is not None]
    print(f"[harmonize] reconstructed {sum(map(len, parts)):,} cell-days ({time.time() - t:.0f}s)", flush=True)
    if not parts:
        raise SystemExit(f"no MODIS data for years {years}")
    df = pd.concat(parts, ignore_index=True)
    df["source"] = "reconstructed"
    if out is not None and Path(out).exists():  # keep other years already reconstructed
        old = pd.read_parquet(out)
        oy = (EPOCH + old["day"].values.astype("timedelta64[D]")).astype("datetime64[Y]").astype(int) + 1970
        old = old[~np.isin(oy, list(years))]
        if len(old):
            df = pd.concat([old, df], ignore_index=True)
    df = df.sort_values(["day", "gx", "gy"], ignore_index=True)
    if out is not None:
        df.to_parquet(out, index=False)
        info = {"rows": len(df), "threshold": th, "model_metrics": str(METRICS_F),
                "rows_per_year": {int(k): int(v) for k, v in
                                  pd.Series((EPOCH + df["day"].values.astype("timedelta64[D]"))
                                            .astype("datetime64[Y]").astype(int) + 1970).value_counts()
                                  .sort_index().items()},
                "viirs_sats": "1 before 2018-04-01, 2 after" if viirs_sats is None else viirs_sats}
        Path(str(out).replace(".parquet", ".json")).write_text(json.dumps(info, indent=1))
        print(f"[harmonize] wrote {out} ({len(df):,} rows)")
    return df


def load_harmonized_daily(start_year=2000, end_year=None, cut_day=VIIRS_START, viirs=None):
    """VIIRS-style daily burning cells for the whole record.

    Rows before `cut_day` (2012-01-20, first S-NPP day) come from the MODIS->VIIRS reconstruction in
    cache/viirs_equiv_daily.parquet (source='reconstructed', p_viirs = model probability); rows from
    cut_day on are real VIIRS from datasets.load_viirs_daily() (source='viirs', p_viirs = 1).
    Columns: day, gx, gy, n, frp, area, p_viirs, source; sorted by (day, gx, gy), RangeIndex.
    `viirs`: an already loaded load_viirs_daily() frame (avoids reading it again; note load_viirs_daily()
    falls back to the full load_cells() when cache/viirs_daily.parquet is stale). Not needed when
    end_year < 2012. Same core columns as load_viirs_daily(), so the direct spread model can run on
    old fires:

        from equifire import direct
        from equifire.harmonize import load_harmonized_daily
        act = load_harmonized_daily(2008)
        model, meta = direct.load()            # V1 features: FireIndex(act) is enough
        fi = direct.FireIndex(act)
        wx = direct.Weather()
        t0 = int((np.datetime64("2010-08-15") - direct.EPOCH).astype(int))
        P, A, dist, A0 = direct.predict_case(model, fi, t0, cx, cy, wx, 7, meta["features"])
    """
    from .datasets import load_viirs_daily
    lo = int((np.datetime64(f"{start_year}-01-01") - EPOCH).astype(int))
    hi = None if end_year is None else int((np.datetime64(f"{end_year + 1}-01-01") - EPOCH).astype(int))
    parts = []
    if lo < cut_day:
        if not OUT.exists():
            raise SystemExit("cache/viirs_equiv_daily.parquet missing; run: .venv/bin/python harmonize.py apply")
        r = pd.read_parquet(OUT, filters=[("day", ">=", lo), ("day", "<", cut_day if hi is None else min(cut_day, hi))])
        parts.append(r)
    if hi is None or hi > cut_day:
        v = load_viirs_daily() if viirs is None else viirs
        v = v[(v["day"] >= max(lo, cut_day)) & ((v["day"] < hi) if hi is not None else True)]
        v = v.assign(p_viirs=np.float32(1.0), source="viirs")
        parts.append(v)
    df = pd.concat(parts, ignore_index=True)
    df["source"] = df["source"].astype("category")
    return df.sort_values(["day", "gx", "gy"], ignore_index=True)


# ----------------------------------------------------------------------------- spread sanity check
def _daily_frame(keys, vals):
    d, x, y = decode_key(keys)
    return pd.DataFrame({"day": d.astype(np.int32), "gx": x.astype(np.int32), "gy": y.astype(np.int32),
                         "n": np.asarray(vals["n"]).astype(np.int32), "frp": np.asarray(vals["frp"], np.float32),
                         "area": np.asarray(vals["area"], np.float32)})


def spread_check(n_cases=60, years=(2023, 2024, 2025), countries=None, days=14, recon=None, seed=0,
                 out=MODELS / "harmonize_spread_check.json"):
    """Direct spread model on real fires of `years` (its held-out period) with three inputs:
    real VIIRS, reconstructed VIIRS-equivalent (from MODIS only) and raw MODIS cells. Truth is always
    real VIIRS (forecast.py rules, from the real start-day fire), so the IoU difference is the accuracy
    an old-fire prediction loses because its input had to be reconstructed."""
    from . import direct
    model, meta = direct.load()
    if model is None:
        raise SystemExit("no models/spread_direct.json")
    if direct.needs_extras(meta["features"]):
        print("[harmonize] NOTE: direct model needs FireIndex.from_cells extras (raw cells); "
              "reconstructed input only provides the daily table, extras are not available for old fires")
    yrs = list(years)
    countries = countries or viirs_countries(yrs)
    vk, vv = viirs_truth(sorted(set([yrs[0] - 1] + yrs)), countries)
    real = _daily_frame(vk, vv)
    if recon is None:
        recon = pd.read_parquet(OUT, filters=[("day", ">=", int(real["day"].min()))])
    mc = read_cells("modis", sorted(set([yrs[0] - 1] + yrs)), countries)
    md = modis_daily(mc)
    del mc
    modis = _daily_frame(md["key"], md)
    fis = {"viirs_real": direct.FireIndex(real), "reconstructed": direct.FireIndex(recon),
           "modis_raw": direct.FireIndex(modis)}
    wx = direct.Weather()
    lo = int((np.datetime64(f"{yrs[0]}-01-01") - EPOCH).astype(int))
    hi = int((np.datetime64(f"{yrs[-1] + 1}-01-01") - EPOCH).astype(int)) - days - 1
    fr = fis["viirs_real"]
    rng = np.random.default_rng(seed)
    cases = direct.sample_cases(fr, (fr.day >= lo) & (fr.day <= hi), n_cases, rng)
    rec = {m: np.full((len(cases), days), np.nan) for m in fis}
    start_iou = {m: [] for m in fis}
    cols = meta["features"]
    t = time.time()
    for ci, (t0, cx, cy) in enumerate(cases):
        A, _ = fr.cube(t0, cx, cy)
        A0r = A[direct.HIST] > 0
        distr = direct.ndimage.distance_transform_cdt(~A0r, metric="chessboard")
        truth = direct.case_truth(A, distr, A0r, days)
        for m, fi in fis.items():
            r = direct.predict_case(model, fi, t0, cx, cy, wx, days, cols)
            if r is None:
                start_iou[m].append(0.0)
                for k in range(days):
                    rec[m][ci, k] = 1.0 if not truth[k].any() else 0.0
                continue
            P, Am, _, A0m = r
            start_iou[m].append(direct.iou_masks(A0m > 0, A0r))
            for k in range(1, days + 1):
                rec[m][ci, k - 1] = direct.iou_masks(direct.select(P[k - 1], direct.rule_for(meta, k)), truth[k - 1])
    res = {"n_cases": len(cases), "years": yrs, "days": days, "model_features_need_extras":
           bool(direct.needs_extras(cols)), "seconds": round(time.time() - t), "by_input": {}}
    print(f"[harmonize] spread check on {len(cases)} real fires {yrs[0]}-{yrs[-1]} ({time.time() - t:.0f}s), "
          f"truth = real VIIRS:")
    for m in fis:
        v = rec[m].mean(0)
        res["by_input"][m] = {"start_day_iou_vs_real": float(np.mean(start_iou[m])),
                              "iou_by_day": [float(x) for x in v], "mean_iou_d1_7": float(v[:7].mean()),
                              "mean_iou_all": float(v.mean())}
        print(f"   {m:>14}: start-fire IoU vs real {np.mean(start_iou[m]):.3f} | spread IoU d1 {v[0]:.3f} "
              f"d3 {v[2]:.3f} d7 {v[6]:.3f} d{days} {v[-1]:.3f} | mean d1-7 {v[:7].mean():.3f}", flush=True)
    if out is not None:
        out.write_text(json.dumps(res, indent=1))
    return res
