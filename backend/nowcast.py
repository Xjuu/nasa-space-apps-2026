"""Satellite Overpass Nowcast: Fire Front Extrapolation & Sensor Footprint Forecasting.

Connects backend/nextobs.py (satellite orbits, pass timing, Open-Meteo cloud cover)
with backend/physics_spread.py (Rothermel MTT fire spread) and equifire/geometry.py
(MODIS / VIIRS bow-tie pixel footprint distortion).

Answers the vital incident commander question:
  "When the next NASA/NOAA satellite flies over in X hours, where will the firefront be,
   what will the sensor resolution be at its scan angle, and will clouds allow detection?"

Scientific & Operational Basis:
  * Overpass geometry: SGP4 orbital propagation from NORAD TLEs (aux/tle).
  * Sensor footprint growth: Wolfe et al. (2002) bow-tie pixel expansion model.
  * Fire front progression: Rothermel (1972) / Finney (2002) MTT surface spread.
  * Cloud attenuation: Open-Meteo total cloud cover proxy at pass timestamp.

Endpoints:
  GET  /nowcast/pass
  POST /nowcast/pass
"""
from __future__ import annotations

import datetime as dt
import math
import time
from typing import Any, Dict, List, Optional, Tuple, Union

import numpy as np
import pandas as pd
from fastapi import APIRouter, Body, HTTPException, Query
from shapely.geometry import Point, Polygon, shape

from equifire import geometry as G
from . import nextobs as N
from . import physics_spread as ps

router = APIRouter(prefix="/nowcast", tags=["nowcast"])

UTC = dt.timezone.utc
M_TO_DEG = 1.0 / 111_320.0


def _bearing(dx: float, dy: float) -> float:
    """Calculates compass bearing (degrees, 0 = North, 90 = East)."""
    return (math.degrees(math.atan2(dx, dy)) + 360.0) % 360.0


def _bearing_name(deg: float) -> str:
    """Converts degrees to 8-point compass name."""
    dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    idx = int(round(deg / 45.0)) % 8
    return dirs[idx]


