#!/usr/bin/env python3
"""ML + physics stacking ensemble: build, fit (no leakage) and honest A/B.

Scheme (per test year Y, default 2024):
  * ML model for the TEST fires     = models/wf_<Y><tag>.json      (trained on fires < Y)
  * ML model for the BLEND-FIT fires = models/wf_<Y-1><tag>.json    (trained on fires < Y-1)
  * blend-fit fires = fires of year Y-1 whose 28-day... no: whose `days`-day outcome ends before Y starts
    (walk_forward's --tune-5km sample, seed 500+Y). They are out-of-sample for wf_<Y-1>, exactly as the
    test fires are out-of-sample for wf_<Y>, so the blender learns how to combine an *out-of-sample* ML
    probability with physics. Nothing from year Y is used for fitting or tuning.
  * test fires = walk_forward's test sample (seed 1000+Y, --runs fires), identical to the published baselines.
  * blender = small XGBoost (depth 3, early-stopped on a fire-grouped hold-out of the fit fires), chosen a
    priori; logistic regression is reported as a secondary variant.
  * cell selection per horizon (strict-IoU rules = direct.RULES, 5 km rules = walk_forward.TOL_RULES) for the
    ensemble is tuned on OUT-OF-FOLD blender predictions of the fit fires (5 fire-grouped folds).
    ML is reported with its saved rules (= the published baseline) and with rules re-tuned on the same fit
    fires (same tuning data as the ensemble). Physics alone: raw (any simulated burn in the cell), raw high
    envelope, and a fraction threshold tuned on the fit fires.

    .venv/bin/python ensemble_eval.py --year 2024 --runs 100 --limit 20 --fit-fires 60   # timing run
    .venv/bin/python ensemble_eval.py --year 2024 --runs 100 --fit-fires 150
Results -> reports/ensemble_ab_<Y>.json; blender -> models/ensemble_blend_<Y><tag>.json (+ _meta.json)
"""
import argparse
import json
import time

import numpy as np
from scipy import ndimage

import walk_forward as wfm
from equifire import direct
from equifire.common import MODELS, REPORTS
from equifire.physcells import FUELS, physics_cells

from equifire.ensemble_core import FEATS, case_rows, blend_predict

PHYS_THR = [0.0, 0.01, 0.03, 0.05, 0.1, 0.2, 0.3, 0.5, 0.7, 0.9]


# ------------------------------------------------------------------ data collection
_G = {}


def _phys_job(c):
    try:
        return physics_cells(_G["fi"], c, _G["days"], calibration=_G["cal"])
    except Exception as e:  # keep going; the case gets an empty physics layer
        print(f"[physics] {c} failed: {type(e).__name__}: {e}", flush=True)
        return None


def _pinit():
    from threadpoolctl import threadpool_limits
    threadpool_limits(limits=1)


def run_physics(fi, cases, days, cal, workers):
    import multiprocessing as mp
    from concurrent.futures import ProcessPoolExecutor
    _G.update(fi=fi, days=days, cal=cal)
    t = time.time()
    out = []
    with ProcessPoolExecutor(workers, mp_context=mp.get_context("fork"), initializer=_pinit) as ex:
        for i, r in enumerate(ex.map(_phys_job, cases, chunksize=1), 1):
            out.append(r)
            if i % 10 == 0 or i == len(cases):
                print(f"[physics] {i}/{len(cases)} fires | {time.time() - t:.0f}s", flush=True)
    return out


