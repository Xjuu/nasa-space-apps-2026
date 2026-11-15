"""Train / evaluate high-confidence 1 km fire-spread alerts (see equifire/alerts1km.py).

Small smoke run:
  .venv/bin/python alerts1km.py --countries Brazil --train-years 2019 --val-years 2020 \
      --test-years 2021 --n-train 60 --n-eval 60 --no-save
Full run:
  .venv/bin/python alerts1km.py
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from multiprocessing import Pool

import numpy as np
import xgboost as xgb

from equifire import alerts1km as A


def _job(a):
    country, year, n, max_cand, keep, radius, use_weather, weather_csv, enhanced = a
    t = time.time()
    from equifire.weather import AlertWeather
    weather = AlertWeather(weather_csv=weather_csv) if use_weather else None
    r = A.build_cases(country, year, n, max_cand, keep_maps=keep, radius_km=radius, weather=weather, enhanced=enhanced)
    if r is not None:
        r["X"] = r["X"].astype(np.float32)
    print(f"  {country} {year}: {0 if r is None else len(r['X'])} rows, "
          f"{0 if r is None else len(np.unique(r['case']))} cases, {time.time()-t:.0f}s", flush=True)
    return r


def _cache(a):
    c, y = a
    t = time.time()
    r = A.load_country_year(c, y)
    print(f"  cache {c} {y}: {0 if r is None else len(r['day'])} cell-days {time.time()-t:.0f}s", flush=True)
    return 0


def build(countries, years, n, max_cand, keep, workers, radius, use_weather=False, weather_csv=None, enhanced=False):
    jobs = [(c, y, n, max_cand, keep, radius, use_weather, weather_csv, enhanced) for y in years for c in countries]
    with Pool(workers, maxtasksperchild=1) as p:
        res = [r for r in p.imap_unordered(_job, jobs) if r is not None]
    return sorted(res, key=lambda r: (r["year"], r["country"]))


def stack(res, h):
    if not res:
        raise ValueError("no fire cases available for requested countries/years")
    X = np.concatenate([r["X"] for r in res]); y = np.concatenate([r[f"y{h}"] for r in res])
    return X, y


def case_eval(res, probs, thr):
    maps, masks, off = [], [], 0
    for r in res:
        for mp in r["maps"]:
            n = len(mp["cy"])
            maps.append(mp); masks.append(probs[off:off + n] >= thr); off += n
    return maps, masks


def report(res, scores, thr):
    maps, masks = case_eval(res, scores, thr)
    return A.eval_alerts(maps, masks)


def fmt(m):
    return (f"P={m['precision']:.3f} R={m['recall']:.3f} alerts/fire-day={m['alerts_per_fire_day']:.2f} "
            f"(alerts={m['alerts']}, new cells={m['new_cells']})")


def choose_threshold(labels, scores, target, min_alerts=50):
    """Maximize validation hits subject to precision and minimum alert support.

    Evaluate every distinct score, including ties; never tune against test outcomes.
    Returns the best supported precision if the requested target is unreachable.
    """
    order = np.argsort(-scores, kind="stable")
    ranked = scores[order]
    ends = np.r_[np.flatnonzero(ranked[:-1] != ranked[1:]), len(ranked) - 1]
    counts = ends + 1
    hits = np.cumsum(labels[order], dtype=np.int64)[ends]
    precision = hits / counts
    supported = counts >= min_alerts
    eligible = np.flatnonzero(supported & (precision >= target))
    if len(eligible):
        best = eligible[np.argmax(hits[eligible])]
    else:
        supported_idx = np.flatnonzero(supported)
        if not len(supported_idx):
            raise ValueError("not enough validation candidates to select a supported threshold")
        best = supported_idx[np.argmax(precision[supported_idx])]
    return float(ranked[ends[best]])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--countries", nargs="+", default=A.COUNTRIES)
    ap.add_argument("--train-years", nargs="+", type=int, default=list(range(2012, 2021)))
    ap.add_argument("--val-years", nargs="+", type=int, default=[2021, 2022])
    ap.add_argument("--test-years", nargs="+", type=int, default=[2023, 2024, 2025])
    ap.add_argument("--n-train", type=int, default=300, help="fire cases per country-year (train)")
    ap.add_argument("--n-eval", type=int, default=400, help="fire cases per country-year (val/test)")
    ap.add_argument("--max-cand", type=int, default=300, help="candidates kept per train case")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--target-precision", type=float, default=0.70)
    ap.add_argument("--device", choices=["cpu", "cuda"], default="cpu", help="XGBoost training device; CUDA memory errors fall back to CPU")
    ap.add_argument("--enhanced", action="store_true", help="add metric fire-front and recurrence features")
    ap.add_argument("--sampling-weights", action="store_true", help="correct candidate subsampling bias")
    ap.add_argument("--trials", type=int, default=1, help="settings compared on model-selection years")
    ap.add_argument("--calibration-year", type=int, help="reserve this validation year exclusively for threshold calibration")
    ap.add_argument("--precision-buffer", type=float, default=0., help="extra validation precision above target")
    ap.add_argument("--weather-csv", type=Path, help="offline daily weather CSV; enables --weather")
    ap.add_argument("--weather-tiles", type=int, default=0, help="download weather for N busiest training tiles (requires --weather)")
    ap.add_argument("--weather", action="store_true", help="use cached weather through the previous day")
    ap.add_argument("--no-save", action="store_true")
    ap.add_argument("--output-dir", type=Path, default=A.MODELS, help="directory for trained models and evaluation report")
    ap.add_argument("--radius-km", type=float, default=3.0,
                    help="great-circle matching tolerance for labels and evaluation (default 3 km)")
    a = ap.parse_args()
    if not 0 < a.radius_km <= 10 or not 0 < a.target_precision < 1:
        ap.error("radius must be in (0, 10] km and target precision in (0, 1)")
    if max(a.train_years) >= min(a.val_years) or max(a.val_years) >= min(a.test_years):
        ap.error("train, validation and test years must be strictly chronological")

    if a.trials < 1 or a.trials > 3 or not 0 <= a.precision_buffer < 1 - a.target_precision:
        ap.error("trials must be 1..3; precision buffer must be nonnegative and target + buffer < 1")
    if a.calibration_year is not None and (a.calibration_year != max(a.val_years) or len(a.val_years) < 2):
        ap.error("calibration year must be the last validation year, with an earlier selection year")
    a.weather = a.weather or a.weather_csv is not None
    A.MODELS = a.output_dir
    t = time.time()
    years = sorted(set(a.train_years + a.val_years + a.test_years) | {y - 1 for y in a.train_years + a.val_years + a.test_years if y > 2012})
    with Pool(a.workers, maxtasksperchild=1) as p:
        list(p.imap_unordered(_cache, [(c, y) for c in a.countries for y in years]))
    if a.weather and a.weather_tiles > 0:
        from equifire.weather import prepare_alert_weather
        prepare_alert_weather(a.countries, a.train_years, max(a.test_years), a.weather_tiles)
    print("building train cases"); tr = build(a.countries, a.train_years, a.n_train, a.max_cand, False, a.workers, a.radius_km, a.weather, a.weather_csv, a.enhanced)
    print("building val cases"); va = build(a.countries, a.val_years, a.n_eval, None, True, a.workers, a.radius_km, a.weather, a.weather_csv, a.enhanced)
    print("building test cases"); te = build(a.countries, a.test_years, a.n_eval, None, True, a.workers, a.radius_km, a.weather, a.weather_csv, a.enhanced)
    print(f"cases built in {time.time()-t:.0f}s")

    from equifire.weather import ALERT_WEATHER_FEATURES
    feats = A.FEATURES + (A.EXTRA_FEATURES if a.enhanced else []) + (ALERT_WEATHER_FEATURES if a.weather else [])
    weather_coverage = {}
    if a.weather:
        for name, rows in (("train", tr), ("validation", va), ("test", te)):
            weather_coverage[name] = float(np.concatenate([r["X"][:, -1] for r in rows]).mean())
        print(f"weather coverage: {weather_coverage}", flush=True)
    fit_va = va
    if a.calibration_year is not None:
        fit_va = [r for r in va if r["year"] < a.calibration_year]
        va = [r for r in va if r["year"] == a.calibration_year]
    weights = np.concatenate([r["weights"] for r in tr]) if a.sampling_weights else None
    if weights is not None:
        weights /= weights.mean()
    ci = feats.index("cnt_r1")
    meta = {"definitions": A.__doc__ + "\nFor this run, label hits and recall use radius_km, not the legacy 8-neighbour tolerance.", "features": feats, "grid_deg": 0.01,
            "feature_version": 2 if a.enhanced else 1, "sampling_weights": a.sampling_weights,
            "calibration_year": a.calibration_year, "precision_buffer": a.precision_buffer,
            "n_train_cases": sum(len(np.unique(r["case"])) for r in tr), "weather_csv": str(a.weather_csv.resolve()) if a.weather_csv else None, "weather": a.weather, "weather_coverage": weather_coverage,
            "weather_cutoff": "previous completed UTC day", "radius_km": a.radius_km, "metric": "alert precision within great-circle radius; recall reported separately",
            "countries": a.countries, "train_years": a.train_years, "val_years": a.val_years,
            "test_years": a.test_years, "target_precision": a.target_precision, "models": {}}
    out_lines = []
    for h in (1, 3):
        Xtr, ytr = stack(tr, h); Xva, yva = stack(va, h); Xte, yte = stack(te, h)
        print(f"\n=== h={h}: train {len(ytr)} (pos {ytr.mean():.3f}), val {len(yva)} (pos {yva.mean():.3f}), "
              f"test {len(yte)} (pos {yte.mean():.3f})")
        Xfit, yfit = stack(fit_va, h)
        dtr = xgb.DMatrix(Xtr, ytr, feature_names=feats, weight=weights)
        dfit = xgb.DMatrix(Xfit, yfit, feature_names=feats)
        dva = xgb.DMatrix(Xva, yva, feature_names=feats)
        from sklearn.metrics import average_precision_score
        configs = [(8, 5, .05), (5, 30, .04), (10, 20, .04)]
        best, trial_results = None, []
        for trial, (depth, child, eta) in enumerate(configs[:a.trials]):
            params = {"objective": "binary:logistic", "eval_metric": "aucpr", "tree_method": "hist",
                      "max_depth": depth, "eta": eta, "subsample": .8, "colsample_bytree": .8,
                      "min_child_weight": child, "nthread": 6, "seed": 0, "device": a.device}
            try:
                candidate = xgb.train(params, dtr, 1500, evals=[(dfit, "selection")],
                                      early_stopping_rounds=60, verbose_eval=200)
            except xgb.core.XGBoostError as exc:
                if params["device"] != "cuda" or not any(word in str(exc).lower() for word in ("out of memory", "bad_alloc")):
                    raise
                print("GPU memory insufficient; retrying this trial on CPU", flush=True)
                params["device"] = "cpu"
                candidate = xgb.train(params, dtr, 1500, evals=[(dfit, "selection")],
                                      early_stopping_rounds=60, verbose_eval=200)
            candidate = candidate[:candidate.best_iteration + 1]
            score = float(average_precision_score(yfit, candidate.predict(dfit)))
            trial_results.append({"params": params, "selection_ap": score, "trees": candidate.num_boosted_rounds()})
            print(f"trial {trial+1}: selection AP={score:.4f}", flush=True)
            if best is None or score > best[0]:
                best = (score, candidate, params)
        _, bst, params = best
        pva = bst.predict(dva); pte = bst.predict(xgb.DMatrix(Xte, feature_names=feats))
        thr = choose_threshold(yva, pva, a.target_precision + a.precision_buffer)
        if yva[pva >= thr].mean() < a.target_precision:
            print(f"  target precision not reached on val; using max-precision thr {thr}")
        mv = report(va, pva, thr); mt = report(te, pte, thr)
        # baseline: persistence ring scored by # burning neighbours (cnt_r1 >= k); k chosen on val
        bva, bte = Xva[:, ci], Xte[:, ci]
        bk = None; bcurve_val = {}
        for k in range(1, 9):
            m = bva >= k
            bcurve_val[k] = float(yva[m].mean()) if m.sum() else float("nan")
            if bk is None and m.sum() >= 50 and yva[m].mean() >= a.target_precision:
                bk = k
        bk_note = "reaches target on val"
        if bk is None:
            bk = int(max(bcurve_val, key=lambda k: bcurve_val[k] if np.isfinite(bcurve_val[k]) else -1))
            bk_note = "target not reachable on val; max-precision k"
        b_all = report(te, bte, 1); b_best = report(te, bte, bk)
        curve = {}
        for g in np.round(np.arange(0.5, 0.951, 0.05), 2):
            curve[f"{g:.2f}"] = report(te, pte, g)[h]
        bcurve = {str(k): report(te, bte, k)[h] for k in range(1, 9)}
        from sklearn.metrics import average_precision_score, roc_auc_score
        auc = float(roc_auc_score(yte, pte)); ap_ = float(average_precision_score(yte, pte))
        lines = [f"--- horizon h={h} day(s) ---",
                 f"val-chosen threshold = {thr:.2f}",
                 f"VAL  model : {fmt(mv[h])}",
                 f"TEST model : {fmt(mt[h])}   [cand AUC={auc:.3f}, AP={ap_:.3f}, base rate={yte.mean():.3f}]",
                 f"TEST baseline ring (all cells adjacent to today's fire, k>=1): {fmt(b_all[h])}",
                 f"TEST baseline ring k>={bk} burning neighbours ({bk_note}): {fmt(b_best[h])}",
                 "TEST model curve (thr: P / R / alerts per fire-day):"]
        lines += [f"   {g}: {c['precision']:.3f} / {c['recall']:.3f} / {c['alerts_per_fire_day']:.2f}"
                  for g, c in curve.items()]
        lines += ["TEST baseline curve (k burning neighbours: P / R / alerts per fire-day):"]
        lines += [f"   k>={k}: {c['precision']:.3f} / {c['recall']:.3f} / {c['alerts_per_fire_day']:.2f}"
                  for k, c in bcurve.items()]
        # per-country breakdown on test at the val-chosen threshold
        per_country, off = {}, 0
        offs = []
        for r in te:
            offs.append(off); off += len(r["X"])
        lines += ["TEST per country at val-chosen threshold (model | ring k>=1):"]
        for c in a.countries:
            sub = [(r, o) for r, o in zip(te, offs) if r["country"] == c]
            if not sub:
                continue
            rs = [r for r, _ in sub]
            ps = np.concatenate([pte[o:o + len(r["X"])] for r, o in sub])
            bs = np.concatenate([bte[o:o + len(r["X"])] for r, o in sub])
            mc = report(rs, ps, thr)[h]; bc = report(rs, bs, 1)[h]
            per_country[c] = {"model": mc, "ring": bc}
            lines.append(f"   {c:14s} {fmt(mc)} | ring P={bc['precision']:.3f} R={bc['recall']:.3f}")
        print("\n".join(lines), flush=True); out_lines += lines
        imp = bst.get_score(importance_type="gain")
        meta["models"][str(h)] = {
            "trials": trial_results, "selected_params": params, "file": "alerts1km.json" if h == 1 else "alerts1km_h3.json", "threshold": thr,
            "best_iteration": int(bst.best_iteration) if hasattr(bst, "best_iteration") else None,
            "val": mv[h], "test": mt[h],
            "target_met_validation": bool(mv[h]["precision"] >= a.target_precision),
            "target_met_test": bool(mt[h]["precision"] >= a.target_precision), "test_auc": auc, "test_ap": ap_, "test_curve": curve,
            "baseline_ring_all_test": b_all[h], "baseline_ring_k": bk, "baseline_ring_k_test": b_best[h],
            "baseline_curve_test": bcurve, "test_per_country": per_country, "baseline_val_precision_by_k": bcurve_val,
            "n_train_rows": int(len(ytr)), "n_val_cases": len(case_eval(va, pva, thr)[0]),
            "n_test_cases": len(case_eval(te, pte, thr)[0]),
            "top_features_gain": dict(sorted(imp.items(), key=lambda kv: -kv[1])[:15])}
        if not a.no_save:
            A.MODELS.mkdir(parents=True, exist_ok=True)
            bst.set_param({"device": "cpu"})
            bst.save_model(str(A.MODELS / meta["models"][str(h)]["file"]))
    meta["report"] = out_lines
    if not a.no_save:
        (A.MODELS / "alerts1km_meta.json").write_text(json.dumps(meta, indent=1, default=float))
        print(f"saved models and report in {A.MODELS}")
    print(f"total {time.time()-t:.0f}s")


if __name__ == "__main__":
    main()
