"""Build the scope reference and measure whether flagged cases do worse (small, honest check, #33).
Usage: .venv/bin/python scripts/scope_flag_eval.py [--n 60] [--year 2024]  -> reports/scope_flag.json
Score = within-5 km overlap on day 1 and day 3 of the saved walk-forward model for that year (models/wf_<year>.json).
"""
import argparse, json, sys
from pathlib import Path
import numpy as np
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import walk_forward as wf
from equifire import direct, scope_flag as sf
from equifire.fireindex_mm import get_fire_index

ap = argparse.ArgumentParser(); ap.add_argument("--n", type=int, default=60); ap.add_argument("--year", type=int, default=2024)
a = ap.parse_args()
fi = get_fire_index(verbose=False)
ref, tiles = sf.load_reference()
if ref is None:
    ref, tiles = sf.build_reference(fi)
print("reference", ref["n_reference_cases"], flush=True)
mf = ROOT / "models" / f"wf_{a.year}.json"
model, meta = direct.load(mf, mf.with_name(mf.stem + "_meta.json"))
wx = direct.Weather()
lo, hi = wf._day(a.year), wf._day(a.year + 1) - 1 - 3
day = np.asarray(fi.day)
ok = (day >= lo) & (day <= hi)
cases = direct.sample_cases(fi, ok, a.n, np.random.default_rng(2024))
rows = []
for c in cases:
    r = wf.score(model, meta, fi, wx, c, 3)
    if r is None:
        continue
    iou, tol = r
    d = sf.descriptors(fi, *c, tiles); f = sf.check(d, ref)
    rows.append({"case": [int(c[0]), int(c[1]), int(c[2])], "in_range": f["in_range"], "reasons": f["reasons"], "d1_tol": float(tol[0]), "d3_tol": float(tol[2]), **{k: d[k] for k in d}})
def agg(rs):
    return {"n": len(rs), "day1_within5km": round(float(np.mean([r["d1_tol"] for r in rs])), 3) if rs else None, "day3_within5km": round(float(np.mean([r["d3_tol"] for r in rs])), 3) if rs else None}
inn = [r for r in rows if r["in_range"]]; out = [r for r in rows if not r["in_range"]]
res = {"year": a.year, "scored_cases": len(rows), "share_flagged_outside": round(len(out) / max(1, len(rows)), 3), "in_range": agg(inn), "outside": agg(out), "all": agg(rows),
       "reference": ref, "cases": rows,
       "note": "Small sample (one year, one model). A range flag, not a confidence. Differences within noise must not be read as a validated effect."}
(ROOT / "reports" / "scope_flag.json").write_text(json.dumps(res, indent=1))
print(json.dumps({k: res[k] for k in ("scored_cases", "share_flagged_outside", "in_range", "outside", "all")}))