def collect(model, meta, fi, wx, cases, days, cal, workers):
    """Per case: ML probabilities, truth, dist, physics cells (cases where the ML cannot predict are dropped)."""
    phys = run_physics(fi, cases, days, cal, workers)
    recs = []
    for c, pc in zip(cases, phys):
        r = direct.predict_case(model, fi, *c, wx, days, meta["features"])
        if r is None:
            continue
        P, A, dist, A0 = r
        truth = direct.case_truth(A, dist, A0, days)
        if pc is None:
            from equifire.direct import S
            pc = {"frac": np.zeros((3, days, S, S), np.float32), "arr_h": np.full((S, S), np.inf),
                  "cover": np.zeros((S, S), np.float32), "meta": {"ok": False, "m": 1.0}}
        X, ids = case_rows(P, dist, pc, days)
        y = truth[ids[:, 0] - 1].reshape(len(ids), -1)[np.arange(len(ids)), ids[:, 1]] if len(ids) else np.zeros(0)
        recs.append({"case": c, "P": P, "truth": truth, "pc": pc, "X": X, "ids": ids, "y": y.astype(np.float32)})
    return recs


# ------------------------------------------------------------------ blender
XGB_P = dict(n_estimators=600, learning_rate=0.05, max_depth=3, min_child_weight=20, subsample=0.8,
             colsample_bytree=0.8, reg_lambda=2.0, tree_method="hist", device="cpu", eval_metric="logloss",
             n_jobs=4)


