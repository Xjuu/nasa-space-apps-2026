#!/usr/bin/env python3
"""Pre-warm every cache the EquiFire demo needs, so the demo still works when the network is flaky.

It (1) builds the landscape rasters (ESA WorldCover, Copernicus DEM, OSM roads/rivers, Sentinel-2) for the
event area with equifire.landscape.get_landscape, and (2) calls the RUNNING backend for every endpoint the
demo uses, which fills the backend's own caches (Open-Meteo / NOAA GFS weather in backend/.cache/meteo_*.json,
cache/gfs_forecast, cache/landscape) and the in-memory result caches. Each response is also saved to
cache/demo/<event>/<name>.json, so the numbers in docs/DEMO_SCRIPT.md can be re-read without the network.

Failures are allowed (Overpass in particular is slow/flaky): they are reported, never fatal.

Usage (backend must be running, e.g. `.venv/bin/python -m uvicorn backend.app:app --port 8080`):
    .venv/bin/python scripts/demo_cache.py                       # default event: Flin Flon, 2025-05-28
    .venv/bin/python scripts/demo_cache.py --event yellowknife   # 2023 event (no combined /predict: see DEMO_SCRIPT)
    .venv/bin/python scripts/demo_cache.py --hours 24 --skip-landscape

Notes
- OpenStreetMap *tiles* are NOT pre-fetched: the OSM tile policy forbids bulk/offline prefetching. Only the
  Overpass vector data (roads/rivers, used by the physics barrier layer) is cached here.
- RAM: this process stays well under 1 GB (landscape peak < 0.5 GB per COORDINATION.md).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# lat/lon = centre of the strongest VIIRS burning 0.05-deg cell of the event day (taken from /fires)
EVENTS = {
    "flin_flon": dict(
        name="Flin Flon / Manitoba wildfires, Canada", lat=54.975, lon=-101.725, date="2025-05-28",
        bbox="-102.5,54.3,-101.0,55.5", size_deg=1.5, stats_year=2025,
        combined=True),  # ensemble_blend_2025 exists (fit on 2024) -> /predict works
    "yellowknife": dict(
        name="Yellowknife / NWT wildfires, Canada", lat=62.725, lon=-115.125, date="2023-08-13",
        bbox="-115.9,62.0,-114.4,63.4", size_deg=1.5, stats_year=2023,
        combined=False),  # no blender trained before 2023 -> /predict refuses (honest, no leakage)
}


def get(base, path, params, timeout):
    url = base + path + "?" + urllib.parse.urlencode(params)
    t = time.time()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "EquiFire-demo-cache/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
            return r.status, json.loads(body), time.time() - t, len(body)
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read()).get("detail")
        except Exception:
            detail = None
        return e.code, {"error": detail or str(e)}, time.time() - t, 0
    except Exception as e:  # timeout, connection refused, bad JSON
        return 0, {"error": f"{type(e).__name__}: {e}"}, time.time() - t, 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--event", choices=list(EVENTS), default="flin_flon")
    ap.add_argument("--base", default="http://127.0.0.1:8080")
    ap.add_argument("--hours", type=int, default=24, help="simulation horizon in hours (1-72)")
    ap.add_argument("--half-km", type=float, default=25.0, help="landscape half-width for the direct warm-up")
    ap.add_argument("--timeout", type=float, default=240.0, help="per-request timeout, seconds")
    ap.add_argument("--skip-landscape", action="store_true", help="skip the direct landscape build")
    a = ap.parse_args()

    ev = EVENTS[a.event]
    lat, lon, date = ev["lat"], ev["lon"], ev["date"]
    out_dir = ROOT / "cache" / "demo" / a.event
    out_dir.mkdir(parents=True, exist_ok=True)
    start = f"{date}T12:00"
    rows = []  # (step, status, seconds, note)

    def record(step, status, secs, note=""):
        rows.append((step, status, secs, note))
        print(f"[{status:>5}] {step:<34} {secs:7.1f} s  {note}", flush=True)

    print(f"Event: {ev['name']}  ({lat}, {lon})  {date}   backend: {a.base}\n", flush=True)

    # 1. landscape, built directly in this process (keyless: WorldCover/DEM on AWS, Overpass, Earth Search)
    if not a.skip_landscape:
        t = time.time()
        try:
            from equifire.landscape import get_landscape
            L = get_landscape(lat, lon, a.half_km, cell_m=250.0, date=dt.datetime.fromisoformat(start))
            meta = L.get("meta", {}) if isinstance(L, dict) else {}
            note = json.dumps({k: meta[k] for k in list(meta)[:6]}, default=str)[:160]
            record("landscape (direct)", "ok", time.time() - t, note)
            (out_dir / "landscape_meta.json").write_text(json.dumps(meta, indent=1, default=str))
        except Exception as e:
            record("landscape (direct)", "FAIL", time.time() - t, f"{type(e).__name__}: {e}"[:160])

    # 2. backend endpoints, in the order the demo uses them
    P = dict(lat=lat, lon=lon, date=date)
    steps = [
        ("health", "/health", {}),
        ("classify", "/classify", P),
        ("stats", "/stats", dict(lat=lat, lon=lon, size_deg=ev["size_deg"], year=ev["stats_year"])),
        ("fires (event day)", "/fires", dict(date=date, bbox=ev["bbox"], limit=200)),
        ("clusters (bow-tie)", "/clusters", dict(date=date, bbox=ev["bbox"])),
        ("forecast (ML, 3 d)", "/forecast", dict(P, days=3)),
        ("simulate/weather", "/simulate/weather", dict(lat=lat, lon=lon, start=start, hours=a.hours)),
        ("simulate (physics, archive)", "/simulate",
         dict(lat=lat, lon=lon, start=start, hours=a.hours, step_min=60, ignition="archive", landscape="on")),
        ("predict (ML+physics, 3 d)", "/predict", dict(P, days=3)),
        ("predict/simulate (combined)", "/predict/simulate",
         dict(lat=lat, lon=lon, start=start, hours=a.hours, step_min=60, ignition="archive")),
        ("live/summary", "/live/summary", {}),
        ("live/alerts", "/live/alerts", dict(limit=5)),
        ("overpass", "/overpass", dict(lat=lat, lon=lon)),
    ]
    summary = {}
    for name, path, params in steps:
        status, data, secs, size = get(a.base, path, params, a.timeout)
        ok = status == 200
        note = f"{size / 1e3:.0f} kB" if ok else str(data.get("error"))[:140]
        if not ok and name.startswith("predict") and not ev["combined"]:
            note += "  (expected: no blender trained before this year)"
        record(name, "ok" if ok else f"{status or 'ERR'}", secs, note)
        fn = out_dir / (name.split(" ")[0].replace("/", "_") + ".json")
        fn.write_text(json.dumps(data))
        summary[name] = {"http": status, "seconds": round(secs, 2), "saved": str(fn.relative_to(ROOT))}

    # 3. headline numbers, read back from what we just cached (nothing is invented)
    print("\nHeadline numbers from the cached responses:")
    try:
        c = json.loads((out_dir / "classify.json").read_text())
        print(f"  classify : {c['label']}  z={c['z']:.1f}  anomaly={c['anomaly_score']}  routine={c['routine_score']}"
              f"  reasons={c['reasons']}")
    except Exception as e:
        print("  classify : unavailable", e)
    try:
        f = json.loads((out_dir / "forecast.json").read_text())["summary"]
        print("  forecast :", [(d["date"], d["predicted_cells"], d["observed_cells"], d["iou"]) for d in f["per_day"]],
              "(date, predicted cells, observed cells, IoU)")
    except Exception as e:
        print("  forecast : unavailable", e)
    try:
        s = json.loads((out_dir / "stats.json").read_text())["selected"]["key"]
        print(f"  stats    : {s['year']} {s['value']:.0f} cell-days vs mean {s['baseline_mean']:.1f}, z={s['z']}, "
              f"label={s['label']}, peak week {s['peak_week']}")
    except Exception as e:
        print("  stats    : unavailable", e)

    failed = [r for r in rows if r[1] != "ok"]
    summary_f = out_dir / "_summary.json"
    summary_f.write_text(json.dumps({"event": ev, "run_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                                     "steps": summary}, indent=1))
    print(f"\nCached {len(rows) - len(failed)}/{len(rows)} steps; responses in {out_dir.relative_to(ROOT)}/")
    if failed:
        print("Not cached (allowed to fail):", ", ".join(f"{r[0]} [{r[1]}]" for r in failed))
    print("Total time: %.0f s" % sum(r[2] for r in rows))


if __name__ == "__main__":
    main()
