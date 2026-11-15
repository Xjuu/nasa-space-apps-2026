"""Step 3: train models, compare against baselines on held-out years, save models and report."""
import json
import time

import joblib
import numpy as np
from sklearn.base import clone
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from sklearn.linear_model import LinearRegression
from sklearn.metrics import average_precision_score, f1_score, mean_absolute_error, precision_recall_curve, roc_auc_score

from .common import MODELS, REPORTS

TEST_FROM_YEAR = 2023  # train on years before, test on 2023+ (no future leakage)
USE_GPU = False  # set by main.py --use-gpu: XGBoost on CUDA instead of scikit-learn on CPU
POOL_PARAMS = {}  # set by main.py --use-pool-params: best XGBoost settings found by the training pool
POOL_NAME = {"size/area": "size_area", "size/frp": "size_frp", "spread": "spread"}


def _fit_pool(model, Xt, yt, name):
    """Train XGBoost with the best settings the volunteer pool found."""
    import xgboost as xgb
    b = POOL_PARAMS[POOL_NAME[name]]
    cls = xgb.XGBClassifier if isinstance(model, HistGradientBoostingClassifier) else xgb.XGBRegressor
    m = cls(device="cuda" if USE_GPU else "cpu", tree_method="hist", n_estimators=b["n_trees"], **b["params"])
    t0 = time.time()
    m.fit(Xt, yt)
    m.set_params(device="cpu")
    print(f"[train {name}] pool settings ({b['metric']} {b['score']:.4f} in pool), {b['n_trees']} trees, "
          f"{len(Xt):,} training rows | {time.time() - t0:.0f}s on {'GPU' if USE_GPU else 'CPU'}", flush=True)
    return m


def _to_gpu_model(model):
    """Equivalent XGBoost model on CUDA for a scikit-learn HistGradientBoosting model."""
    import xgboost as xgb
    p = model.get_params()
    common = dict(device="cuda", tree_method="hist", learning_rate=p["learning_rate"],
                  max_leaves=p["max_leaf_nodes"], max_depth=0, grow_policy="lossguide",
                  reg_lambda=p["l2_regularization"], min_child_weight=p["min_samples_leaf"], max_bin=255)
    if isinstance(model, HistGradientBoostingClassifier):
        return xgb.XGBClassifier(objective="binary:logistic", **common)
    return xgb.XGBRegressor(objective="reg:squarederror", **common)


def _fit_progress_gpu(model, Xt, yt, Xv, yv, name, score, label, higher_better, max_iter, step, patience):
    model = _to_gpu_model(model)
    t0 = time.time()
    best, best_it, bad, booster = None, step, 0, None
    for it in range(step, max_iter + 1, step):
        model.set_params(n_estimators=step)
        model.fit(Xt, yt, xgb_model=booster)  # continue training: +step trees on GPU
        booster = model.get_booster()
        s = score(model, Xv, yv)
        better = best is None or (s > best if higher_better else s < best)
        if better:
            best, best_it, bad = s, it, 0
        else:
            bad += 1
        print(f"[train {name} GPU] trees {it:>3} | {len(Xt):,} training rows x {Xt.shape[1]} features | "
              f"validation {label} {s:.4f}{' *best' if better else ''} | {time.time() - t0:.0f}s", flush=True)
        if bad >= patience:
            print(f"[train {name} GPU] validation stopped improving -> keeping {best_it} trees")
            break
    final = model.__class__(**{**model.get_params(), "n_estimators": best_it})
    final.fit(Xt, yt)
    final.set_params(device="cpu")  # saved model predicts anywhere; forecast --use-gpu switches back
    return final


def _split(year):
    """Test on the newest unseen years. Falls back to the last year when little history is cached."""
    global TEST_FROM_YEAR
    years = np.unique(year)
    if len(years) < 2:
        raise SystemExit(f"need at least 2 years of data, have {years.tolist()}")
    if not (years < TEST_FROM_YEAR).any() or not (years >= TEST_FROM_YEAR).any():
        TEST_FROM_YEAR = int(years.max())
        print(f"[split]  little history cached yet -> testing on year {TEST_FROM_YEAR}")
    tr = year < TEST_FROM_YEAR
    return tr, ~tr


def val_mask(n, seed=0):
    return np.random.default_rng(seed).random(n) < 0.1