def build_nowcast(
    lat: float,
    lon: float,
    start: Optional[Union[str, dt.datetime]] = None,
    fuel: str = "auto",
    wx: Optional[pd.DataFrame] = None,
    elev_fn=None,
    getter=None,
    radius_km: float = 15.0,
    preferred_sat: Optional[str] = None,
) -> Dict[str, Any]:
    """Generates a complete nowcast for the next satellite overpass."""
    t_start_wall = time.time()
    lat, lon = float(lat), float(lon)

    # 1. Resolve start time
    if start is None:
        t_ref = dt.datetime.now(UTC)
    elif isinstance(start, str):
        try:
            t_ref = dt.datetime.fromisoformat(start.replace("Z", "+00:00"))
        except Exception:
            t_ref = dt.datetime.now(UTC)
    elif isinstance(start, dt.datetime):
        t_ref = start if start.tzinfo else start.replace(tzinfo=UTC)
    else:
        t_ref = dt.datetime.now(UTC)

    # 2. Get upcoming satellite passes via nextobs (or nominal fallback)
    passes = []
    sim_until = {}
    is_fallback = False
    fallback_reason = None
    try:
        obs_data = N.nextobs(lat=lat, lon=lon, time=t_ref.isoformat(), radius_km=radius_km)
        passes = obs_data.get("passes", [])
        sim_until = obs_data.get("simulate_until", {})
    except Exception as exc:
        is_fallback = True
        fallback_reason = f"nextobs_exception: {type(exc).__name__}: {str(exc)[:120]}"

    if not passes:
        # Fallback to nominal sun-synchronous passes if TLEs unavailable or offline
        is_fallback = True
        if fallback_reason is None:
            fallback_reason = "no_passes_in_nextobs"
        nominal = N._nominal_passes(lon, t_ref, hours=N.LOOKAHEAD_H)
        for p in nominal:
            best = p["best"] if isinstance(p["best"], dt.datetime) else dt.datetime.fromisoformat(str(p["best"]))
            delta_h = max(0.5, round((best - t_ref).total_seconds() / 3600.0, 2))
            passes.append({
                "satellite": p["satellite"],
                "instrument": p["instrument"],
                "time_utc": best.isoformat().replace("+00:00", "Z"),
                "hours_from_ref": delta_h,
                "delta_hours": delta_h,
                "scan_angle_deg": 18.0,
                "quality_tier": "nominal",
                "cloud": {
                    "cover_pct": None,
                    "visibility": "unknown",
                    "source": "nominal_pass_schedule (no cloud forecast)",
                },
                "usable": True,
                "geometry_ok": True,
                "is_fallback": True,
            })
        if passes:
            sim_until = {"hours": max(1, math.ceil(passes[0]["delta_hours"])), "until_utc": passes[0]["time_utc"]}

    if not passes:
        raise ValueError("No satellite overpasses found in lookahead window.")

    # Pick the target pass: either user preferred, or simulate_until usable pass, or first upcoming
    target_pass = None
    if preferred_sat:
        for p in passes:
            if preferred_sat.lower() in str(p.get("satellite", "")).lower():
                target_pass = p
                break

    if target_pass is None and sim_until:
        # Find next usable pass from sim_until
        target_pass_time = sim_until.get("until_utc")
        for p in passes:
            if p.get("time_utc") == target_pass_time:
                target_pass = p
                break

    if target_pass is None and passes:
        target_pass = passes[0]

    # Horizon until pass in hours
    delta_h = float(target_pass.get("hours_from_ref") or target_pass.get("delta_hours") or 2.0)
    delta_h = max(0.5, delta_h)
    sim_hours = int(math.ceil(delta_h))

    # 3. Simulate fire spread from t_ref to target_pass
    sim_start_ts = pd.Timestamp(t_ref)
    sim_res = ps.simulate(
        lat=lat,
        lon=lon,
        start=sim_start_ts,
        hours=max(2, sim_hours),
        step_min=30,
        fuel=fuel,
        wx=wx,
        elev_fn=elev_fn,
        getter=getter,
        envelope=False,
        frames=True,
    )

    # Find the frame closest to delta_h
    frames = sim_res.get("frames", [])
    best_frame = frames[-1] if frames else {}
    if frames:
        best_frame = min(frames, key=lambda f: abs(float(f.get("t_hours", 0.0)) - delta_h))

    burned_km2 = float(best_frame.get("burned_km2", 0.0))
    new_km2 = float(best_frame.get("new_km2", 0.0))
    max_intensity = float(best_frame.get("max_intensity_kw_m", 0.0))
    max_ros_m_min = float(best_frame.get("max_ros_m_min", 0.0))

    # Estimate head fire advance distance and bearing from ignition
    wind_info = best_frame.get("weather") or {}
    wind_kmh = float(wind_info.get("wind_kmh", 15.0))
    wind_dir = float(wind_info.get("wind_dir_deg", 270.0))
    spread_dir = (wind_dir + 180.0) % 360.0

    # Truly integrate head fire advance across intermediate simulation frames up to delta_h
    advance_dist_m = 0.0
    prev_t = 0.0
    for f in frames:
        t_f = float(f.get("t_hours", 0.0))
        if t_f <= 0.0:
            continue
        dt_min = (min(t_f, delta_h) - prev_t) * 60.0
        if dt_min > 0:
            # Rothermel MTT head ROS along fastest travel path
            f_ros = float(f.get("max_ros_m_min", 0.0)) * 0.65
            advance_dist_m += f_ros * dt_min
            prev_t = min(t_f, delta_h)
        if t_f >= delta_h:
            break
    advance_dist_m = round(advance_dist_m, 1)

    # 4. Satellite sensor pixel size & bow-tie distortion
    sat_name = str(target_pass["satellite"]).lower()
    scan_deg = float(target_pass.get("scan_angle_deg", 15.0))

    # Match satellite to G.SATS
    matched_sat = G.SATS.get("noaa20")
    for key, sat_obj in G.SATS.items():
        if key in sat_name or sat_obj.name.lower() in sat_name:
            matched_sat = sat_obj
            break

    scan_size_km, track_size_km = G.pixel_size_model(matched_sat, scan_deg)
    pixel_area_km2 = round(float(scan_size_km * track_size_km), 4)

    # 5. Expected detection & cloud attenuation
    # Extract cloud information properly from nextobs pass structure:
    # target_pass["cloud"] = {"cover_pct": int | None, "visibility": str, "source": str}
    cloud_info = target_pass.get("cloud")
    if isinstance(cloud_info, dict):
        raw_cover = cloud_info.get("cover_pct")
        cloud_pct = float(raw_cover) if raw_cover is not None else None
        cloud_tier = str(cloud_info.get("visibility") or "unknown")
        cloud_src = str(cloud_info.get("source") or "Open-Meteo hourly cloud_cover (model)")
    else:
        raw_cover = target_pass.get("cloud_cover_pct")
        cloud_pct = float(raw_cover) if raw_cover is not None else None
        cloud_tier = str(target_pass.get("cloud_tier") or "unknown")
        cloud_src = "provided_or_nominal"

    scan_tier = str(target_pass.get("quality_tier") or target_pass.get("scan_tier") or "unknown")

    # Active flaming front area is roughly proportional to new_km2 / perimeter depth
    flaming_area_km2 = max(0.05, new_km2 if new_km2 > 0 else burned_km2 * 0.25)
    expected_pixel_count = max(1, int(round(flaming_area_km2 / max(0.01, pixel_area_km2))))

    # Total FRP estimate (MW): Heuristic scaling based on flaming zone area and Byram intensity
    # Note: this is a physical heuristic indicator, not an empirical sensor-calibrated FRP.
    est_total_frp_mw = round(float(flaming_area_km2 * 25.0 + max_intensity * 0.01), 1)

    # Detection confidence (strictly accounting for missing/unknown cloud forecasts)
    if is_fallback or cloud_pct is None or cloud_tier == "unknown":
        det_conf = "unknown"
        det_prob = None
        if is_fallback:
            cloud_desc = "Nominal orbital pass fallback (no live TLE / no cloud forecast). Detection confidence cannot be calculated."
        else:
            cloud_desc = "Cloud cover unknown (no weather forecast data available). Detection confidence cannot be verified."
    elif cloud_pct < 30.0 and cloud_tier in ("clear", "good"):
        det_conf = "high"
        det_prob = round(max(0.1, 0.92 - (scan_deg / 100.0)), 2)
        cloud_desc = "Clear sky — optimal conditions for infrared active fire detection."
    elif cloud_pct < 70.0 and cloud_tier not in ("cloudy", "obscured"):
        det_conf = "moderate"
        det_prob = round(max(0.05, 0.55 - (scan_deg / 120.0)), 2)
        cloud_desc = "Partly cloudy — detection possible between cloud gaps; potential partial attenuation."
    else:
        det_conf = "low"
        det_prob = round(max(0.01, 0.18 - (scan_deg / 150.0)), 2)
        cloud_desc = "Heavy cloud cover or obscured visibility — high risk of optical obscuration. Sensor may miss hot-spots."

    # 6. Concise incident commander briefing
    pass_time_str = str(target_pass.get("time_utc", "t+pass"))
    cloud_str = f"{cloud_pct:.0f}%" if cloud_pct is not None else "unknown"
    briefing = (
        f"Next pass: {target_pass['satellite']} in {delta_h:.1f} h ({pass_time_str}). "
        f"Scan angle {scan_deg:.1f}° ({scan_size_km*1000:.0f}×{track_size_km*1000:.0f} m pixel footprint). "
        f"Firefront integrated advance ~{advance_dist_m:,.0f} m toward {_bearing_name(spread_dir)} ({spread_dir:.0f}°). "
        f"Cloud cover: {cloud_str} ({det_conf} detection confidence). "
        f"Expecting ~{expected_pixel_count} active {matched_sat.instrument} hot-spot pixels (~{est_total_frp_mw} MW FRP heuristic)."
    )

    elapsed_wall = round(time.time() - t_start_wall, 3)

    return {
        "status": "success",
        "runtime_seconds": elapsed_wall,
        "query": {
            "lat": lat,
            "lon": lon,
            "ref_time": str(t_ref),
            "fuel": fuel,
        },
        "provenance": {
            "is_fallback": is_fallback,
            "fallback_reason": fallback_reason,
            "orbit_source": "nominal_pass_fallback" if is_fallback else "sgp4_orbital_passes",
            "cloud_source": cloud_src,
        },
        "target_overpass": {
            "satellite": target_pass["satellite"],
            "instrument": matched_sat.instrument,
            "time_utc": target_pass["time_utc"],
            "delta_hours": delta_h,
            "scan_angle_deg": scan_deg,
            "quality_tier": scan_tier,
            "scan_tier": scan_tier,
            "cloud": {
                "cover_pct": cloud_pct,
                "visibility": cloud_tier,
                "source": cloud_src,
            },
            "cloud_cover_pct": cloud_pct,
            "cloud_tier": cloud_tier,
            "visibility_note": cloud_desc,
        },
        "sensor_footprint": {
            "along_scan_km": round(float(scan_size_km), 3),
            "along_track_km": round(float(track_size_km), 3),
            "pixel_area_km2": pixel_area_km2,
            "pixel_area_ha": round(pixel_area_km2 * 100.0, 1),
            "bow_tie_distortion": "severe" if scan_deg > 45 else ("moderate" if scan_deg > 30 else "minimal"),
        },
        "projected_fire_state": {
            "forecast_horizon_hours": delta_h,
            "burned_area_km2": round(burned_km2, 3),
            "burned_area_ha": round(burned_km2 * 100.0, 1),
            "active_front_area_km2": round(flaming_area_km2, 3),
            "front_advance_dist_m": advance_dist_m,
            "front_advance_bearing_deg": round(spread_dir, 1),
            "front_advance_compass": _bearing_name(spread_dir),
            "max_ros_m_min": round(max_ros_m_min, 2),
            "active_perimeter": best_frame.get("burned"),
            "flaming_front": best_frame.get("front"),
        },
        "sensor_detection_forecast": {
            "expected_pixels": expected_pixel_count,
            "heuristic_estimated_frp_mw": est_total_frp_mw,
            "expected_total_frp_mw": est_total_frp_mw,
            "detection_confidence": det_conf,
            "estimated_detection_probability": det_prob,
            "frp_calculation_basis": "Heuristic flaming front scaling (approx. 25 MW/km2 active zone + 0.01*intensity); not calibrated against satellite radiometers.",
        },
        "commander_briefing": briefing,
        "warnings": [
            "Nowcast is a screening projection based on Rothermel MTT physics and SGP4 orbital mechanics.",
            "Atmospheric cloud cover is an Open-Meteo numerical forecast indicator, not an empirical proof of obscuration.",
            "Pixel area expands significantly toward the swath edge due to Earth curvature and sensor geometry (bow-tie effect).",
            "FRP estimation is a heuristic forward indicator based on Rothermel intensity and flaming front area, not calibrated radiometry.",
        ],
    }


