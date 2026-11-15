"""Small frozen-model weather-input sensitivity pilot, NOT as-issued skill.

The public 2024/25 IFS archive is labelled hindcast/unverified. Strict provider
access must reject it. This harness explicitly opts into research-only access,
keeps missing baseline weather missing, and never updates model weights/rules.
"""
from __future__ import annotations

import argparse
from datetime import timedelta
import hashlib
import json
import math
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np

from equifire import direct
from equifire.common import EPOCH, cell_center
from equifire.forecast_weather import ForecastWeather, ForecastWeatherError, daily_tuple, select_run, utc, iso


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def choose_cases(weather):
    """Four input-defined cases/year; include the sole observed-weather pair.

    Nothing about future fire outcomes is used in selection. Existing benchmark
    reuse means this is explicitly retrospective, not a fresh test sample.
    """
    selected, coverage = [], {}
    for year in (2024, 2025):
        cases = json.loads((ROOT/f"reports/ensemble_ab_{year}.json").read_text())["test_cases"]
        complete = [c for c in cases if np.isfinite(weather.get(c[0], *cell_center(c[1], c[2]))).all()]
        coverage[str(year)] = {"complete_baseline_cases": len(complete), "benchmark_cases": len(cases)}
        chosen = []
        if complete:
            chosen.append((complete[0], "first complete baseline input tuple; no outcome selection"))
        for month in (4, 7, 10, 12):
            date = np.datetime64(f"{year}-{month:02d}-01")
            eligible = [c for c in cases if EPOCH+np.timedelta64(c[0], "D") >= date and c not in [x[0] for x in chosen]]
            if eligible and len(chosen) < 4:
                chosen.append((eligible[0], f"first benchmark case on/after {date}; no outcome selection"))
        selected.extend((year, case, reason) for case, reason in chosen)
    return selected, coverage


def existing_index_readonly():
    """Open existing mapped arrays only; never invoke the automatic cache rebuild."""
    from equifire import fireindex_mm as mm
    meta = json.loads((mm.DIR/"meta.json").read_text())
    fi = direct.FireIndex.__new__(direct.FireIndex)
    fi.v = mm._load_tiled("v", meta["v_vals"])
    fi.m = mm._load_tiled("m", ["frp"]) if meta["has_m"] else None
    fi.has_dn = True
    fi.day = np.load(mm.DIR/"s_day.npy", mmap_mode="r")
    fi.gx = np.load(mm.DIR/"s_gx.npy", mmap_mode="r")
    fi.gy = np.load(mm.DIR/"s_gy.npy", mmap_mode="r")
    from equifire.subcell import SubcellIndex
    fi.sub = SubcellIndex() if SubcellIndex.available() else None
    return fi


class FixedWeather:
    def __init__(self, values):
        self.values = values

    def get(self, day, lat, lon):
        return self.values


