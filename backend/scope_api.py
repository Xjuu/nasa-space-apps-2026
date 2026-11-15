"""GET /scope/check: range flag for a forecast case (idea #33). A RANGE flag, not a confidence: see equifire/scope_flag.py."""
from __future__ import annotations

import datetime as dt

import numpy as np
from fastapi import APIRouter, HTTPException, Query

router = APIRouter(tags=["scope"])
_CACHE: dict = {}


def _state():
    if "fi" not in _CACHE:
        from equifire import scope_flag as sf
        from equifire.fireindex_mm import get_fire_index
        ref, tiles = sf.load_reference()
        if ref is None:
            raise HTTPException(503, "scope reference not built yet: run scripts/scope_flag_eval.py once")
        _CACHE.update(fi=get_fire_index(verbose=False), ref=ref, tiles=tiles, sf=sf)
    return _CACHE


@router.get("/scope/check", summary="Is this case inside the range of cases the spread model was trained on? (range flag, not a confidence)")
def scope_check(lat: float = Query(..., ge=-90, le=90), lon: float = Query(..., ge=-180, le=180),
                date: str = Query(None, description="YYYY-MM-DD (UTC), default yesterday")) -> dict:
    s = _state()
    from equifire.common import GRID
    d = dt.date.fromisoformat(date) if date else (dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=1))
    t0 = int((np.datetime64(d.isoformat()) - np.datetime64("2000-01-01")).astype(int))
    cx, cy = int((lon + 180) / GRID), int((lat + 90) / GRID)
    out = s["sf"].check(s["sf"].descriptors(s["fi"], t0, cx, cy, s["tiles"]), s["ref"])
    out.update({"date": d.isoformat(), "meaning": "Range flag only: compares simple descriptors with 2013-2022 training-like cases. It is not a probability and does not say how accurate the forecast is.",
                "reference_years": s["ref"]["years"], "reference_cases": s["ref"]["n_reference_cases"]})
    return out
