"""#6: "explosive growth" early-warning classifier (research experiment, not wired into the app).

Question: given only what is known at the end of day t0, will the burning footprint at t0+1 add many new cells?
Case: (t0, cx, cy) from direct.sample_cases (a burning cell with >= 5 burning cells in its 41x41 window), same sampler as the benchmark.
Label (explosive): new cells on day t0+1 (burning at t0+1, not at t0, within 2 cells ~10 km of a day-t0 burning cell) >= max(10, n0),
  i.e. the footprint at least doubles and adds >= 10 cells.
Features (all known at t0): n0, cells burning at t0-1 and t0-2, growth ratios, 30/365-day history, FRP sum and max at t0, ring counts, season, |lat|.
Split: train 2013-2021, validation 2022 (threshold), test 2024 (never used for any choice). Baseline: n0 alone.
Metrics: ROC-AUC, precision / recall at the validation-chosen threshold, base rate. Output reports/explosive_growth.json.
Usage: .venv/bin/python scripts/explosive_growth.py [--train 6000] [--test 400]
"""
import argparse, json, sys, time
from pathlib import Path
import numpy as np
from scipy import ndimage

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from equifire import direct
from equifire.fireindex_mm import get_fire_index
from equifire.common import GRID

W = 20


def D(y):
    return int((np.datetime64(f"{y}-01-01") - np.datetime64("2000-01-01")).astype(int))


def feats_label(fi, t0, cx, cy, frp):
    def grid(d0, d1):
        i = fi.v.query(d0, d1, cx, cy)
        m = np.zeros((2 * W + 1, 2 * W + 1), bool)
        if len(i):
            m[np.asarray(fi.v.gx)[i].astype(int) - cx + W, np.asarray(fi.v.gy)[i].astype(int) - cy + W] = True
        return m, i
    B0, i0 = grid(t0, t0)
    B1, _ = grid(t0 + 1, t0 + 1)
    Bm1, _ = grid(t0 - 1, t0 - 1)
    Bm2, _ = grid(t0 - 2, t0 - 2)
    H30, _ = grid(t0 - 30, t0 - 1)
    H365, _ = grid(t0 - 365, t0 - 1)
    n0 = int(B0.sum())
    near = ndimage.binary_dilation(B0, np.ones((5, 5), bool))
    new = int((B1 & ~B0 & near).sum())
    y = int(new >= max(10, n0))
    fr = np.asarray(frp)[i0] if len(i0) else np.zeros(1)
    ring2 = int(ndimage.binary_dilation(B0, np.ones((5, 5), bool)).sum() - n0)
    doy = (t0 % 365.25) / 365.25 * 2 * np.pi
    lat = abs(cy * GRID - 90)
    x = [n0, int(Bm1.sum()), int(Bm2.sum()), n0 / max(1, int(Bm1.sum())), int(Bm1.sum()) / max(1, int(Bm2.sum())), int(H30.sum()), int(H365.sum()),
         float(fr.sum()), float(fr.max()), ring2, float(np.sin(doy)), float(np.cos(doy)), lat]
    return x, y, new, n0


def build(fi, frp, y0, y1, n, seed):
    day = np.asarray(fi.day)
    ok = (day >= D(y0)) & (day < D(y1 + 1) - 2)
    cases = direct.sample_cases(fi, ok, n, np.random.default_rng(seed))
    X, Y, N = [], [], []
    for c in cases:
        x, y, new, n0 = feats_label(fi, *c, frp)
        X.append(x); Y.append(y); N.append(n0)
    return np.array(X, float), np.array(Y, int), np.array(N, float)


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--train", type=int, default=6000); ap.add_argument("--test", type=int, default=400)
    a = ap.parse_args()
    import xgboost as xgb
    from sklearn.metrics import roc_auc_score
    fi = get_fire_index(verbose=False)
    frp = getattr(fi.v, "frp", None)
    if frp is None:
        frp = np.zeros(len(fi.v.gx))
    t = time.time()
    Xtr, Ytr, _ = build(fi, frp, 2013, 2021, a.train, 1); print("train", Xtr.shape, Ytr.mean().round(3), round(time.time() - t), "s", flush=True)
    Xva, Yva, Nva = build(fi, frp, 2022, 2022, max(500, a.train // 6), 2)
    Xte, Yte, Nte = build(fi, frp, 2024, 2024, a.test, 3)
    m = xgb.XGBClassifier(n_estimators=300, max_depth=4, learning_rate=0.05, subsample=0.8, colsample_bytree=0.8, eval_metric="auc", early_stopping_rounds=30, n_jobs=4)
    m.fit(Xtr, Ytr, eval_set=[(Xva, Yva)], verbose=False)
    pva, pte = m.predict_proba(Xva)[:, 1], m.predict_proba(Xte)[:, 1]
    # threshold chosen on validation: maximise F1
    best = (0, 0.5)
    for th in np.linspace(0.05, 0.95, 91):
        p = pva >= th; tp = (p & (Yva == 1)).sum(); pr = tp / max(1, p.sum()); rc = tp / max(1, (Yva == 1).sum()); f1 = 2 * pr * rc / max(1e-9, pr + rc)
        if f1 > best[0]: best = (f1, th)
    th = best[1]
    def met(p, y):
        pos = p; tp = (pos & (y == 1)).sum()
        return {"precision": round(float(tp / max(1, pos.sum())), 3), "recall": round(float(tp / max(1, (y == 1).sum())), 3), "flagged": int(pos.sum())}
    base_va_th = np.percentile(Nva, 100 * (1 - Yva.mean()))            # baseline: flag the largest fires at the same flag rate as the base rate
    out = {"label": "new cells at t0+1 (within 2 cells of day-t0 burning cells) >= max(10, n0)", "split": "train 2013-2021, val 2022, test 2024",
           "n_train": len(Ytr), "n_val": len(Yva), "n_test": len(Yte), "base_rate_train": round(float(Ytr.mean()), 3), "base_rate_test": round(float(Yte.mean()), 3),
           "test": {"auc_model": round(float(roc_auc_score(Yte, pte)), 3) if Yte.min() != Yte.max() else None,
                    "auc_baseline_n0": round(float(roc_auc_score(Yte, Nte)), 3) if Yte.min() != Yte.max() else None,
                    "threshold_from_validation": round(float(th), 2), "model": met(pte >= th, Yte), "baseline_n0_same_rate": met(Nte >= base_va_th, Yte)},
           "note": "Research experiment on 400 test cases from one year; not wired into the app. Label is a footprint-growth definition on VIIRS cells, not a measure of danger."}
    (ROOT / "reports" / "explosive_growth.json").write_text(json.dumps(out, indent=1))
    print(json.dumps(out["test"]), out["base_rate_train"], out["base_rate_test"])


if __name__ == "__main__":
    main()