def _fit_progress(model, X, y, name, score, label, higher_better, max_iter=600, step=50, patience=2, seed=0):
    """Train in steps of `step` trees, print progress + validation score, stop when validation
    stops improving, and return the model refit at the best tree count plus the validation mask."""
    rng = np.random.default_rng(seed)
    val = rng.random(len(X)) < 0.1
    Xt, yt, Xv, yv = X[~val], y[~val], X[val], y[val]
    if POOL_PARAMS.get(POOL_NAME.get(name)):
        return _fit_pool(model, Xt, yt, name), val
    if USE_GPU:
        return _fit_progress_gpu(model, Xt, yt, Xv, yv, name, score, label, higher_better,
                                 max_iter, step, patience), val
    model.set_params(warm_start=True, early_stopping=False)
    t0 = time.time()
    best, best_it, bad = None, step, 0
    for it in range(step, max_iter + 1, step):
        model.set_params(max_iter=it)
        model.fit(Xt, yt)
        s = score(model, Xv, yv)
        better = best is None or (s > best if higher_better else s < best)
        if better:
            best, best_it, bad = s, it, 0
        else:
            bad += 1
        print(f"[train {name}] trees {it:>3} | {len(Xt):,} training rows x {X.shape[1]} features | "
              f"validation {label} {s:.4f}{' *best' if better else ''} | {time.time() - t0:.0f}s", flush=True)
        if bad >= patience:
            print(f"[train {name}] validation stopped improving -> keeping {best_it} trees")
            break
    final = clone(model).set_params(max_iter=best_it, warm_start=False)
    final.fit(Xt, yt)
    return final, val


def _pct(new, old):
    return 100 * (old - new) / old if old else float("nan")


def train_size(X, Y, year, pre=None):
    tr, te = _split(year)
    if te.sum() == 0 or tr.sum() == 0:
        raise SystemExit("size: need data both before and from %d" % TEST_FROM_YEAR)
    print(f"[size]   train {tr.sum():,} pairs (< {TEST_FROM_YEAR}), test {te.sum():,} pairs (>= {TEST_FROM_YEAR})")
    results, models = {}, {}
    raw_col = {"area": "area_sum", "frp": "frp_sum"}
    unit = {"area": "km2", "frp": "MW"}
    for target in ("area", "frp"):
        y = Y[target].values
        x_raw = X[raw_col[target]].values
        lin = LinearRegression().fit(x_raw[tr, None], y[tr])

        if pre and target in pre:
            hgb = pre[target]  # already trained by --parallel lanes
        else:
            hgb = HistGradientBoostingRegressor(learning_rate=0.08, max_leaf_nodes=63, l2_regularization=1.0,
                                                min_samples_leaf=50)
            hgb, _ = _fit_progress(
                hgb, X[tr], np.log1p(y[tr]), f"size/{target}",
                lambda m, Xv, yv: mean_absolute_error(np.expm1(yv), np.expm1(m.predict(Xv))),
                f"MAE ({unit[target]})", higher_better=False)
        pred = {
            "raw MODIS": x_raw[te],
            "linear calibration": lin.predict(x_raw[te, None]),
            "ML model": np.expm1(hgb.predict(X[te])),
        }
        res = {}
        for k, p in pred.items():
            p = np.maximum(p, 0)
            err = np.abs(p - y[te])
            res[k] = {"MAE": float(err.mean()), "median_abs_err": float(np.median(err)),
                      "within_50pct": float(np.mean(err <= 0.5 * np.maximum(y[te], 1e-6)))}
        results[target] = {"unit": unit[target], "test_pairs": int(te.sum()), "metrics": res,
                           "improvement_vs_raw_pct": _pct(res["ML model"]["MAE"], res["raw MODIS"]["MAE"]),
                           "improvement_vs_linear_pct": _pct(res["ML model"]["MAE"], res["linear calibration"]["MAE"])}
        models[target] = hgb
    return results, models


