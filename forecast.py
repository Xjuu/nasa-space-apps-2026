"""EquiFire fire-spread forecast.

Backtest (default): pick N real fires from years the model never trained on. For each, give the
model ONLY what was known on the start day (burning cells today + last 7 days), let it simulate
the spread day by day for --days days, then compare with what really burned.

    .venv/bin/python forecast.py                    # 100 fires, 5-day forecasts
    .venv/bin/python forecast.py --runs 100 --days 7
    .venv/bin/python forecast.py --live             # forecast from the newest data -> GeoJSON
    .venv/bin/python forecast.py --parallel         # many fires side by side (65% CPU, 18 GB RAM cap)
    .venv/bin/python forecast.py --use-gpu          # predictions on GPU (needs main.py --use-gpu model)

Accuracy = overlap (IoU) between predicted and real newly burned area, in %. 100% = perfect,
both-empty (correctly predicted "no spread") also counts as 100%.
Baselines: "rule" = burning-neighbour rule of thumb, "no spread" = fire stays as is.
"""
import argparse
import json
import time

import joblib
import numpy as np
import pandas as pd

from equifire import datasets, weather
from equifire.collect import load_cells
from equifire.fireindex_mm import get_fire_index
from equifire.common import (EPOCH, GRID, MODELS, REPORTS, STRIDE_DAY, KeySet, cell_center, cell_key,
                             day_to_year)

HISTORY_DAYS = 7


USE_GPU = False


def load_model():
    res = json.loads((REPORTS / "results.json").read_text())["spread"]
    if "threshold_ml" not in res:
        raise SystemExit("Model is from an older version. Retrain first: .venv/bin/python main.py train")
    model = joblib.load(MODELS / "spread.joblib")
    if USE_GPU:
        if not hasattr(model, "get_booster"):
            raise SystemExit("--use-gpu needs a GPU-trained model. Retrain: .venv/bin/python main.py --use-gpu")
        model.set_params(device="cuda")
        print("[gpu] forecasting on CUDA GPU")
    return model, res


def persistence_days(act, ks, test_from):
    """How many days a burning cell typically keeps burning (from training years only)."""
    tr = act[day_to_year(act["day"].values) < test_from]
    k = cell_key(tr["day"], tr["gx"], tr["gy"])
    p = ks.has(k + STRIDE_DAY).mean()
    return max(1, int(round(1 / max(1e-3, 1 - p)))), p


class Simulator:
    def __init__(self, model, res, frames):
        self.model, self.res, self.frames = model, res, frames
        self.features = res["features"]
        self.use_weather = "wind_speed" in self.features

    def _score(self, X, meta, method):
        if method == "rule":
            return X[self.res["baseline_col"]].values, self.res["threshold_baseline"]
        if self.use_weather:
            X = weather.join_weather(X, meta, self.frames, verbose=False)
        return self.model.predict_proba(X[self.features])[:, 1], self.res["threshold_ml"]

    def run(self, t0, hist, start, days, persist, method, window=None):
        """hist: DataFrame day,gx,gy,frp,n,area of observed burning cells (days <= t0).
        start: burning cells on t0. Returns list of sets of newly ignited (gx,gy) per day, + probs."""
        typical = start[["frp", "n", "area"]].median()
        hist = hist[["day", "gx", "gy", "frp", "n", "area"]].copy()
        active = {(int(x), int(y)): 0 for x, y in zip(start["gx"], start["gy"])}  # cell -> days burning
        out, probs = [], []
        for k in range(days):
            t = t0 + k
            if method == "none" or not active:
                out.append(set())
                continue
            hk = cell_key(hist["day"], hist["gx"], hist["gy"])
            ks = KeySet(hk)
            fr = hist[hist["day"] == t].copy()
            fr["prev"] = ks.has(cell_key(fr["day"], fr["gx"], fr["gy"]) - STRIDE_DAY).astype(np.float32)
            g = datasets.spread_candidates(fr, ks)
            X, d, x, y = datasets.spread_features(g, ks)
            if window is not None:
                (cx, cy, w) = window
                m = (np.abs(x - cx) <= w) & (np.abs(y - cy) <= w)
                X, d, x, y = X[m], d[m], x[m], y[m]
            new = set()
            if len(X):
                lat, lon = cell_center(x, y)
                meta = pd.DataFrame({"day": d, "lat": lat, "lon": lon})
                s, thr = self._score(X.reset_index(drop=True), meta, method)
                ign = s >= thr
                new = {(int(a), int(b)) for a, b in zip(x[ign], y[ign])}
                probs.append(pd.DataFrame({"day": t + 1, "lat": lat, "lon": lon, "p": s}))
            # next day's burning cells: newly ignited + old ones that keep burning
            active = {c: a + 1 for c, a in active.items() if a + 1 < persist}
            for c in new:
                active.setdefault(c, 0)
            nxt = pd.DataFrame({"day": t + 1, "gx": [c[0] for c in active], "gy": [c[1] for c in active],
                                "frp": typical["frp"], "n": typical["n"], "area": typical["area"]})
            hist = pd.concat([hist, nxt], ignore_index=True)
            out.append(new)
        return out, (pd.concat(probs) if probs else None)