def fit_xgb(X, y, groups, seed=0):
    import xgboost as xgb
    rng = np.random.default_rng(seed)
    ug = np.unique(groups)
    hold = set(rng.choice(ug, max(1, len(ug) // 5), replace=False).tolist())
    v = np.isin(groups, list(hold))
    m = xgb.XGBClassifier(early_stopping_rounds=40, **XGB_P)
    m.fit(X[~v], y[~v], eval_set=[(X[v], y[v])], verbose=False)
    n = m.best_iteration + 1
    m2 = xgb.XGBClassifier(**{**XGB_P, "n_estimators": n})
    m2.fit(X, y, verbose=False)
    return m2


class LR:
    def __init__(self, X, y):
        from sklearn.linear_model import LogisticRegression
        self.mu, self.sd = X.mean(0), X.std(0) + 1e-6
        self.m = LogisticRegression(C=1.0, max_iter=2000).fit((X - self.mu) / self.sd, y)

    def predict_proba(self, X):
        return self.m.predict_proba((X - self.mu) / self.sd)


def blend_fit(recs, kind, seed=0):
    X = np.concatenate([r["X"] for r in recs])
    y = np.concatenate([r["y"] for r in recs])
    g = np.concatenate([np.full(len(r["y"]), i) for i, r in enumerate(recs)])
    return fit_xgb(X, y, g, seed) if kind == "xgb" else LR(X, y)


def oof_predict(recs, kind, days, folds=5, seed=0):
    out = [None] * len(recs)
    idx = np.arange(len(recs))
    np.random.default_rng(seed).shuffle(idx)
    for f in range(folds):
        te = idx[f::folds]
        tr = [recs[i] for i in idx if i not in set(te)]
        m = blend_fit(tr, kind, seed + f)
        for i in te:
            out[i] = blend_predict(m, recs[i], days)
    return out


# ------------------------------------------------------------------ scoring
def score_sel(sel, truth):
    """sel, truth: [days, S, S] bool -> (strict IoU [days], 5 km [days])."""
    return (np.array([direct.iou_masks(sel[k], truth[k]) for k in range(len(truth))]),
            np.array([wfm._tol(sel[k], truth[k]) for k in range(len(truth))]))


def select_all(P, rules):
    return np.stack([direct.select(P[k], rules[k]) for k in range(len(P))])


def tune(Ps, truths, rules, metric):
    """Best rule per horizon on (out-of-sample) predictions of the fit fires."""
    f = direct.iou_masks if metric == "iou" else wfm._tol
    days = truths[0].shape[0]
    G = np.zeros((days, len(rules)))
    for P, t in zip(Ps, truths):
        for k in range(days):
            for j, r in enumerate(rules):
                G[k, j] += f(direct.select(P[k], r), t[k])
    return [rules[int(np.argmax(G[k]))] for k in range(days)], (G.max(1) / len(Ps)).tolist()


def phys_P(rec, v=1):
    """Physics 'probability' = fraction of the cell burned by day k, on the ML-eligible cells only."""
    F = rec["pc"]["frac"][v].copy()
    F[~np.isfinite(rec["P"])] = np.nan
    return F


def _at(P, ids):
    return P.reshape(len(P), -1)[ids[:, 0] - 1, ids[:, 1]]


def boot(a, b, n=2000, seed=0):
    d = np.asarray(a) - np.asarray(b)
    rng = np.random.default_rng(seed)
    bs = d[rng.integers(0, len(d), (n, len(d)))].mean(1)
    return [float(d.mean()), float(np.percentile(bs, 2.5)), float(np.percentile(bs, 97.5))]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, default=2024)
    ap.add_argument("--tag", default="", help="ML model tag: models/wf_<Y><tag>.json and wf_<Y-1><tag>.json")
    ap.add_argument("--fit-tag", default=None, help="tag of the Y-1 model (default = --tag)")
    ap.add_argument("--runs", type=int, default=100, help="test fires sampled as in walk_forward")
    ap.add_argument("--limit", type=int, default=0, help="score only the first N test fires (timing runs)")
    ap.add_argument("--fit-fires", type=int, default=150)
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--no-cal", action="store_true", help="physics without calibration")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--save-blender", action="store_true", help="save the blender fit on all fit fires")
    a = ap.parse_args()
    t_all = time.time()
    Y, days = a.year, a.days
    from equifire.fireindex_mm import get_fire_index
    fi = get_fire_index()
    wx = direct.Weather()
    fit_tag = a.tag if a.fit_tag is None else a.fit_tag
    m_test, meta_test = direct.load(MODELS / f"wf_{Y}{a.tag}.json")
    m_fit, meta_fit = direct.load(MODELS / f"wf_{Y - 1}{fit_tag}.json")
    assert m_test is not None and m_fit is not None, "missing walk-forward models"
    assert meta_fit["test_from"] <= Y - 1 and meta_test["test_from"] <= Y
    last = int(fi.day.max())
    lo, hi = wfm._day(Y), min(wfm._day(Y + 1) - 1, last - days)
    test_cases = sorted(direct.sample_cases(fi, (fi.day >= lo) & (fi.day <= hi), a.runs,
                                            np.random.default_rng(1000 + Y)))
    if a.limit:
        test_cases = test_cases[:a.limit]
    vlo, vhi = wfm._day(Y - 1), wfm._day(Y) - 1 - days
    fit_cases = direct.sample_cases(fi, (fi.day >= vlo) & (fi.day <= vhi), a.fit_fires,
                                    np.random.default_rng(500 + Y))
    print(f"[ensemble] fold {Y}: {len(fit_cases)} blend-fit fires from {Y - 1} (ML wf_{Y - 1}{fit_tag}, "
          f"trained < {meta_fit['test_from']}), {len(test_cases)} test fires from {Y} (ML wf_{Y}{a.tag})", flush=True)
    cal = not a.no_cal
    fit = collect(m_fit, meta_fit, fi, wx, fit_cases, days, cal, a.workers)
    # the ML model's feature list is a module global set by load(): reload the test model before predicting
    m_test, meta_test = direct.load(MODELS / f"wf_{Y}{a.tag}.json")
    test = collect(m_test, meta_test, fi, wx, test_cases, days, cal, a.workers)
    print(f"[ensemble] rows: fit {sum(len(r['y']) for r in fit):,} ({np.mean(np.concatenate([r['y'] for r in fit])):.1%}"
          f" positive), test {sum(len(r['y']) for r in test):,} | {time.time() - t_all:.0f}s", flush=True)

    ftruth = [r["truth"] for r in fit]
    res, sels = {}, {}
    methods = {}
    # ML alone
    methods["ml_saved_rules"] = ([r["P"] for r in test], {"iou": [direct.rule_for(meta_test, k) for k in
                                                                  range(1, days + 1)]}, None)
    ml_iou, _ = tune([r["P"] for r in fit], ftruth, direct.RULES, "iou")
    ml_tol, _ = tune([r["P"] for r in fit], ftruth, wfm.TOL_RULES, "tol")
    methods["ml_retuned"] = ([r["P"] for r in test], {"iou": ml_iou, "tol": ml_tol}, None)
    # physics alone
    praw = [("thr", 1e-6)] * days
    methods["physics_raw"] = ([phys_P(r, 1) for r in test], {"iou": praw}, None)
    methods["physics_raw_high"] = ([phys_P(r, 2) for r in test], {"iou": praw}, None)
    prules = [("thr", t + 1e-6) for t in PHYS_THR]
    ph_iou, _ = tune([phys_P(r, 1) for r in fit], ftruth, prules, "iou")
    ph_tol, _ = tune([phys_P(r, 1) for r in fit], ftruth, prules, "tol")
    methods["physics_tuned"] = ([phys_P(r, 1) for r in test], {"iou": ph_iou, "tol": ph_tol}, None)
    # ensemble
    blend_info = {}
    for kind in ("xgb", "lr"):
        t = time.time()
        oof = oof_predict(fit, kind, days)
        e_iou, v_iou = tune(oof, ftruth, direct.RULES, "iou")
        e_tol, v_tol = tune(oof, ftruth, wfm.TOL_RULES, "tol")
        bm = blend_fit(fit, kind)
        methods[f"ensemble_{kind}"] = ([blend_predict(bm, r, days) for r in test], {"iou": e_iou, "tol": e_tol}, bm)
        # out-of-fold log loss on the fit fires (a priori choice check, no test data)
        yo = np.concatenate([r["y"] for r in fit])
        po = np.concatenate([_at(o, r["ids"]) for o, r in zip(oof, fit)])
        po = np.clip(po, 1e-6, 1 - 1e-6)
        pm = np.clip(np.concatenate([_at(r["P"], r["ids"]) for r in fit]), 1e-6, 1 - 1e-6)
        ll = lambda p: float(-np.mean(yo * np.log(p) + (1 - yo) * np.log(1 - p)))  # noqa: E731
        blend_info[kind] = {"oof_logloss": ll(po), "ml_logloss_same_rows": ll(pm), "val_iou_oof": v_iou,
                            "val_tol_oof": v_tol, "rules_iou": [list(r) for r in e_iou],
                            "rules_tol": [list(r) for r in e_tol], "secs": round(time.time() - t, 1)}
        print(f"[ensemble] blender {kind}: OOF logloss {ll(po):.4f} vs ML {ll(pm):.4f} on fit rows | "
              f"{time.time() - t:.0f}s", flush=True)
        if kind == "xgb" and a.save_blender:
            f = MODELS / f"ensemble_blend_{Y}{a.tag}.json"
            bm.save_model(f)
            f.with_name(f.stem + "_meta.json").write_text(json.dumps({
                "features": FEATS, "rules_iou": [list(r) for r in e_iou], "rules_tol": [list(r) for r in e_tol],
                "days": days, "fit_year": Y - 1, "fit_ml_model": f"wf_{Y - 1}{fit_tag}", "apply_ml_model":
                f"wf_{Y}{a.tag}", "n_fit_fires": len(fit), "calibration": cal, "xgb_params": XGB_P,
                "physics": "equifire.physcells.physics_cells(radius_km=60, flat terrain, archive ignition)"}))
            print(f"[ensemble] saved {f.name}", flush=True)

    truths = [r["truth"] for r in test]
    for name, (Ps, rules, _) in methods.items():
        res[name] = {}
        for obj, rr in rules.items():
            iou = np.array([score_sel(select_all(P, rr), t)[0] for P, t in zip(Ps, truths)])
            tol = np.array([score_sel(select_all(P, rr), t)[1] for P, t in zip(Ps, truths)])
            res[name][obj] = {"iou": iou, "tol": tol, "rules": [list(r) for r in rr]}
    # tables
    k3 = min(3, days) - 1
    print("\n" + "=" * 100)
    print(f" ENSEMBLE A/B {Y}: {len(test)} test fires, {days}-day forecasts (blend fitted on {len(fit)} fires of {Y - 1})")
    print("=" * 100)
    print(f"  {'method':<22}{'rules tuned for':<16}{'5km d1':>8}{'5km d3':>8}{'strict d1':>10}{'strict d3':>10}"
          f"{'strict mean':>12}")
    summary = {}
    for name, d in res.items():
        for obj, s in d.items():
            row = {"tol_d1": float(s["tol"][:, 0].mean()), "tol_d3": float(s["tol"][:, k3].mean()),
                   "iou_d1": float(s["iou"][:, 0].mean()), "iou_d3": float(s["iou"][:, k3].mean()),
                   "iou_mean": float(s["iou"].mean()), "tol_mean": float(s["tol"].mean()), "rules": s["rules"]}
            summary[f"{name}|{obj}"] = row
            print(f"  {name:<22}{obj:<16}{row['tol_d1']:>8.1%}{row['tol_d3']:>8.1%}{row['iou_d1']:>10.1%}"
                  f"{row['iou_d3']:>10.1%}{row['iou_mean']:>12.1%}")
    # paired bootstrap CIs: ensemble/physics vs ML
    cis = {}
    base = res["ml_saved_rules"]["iou"]
    pairs = [("ensemble_xgb|iou", "ml_saved_rules|iou"), ("ensemble_xgb|tol", "ml_retuned|tol"),
             ("ensemble_xgb|iou", "ml_retuned|iou"), ("ensemble_lr|iou", "ml_saved_rules|iou"),
             ("ensemble_lr|tol", "ml_retuned|tol"), ("physics_raw|iou", "ml_saved_rules|iou"),
             ("physics_tuned|iou", "ml_retuned|iou"), ("physics_tuned|tol", "ml_retuned|tol")]
    print(f"\n  paired bootstrap over fires (2000 resamples), difference in points [95% CI]:")
    for a_, b_ in pairs:
        A = res[a_.split("|")[0]][a_.split("|")[1]]
        B = res[b_.split("|")[0]][b_.split("|")[1]]
        c = {f"{m}_d{k + 1}": boot(A[m][:, k], B[m][:, k]) for m in ("tol", "iou") for k in (0, k3)}
        cis[f"{a_} - {b_}"] = c
        print(f"  {a_:<20} - {b_:<20}: " + "  ".join(
            f"{m}: {100 * v[0]:+.1f} [{100 * v[1]:+.1f},{100 * v[2]:+.1f}]" for m, v in c.items()))
    del base
    phys_meta = [r["pc"]["meta"] for r in test]
    out = {"year": Y, "days": days, "n_test": len(test), "n_fit": len(fit), "test_ml_model": f"wf_{Y}{a.tag}",
           "fit_ml_model": f"wf_{Y - 1}{fit_tag}", "calibration": cal, "summary": summary, "bootstrap": cis,
           "blender": blend_info, "features": FEATS,
           "physics_test": {"cal_applied": float(np.mean([bool(m.get("cal_applied")) for m in phys_meta])),
                            "median_m": float(np.median([m.get("m", 1.0) for m in phys_meta])),
                            "weather_fallback": float(np.mean([bool(m.get("weather_fallback")) for m in phys_meta])),
                            "fuels": {f: int(sum(m.get("fuel") == f for m in phys_meta)) for f in FUELS}},
           "per_fire": {n: {o: {"iou": s["iou"].tolist(), "tol": s["tol"].tolist()} for o, s in d.items()}
                        for n, d in res.items()},
           "test_cases": [list(map(int, r["case"])) for r in test], "secs": round(time.time() - t_all)}
    REPORTS.mkdir(exist_ok=True)
    f = REPORTS / f"ensemble_ab_{Y}{a.tag}{'_lim' + str(a.limit) if a.limit else ''}{'_nocal' if a.no_cal else ''}.json"
    f.write_text(json.dumps(out, indent=1))
    print(f"\n  -> {f} | {time.time() - t_all:.0f}s")


if __name__ == "__main__":
    main()
