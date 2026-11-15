"""GET /harmonized/series: 2000-2025 fire series, raw MODIS / raw VIIRS / harmonized (one record).

    GET /harmonized/series                                   -> lists regions, measures, series
    GET /harmonized/series?region=amazon_arc&measure=cell_days[&resolution=year|month]

Data comes from the files written by scripts/harmonized_series.py (reports/harmonized_series.csv and
reports/harmonization_headline.json); nothing is computed per request. See that script's docstring for
the exact definition of each series.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
from threading import Lock

from fastapi import APIRouter, HTTPException, Query

router = APIRouter(tags=["harmonized"])
REPORTS = Path(__file__).resolve().parent.parent / "reports"
CSV_F, HEAD_F = REPORTS / "harmonized_series.csv", REPORTS / "harmonization_headline.json"
_MEASURE_COL = {"cell_days": "burning_cell_days", "detections": "detections", "frp_mw": "frp_sum_mw"}
_ROLE = {"modis_raw": "raw", "viirs_snpp_raw": "raw", "viirs_n20_raw": "raw", "viirs_all_raw": "raw",
         "raw_splice": "naive", "recon_from_modis": "reference", "harmonized": "harmonized", "harmonized_cal": "harmonized"}
_ORDER = ["modis_raw", "viirs_snpp_raw", "viirs_n20_raw", "viirs_all_raw", "raw_splice", "recon_from_modis",
          "harmonized", "harmonized_cal"]
_lock = Lock()
_mem: dict = {"mtime": None}


def _load():
    if not CSV_F.exists() or not HEAD_F.exists():
        raise HTTPException(503, "harmonized series not built yet (run scripts/harmonized_series.py report)")
    mt = (CSV_F.stat().st_mtime, HEAD_F.stat().st_mtime)
    with _lock:
        if _mem["mtime"] == mt:
            return _mem
        data: dict = {}
        with open(CSV_F, newline="") as f:
            for r in csv.DictReader(f):
                d = data.setdefault((r["region"], r["series"]), {})
                d[(int(r["year"]), int(r["month"]))] = (r["burning_cell_days"], r["detections"], r["frp_sum_mw"])
        _mem.update(mtime=mt, data=data, head=json.loads(HEAD_F.read_text()))
        return _mem


def _num(x):
    return None if x in ("", None) else float(x)


@router.get("/harmonized/series")
def harmonized_series(region: str | None = Query(None), measure: str | None = Query(None),
                      resolution: str = Query("year", pattern="^(year|month)$")):
    m = _load()
    head = m["head"]
    if region is None and measure is None:
        return {"regions": {k: v["label"] for k, v in head["regions"].items()},
                "measures": head["measures"], "series": {s: head["definitions"][s] for s in _ORDER},
                "resolutions": ["year", "month"]}
    if region not in head["regions"]:
        raise HTTPException(404, f"unknown region; use one of {sorted(head['regions'])}")
    if measure not in _MEASURE_COL:
        raise HTTPException(400, f"unknown measure; use one of {sorted(_MEASURE_COL)}")
    k = list(_MEASURE_COL).index(measure)
    months = sorted({ym for s in _ORDER for ym in m["data"].get((region, s), {})})
    if resolution == "month":
        periods = [f"{y}-{mo:02d}" for y, mo in months]
        slots = [[ym] for ym in months]
    else:
        ys = sorted({y for y, _ in months})
        periods = ys
        slots = [[(y, mo) for mo in range(1, 13)] for y in ys]
    series = []
    for s in _ORDER:
        d = m["data"].get((region, s), {})
        vals = []
        for slot in slots:
            v = [_num(d[ym][k]) for ym in slot if ym in d and _num(d[ym][k]) is not None]
            # a year is only reported when all 12 months exist for that sensor (partial years -> null),
            # except the years flagged in `partial_years` where the sensor started / the record is incomplete
            vals.append(round(sum(v), 1) if v and (resolution == "month" or len(v) == 12) else None)
        series.append({"id": s, "label": head["definitions"][s], "role": _ROLE[s], "values": vals})
    tr = {k2.split("|")[0]: {"trend_pct_per_year": v["trend_only"]["trend_pct_per_year"],
                             "trend_pct_per_year_with_2012_step": v["trend_step2012"]["trend_pct_per_year"],
                             "step2012_ratio": v["trend_step2012"]["step2012_ratio"],
                             "verdict": v.get("verdict")}
          for k2, v in head["trend"][region][measure].items() if k2.endswith("2003_2025") and v}
    return {
        "region": region, "region_label": head["regions"][region]["label"], "measure": measure,
        "measure_label": head["measures"][measure]["label"], "unit": head["measures"][measure]["unit"],
        "resolution": resolution, "periods": periods, "series": series,
        "events": [{"date": "2012-01-20", "label": "VIIRS S-NPP starts (375 m)"},
                   {"date": "2018-04-01", "label": "NOAA-20 added"},
                   {"date": "2002-07-04", "label": "Aqua starts (Terra only before)"}],
        "reconstructed_until": "2012-01-19",
        "partial_years": {"2000": "MODIS Terra only from 2000-11-01", "2001": "Terra only (Aqua from 2002-07-04)",
                          "2002": "Terra only until 2002-07-03", "2012": "VIIRS from 2012-01-20 (harmonized year is complete)"},
        "discontinuity": head["discontinuity"][region][measure], "trend_2003_2025": tr,
        "bridge": head["bridge"][region][measure],
        "model_metrics_heldout_2021_2025": head.get("model_metrics_heldout_2021_2025"),
        "notes": ["harmonized = MODIS reconstructed to VIIRS S-NPP equivalent before 2012-01-20 + real S-NPP after; "
                  "NOAA-20 is not added.",
                  "Reconstruction recall is limited (about 35% of VIIRS-active cell-days are recovered, "
                  "predicted/true cell-days about 0.7-1.2 depending on region); totals of detections and FRP are matched "
                  "better than cell-day counts. See bridge for per-year ratios.",
                  "Only 5 countries (Brazil, Australia, India, United States, Canada) have a full 2000-2025 archive here."],
    }