# ------------------------------------------------------------------ API Routes
@router.get("/pass")
def get_nowcast(
    lat: float = Query(54.975, ge=-90.0, le=90.0),
    lon: float = Query(-101.725, ge=-180.0, le=180.0),
    start: Optional[str] = Query(None),
    fuel: str = Query("auto"),
    sat: Optional[str] = Query(None, description="Preferred satellite (e.g. NOAA-20, Aqua)"),
):
    """Computes firefront extrapolation and sensor footprint nowcast for next satellite pass."""
    try:
        res = build_nowcast(lat=lat, lon=lon, start=start, fuel=fuel, preferred_sat=sat)
        return res
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Nowcast evaluation error: {str(exc)}")


@router.post("/pass")
def post_nowcast(
    body: Dict[str, Any] = Body(...),
    lat: Optional[float] = Query(None),
    lon: Optional[float] = Query(None),
    start: Optional[str] = Query(None),
    fuel: str = Query("auto"),
    sat: Optional[str] = Query(None),
):
    """POST endpoint accepting custom query or simulation parameters for nowcast."""
    req_lat = float(body.get("lat", lat if lat is not None else 54.975))
    req_lon = float(body.get("lon", lon if lon is not None else -101.725))
    req_start = body.get("start", start)
    req_fuel = str(body.get("fuel", fuel))
    req_sat = body.get("sat", sat)

    try:
        res = build_nowcast(lat=req_lat, lon=req_lon, start=req_start, fuel=req_fuel, preferred_sat=req_sat)
        return res
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Nowcast calculation error: {str(exc)}")
