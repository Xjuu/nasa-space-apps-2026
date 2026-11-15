"""Scope flag (#33): is a forecast case similar to the cases the spread model was trained on?

This is a RANGE flag, not a confidence. It compares simple descriptors of a case with the distribution of the same descriptors
over training-like cases (VIIRS fires 2013-2022):
  n0      cells burning at t0 in the 41x41 window (case size)
  h30     distinct cells that burned in the window during the 30 days before t0 (recent history)
  h365    same over the 365 days before t0 (long history)
  tile    log10 of the number of VIIRS cell-days in the surrounding 5x5 degree tile over 2013-2022 (how much training data exists nearby)
A case is "outside" when n0/h30/h365 fall outside the 1st-99th percentile of the reference, or tile is below the 5th percentile.
Honest limits: it does not know the model's features beyond these four, and a case inside the range can still be predicted badly.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from . import direct
from .common import CACHE, GRID

W = 20
TILE = 100          # cells = 5 degrees
REF = CACHE / "scope_ref.json"


def _day(y):
    return int((np.datetime64(f"{y}-01-01") - np.datetime64("2000-01-01")).astype(int))


def _cells(fi, d0, d1, cx, cy):
    i = fi.v.query(d0, d1, cx, cy)
    if len(i) == 0:
        return 0
    return int(len(np.unique(np.asarray(fi.v.gx)[i].astype(np.int64) * 100000 + np.asarray(fi.v.gy)[i].astype(np.int64))))


def tile_counts(fi, y0=2013, y1=2022, chunk=20_000_000):
    day = np.asarray(fi.day)
    lo, hi = np.searchsorted(day, [_day(y0), _day(y1 + 1)]) if np.all(day[:-1] <= day[1:]) else (0, len(day))
    nx, ny = 360 * 20 // TILE * 5 // 5, 180 * 20 // TILE
    nx, ny = int(360 / GRID) // TILE + 1, int(180 / GRID) // TILE + 1
    cnt = np.zeros(nx * ny, np.int64)
    for a in range(lo, hi, chunk):
        b = min(hi, a + chunk)
        d = day[a:b]
        m = (d >= _day(y0)) & (d < _day(y1 + 1))
        tx = (np.asarray(fi.gx[a:b])[m].astype(np.int64)) // TILE
        ty = (np.asarray(fi.gy[a:b])[m].astype(np.int64)) // TILE
        cnt += np.bincount(tx * ny + ty, minlength=nx * ny)[:nx * ny]
    return cnt.reshape(nx, ny)


def descriptors(fi, t0, cx, cy, tiles):
    return {"n0": _cells(fi, t0, t0, cx, cy), "h30": _cells(fi, t0 - 30, t0 - 1, cx, cy), "h365": _cells(fi, t0 - 365, t0 - 1, cx, cy),
            "tile": float(np.log10(1 + tiles[cx // TILE, cy // TILE]))}


def build_reference(fi, n=1500, seed=0):
    tiles = tile_counts(fi)
    day = np.asarray(fi.day)
    ok = (day >= _day(2013)) & (day < _day(2023))
    rng = np.random.default_rng(seed)
    rows = [descriptors(fi, *c, tiles) for c in direct.sample_cases(fi, ok, n, rng)]
    ref = {k: {"p1": float(np.percentile([r[k] for r in rows], 1)), "p5": float(np.percentile([r[k] for r in rows], 5)),
               "p50": float(np.percentile([r[k] for r in rows], 50)), "p99": float(np.percentile([r[k] for r in rows], 99))} for k in rows[0]}
    out = {"n_reference_cases": len(rows), "years": "2013-2022", "quantiles": ref}
    np.save(CACHE / "scope_tiles.npy", tiles)
    REF.write_text(json.dumps(out, indent=1))
    return out, tiles


def load_reference():
    if REF.exists() and (CACHE / "scope_tiles.npy").exists():
        return json.loads(REF.read_text()), np.load(CACHE / "scope_tiles.npy")
    return None, None


def check(desc, ref):
    q = ref["quantiles"]
    reasons = []
    for k in ("n0", "h30", "h365"):
        if desc[k] < q[k]["p1"] or desc[k] > q[k]["p99"]:
            reasons.append(f"{k}={desc[k]} outside the training range [{q[k]['p1']:.0f}, {q[k]['p99']:.0f}]")
    if desc["tile"] < q["tile"]["p5"]:
        reasons.append(f"little training data in this 5-degree tile (log10 cell-days {desc['tile']:.2f} < {q['tile']['p5']:.2f})")
    return {"in_range": not reasons, "reasons": reasons, "descriptors": desc}
