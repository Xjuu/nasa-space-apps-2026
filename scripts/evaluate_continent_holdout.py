#!/usr/bin/env python3
"""Offline ML-only geographic holdout diagnostic, with a matched temporal control.

The four represented groups are NOT global continental coverage. Country-tagged
SNPP cache files select case centres; global MODIS/VIIRS index supplies local
history/truth. Each excluded group is absent from fitting, early stopping AND
threshold calibration. Production model artifacts are never read or replaced.
"""
from __future__ import annotations
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import socket
import sys
import time

for _name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ[_name] = "2"
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import numpy as np
from scipy import ndimage

GROUPS = {"South_America": ["Brazil"], "Oceania": ["Australia"],
          "Asia": ["India"], "North_America": ["United_States", "Canada"]}
THRESHOLDS = [.05, .1, .15, .2, .25, .3, .4, .5]
HORIZONS = (1, 2, 3)


def day(year):
    return int((np.datetime64(f"{year}-01-01") - np.datetime64("2000-01-01")).astype(int))


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def case_tuple(case):
    return tuple(int(case[k]) for k in ("day", "gx", "gy"))


def validate_partition(cases, excluded, start_year, end_year, horizon=3):
    """Fail closed if a held-out group or future label crosses a training stage."""
    allowed = {country for group, countries in GROUPS.items() if group != excluded for country in countries}
    for c in cases:
        if c["country"] not in allowed or c["group"] == excluded:
            raise ValueError("Excluded geography entered a source partition")
        if not day(start_year) <= c["day"] or c["day"] + horizon >= day(end_year + 1):
            raise ValueError("A label crosses the temporal partition boundary")
        if c["country"] not in GROUPS.get(c["group"], []):
            raise ValueError("Country/group mismatch")
    if len({case_tuple(c) for c in cases}) != len(cases):
        raise ValueError("Duplicate case in a partition")


