#!/usr/bin/env python3
"""Paired per-fire A/B of two saved walk-forward models on the same test fires (+ bootstrap CIs).

    .venv/bin/python scripts/modisera_ab.py --year 2024 --a _ab_v3_cpu --b _ab_v3_modisera
Test fires = walk_forward's sample (seed 1000+Y, 100 fires). Saved selection rules of each model (tuned on that
model's own held-out training-period fires; no test data)."""
import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import walk_forward as wf  # noqa: E402
from equifire import direct  # noqa: E402
from equifire.common import MODELS, REPORTS  # noqa: E402
from equifire.fireindex_mm import get_fire_index  # noqa: E402


def boot(d, n=4000):
    rng = np.random.default_rng(0)
    b = d[rng.integers(0, len(d), (n, len(d)))].mean(1)
    return [round(100 * float(x), 2) for x in (d.mean(), np.percentile(b, 2.5), np.percentile(b, 97.5))]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, default=2024)
    ap.add_argument("--a", required=True)
    ap.add_argument("--b", required=True)
    ap.add_argument("--runs", type=int, default=100)
    ap.add_argument("--days", type=int, default=3)
    a = ap.parse_args()
    fi = get_fire_index(False)
    wx = direct.Weather()
    Y = a.year
    lo, hi = wf._day(Y), min(wf._day(Y + 1) - 1, int(fi.day.max()) - a.days)
    cases = sorted(direct.sample_cases(fi, (fi.day >= lo) & (fi.day <= hi), a.runs, np.random.default_rng(1000 + Y)))
    res = {}
    for tag in (a.a, a.b):
        m, meta = direct.load(MODELS / f"wf_{Y}{tag}.json")
        rows = [wf.score(m, meta, fi, wx, c, a.days) for c in cases]
        ok = [r for r in rows]
        res[tag] = (np.array([r[0] for r in ok if r]), np.array([r[1] for r in ok if r]), meta.get("modis_era"))
    out = {"year": Y, "n": len(res[a.a][0]), "a": a.a, "b": a.b}
    for tag, (iou, tol, era) in res.items():
        out[tag] = {"tol_d1": float(tol[:, 0].mean()), "tol_d3": float(tol[:, -1].mean()),
                    "iou_d1": float(iou[:, 0].mean()), "iou_d3": float(iou[:, -1].mean()), "modis_era": era}
        print(tag, {k: round(v, 4) for k, v in out[tag].items() if k != "modis_era"})
    A, B = res[a.a], res[a.b]
    out["b_minus_a_points_ci"] = {f"{m}_d{d}": boot(B[i][:, k] - A[i][:, k])
                                  for m, i in (("tol", 1), ("iou", 0)) for d, k in ((1, 0), (3, a.days - 1))}
    print("B - A (points, [mean, lo, hi]):", out["b_minus_a_points_ci"])
    (REPORTS / f"modisera_ab_{Y}.json").write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
