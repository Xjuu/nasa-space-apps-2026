#!/usr/bin/env python3
"""Walk-forward training + predict-then-learn test for the direct spread model.

For each test year Y (default 2023, 2024, 2025):
  1. Train a fresh base model on ALL fires before Y.                 -> models/wf_<Y>.json
  2. Go through year Y's test fires in date order. For every fire:
       a. PREDICT its spread with what is known on its start day,
       b. SCORE the prediction against what really burned,
       c. once that outcome is in the past (start day + forecast days <= the next fire's start day),
          LEARN from it - whether the prediction was right or wrong - by adding a few trees trained
          on that fire's real outcome (mixed with a replay sample of older training fires so one
          fire cannot drag the model away).
     The model never learns from a fire before predicting it, and never learns an outcome that
     lies in the future of the fire it is predicting, so every score stays honest.
  3. The same fires are also predicted by the base model without learning ("no learning"),
     so you see exactly what learning from its own predictions adds.
Year Y's fires then join the base training data for year Y+1.

    .venv/bin/python walk_forward.py                         # 3 years, 3000 training fires, 60 test fires/year, 7 days
    .venv/bin/python forecast.py --walk-forward --runs 100 --days 28
    .venv/bin/python walk_forward.py --no-learn              # only the yearly retraining
Results -> reports/walk_forward.json, learned models -> models/wf_<Y>_learned.json
"""
import argparse
import json
import time

import numpy as np
import xgboost as xgb
from scipy import ndimage

from equifire import direct
from equifire.collect import load_cells
from equifire.common import EPOCH, MODELS, REPORTS

UPDATE_TREES, UPDATE_ETA, BATCH, REPLAY_ROWS = 4, 0.03, 3, 30_000


def _day(y):
    return int((np.datetime64(f"{y}-01-01") - EPOCH).astype(int))


def _date(d):
    return str(EPOCH + np.timedelta64(int(d), "D"))


def _tol(p, t):
    """Overlap with 1-cell (~5 km) tolerance; both empty = 1."""
    if not p.any() and not t.any():
        return 1.0
    if not p.any() or not t.any():
        return 0.0
    k = np.ones((3, 3), bool)
    return ((p & ndimage.binary_dilation(t, k)).sum() + (t & ndimage.binary_dilation(p, k)).sum()) \
        / (p.sum() + t.sum())


TOL_RULES = ([("thr", t) for t in (0.02, 0.03, 0.05, 0.08, 0.1, 0.12, 0.15, 0.2, 0.25, 0.3, 0.4)]
             + [("eiou", s_) for s_ in (0.8, 1.0, 1.2, 1.5, 2.0, 3.0)]
             + [("topn", s_, g) for s_ in (0.7, 1.0, 1.5, 2.0, 3.0) for g in (0.0, 0.5, 1.0)])


def score(model, meta, fi, wx, case, days, rules=None):
    """rules: optional list of per-day rules (overrides the ones saved with the model)."""
    t0, cx, cy = case
    r = direct.predict_case(model, fi, t0, cx, cy, wx, days, meta["features"])
    if r is None:
        return None
    P, A, dist, A0 = r
    truth = direct.case_truth(A, dist, A0, days)
    iou, tol = [], []
    for k in range(1, days + 1):
        m = direct.select(P[k - 1], rules[k - 1] if rules else direct.rule_for(meta, k))
        iou.append(direct.iou_masks(m, truth[k - 1]))
        tol.append(_tol(m, truth[k - 1]))
    return np.array(iou), np.array(tol)


_P = {}


def _score_base(c):
    return score(_P["model"], _P["meta"], _P["fi"], _P["wx"], c, _P["days"], _P.get("rules"))


def _rule_grid(c):
    """5 km score of every rule in TOL_RULES for one fire: array [days, rules]."""
    t0, cx, cy = c
    r = direct.predict_case(_P["model"], _P["fi"], t0, cx, cy, _P["wx"], _P["days"], _P["meta"]["features"])
    if r is None:
        return None
    P, A, dist, A0 = r
    truth = direct.case_truth(A, dist, A0, _P["days"])
    return np.array([[_tol(direct.select(P[k], rule), truth[k]) for rule in TOL_RULES]
                     for k in range(_P["days"])])


def tune_tol_rules(model, meta, fi, wx, cases, days, workers):
    """Per forecast day, the cell-selection rule with the best 5 km score on `cases` (pre-test fires)."""
    import multiprocessing as mp
    from concurrent.futures import ProcessPoolExecutor
    _P.update(model=model, meta=meta, fi=fi, wx=wx, days=days, rules=None)
    with ProcessPoolExecutor(workers, mp_context=mp.get_context("fork"), initializer=_pinit) as ex:
        g = [x for x in ex.map(_rule_grid, cases, chunksize=2) if x is not None]
    G = np.mean(g, 0)  # [days, rules]
    best = [TOL_RULES[int(np.argmax(G[k]))] for k in range(days)]
    return best, G.max(1)