def allocations(n, groups):
    if not groups:
        raise ValueError("No source groups")
    return {g: n // len(groups) + (i < n % len(groups)) for i, g in enumerate(groups)}


def sample_partition(fi, excluded, years, n, seed, source_manifest):
    import pyarrow.parquet as pq
    rng = np.random.default_rng(seed)
    selected = []
    # Balanced group budgets avoid silently granting more fit examples to the control.
    for group, quota in allocations(n, [g for g in GROUPS if g != excluded]).items():
        candidates = []
        for country in GROUPS[group]:
            for year in years:
                path = ROOT / "cache/cells" / f"viirs-snpp_{year}_{country}.parquet"
                if not path.exists():
                    raise FileNotFoundError(f"Required country-labelled cache is absent: {path.name}")
                pf = pq.ParquetFile(path)
                source_manifest[str(path.relative_to(ROOT))] = {
                    "size": path.stat().st_size, "mtime_ns": path.stat().st_mtime_ns,
                    "rows": pf.metadata.num_rows, "fingerprint_kind": "size/mtime/schema, not a full-content checksum",
                    "schema_sha256": hashlib.sha256(str(pf.schema).encode()).hexdigest()}
                table = pf.read(columns=["day", "gx", "gy"])
                cols = [table[c].to_numpy() for c in ("day", "gx", "gy")]
                count = min(len(cols[0]), max(300, quota * 35))
                indices = rng.choice(len(cols[0]), count, replace=False)
                for idx in indices:
                    d, x, y = (int(a[idx]) for a in cols)
                    if d + 3 >= day(max(years) + 1):
                        continue
                    candidates.append({"day": d, "gx": x, "gy": y, "country": country,
                                       "group": group, "source": path.name})
        rng.shuffle(candidates)
        chosen, blocks, exact = [], set(), set()
        for c in candidates:
            key = case_tuple(c)
            block = (c["day"] // 28, c["gx"] // 10, c["gy"] // 10)
            if key in exact or block in blocks:
                continue
            if len(fi.v.query(key[0], key[0], key[1], key[2])) < 5:
                continue
            chosen.append(c); exact.add(key); blocks.add(block)
            if len(chosen) == quota:
                break
        if len(chosen) != quota:
            raise ValueError(f"Insufficient eligible {group} cases: wanted {quota}, found {len(chosen)}")
        selected.extend(chosen)
    validate_partition(selected, excluded, min(years), max(years))
    return sorted(selected, key=case_tuple)


def mask_metrics(predicted, truth, probabilities, eligible):
    p, t = np.asarray(predicted, bool), np.asarray(truth, bool)
    tp, npred, nobs = int((p & t).sum()), int(p.sum()), int(t.sum())
    union = int((p | t).sum())
    if npred + nobs:
        tolerant = float(((p & ndimage.binary_dilation(t, np.ones((3, 3)))).sum() +
                          (t & ndimage.binary_dilation(p, np.ones((3, 3)))).sum()) / (npred + nobs))
    else:
        tolerant = 1.0
    q, y = probabilities[eligible], t[eligible]
    return {"iou": tp / union if union else 1.0, "within_5km": tolerant,
            "precision": tp / npred if npred else None, "recall": tp / nobs if nobs else None,
            "brier": float(np.mean((q - y) ** 2)) if len(q) else None,
            "predicted_cells": npred, "observed_cells": nobs, "eligible_cells": int(eligible.sum())}


def predictions(model, fi, weather, cases, features):
    from equifire import direct
    out = []
    for case in cases:
        r = direct.predict_case(model, fi, *case_tuple(case), weather, 3, features)
        if r is None:
            raise ValueError(f"No model inputs for preselected case {case_tuple(case)}")
        p, a, dist, a0 = r
        out.append((case, p, direct.case_truth(a, dist, a0, 3), dist))
    return out


def choose_thresholds(rows):
    """Selection uses source-only calibration cases, never the excluded test region."""
    scores = np.zeros((3, len(THRESHOLDS)))
    for _, p, truth, _ in rows:
        for k in range(3):
            for j, threshold in enumerate(THRESHOLDS):
                mask = np.nan_to_num(p[k], nan=-1) >= threshold
                union = (mask | truth[k]).sum()
                scores[k, j] += (mask & truth[k]).sum() / union if union else 1.0
    if not rows:
        raise ValueError("No source calibration cases")
    return [THRESHOLDS[int(s.argmax())] for s in scores]


def evaluate(rows, thresholds):
    by_case = []
    for case, p, truth, dist in rows:
        days = []
        for k in range(3):
            mask = np.nan_to_num(p[k], nan=-1) >= thresholds[k]
            eligible = (dist >= 1) & (dist <= k + 1) & np.isfinite(p[k])
            days.append(mask_metrics(mask, truth[k], p[k], eligible))
        by_case.append({"case": case, "horizons": days})
    aggregate = []
    for k in range(3):
        scores = {}
        for name in ("iou", "within_5km", "precision", "recall", "brier"):
            values = [c["horizons"][k][name] for c in by_case if c["horizons"][k][name] is not None]
            scores[name] = float(np.mean(values)) if values else None
            scores[name + "_cases"] = len(values)
        scores["empty_truth_cases"] = sum(c["horizons"][k]["observed_cells"] == 0 for c in by_case)
        scores["eligible_cells"] = sum(c["horizons"][k]["eligible_cells"] for c in by_case)
        aggregate.append(scores)
    return {"horizons": aggregate, "cases": by_case, "n_cases": len(rows),
            "countries": dict(Counter(c["country"] for c, *_ in rows))}


def case_summary(cases):
    return {"n": len(cases), "countries": dict(Counter(c["country"] for c in cases)),
            "groups": dict(Counter(c["group"] for c in cases)),
            "first_day": min(c["day"] for c in cases), "last_label_day": max(c["day"] + 3 for c in cases),
            "cases_sha256": digest(cases), "cases": cases}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--test-year", type=int, default=2024)
    parser.add_argument("--fit-cases", type=int, default=400)
    parser.add_argument("--early-cases", type=int, default=80)
    parser.add_argument("--policy-cases", type=int, default=80)
    parser.add_argument("--test-cases", type=int, default=20, help="per represented heldout group")
    parser.add_argument("--groups", nargs="+", choices=list(GROUPS), default=list(GROUPS))
    parser.add_argument("--run-id", default=datetime.now(timezone.utc).strftime("pilot_%Y%m%d_%H%M%S"))
    parser.add_argument("--seed", type=int, default=20261115)
    args = parser.parse_args()
    if any(n < 4 for n in (args.fit_cases, args.early_cases, args.policy_cases, args.test_cases)):
        parser.error("Each sample budget must be at least 4")
    if not args.run_id.replace("_", "").replace("-", "").isalnum():
        parser.error("run-id must contain letters, digits, underscore or hyphen")
    started = time.perf_counter()
    # This experiment is expressly offline; an accidental future fetch must fail.
    def no_network(*a, **kw):
        raise RuntimeError("Network is forbidden in the continent holdout experiment")
    socket.socket.connect = no_network
    socket.create_connection = no_network
    from equifire import direct, short_spread
    from equifire.fireindex_mm import get_fire_index, DIR as INDEX_DIR
    import xgboost as xgb
    fi = get_fire_index(); weather = direct.Weather(); features = list(direct.V2_FEATURES)
    run_dir = ROOT / "models/continent_holdout" / args.run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    sources = {}
    settings = vars(args)
    test_year = args.test_year
    tests = {}
    for i, group in enumerate(args.groups):
        # Sample the wanted test geography alone; no test labels influence model setup.
        # A common country-labelled sampler fixes test cases independently of models.
        all_test = sample_partition(fi, None, [test_year], args.test_cases * 4, args.seed + 900 + i, sources)
        tests[group] = [c for c in all_test if c["group"] == group]
    report = {"scope": "ML-only daily diagnostic; not combined ML + physics and not global validation",
              "groups": GROUPS, "settings": settings,
              "protocol": {"fit_years": list(range(test_year - 5, test_year - 2)), "early_year": test_year - 2,
                           "policy_year": test_year - 1, "test_year": test_year,
                           "label_horizons": [1, 2, 3], "threshold_grid_fixed_before_test": THRESHOLDS,
                           "balanced_source_group_case_budget": True, "threads": 2, "network": "disabled",
                           "weather": "legacy local ERA5 cache; missing values retained, not imputed from future forecasts",
                           "source_cases": "country-labelled SNPP cache, >=5 active cells at t0; unique 28day/10cell proxy blocks",
                           "validation": "early stopping and policy calibration exclude heldout geography as well",
                           "production_models": "not loaded, not overwritten"},
              "index_meta_sha256": hashlib.sha256((INDEX_DIR / "meta.json").read_bytes()).hexdigest(),
              "implementation_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "folds": {}, "comparisons": {}}
    models = {}
    for fold_index, excluded in enumerate([None] + args.groups):
        name = excluded or "all_regions_control"
        t = time.perf_counter()
        train = sample_partition(fi, excluded, range(test_year - 5, test_year - 2), args.fit_cases, args.seed + fold_index, sources)
        early = sample_partition(fi, excluded, [test_year - 2], args.early_cases, args.seed + 100 + fold_index, sources)
        policy = sample_partition(fi, excluded, [test_year - 1], args.policy_cases, args.seed + 200 + fold_index, sources)
        print(f"[{name}] sampled fit/early/policy {len(train)}/{len(early)}/{len(policy)}", flush=True)
        xt, yt, wt = short_spread.rows(fi, [case_tuple(c) for c in train], weather, features, args.seed)
        xv, yv, wv = short_spread.rows(fi, [case_tuple(c) for c in early], weather, features, args.seed + 1)
        rows_seconds = time.perf_counter() - t
        if len(np.unique(yt)) != 2:
            raise ValueError("Fit labels do not contain both classes")
        model = xgb.XGBClassifier(objective="binary:logistic", tree_method="hist", device="cpu", n_jobs=2,
            n_estimators=200, max_depth=5, learning_rate=.05, min_child_weight=10, subsample=.8,
            colsample_bytree=.8, reg_lambda=2., eval_metric="logloss", early_stopping_rounds=25, random_state=args.seed)
        model.fit(xt, yt, sample_weight=wt, eval_set=[(xv, yv)], sample_weight_eval_set=[wv], verbose=False)
        best = model.best_iteration + 1
        model = direct._reload(model.get_booster()[:best]); model.set_params(n_jobs=2, device="cpu")
        missing_weather = float(np.isnan(xt[:, features.index("wind_speed")]).mean())
        del xt, yt, wt, xv, yv, wv
        thresholds = choose_thresholds(predictions(model, fi, weather, policy, features + ["k"]))
        path = run_dir / f"{name}.json"; model.save_model(path)
        meta = {"excluded_group": excluded, "features": features + ["k"], "trees": best,
                "thresholds": thresholds, "fitting": case_summary(train), "early_stopping": case_summary(early),
                "policy_calibration": case_summary(policy), "training_row_missing_wind_fraction": missing_weather,
                "model_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "rows_seconds": round(rows_seconds, 3), "elapsed_seconds": round(time.perf_counter() - t, 3)}
        path.with_name(path.stem + "_meta.json").write_text(json.dumps(meta, indent=2))
        report["folds"][name] = meta
        models[name] = (model, thresholds)
        print(f"[{name}] {best} trees, thresholds={thresholds}, {meta['elapsed_seconds']:.1f}s", flush=True)
    for group, cases in tests.items():
        comparisons = {}
        for name in ("all_regions_control", group):
            model, thresholds = models[name]
            comparisons[name] = evaluate(predictions(model, fi, weather, cases, features + ["k"]), thresholds)
        comparisons["paired_delta_holdout_minus_control"] = [
            {metric: comparisons[group]["horizons"][k][metric] - comparisons["all_regions_control"]["horizons"][k][metric]
             for metric in ("iou", "within_5km", "brier")} for k in range(3)]
        report["comparisons"][group] = comparisons
        print(f"[test {group}] n={len(cases)}, day1 tolerant control/heldout "
              f"{comparisons['all_regions_control']['horizons'][0]['within_5km']:.3f}/"
              f"{comparisons[group]['horizons'][0]['within_5km']:.3f}", flush=True)
    report["source_files"] = sources
    report["elapsed_seconds"] = round(time.perf_counter() - started, 3)
    report["limitations"] = ["Five selected countries in four represented continental groups; no Africa/Europe validation.",
        "Small retrospective pilot; proxy blocks are not independently identified wildfire events.",
        "Control and excluded fits have matched budgets, but sampling variance remains; one seed is not significance.",
        "ML-only v2 experiment, not a production ML+physics benchmark or calibrated incident confidence.",
        "VIIRS detections are imperfect truth; weather cache may be missing and is reanalysis, not as-issued forecasts.",
        "Same test cases used for both models, no test-dependent thresholds or online learning."]
    out = ROOT / "reports" / f"continent_holdout_{args.run_id}.json"
    out.write_text(json.dumps(report, indent=2)); print(f"WROTE {out} in {report['elapsed_seconds']:.1f}s", flush=True)


if __name__ == "__main__":
    main()