def iou(a, b):
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)


_NB = [(dx, dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1)]


def iou_tol(pred, truth, r=1):
    """Overlap with a position tolerance of r cells (~5 km per cell): a predicted cell counts as correct
    if a real burned cell is within r cells, and a real cell counts as found if a prediction is within r.
    F = (matched predicted + matched real) / (all predicted + all real) is a tolerant Dice/F1 score; it is
    turned into the IoU scale with IoU = F / (2 - F), so with r = 0 it equals the strict IoU exactly.
    Both empty = 100%."""
    if not pred and not truth:
        return 1.0
    if not pred or not truth:
        return 0.0
    nb = [(dx, dy) for dx in range(-r, r + 1) for dy in range(-r, r + 1)]
    near = lambda c, S: any((c[0] + dx, c[1] + dy) in S for dx, dy in nb)
    mp = sum(near(c, truth) for c in pred)
    mt = sum(near(c, pred) for c in truth)
    f = (mp + mt) / (len(pred) + len(truth))
    return f / (2 - f)


CTX = {}  # filled before workers fork, so every worker shares the loaded data (copy-on-write)
METHODS = ["ML", "ML-step", "rule", "none"]


def run_one(i):
    """One independent backtest run. Fire choice depends only on (seed, i): same result in any order."""
    c, a = CTX, CTX["a"]
    act, days_arr, pool, W = c["act"], c["days_arr"], c["pool"], a.window
    rng = np.random.default_rng(a.seed * 100_003 + i)

    def day_rows(dd):
        lo, hi = np.searchsorted(days_arr, [dd, dd + 1])
        return act.iloc[lo:hi]

    for _ in range(300):
        s = pool.iloc[int(rng.integers(len(pool)))]
        t0, cx, cy = int(s["day"]), int(s["gx"]), int(s["gy"])

        def win(df):
            return df[(np.abs(df["gx"] - cx) <= W) & (np.abs(df["gy"] - cy) <= W)]

        start = win(day_rows(t0))
        if len(start) < a.min_cells:
            continue
        # INPUT: only what is known on day t0
        hist = pd.concat([win(day_rows(t0 - k)) for k in range(HISTORY_DAYS, -1, -1)], ignore_index=True)
        # TRUTH (hidden from the model): cells that newly burned, reachable at <= 1 cell/day
        init = np.stack([start["gx"].values, start["gy"].values], 1)
        init_set = {tuple(c_) for c_ in init.tolist()}
        truth, cum = [], set()
        for k in range(1, a.days + 1):
            tr = win(day_rows(t0 + k))
            pts = np.stack([tr["gx"].values, tr["gy"].values], 1)
            if len(pts):
                pts = pts[np.abs(pts[:, None, :] - init[None, :, :]).max(2).min(1) <= k]
            cum |= {tuple(c_) for c_ in pts.tolist()} - init_set
            truth.append(set(cum))
        rec = {"date": str(EPOCH + np.timedelta64(t0, "D")),
               "lat": float(cell_center(cx, cy)[0]), "lon": float(cell_center(cx, cy)[1]),
               "start_cells": len(start)}
        for m in c["methods"]:
            if m == "ML":
                new_days = direct_days(t0, cx, cy, a.days)
                if new_days is None:
                    continue
            else:
                new_days, _ = c["sim"].run(t0, hist, start, a.days, c["persist"],
                                           "ML" if m == "ML-step" else m, window=(cx, cy, W))
            cum = set()
            for k, nd in enumerate(new_days, 1):
                cum |= nd
                rec[f"{m}_iou_d{k}"] = iou(cum, truth[k - 1])
                rec[f"{m}_tol_d{k}"] = iou_tol(cum, truth[k - 1])
                true_area = len(init_set) + len(truth[k - 1])
                rec[f"{m}_size_d{k}"] = max(0.0, 1 - abs(len(init_set) + len(cum) - true_area) / true_area)
        return rec
    return None