def inference_pair(year, case, weather, values, fi, models):
    if year not in models:
        path = ROOT/f"models/wf_{year}.json"
        model, meta = direct.load(path)
        if model is None or int(meta["test_from"]) > year:
            raise RuntimeError("Frozen ML artifact is missing or sees the outcome year.")
        if any(c in direct.V4_EXTRA for c in meta["features"]):
            raise RuntimeError("This pilot refuses extra physical/network-derived feature semantics.")
        model.set_params(n_jobs=2, device="cpu")
        models[year] = model, meta, sha(path), sha(ROOT/f"models/wf_{year}_meta.json")
    model, meta, model_hash, meta_hash = models[year]
    raw = direct.predict_case(model, fi, *case, weather, 3, meta["features"])
    forecast = direct.predict_case(model, fi, *case, FixedWeather(values), 3, meta["features"])
    if raw is None or forecast is None:
        raise RuntimeError("No eligible fire history for registered case.")
    P, A, dist, A0 = raw
    Q = forecast[0]
    if not np.array_equal(np.isfinite(P), np.isfinite(Q)) or not np.array_equal(A, forecast[1]):
        raise RuntimeError("Counterfactual changed eligible cells or fire-history/truth inputs.")
    truth = direct.case_truth(A, dist, A0, 3)
    rows = []
    for h in range(3):
        mask = np.isfinite(P[h]) & np.isfinite(Q[h])
        y, p, q = truth[h][mask], P[h][mask], Q[h][mask]
        rule = direct.rule_for(meta, h+1)
        rows.append({"horizon_day": h+1, "eligible_cells": int(mask.sum()),
                     "baseline_brier": float(np.mean((p-y)**2)), "research_hindcast_brier": float(np.mean((q-y)**2)),
                     "baseline_strict_iou": float(direct.iou_masks(direct.select(P[h], rule), truth[h])),
                     "research_hindcast_strict_iou": float(direct.iou_masks(direct.select(Q[h], rule), truth[h])),
                     "mean_abs_probability_change": float(np.mean(np.abs(q-p))), "frozen_rule": list(rule)})
    return {"model_sha256": model_hash, "meta_sha256": meta_hash, "weights_and_rules_changed": False, "horizons": rows}


