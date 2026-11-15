"""#32: wall-clock distribution of the full ML + physics result (GET /predict/simulate) on a running server.

Cases: the saved incidents (data/incidents.json, their areas are usually cached) and a few fresh fire cases sampled from the
2025 archive (areas not yet cached). For each case two calls with the SAME area:
  first  = hours=24  (cold if the area was never computed: landscape/weather download + OSM)
  second = hours=23  (different cache key, so the in-memory result cache does not answer; disk caches are warm)
Reports p50 / p95 / share above 60 s for the first and second calls separately, plus the server-side phase timings
(`timings` in the response). No timeout masks slow calls: a call that exceeds --timeout is recorded as failed, not dropped.
Usage:  .venv/bin/python scripts/measure_predict_time.py [--base http://localhost:8080] [--fresh 3] [--timeout 420]
"""
import argparse, json, sys, time, urllib.error, urllib.parse, urllib.request
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def call(base, p, timeout):
    t = time.time()
    for attempt in range(2):          # one retry after a 503 (server restart by another agent)
        try:
            with urllib.request.urlopen(base + "/predict/simulate?" + urllib.parse.urlencode(p), timeout=timeout) as r:
                d = json.loads(r.read())
            return {"ok": True, "wall_s": round(time.time() - t, 1), "timings": d.get("timings"), "ignition": (d.get("ignition") or {}).get("mode")}
        except urllib.error.HTTPError as e:
            if e.code == 503 and attempt == 0:
                time.sleep(15); t = time.time(); continue
            return {"ok": False, "wall_s": round(time.time() - t, 1), "error": f"HTTP {e.code}"}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "wall_s": round(time.time() - t, 1), "error": f"{type(e).__name__}: {str(e)[:120]}"}


def fresh_cases(n, seed):
    from equifire import direct, fireindex_mm as fm
    from equifire.common import GRID
    fi = fm.get_fire_index(verbose=False)
    d0 = int((np.datetime64("2025-01-01") - np.datetime64("2000-01-01")).astype(int))
    day = np.asarray(fi.day)
    ok = (day >= d0 + 10) & (day < d0 + 330)
    rng = np.random.default_rng(seed)
    out = []
    boxes = [(-34, 5, -74, -34), (-44, -10, 113, 154), (6, 36, 68, 98), (25, 49, -125, -67), (42, 70, -141, -52)]   # the 5 processed countries (bbox)
    inside = lambda la, lo: any(a <= la <= b and c <= lo <= d for a, b, c, d in boxes)
    cand = direct.sample_cases(fi, ok, n * 40, rng)
    cand = [c for c in cand if inside(c[2] * GRID - 90, c[1] * GRID - 180)][:n]
    for t0, cx, cy in cand:
        date = str(np.datetime64("2000-01-01") + np.timedelta64(int(t0), "D"))
        out.append({"name": f"fresh {date}", "lat": round(cy * GRID - 90 + GRID / 2, 3), "lon": round(cx * GRID - 180 + GRID / 2, 3), "date": date})
    return out


def summ(v):
    a = np.array([x["wall_s"] for x in v if x["ok"]])
    return {"n": len(v), "failed": sum(1 for x in v if not x["ok"]),
            "p50_s": round(float(np.median(a)), 1) if a.size else None, "p95_s": round(float(np.percentile(a, 95)), 1) if a.size else None,
            "max_s": round(float(a.max()), 1) if a.size else None, "share_over_60s": round(float(np.mean(np.r_[a > 60, [True] * sum(1 for x in v if not x['ok'])])), 2) if v else None}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8080"); ap.add_argument("--fresh", type=int, default=3); ap.add_argument("--timeout", type=int, default=420)
    ap.add_argument("--incidents", type=int, default=3); ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    inc = json.loads((ROOT / "data" / "incidents.json").read_text())
    inc = inc if isinstance(inc, list) else inc.get("incidents", [])
    cases = [{"name": i["id"], "lat": i["lat"], "lon": i["lon"], "date": i["date"], "group": "saved incident"} for i in inc[:a.incidents]]
    cases += [dict(c, group="fresh area") for c in fresh_cases(a.fresh, a.seed)]
    rows = []
    for c in cases:
        base = {"lat": c["lat"], "lon": c["lon"], "start": c["date"] + "T12:00:00Z", "ignition": "archive", "step_min": 60}
        r1 = call(a.base, dict(base, hours=24), a.timeout)
        r2 = call(a.base, dict(base, hours=23), a.timeout)
        rows.append({"case": c, "first": r1, "second": r2}); print(c["name"], c["group"], r1["wall_s"], r1["ok"], "|", r2["wall_s"], r2["ok"], flush=True)
    out = {"measured_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "base": a.base,
           "first_call": {g: summ([r["first"] for r in rows if r["case"]["group"] == g]) for g in ("saved incident", "fresh area")},
           "second_call_warm": {g: summ([r["second"] for r in rows if r["case"]["group"] == g]) for g in ("saved incident", "fresh area")},
           "cases": rows,
           "note": "Small sample, one machine, server shared with other work. Wall clock of the whole HTTP call (ML daily guidance + physics + weather/landscape). Not a benchmark of every location."}
    (ROOT / "reports" / "ml_physics_timing.json").write_text(json.dumps(out, indent=1))
    print(json.dumps({k: out[k] for k in ("first_call", "second_call_warm")}), flush=True)


if __name__ == "__main__":
    main()