def direct_days(t0, cx, cy, days):
    """Direct model: predicted newly burning cells per day as sets of global (gx, gy)."""
    from equifire import direct
    c = CTX
    if c.get("dmodel") is None:
        return None
    r = direct.predict_case(c["dmodel"], c["fi"], t0, cx, cy, c["dwx"], days, c["dmeta"]["features"])
    if r is None:
        return [set() for _ in range(days)]
    P = r[0]
    out = []
    for k in range(1, days + 1):
        xs, ys = np.nonzero(direct.select(P[k - 1], direct.rule_for(c["dmeta"], k)))
        out.append({(int(x + cx - direct.W), int(y + cy - direct.W)) for x, y in zip(xs, ys)})
    return out


def _worker_init():
    from threadpoolctl import threadpool_limits
    threadpool_limits(limits=1)  # one thread per worker; parallelism comes from the worker count
    for m in (CTX["sim"].model, CTX.get("dmodel")):
        if m is not None and hasattr(m, "set_params") and "n_jobs" in m.get_params():
            m.set_params(n_jobs=1)
            m.get_booster().set_param({"nthread": 1})


def backtest(a):
    model, res = load_model()
    test_from = res["test_from_year"]
    act = datasets.load_viirs_daily().sort_values("day", kind="stable").reset_index(drop=True)
    ks_all = KeySet(cell_key(act["day"], act["gx"], act["gy"]))
    persist, p = persistence_days(act, ks_all, test_from)
    print(f"[forecast] burning cells stay active ~{persist} day(s) (P(still burning tomorrow) = {p:.0%}, "
          f"learned from years < {test_from})")
    days_arr = act["day"].values
    pool = act[(day_to_year(days_arr) >= test_from) & (days_arr <= days_arr.max() - a.days)]
    if pool.empty:
        raise SystemExit(f"no fires with {a.days} days of data after them in years >= {test_from}")
    CTX.update(methods=METHODS if a.compare else ["ML", "rule", "none"])
    CTX.update(a=a, act=act, days_arr=days_arr, pool=pool, persist=persist,
               sim=Simulator(model, res, weather.cached_frames() if a.weather else []))
    from equifire import direct
    dmodel, dmeta = direct.load(a.direct_model)  # None -> models/spread_direct.json
    if dmodel is None:
        print("[forecast] no direct model yet (run: .venv/bin/python main.py direct) -> 'ML' row empty")
    else:
        if dmeta["test_from"] != test_from:
            print(f"[forecast] WARNING direct model trained with test years >= {dmeta['test_from']}")
        # version-2 features need MODIS + day/night overpasses -> raw cells; older models need only VIIRS days
        fi = get_fire_index() if direct.needs_extras(dmeta["features"]) \
            else direct.FireIndex(act)
        CTX.update(dmodel=dmodel, dmeta=dmeta, fi=fi,
                   dwx=direct.Weather() if a.weather else None)
        print(f"[forecast] ML = direct multi-day model (val ROC-AUC {dmeta['val_auc']:.3f}); "
              f"ML-step = old day-by-day simulation")
    print(f"[forecast] choosing {a.runs} fires from {len(pool):,} burning cell-days in unseen years "
          f">= {test_from} | {a.days}-day forecasts | window +-{a.window} cells (~{a.window * GRID * 111:.0f} km)")

    rows, t_start = [], time.time()

    def report(rec):
        rows.append(rec)
        df = pd.DataFrame(rows)
        cols = lambda m: [f"{m}_iou_d{k}" for k in range(1, a.days + 1)]
        mean = {m: (df[cols(m)].values.mean() if cols(m)[0] in df else float("nan")) for m in METHODS}
        tol = df[f"ML_tol_d{min(3, a.days)}"].mean() if f"ML_tol_d1" in df else float("nan")
        print(f"[run {len(rows):>3}/{a.runs}] {rec['date']} lat {rec['lat']:+6.2f} lon {rec['lon']:+7.2f} "
              f"{rec['start_cells']:>3} burning cells | running accuracy: ML {mean['ML']:.1%} "
              f"(within 5 km, day {min(3, a.days)}: {tol:.1%})  rule {mean['rule']:.1%}  "
              f"no-spread {mean['none']:.1%} | {time.time() - t_start:.0f}s", flush=True)

    if a.parallel:
        from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
        import multiprocessing as mp
        from equifire import parallel
        gov = parallel.Governor(use_gpu=False)
        workers = max(1, int(parallel.CORES * parallel.CPU_TARGET / 100))
        print(f"[parallel] {workers} of {parallel.CORES} CPU workers (target CPU {parallel.CPU_TARGET:.0f}%), "
              f"RAM capped at {parallel.RAM_GB:g} GB. Runs are independent, so they run side by side.", flush=True)
        todo, inflight = iter(range(a.runs)), set()
        with ProcessPoolExecutor(workers, mp_context=mp.get_context("fork"), initializer=_worker_init) as ex:
            while True:
                while len(inflight) < workers:
                    i = next(todo, None)
                    if i is None:
                        break
                    gov.pace("cpu")
                    inflight.add(ex.submit(run_one, i))
                if not inflight:
                    break
                done, inflight = wait(inflight, return_when=FIRST_COMPLETED)
                for f in done:
                    if f.result() is not None:
                        report(f.result())
        gov.stop = True
    else:
        for i in range(a.runs):
            rec = run_one(i)
            if rec is not None:
                report(rec)

    df = pd.DataFrame(rows)
    REPORTS.mkdir(exist_ok=True)
    df.to_csv(REPORTS / "forecast_runs.csv", index=False)
    methods = METHODS
    names = {"ML": "ML direct", "ML-step": "ML day-by-day", "rule": "neighbour rule", "none": "no spread"}
    methods = [m for m in METHODS if f"{m}_iou_d1" in df]
    print("\n" + "=" * 78)
    print(f" FORECAST ACCURACY - {len(df)} real fires, unseen years >= {test_from}")
    print("=" * 78)
    print(" Spread accuracy (overlap of predicted vs real newly burned area):")
    show = [k for k in (1, 2, 3, 5, 7, 10, 14, 21, 28, 35, 42) if k <= a.days] or [a.days]
    if a.days not in show:
        show.append(a.days)
    print(f"  {'method':<16}" + "".join(f"{'day ' + str(k):>9}" for k in show) + f"{'mean':>9}")
    for m in methods:
        v = [df[f"{m}_iou_d{k}"].mean() for k in range(1, a.days + 1)]
        print(f"  {names[m]:<16}" + "".join(f"{v[k - 1]:>9.1%}" for k in show) + f"{np.mean(v):>9.1%}")
    print(" Spread accuracy within 5 km (a prediction 1 cell off still counts):")
    for m in methods:
        v = [df[f"{m}_tol_d{k}"].mean() for k in range(1, a.days + 1)]
        print(f"  {names[m]:<16}" + "".join(f"{v[k - 1]:>9.1%}" for k in show) + f"{np.mean(v):>9.1%}")
    print(" Fire size accuracy (100% - relative error of total burning area):")
    for m in methods:
        v = [df[f"{m}_size_d{k}"].mean() for k in range(1, a.days + 1)]
        print(f"  {names[m]:<16}" + "".join(f"{v[k - 1]:>9.1%}" for k in show) + f"{np.mean(v):>9.1%}")
    first = df[[f"ML_iou_d{k}" for k in range(1, a.days + 1)]].mean(axis=1)
    print(f"\n ML accuracy, runs 1-10: {first[:10].mean():.1%} | runs 1-{len(df)}: {first.mean():.1%} "
          f"(min {first.min():.1%}, max {first.max():.1%})")
    print(f" per-run details -> reports/forecast_runs.csv")
    print("=" * 78)


