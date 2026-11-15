"""Feasibility test for idea #4: do GOES 10-minute fire pixels of the last hours of day 0 cover day-1 burning cells
that the day-0 VIIRS footprint does not? Small, honest measurement (default 15 fires, 2024, Americas). No model is trained.

For each case (t0, cx, cy) from direct.sample_cases (same sampler as the walk-forward benchmark):
  burn0  = 0.05 deg cells with VIIRS detections on day t0 inside the 41x41 window
  T1     = cells burning on day t0+1 that were not burning on day t0 (new burning cells, VIIRS = truth)
  G      = cells that hold a GOES fire pixel (FDC categories good/saturated/high-prob/medium-prob... conf_rank>=min_conf)
           between 14:00 and 24:00 UTC on day t0
Report (pooled over cases and per-case median):
  base   = share of T1 within 1 cell (~5 km) of burn0   (what a footprint-only forecast starts from)
  extra  = share of T1 within 1 cell of G but NOT within 1 cell of burn0  (what GOES could add)
  prec   = share of GOES-only cells (within 1 cell of G, outside burn0 dilation) that are within 1 cell of
           burning cells on day t0 or t0+1 (VIIRS)   (how many GOES-only cells are real)
Usage: .venv/bin/python scripts/goes_feasibility.py [--n 15] [--seed 1] [--year 2024] [--min-conf 4]
"""
import argparse, json, sys, time
from pathlib import Path
import numpy as np
from scipy import ndimage
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from equifire import direct, fireindex_mm as fm, goes
from equifire.common import GRID

W = 20


def day_index(y):
    return int((np.datetime64(f"{y}-01-01") - np.datetime64("2000-01-01")).astype(int))


def cells(fi, d, cx, cy):
    i = fi.v.query(d, d, cx, cy)
    return set(zip(np.asarray(fi.v.gx)[i].astype(int).tolist(), np.asarray(fi.v.gy)[i].astype(int).tolist()))


def grid(cs, cx, cy):
    m = np.zeros((2 * W + 1, 2 * W + 1), bool)
    for x, y in cs:
        if abs(x - cx) <= W and abs(y - cy) <= W:
            m[x - cx + W, y - cy + W] = True
    return m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=15); ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--year", type=int, default=2024); ap.add_argument("--min-conf", type=int, default=4)
    ap.add_argument("--step", type=int, default=60)
    a = ap.parse_args()
    fi = fm.get_fire_index(verbose=False)
    d0 = day_index(a.year)
    day = np.asarray(fi.day)
    ok = (day >= d0 + 5) & (day < d0 + 360)
    rng = np.random.default_rng(a.seed)
    cases = []
    tries = 0
    while len(cases) < a.n and tries < 4000:
        tries += 1
        c = direct.sample_cases(fi, ok, 1, rng)
        if not c:
            continue
        t0, cx, cy = c[0]
        lon, lat = cx * GRID - 180 + GRID / 2, cy * GRID - 90 + GRID / 2
        if -55 <= lat <= 60 and -165 <= lon <= -35:      # GOES-East / West coverage
            cases.append((t0, cx, cy, lon, lat))
    print("cases", len(cases), flush=True)
    k = np.ones((3, 3), bool)
    rows = []
    for t0, cx, cy, lon, lat in cases:
        t = time.time()
        date = np.datetime64("2000-01-01") + int(t0)
        start = pd.Timestamp(str(date)) + pd.Timedelta(hours=14)
        end = pd.Timestamp(str(date)) + pd.Timedelta(hours=23, minutes=59)
        bbox = (lon - W * GRID, lat - W * GRID, lon + W * GRID, lat + W * GRID)
        try:
            g = goes.goes_fires(bbox, start.to_pydatetime(), end.to_pydatetime(), step_min=a.step, min_conf=a.min_conf, workers=1)
        except Exception as e:  # noqa: BLE001
            print("goes error", type(e).__name__, str(e)[:100], flush=True)
            continue
        b0, b1 = cells(fi, t0, cx, cy), cells(fi, t0 + 1, cx, cy)
        B0, B1 = grid(b0, cx, cy), grid(b1, cx, cy)
        T1 = B1 & ~B0
        gx = np.floor((g.lon.values + 180) / GRID).astype(int) if len(g) else np.zeros(0, int)
        gy = np.floor((g.lat.values + 90) / GRID).astype(int) if len(g) else np.zeros(0, int)
        G = grid(set(zip(gx.tolist(), gy.tolist())), cx, cy)
        near_b0 = ndimage.binary_dilation(B0, k)
        near_g = ndimage.binary_dilation(G, k)
        n_t1 = int(T1.sum())
        extra_cells = G & ~near_b0
        real = ndimage.binary_dilation(B0 | B1, k)
        row = {"t0": str(date), "lat": round(lat, 2), "lon": round(lon, 2), "goes_scans": g.attrs.get("scans", 0), "goes_pixels": int(len(g)),
               "burn0": int(B0.sum()), "new_day1": n_t1,
               "base_cov": float((T1 & near_b0).sum() / n_t1) if n_t1 else None,
               "extra_cov": float((T1 & near_g & ~near_b0).sum() / n_t1) if n_t1 else None,
               "goes_only_cells": int(extra_cells.sum()),
               "goes_only_real": int((extra_cells & real).sum()), "sec": round(time.time() - t, 1)}
        rows.append(row); print(row, flush=True)
    use = [r for r in rows if r["new_day1"]]
    tot = sum(r["new_day1"] for r in use)
    out = {"n_cases": len(rows), "year": a.year, "min_conf": a.min_conf, "step_min": a.step,
           "pooled": {"new_cells_day1": tot,
                      "base_cov": round(sum(r["base_cov"] * r["new_day1"] for r in use) / tot, 3) if tot else None,
                      "extra_cov": round(sum(r["extra_cov"] * r["new_day1"] for r in use) / tot, 3) if tot else None,
                      "goes_only_cells": sum(r["goes_only_cells"] for r in rows),
                      "goes_only_real": sum(r["goes_only_real"] for r in rows)},
           "cases": rows,
           "note": "Coverage test only (no model trained). base = new day-1 cells within 1 cell of day-0 VIIRS footprint; extra = additional share covered only by GOES. Small sample; not a forecast score."}
    (ROOT / "reports" / "goes_feasibility.json").write_text(json.dumps(out, indent=1))
    print(json.dumps(out["pooled"]), flush=True)


if __name__ == "__main__":
    main()