def _pinit():
    from threadpoolctl import threadpool_limits
    threadpool_limits(limits=1)


def score_many(model, meta, fi, wx, cases, days, workers, rules=None):
    """Score fires in parallel (independent predictions, no learning)."""
    import multiprocessing as mp
    from concurrent.futures import ProcessPoolExecutor
    _P.update(model=model, meta=meta, fi=fi, wx=wx, days=days, rules=rules)
    if workers <= 1:
        return [_score_base(c) for c in cases]
    with ProcessPoolExecutor(workers, mp_context=mp.get_context("fork"), initializer=_pinit) as ex:
        return list(ex.map(_score_base, cases, chunksize=2))


def copy_booster(model):
    bst = model.get_booster() if hasattr(model, "get_booster") else model
    out = xgb.Booster()
    out.load_model(bytearray(bst.save_raw()))
    return out


class OnlineLearner:
    """Base model + small updates from fires whose outcome is already known."""

    def __init__(self, model, meta, fi, wx, replay, days, use_gpu, threads):
        self.bst, self.meta, self.fi, self.wx, self.days = copy_booster(model), meta, fi, wx, days
        self.cols = [c for c in meta["features"] if c != "k"]
        self.ks = [k for k in direct.TRAIN_KS if k <= days] or [days]
        self.replay = replay
        self.rng = np.random.default_rng(5)
        self.pending, self.buffer = [], []
        self.learned_fires = self.updates = 0
        p = meta.get("params", {})
        self.params = {"objective": "binary:logistic", "eta": UPDATE_ETA, "max_depth": int(p.get("max_depth", 8)),
                       "min_child_weight": float(p.get("min_child_weight", 10)), "subsample": 0.8,
                       "colsample_bytree": 0.8, "tree_method": "hist", "max_bin": 256,
                       "device": "cuda" if use_gpu else "cpu", "nthread": threads}

    def reveal_until(self, today):
        """Fires whose whole forecast period ended by `today` become training data."""
        ready = [c for c in self.pending if c[0] + self.days <= today]
        self.pending = [c for c in self.pending if c[0] + self.days > today]
        for c in ready:
            X, y, w, _ = _rows(self.fi, [c], self.wx, self.ks, self.cols, self.rng)
            if X is not None:
                self.buffer.append((X, y, w))
                self.learned_fires += 1
        if len(self.buffer) >= BATCH:
            self._update()

    def _update(self):
        X = np.concatenate([b[0] for b in self.buffer])
        y = np.concatenate([b[1] for b in self.buffer])
        w = np.concatenate([b[2] for b in self.buffer])
        Xr, yr, wr = self.replay
        i = self.rng.choice(len(yr), min(REPLAY_ROWS, len(yr)), replace=False)
        d = xgb.DMatrix(np.concatenate([Xr[i], X]), label=np.concatenate([yr[i], y]),
                        weight=np.concatenate([wr[i], w]))
        self.bst = xgb.train(self.params, d, num_boost_round=UPDATE_TREES, xgb_model=self.bst)
        self.bst.set_param({"device": "cpu"})  # predictions on CPU arrays
        self.buffer = []
        self.updates += 1