def live(a):
    model, res = load_model()
    act = datasets.load_viirs_daily()
    t0 = int(act["day"].max())
    ks_all = KeySet(cell_key(act["day"], act["gx"], act["gy"]))
    persist, _ = persistence_days(act, ks_all, res["test_from_year"])
    hist = act[act["day"] >= t0 - HISTORY_DAYS]
    start = act[act["day"] == t0]
    sim = Simulator(model, res, weather.cached_frames() if a.weather else [])
    print(f"[live] newest data day {EPOCH + np.timedelta64(t0, 'D')}: {len(start):,} burning cells -> "
          f"forecasting {a.days} days")
    new_days, probs = sim.run(t0, hist, start, a.days, persist, "ML")
    feats = []
    for k, nd in enumerate(new_days, 1):
        date = str(EPOCH + np.timedelta64(t0 + k, "D"))
        print(f"[live] {date}: {len(nd):,} new cells predicted to ignite")
        for gx, gy in nd:
            lat, lon = cell_center(gx, gy)
            h = GRID / 2
            feats.append({"type": "Feature", "properties": {"date": date, "forecast_day": k},
                          "geometry": {"type": "Polygon", "coordinates": [[
                              [lon - h, lat - h], [lon + h, lat - h], [lon + h, lat + h],
                              [lon - h, lat + h], [lon - h, lat - h]]]}})
    REPORTS.mkdir(exist_ok=True)
    out = REPORTS / "forecast_live.geojson"
    out.write_text(json.dumps({"type": "FeatureCollection", "features": feats}))
    print(f"[live] {len(feats):,} predicted cells -> {out}")


