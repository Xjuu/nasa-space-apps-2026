"""Frozen research calibration diagnostics; never relabel live forecasts as validated risk."""
from __future__ import annotations

from functools import lru_cache
import hashlib
import json
import math
from pathlib import Path

import numpy as np
from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.routing import APIRoute
from shapely.geometry import mapping, shape
from shapely.ops import unary_union

from equifire import risk_calibration as rc

ROOT = Path(__file__).resolve().parents[1]
MODEL_DIR = ROOT / "models"
REPORT_PATH = ROOT / "reports/daily_risk_calibration_current.json"
ARTIFACT_PATH = MODEL_DIR / "daily_risk_calibration_current/calibrator.json"


@lru_cache(maxsize=32)
def _digest(path: str, size: int, modified_ns: int) -> str:
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def fingerprint(path: Path) -> str | None:
    if not path.is_file():
        return None
    stat = path.stat()
    return _digest(str(path.resolve()), stat.st_size, stat.st_mtime_ns)


def model_fingerprints(model_dir: str | Path, blender: str, ml: str) -> dict:
    base = Path(model_dir)
    # Names come from the server's loaded bundle, never from a client path.
    return {"blender_sha256": fingerprint(base / (blender + ".json")),
            "ml_sha256": fingerprint(base / (Path(ml).stem + ".json")),
            "blender_meta_sha256": fingerprint(base / (blender + "_meta.json"))}


def load_diagnostics() -> tuple[dict, dict]:
    artifact = json.loads(ARTIFACT_PATH.read_text())
    report = json.loads(REPORT_PATH.read_text())
    name = Path(artifact["source_file"]).stem
    actual = model_fingerprints(MODEL_DIR, name, "wf_2025")
    matches = bool(actual["blender_sha256"] == artifact["source_sha256"]
                   and actual["ml_sha256"] == artifact["base_ml_sha256"]
                   and actual["blender_meta_sha256"] == artifact["source_meta_sha256"]
                   and fingerprint(ARTIFACT_PATH) == report["artifact_sha256"])
    result = {"available": True, "status": "research_candidate_not_promoted", "model_matches": matches,
              "source_model": name, "fingerprints": actual,
              "calibration_outcomes_through": artifact["calibration_outcomes_through_date"],
              "calibration_cases": report["n_calibration_cases"], "test_cases": report["n_test_cases"],
              "test_year": artifact["test_year"], "horizons": report["horizons"],
              "target": artifact["target"], "limits": report["limits"],
              "scope": "Retrospective archive-cell calibration; not individual-fire confidence or perimeter uncertainty.",
              "spatial_levels": [0.5, 0.8, 0.95],
              "spatial_level_meaning": "Thresholds of a research cell-probability estimate, not 50/80/95% confidence regions."}
    return artifact, result


def _rectangle(geometry: dict):
    if not isinstance(geometry, dict) or geometry.get("type") != "Polygon":
        raise ValueError("Only coarse-cell polygons can be calibrated.")
    rings = geometry.get("coordinates", [])
    if len(rings) != 1 or len(rings[0]) != 5:
        raise ValueError("Expected one five-point coarse-cell ring.")
    ring = rings[0]
    if any(len(p) != 2 or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in p)
           or abs(p[0]) > 180 or abs(p[1]) > 90 for p in ring):
        raise ValueError("Invalid cell coordinates.")
    xs, ys = {round(p[0], 8) for p in ring}, {round(p[1], 8) for p in ring}
    if len(xs) != 2 or len(ys) != 2 or ring[0] != ring[-1]:
        raise ValueError("Expected an axis-aligned closed cell.")
    if max(xs)-min(xs) > .051 or max(ys)-min(ys) > .051:
        raise ValueError("Cell exceeds the fitted 0.05 degree grid.")
    poly = shape(geometry)
    if not poly.is_valid or poly.area <= 0:
        raise ValueError("Invalid cell polygon.")
    return poly