def train_spread(X, label, year, pre=None):
    tr, te = _split(year)
    if te.sum() == 0 or tr.sum() == 0:
        raise SystemExit("spread: need data both before and from %d" % TEST_FROM_YEAR)
    print(f"[spread] train {tr.sum():,} cells (< {TEST_FROM_YEAR}), test {te.sum():,} cells (>= {TEST_FROM_YEAR})")
    y = label

    # Baseline: rule of thumb "more / hotter burning neighbours = more likely to catch fire".
    base_col = max(("nbr_n", "nbr_frp_sum"), key=lambda c: roc_auc_score(y[tr], X[c].values[tr]))

    if pre is not None:
        clf, val = pre, val_mask(int(tr.sum()))  # trained by --parallel GPU lane on the same split
    else:
        clf = HistGradientBoostingClassifier(learning_rate=0.05, max_leaf_nodes=31, l2_regularization=1.0,
                                             min_samples_leaf=100)
        clf, val = _fit_progress(clf, X[tr], y[tr], "spread",
                                 lambda m, Xv, yv: roc_auc_score(yv, m.predict_proba(Xv)[:, 1]),
                                 "ROC-AUC", higher_better=True)

    def best_threshold(yt, st):
        p, r, t = precision_recall_curve(yt, st)
        f = 2 * p * r / np.maximum(p + r, 1e-9)
        return t[np.argmax(f[:-1])]

    # Decision thresholds come from training-period data the model did not fit on (validation rows).
    Xtr, ytr = X[tr], y[tr]
    scores_val = {"baseline": Xtr[base_col].values[val], "ML model": clf.predict_proba(Xtr[val])[:, 1]}
    scores_te = {"baseline": X[base_col].values[te], "ML model": clf.predict_proba(X[te])[:, 1]}
    res, thresholds = {}, {}
    for k in scores_te:
        thr = best_threshold(ytr[val], scores_val[k])
        thresholds[k] = float(thr)
        res[k] = {"ROC_AUC": float(roc_auc_score(y[te], scores_te[k])),
                  "avg_precision": float(average_precision_score(y[te], scores_te[k])),
                  "F1": float(f1_score(y[te], scores_te[k] >= thr))}
    imp = {m: 100 * (res["ML model"][m] - res["baseline"][m]) / res["baseline"][m] for m in res["baseline"]}
    return {"baseline_rule": f"score = {base_col}", "test_cells": int(te.sum()),
            "positive_rate": float(y[te].mean()), "metrics": res, "improvement_pct": imp,
            "features": list(X.columns), "baseline_col": base_col,
            "threshold_ml": thresholds["ML model"], "threshold_baseline": thresholds["baseline"],
            "test_from_year": TEST_FROM_YEAR}, clf


def save(models, results):
    MODELS.mkdir(exist_ok=True)
    REPORTS.mkdir(exist_ok=True)
    for name, m in models.items():
        joblib.dump(m, MODELS / f"{name}.joblib")
    (REPORTS / "results.json").write_text(json.dumps(results, indent=2))


def print_report(results):
    print("\n" + "=" * 72)
    print(" EQUIFIRE - ACCURACY REPORT (test = unseen years >= %d)" % TEST_FROM_YEAR)
    print("=" * 72)
    if "size" in results:
        for target, r in results["size"].items():
            label = "Fire size (burning footprint)" if target == "area" else "Fire radiative power"
            print(f"\n{label}, {r['unit']}  -  MODIS -> VIIRS-equivalent, {r['test_pairs']:,} test pairs")
            print(f"  {'method':<22}{'MAE':>12}{'median err':>13}{'within 50%':>12}")
            for k, m in r["metrics"].items():
                print(f"  {k:<22}{m['MAE']:>12.3f}{m['median_abs_err']:>13.3f}{m['within_50pct']:>11.1%}")
            print(f"  -> ML error {r['improvement_vs_raw_pct']:.1f}% lower than raw MODIS, "
                  f"{r['improvement_vs_linear_pct']:.1f}% lower than linear calibration")
    if "spread" in results:
        r = results["spread"]
        print(f"\nFire spread next day, {r['test_cells']:,} test cells, {r['positive_rate']:.1%} caught fire")
        print(f"  baseline: {r['baseline_rule']}")
        print(f"  {'method':<22}{'ROC-AUC':>10}{'avg prec':>10}{'F1':>8}")
        for k, m in r["metrics"].items():
            print(f"  {k:<22}{m['ROC_AUC']:>10.4f}{m['avg_precision']:>10.4f}{m['F1']:>8.4f}")
        i = r["improvement_pct"]
        print(f"  -> ML better by: ROC-AUC {i['ROC_AUC']:+.1f}%, avg precision {i['avg_precision']:+.1f}%, "
              f"F1 {i['F1']:+.1f}%")
    print("=" * 72)