def main():
    import sys
    if "--walk-forward" in sys.argv:
        # forecast.py --walk-forward [walk_forward.py options] -> retrain per year and test on the next year
        sys.argv = [sys.argv[0]] + [x for x in sys.argv[1:] if x not in ("--walk-forward", "--parallel")]
        import walk_forward
        return walk_forward.main()
    ap = argparse.ArgumentParser()
    ap.add_argument("--walk-forward", action="store_true",
                    help="train a new model per year and test on the following year (see walk_forward.py)")
    ap.add_argument("--runs", type=int, default=100)
    ap.add_argument("--days", type=int, default=5)
    ap.add_argument("--window", type=int, default=20, help="half-size of area around each fire, in 0.05 deg cells")
    ap.add_argument("--min-cells", type=int, default=5, help="only fires with at least this many burning cells")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--no-weather", dest="weather", action="store_false")
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--compare", action="store_true",
                    help="also run the old day-by-day model (slow) for comparison")
    ap.add_argument("--parallel", action="store_true",
                    help="run fires side by side on ~65%% of CPU cores, RAM capped at 18 GB")
    ap.add_argument("--use-gpu", action="store_true", help="run predictions on CUDA GPU (GPU-trained model)")
    ap.add_argument("--direct-model", default=None,
                    help="direct model file to test (default models/spread_direct.json; meta = <name>_meta.json)")
    a = ap.parse_args()
    if a.parallel:
        from equifire import parallel
        parallel.reexec_with_memory_cap()  # re-runs this command inside an 18 GB cgroup limit
    global USE_GPU
    USE_GPU = a.use_gpu
    live(a) if a.live else backtest(a)


if __name__ == "__main__":
    main()