def calibrate_guidance(body: dict) -> dict:
    artifact, diagnostics = load_diagnostics()
    if not diagnostics["model_matches"]:
        raise ValueError("The research calibrator does not match the installed model artifacts.")
    guidance = body.get("guidance") or {}
    if not isinstance(guidance, dict) or not all(isinstance(guidance.get(key, {}), dict) for key in ("history", "model", "query")) or not isinstance(body.get("ignition", {}), dict):
        raise ValueError("Expected guidance metadata and ignition objects.")
    history, model = guidance.get("history") or {}, guidance.get("model") or {}
    if history.get("reduced") is not False or (body.get("ignition") or {}).get("mode") != "archive":
        raise ValueError("Research calibration supports archive inputs only, never live reduced-history forecasts.")
    if model.get("blender") != diagnostics["source_model"] or model.get("fingerprints") != diagnostics["fingerprints"]:
        raise ValueError("Forecast model fingerprints do not match. Run a compatible archive forecast again.")
    date = (guidance.get("query") or {}).get("date", "")
    if len(date) != 10:
        raise ValueError("An archive prediction date is required.")
    year = int(date[:4])
    if year != artifact["test_year"]:
        raise ValueError("The display candidate is restricted to its retrospective 2025 archive scope.")
    features = guidance.get("features") or []
    if not isinstance(features, list) or not 1 <= len(features) <= 5043:
        raise ValueError("Expected 1..5043 eligible coarse-cell features.")
    raw, days, locations, polygons, source = [], [], [], [], []
    cell_ids = {}
    for feature in features:
        if not isinstance(feature, dict) or not isinstance(feature.get("properties", {}), dict):
            raise ValueError("Expected GeoJSON feature objects.")
        p = feature.get("properties") or {}
        if p.get("kind") != "combined_forecast":
            continue
        day, probability = p.get("day"), p.get("combined_probability")
        if day not in (1, 2, 3) or not isinstance(probability, (int, float)) or not math.isfinite(probability) or not 0 <= probability <= 1:
            raise ValueError("Expected horizons 1..3 and finite raw scores in [0,1].")
        poly = _rectangle(feature.get("geometry") or {})
        key = tuple(round(x, 8) for x in poly.bounds)
        cell_ids.setdefault(key, len(cell_ids))
        locations.append(cell_ids[key]); raw.append(probability); days.append(day); polygons.append(poly); source.append(feature)
    if not raw:
        raise ValueError("No combined forecast cell scores supplied.")
    raw, days = np.asarray(raw), np.asarray(days)
    calibrated = np.empty(len(raw))
    for day in np.unique(days):
        mask = days == day
        calibrated[mask] = rc.apply(raw[mask], int(day), artifact, source_sha256=diagnostics["fingerprints"]["blender_sha256"],
                                   prediction_year=year, observation_mode="archive", prediction_date=date)
    coherent = rc.cumulative(calibrated, days, np.asarray(locations))
    result, contours, per_day = [], [], []
    for i, feature in enumerate(source):
        result.append({"type": "Feature", "geometry": feature["geometry"], "properties": {
            "day": int(days[i]), "raw_probability": float(raw[i]), "research_probability": round(float(coherent[i]), 6),
            "kind": "research_calibrated_cell", "estimate": True}})
    for day in sorted(np.unique(days)):
        counts = []
        for level in diagnostics["spatial_levels"]:
            selected = [polygons[i] for i in range(len(raw)) if days[i] == day and coherent[i] >= level]
            counts.append({"level": level, "cells": len(selected)})
            if selected:
                contours.append({"type": "Feature", "geometry": mapping(unary_union(selected)), "properties": {
                    "day": int(day), "level": level, "cells": len(selected), "kind": "research_probability_threshold", "estimate": True}})
        per_day.append({"day": int(day), "eligible_cells": int((days == day).sum()), "thresholds": counts})
    return {"type": "FeatureCollection", "features": result, "contours": {"type": "FeatureCollection", "features": contours},
            "per_day": per_day, "query": guidance.get("query"), "status": diagnostics["status"],
            "target": diagnostics["target"], "scope": diagnostics["scope"],
            "coherence_adjusted_cells": int(np.sum(coherent != calibrated)),
            "warnings": ["Research candidate; not promoted to production and not an independently validated confidence map.",
                         "The contours are thresholds of coarse-cell probability estimates, not confidence intervals for a fire perimeter.",
                         "The original forecast selection and physical animation have not been altered."]}


class BoundedRiskRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def limited(request: Request):
            data = bytearray()
            async for chunk in request.stream():
                data.extend(chunk)
                if len(data) > 6 * 1024 * 1024:
                    raise HTTPException(413, "Calibration payload exceeds 6 MiB.")
            request._body = bytes(data)
            return await handler(request)
        return limited


router = APIRouter(tags=["risk diagnostics"], route_class=BoundedRiskRoute)


@router.get("/risk/diagnostics")
def diagnostics():
    try:
        return load_diagnostics()[1]
    except (OSError, KeyError, ValueError):
        return {"available": False, "reason": "Research calibration artifacts are not installed."}


@router.post("/risk/calibrate")
def calibrated(body: dict = Body(...)):
    try:
        return calibrate_guidance(body)
    except (OSError, KeyError):
        raise HTTPException(503, "Research calibration artifacts are unavailable.") from None
    except (TypeError, ValueError) as error:
        raise HTTPException(422, str(error)) from None