def _rows(fi, cases, wx, ks, cols, rng):
    try:
        return direct.build_rows(fi, cases, wx, ks=ks, ks_per_case=0, rng=rng, verbose=False, cols=cols)
    except ValueError:  # no eligible cells
        return None, None, None, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", nargs="+", type=int, default=[2023, 2024, 2025])
    ap.add_argument("--cases", type=int, default=3000, help="training fires per base model")
    ap.add_argument("--runs", type=int, default=60, help="test fires per year (predicted, then learned)")
    ap.add_argument("--days", type=int, default=7, help=f"forecast length, max {direct.MAX_K}")
    ap.add_argument("--trials", type=int, default=1)
    ap.add_argument("--no-learn", action="store_true", help="skip learning from predictions")
    ap.add_argument("--tune-5km", action="store_true",
                    help="also pick cells to maximize the 5 km score (tuned on fires from the year before)")
    ap.add_argument("--tune-fires", type=int, default=150, help="fires from year Y-1 used for --tune-5km")
    ap.add_argument("--features", choices=["v1", "v2", "v3", "v4"], default="v2",
                    help="input set: v3 adds sub-cell detection positions (needs: python -m equifire.subcell build); "
                         "v4 = v3 + per-fire weather and Rothermel physics (needs: python -m equifire.firewx prefetch)")
    ap.add_argument("--tag", default="", help="suffix for saved models, e.g. _c8k -> models/wf_<Y>_c8k.json")
    ap.add_argument("--modis-era-cases", type=int, default=0,
                    help="extra reconstructed 2008-2011 training fires (requires a separate --tag)")
    ap.add_argument("--modis-era-weight", type=float, default=0.5,
                    help="relative weight of reconstructed training rows; validation stays real VIIRS")
    ap.add_argument("--retrain", action="store_true", help="train base models again even if saved ones match")
    ap.add_argument("--cpu", action="store_true", help="train on CPU instead of the NVIDIA GPU")
    ap.add_argument("--threads", type=int, default=12)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--workers", type=int, default=10, help="parallel workers for predictions without learning")
    a = ap.parse_args()
    if a.modis_era_cases < 0 or not 0 < a.modis_era_weight <= 1:
        ap.error("--modis-era-cases must be nonnegative and --modis-era-weight must be > 0 and <= 1")
    if a.modis_era_cases and not a.tag:
        ap.error("use a separate --tag when adding reconstructed MODIS-era cases")
    if a.days > direct.MAX_K:
        print(f"[walk-forward] --days {a.days} > {direct.MAX_K}: the model forecasts at most {direct.MAX_K} days; "
              f"using {direct.MAX_K}")
        a.days = direct.MAX_K
    t_all = time.time()

    from equifire.fireindex_mm import get_fire_index
    fi = get_fire_index()  # memory-mapped, shared, ~no RAM
    cells = None
    wx = direct.Weather()
    last_day = int(fi.day.max())
    first_day = int(fi.day.min())
    MODELS.mkdir(exist_ok=True)
    REPORTS.mkdir(exist_ok=True)
    learn = not a.no_learn
    results = []

    for i, Y in enumerate(a.years):
        t = time.time()
        print(f"\n===== fold {i + 1}/{len(a.years)}: train on fires before {Y}, then predict+learn through {Y} "
              f"=====", flush=True)
        mf, af = MODELS / f"wf_{Y}{a.tag}.json", MODELS / f"wf_{Y}{a.tag}_args.json"
        want = {"cases": a.cases, "trials": a.trials, "seed": a.seed + i,
                "feature_version": getattr(direct, "FEATURE_VERSION", None)}
        if a.features != "v2":
            want["features"] = a.features
        if a.modis_era_cases:
            from equifire.common import CACHE
            files = (CACHE / "viirs_equiv_daily.parquet", MODELS / "harmonize_meta.json")
            want["modis_era"] = {"cases": a.modis_era_cases, "weight": a.modis_era_weight,
                                  "files": [[str(f), f.stat().st_size, f.stat().st_mtime_ns] for f in files]}
        model = None
        if not a.retrain and mf.exists() and af.exists() and json.loads(af.read_text()) == want:
            model, meta = direct.load(mf, mf.with_name(mf.stem + "_meta.json"))
            if model is not None:
                print(f"[walk-forward] reusing saved {mf.name} (same settings, trained on fires before {Y}); "
                      f"--retrain to train again", flush=True)
        if model is None:
            model, meta = direct.train(cells, Y, n_cases=a.cases, n_val=max(200, a.cases // 10),
                                       use_gpu=not a.cpu, trials=a.trials, model_f=mf, seed=a.seed + i,
                                       features={"v1": direct.V1_FEATURES, "v2": direct.V2_FEATURES,
                                                 "v3": direct.V3_FEATURES, "v4": direct.V4_FEATURES}[a.features],
                                       modis_era_cases=a.modis_era_cases, modis_era_weight=a.modis_era_weight)
            af.write_text(json.dumps(want))
        cols = [c for c in meta["features"] if c != "k"]
        back = direct.LONG if direct.needs_extras(meta["features"]) else direct.HIST

        lo, hi = _day(Y), min(_day(Y + 1) - 1, last_day - a.days)
        rng = np.random.default_rng(1000 + Y)  # same test fires whatever the training was
        cases = sorted(direct.sample_cases(fi, (fi.day >= lo) & (fi.day <= hi), a.runs, rng))

        learner = None
        if learn:
            ok = (fi.day + direct.MAX_K < lo) & (fi.day - back >= first_day)
            rcases = direct.sample_cases(fi, ok, 400, np.random.default_rng(77 + Y))
            Xr, yr, wr, _ = _rows(fi, rcases, wx, direct.TRAIN_KS, cols, np.random.default_rng(78))
            learner = OnlineLearner(model, meta, fi, wx, (Xr, yr, wr), a.days, not a.cpu, a.threads)
            print(f"[learn] replay sample: {len(yr):,} rows from {len(rcases)} older fires | update: "
                  f"{UPDATE_TREES} trees every {BATCH} learned fires", flush=True)

        tol_all = None
        if a.tune_5km:
            vlo, vhi = _day(Y - 1), _day(Y) - 1 - a.days  # outcomes end before year Y starts
            vcases = direct.sample_cases(fi, (fi.day >= vlo) & (fi.day <= vhi), a.tune_fires,
                                         np.random.default_rng(500 + Y))
            t_t = time.time()
            tol_rules, tol_val = tune_tol_rules(model, meta, fi, wx, vcases, a.days, a.workers)
            print(f"[{Y}] 5 km cell-selection tuned on {len(vcases)} fires from {Y - 1} (before the test year): "
                  f"day1 {tol_rules[0]}, day3 {tol_rules[min(3, a.days) - 1]} | {time.time() - t_t:.0f}s", flush=True)
            tol_all = score_many(model, meta, fi, wx, cases, a.days, a.workers, tol_rules)
        t_pred = time.time()
        base_all = score_many(model, meta, fi, wx, cases, a.days, a.workers)  # parallel: no learning
        print(f"[{Y}] no-learning predictions for {len(cases)} fires in {time.time() - t_pred:.0f}s "
              f"({a.workers} workers)", flush=True)
        base_s, learn_s = [], []
        for n, (c, sb) in enumerate(zip(cases, base_all), 1):
            if sb is None:
                continue
            if learner:
                learner.reveal_until(c[0])
            base_s.append(sb)
            if learner:
                sl = score(learner.bst, meta, fi, wx, c, a.days)
                learn_s.append(sl)
                learner.pending.append(c)
            k3 = min(3, a.days) - 1
            B = np.array([s[0] for s in base_s])
            msg = (f"[{Y} fire {n:>3}/{len(cases)}] {_date(c[0])} | day3 this fire: base {sb[0][k3]:.0%}"
                   + (f" learning {sl[0][k3]:.0%}" if learner else "")
                   + f" | running mean: base {B.mean():.1%}")
            if learner:
                L = np.array([s[0] for s in learn_s])
                msg += (f"  learning {L.mean():.1%} ({100 * (L.mean() - B.mean()):+.1f} pts) | learned from "
                        f"{learner.learned_fires} fires, {learner.updates} updates")
            print(msg, flush=True)

        def summ(sc):
            iou = np.array([s[0] for s in sc])
            tol = np.array([s[1] for s in sc])
            return {"iou": iou.mean(0).tolist(), "tol": tol.mean(0).tolist(), "n": len(sc)}

        r = {"year": Y, "train_fires": meta["n_train_fires"], "val_auc": meta["val_auc"], "days": a.days,
             "base": summ(base_s)}
        if tol_all is not None:
            r["tuned_5km"] = summ([x for x in tol_all if x is not None])
        if learner:
            r["learning"] = summ(learn_s)
            r["learned_fires"], r["updates"] = learner.learned_fires, learner.updates
            mf = MODELS / f"wf_{Y}{a.tag}_learned.json"
            learner.bst.save_model(mf)
        results.append(r)
        (REPORTS / "walk_forward.json").write_text(json.dumps(results, indent=1))
        print(f"[fold {Y}] done in {time.time() - t:.0f}s", flush=True)

    k3 = min(3, a.days) - 1
    print("\n" + "=" * 92)
    print(f" WALK-FORWARD RESULT - {a.days}-day forecasts; every fire predicted BEFORE the model learned from it")
    print("=" * 92)
    print(f"  {'year':<6}{'model':<14}{'day1':>8}{'day3':>8}{f'day{a.days}':>8}{'mean':>8}   "
          f"{'5km d1':>8}{'5km d3':>8}{'5km mean':>9}")
    for r in results:
        for name in ("base", "learning", "tuned_5km"):
            if name not in r:
                continue
            s = r[name]
            label = {"base": "no learning", "learning": "learning", "tuned_5km": "5km-tuned"}[name]
            print(f"  {r['year']:<6}{label:<14}{s['iou'][0]:>8.1%}{s['iou'][k3]:>8.1%}{s['iou'][-1]:>8.1%}"
                  f"{np.mean(s['iou']):>8.1%}   {s['tol'][0]:>8.1%}{s['tol'][k3]:>8.1%}{np.mean(s['tol']):>9.1%}")
    if learn:
        gain = np.mean([np.mean(r["learning"]["iou"]) - np.mean(r["base"]["iou"]) for r in results])
        print(f"\n  learning from its own predictions changed mean accuracy by {100 * gain:+.1f} points")
    print("  strict = exact 5 km cells; 5km = a prediction 1 cell off still counts.")
    print(f"  results -> reports/walk_forward.json | total {time.time() - t_all:.0f}s")
    print("=" * 92)


if __name__ == "__main__":
    main()