def json_clean(value):
    if isinstance(value, np.ndarray):
        return json_clean(value.tolist())
    if isinstance(value, dict):
        return {str(k): json_clean(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [json_clean(v) for v in value]
    if isinstance(value, (float, np.floating)):
        return float(value) if math.isfinite(value) else None
    if isinstance(value, np.integer):
        return int(value)
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true", help="Use the explicit forecast cache only.")
    parser.add_argument("--skip-inference", action="store_true")
    args = parser.parse_args()
    from threadpoolctl import threadpool_limits
    threadpool_limits(limits=2)
    started = time.perf_counter()
    weather = direct.Weather()
    chosen, coverage = choose_cases(weather)
    provider = ForecastWeather(max_requests=0 if args.offline else 8)
    fi = None if args.skip_inference else existing_index_readonly()
    models, rows = {}, []
    for year, case, reason in chosen:
        date = str(EPOCH+np.timedelta64(case[0], "D"))
        cutoff = utc(date+"T00:00:00Z")
        # Actual API constraint: daily= is supported only with a midnight run.
        # Round further backwards; never select a future/newer run to satisfy it.
        init = select_run(cutoff, assumed_latency_hours=12).replace(hour=0)
        lat, lon = (float(v) for v in cell_center(case[1], case[2]))
        # Match the old provider's requested 2-degree tile centre, not fire centroids.
        tile_lat, tile_lon = (math.floor(lat/2)+0.5)*2, (math.floor(lon/2)+0.5)*2
        baseline = weather.get(case[0], lat, lon)
        row = {"year": year, "case": case, "selection": reason, "valid_date": date,
               "fire_location": [lat, lon], "weather_requested_location": [tile_lat, tile_lon],
               "weather_cutoff_utc": iso(cutoff), "run_init_utc": iso(init),
               "benchmark_fire_information_cutoff_proxy": iso(cutoff+timedelta(days=1)),
               "baseline_values": baseline, "baseline_complete": bool(np.isfinite(baseline).all())}
        try:
            provider.fetch(tile_lat, tile_lon, init, cutoff)  # strict: must reject these hindcasts before HTTP
            raise AssertionError("Historical input unexpectedly accepted as as-issued.")
        except ForecastWeatherError as error:
            row["strict_rejection"] = str(error)
        try:
            run = provider.fetch(tile_lat, tile_lon, init, cutoff, require_as_issued=False)
            values = daily_tuple(run, date, require_as_issued=False)
            row.update(status="research_hindcast_only", forecast_values=values, metadata=run["metadata"])
            row["future_feature_harness"] = [r for r in run["daily"] if date < r["date"] <= str((cutoff+timedelta(days=3)).date())]
            if row["baseline_complete"]:
                delta = np.asarray(values)-baseline
                delta[1] = (delta[1]+180)%360-180
                row["forecast_minus_existing_values"] = delta
            if fi is not None:
                row["frozen_ml_input_sensitivity"] = inference_pair(year, case, weather, values, fi, models)
        except Exception as error:
            row.update(status="unavailable_no_fallback", error=str(error))
        rows.append(row)
        print(date, row["status"], "baseline="+str(row["baseline_complete"]), row.get("error", ""), flush=True)
        if row.get("error", "").startswith("Single Runs HTTP 429"):
            break  # no back-off/retry loop or silent alternate dataset
    result = {"protocol": {"task": "weather_as_issued", "as_issued_skill_established": False,
               "purpose": "retrospective input-sensitivity/coverage pilot, not an operational accuracy gain",
               "case_selection": "fixed dates + first complete baseline tuple; no outcome-based selection",
               "weather_days": "Same UTC valid day and five daily variable meanings as direct.Weather; no future-day feature substitution",
               "weather_cutoff": "00UTC of input weather day; init selected >=12h earlier and rounded BACK to midnight for daily API support; not proof of availability",
               "fire_cutoff": "benchmark uses detections through t0; its next-midnight proxy is later than the conservative weather cutoff",
               "future_features": "future_feature_harness gives day+1..3; introducing those into ML requires a new feature schema and chronological training",
               "training": "none; frozen wf_2024/wf_2025 weights and selection rules reused unchanged; existing inspected benchmarks",
               "counterfactual": "pre-May2026 IFS Cycle49R1 hindcast/unverified, explicitly refused by strict as-issued guard",
               "baseline": "Actual cached five-element tuple, missing values retained; never replaced by freshly fetched reanalysis",
               "sources": ["https://open-meteo.com/en/docs/single-runs-api", "https://open-meteo.com/en/docs/historical-forecast-api", "https://open-meteo.com/en/docs/model-updates"]},
              "baseline_benchmark_coverage": coverage, "cases": rows,
              "http_requests_this_run": provider.requests_made,
              "prior_discovery_http_requests": 2,
              "prior_failed_pilot_http_requests": 8,
              "prior_successful_pilot_http_requests": 8 if args.offline else 0,
              "prior_failed_pilot_reason": "HTTP400: daily variables require the requested run to start at 00:00 in the requested timezone; corrected to older00UTC run, no fallback",
              "elapsed_s": round(time.perf_counter()-started, 3)}
    valid = [r for r in rows if "frozen_ml_input_sensitivity" in r]
    for year, (_, _, model_hash, meta_hash) in models.items():
        if sha(ROOT/f"models/wf_{year}.json") != model_hash or sha(ROOT/f"models/wf_{year}_meta.json") != meta_hash:
            raise RuntimeError("Frozen source artifact changed during the pilot.")
    result["summary"] = {"cases_attempted": len(rows), "research_cases_complete": len(valid),
                         "as_issued_eligible_cases": sum(r.get("metadata", {}).get("as_issued_eligible", False) for r in rows),
                         "baseline_complete_cases": sum(r["baseline_complete"] for r in rows), "horizons": []}
    for h in range(3):
        if not valid:
            break
        names = ("baseline_brier", "research_hindcast_brier", "baseline_strict_iou", "research_hindcast_strict_iou", "mean_abs_probability_change")
        result["summary"]["horizons"].append({"horizon_day": h+1, **{n: float(np.mean([r["frozen_ml_input_sensitivity"]["horizons"][h][n] for r in valid])) for n in names}})
    out = ROOT/"reports/forecast_weather_pilot.json"
    out.write_text(json.dumps(json_clean(result), indent=2, allow_nan=False)+"\n")
    print(json.dumps(json_clean(result["summary"]), indent=2), flush=True)
    print("HTTP", provider.requests_made, "elapsed", result["elapsed_s"], flush=True)


if __name__ == "__main__":
    main()
