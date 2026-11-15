"""Monte Carlo Ensemble Simulation: Burn Frequency & Weather Sensitivity Analysis.

Generates an ensemble of physical fire-spread simulations with perturbed weather
(wind speed, wind direction, fuel moisture / relative humidity, rate of spread)
to construct ensemble burn-frequency contours (izolinie częstości w zespole).

SCIENTIFIC & HONESTY NOTICE:
  Values reflect the fraction of perturbed simulation runs that burned each raster cell.
  Consistent with project consensus and COORDINATION.md / komunikacja.md agreements:
  This is strictly designated as "Ensemble simulation frequency" (częstość w zespole symulacji),
  NOT an empirically calibrated real-world burn probability.

Endpoints:
  GET  /montecarlo/presets
  POST /montecarlo/run
  GET  /montecarlo/run
"""
from __future__ import annotations

import math
import time
from typing import Any, Dict, List, Optional, Tuple, Union

import numpy as np
import pandas as pd
from fastapi import APIRouter, Body, HTTPException, Query
from shapely.geometry import shape

from . import physics_spread as ps

router = APIRouter(prefix="/montecarlo", tags=["montecarlo"])

PRESETS: Dict[str, Dict[str, Any]] = {
    "standard": {
        "id": "standard",
        "name": "Prescribed Sensitivity Ensemble (30 members)",
        "name_pl": "Preskryptywny zespół wrażliwości (30 przebiegów)",
        "members": 30,
        "wind_noise_pct": 15.0,
        "wind_dir_noise_deg": 15.0,
        "rh_noise_pct": 8.0,
        "ros_mult_noise_pct": 15.0,
        "description": "Prescribed weather-perturbation sensitivity bounds (synthetic statistical spread, not operational NWP ensemble members).",
    },
    "deep_uncertainty": {
        "id": "deep_uncertainty",
        "name": "Deep Uncertainty / Stormy Gusts (50 members)",
        "name_pl": "Wysoka niepewność / porywisty wiatr (50 przebiegów)",
        "members": 50,
        "wind_noise_pct": 25.0,
        "wind_dir_noise_deg": 30.0,
        "rh_noise_pct": 15.0,
        "ros_mult_noise_pct": 25.0,
        "description": "Enhanced dispersion reflecting complex convective gust fronts and shifting winds.",
    },
    "fuel_sensitivity": {
        "id": "fuel_sensitivity",
        "name": "Fuel Moisture & Drought Sensitivity (35 members)",
        "name_pl": "Czułość wilgotności ściółki (35 przebiegów)",
        "members": 35,
        "wind_noise_pct": 10.0,
        "wind_dir_noise_deg": 10.0,
        "rh_noise_pct": 20.0,
        "ros_mult_noise_pct": 30.0,
        "description": "Focus on fine fuel moisture variations and extreme fuel dryness scenarios.",
    },
}

DEFAULT_THRESHOLDS = [0.20, 0.50, 0.80, 0.95]


def _perturb_weather(
    base_wx: pd.DataFrame,
    wind_noise_pct: float,
    wind_dir_noise_deg: float,
    rh_noise_pct: float,
    rng: np.random.Generator,
) -> pd.DataFrame:
    """Creates a physically constrained perturbed weather series."""
    wx_pert = base_wx.copy()

    # Log-normal scaling for wind speed (strictly positive)
    sigma_u = wind_noise_pct / 100.0
    u_factor = float(np.clip(rng.lognormal(mean=0.0, sigma=sigma_u), 0.5, 2.0))
    wx_pert["wind_kmh"] = np.clip(wx_pert["wind_kmh"] * u_factor, 1.0, 160.0)
    wx_pert["gust_kmh"] = np.clip(wx_pert["gust_kmh"] * u_factor, 1.0, 200.0)

    # Direction shift: random walk or persistent shift + hour-to-hour jitter
    base_dir_shift = float(rng.normal(0.0, wind_dir_noise_deg))
    hourly_jitter = rng.normal(0.0, wind_dir_noise_deg * 0.4, size=len(wx_pert))
    total_dir_shift = base_dir_shift + hourly_jitter
    wx_pert["wind_dir"] = (wx_pert["wind_dir"] + total_dir_shift) % 360.0

    # Relative humidity perturbation
    rh_shift = float(rng.normal(0.0, rh_noise_pct))
    wx_pert["rh"] = np.clip(wx_pert["rh"] + rh_shift, 5.0, 98.0)

    return wx_pert


def run_ensemble(
    lat: float,
    lon: float,
    start: Optional[Union[str, pd.Timestamp]] = None,
    hours: int = 24,
    members: int = 30,
    fuel: str = "auto",
    ignition: Optional[Dict[str, Any]] = None,
    wx: Optional[pd.DataFrame] = None,
    wx_info: Optional[Dict[str, Any]] = None,
    elev_fn=None,
    getter=None,
    wind_noise_pct: float = 15.0,
    wind_dir_noise_deg: float = 15.0,
    rh_noise_pct: float = 8.0,
    ros_mult_noise_pct: float = 15.0,
    thresholds: Optional[List[float]] = None,
    seed: Optional[int] = 42,
) -> Dict[str, Any]:
    """Runs a Monte Carlo ensemble simulation over perturbed weather and fuel parameters."""
    t0 = time.time()
    thresholds = sorted(thresholds or DEFAULT_THRESHOLDS)
    members = int(np.clip(members, 5, 100))
    hours = int(np.clip(hours, 1, 72))

    # 1. Base simulation setup to get shared grid, ignition, terrain, and weather
    start = ps._parse_start(start)
    if ignition is None:
        ignition = {
            "mode": "point",
            "lats": np.array([lat]),
            "lons": np.array([lon]),
            "estimate": True,
            "note": "single-cell ignition at requested point",
        }
    ilats = np.asarray(ignition["lats"], float)
    ilons = np.asarray(ignition["lons"], float)
    clat, clon = float(ilats.mean()), float(ilons.mean())

    fuel_class, fuel_why = ps.choose_fuel(clat, fuel)

    if wx is None:
        wx, wx_info = ps.fetch_weather(clat, clon, start, hours, getter=getter)
    wx_info = wx_info or {"sources": [{"api": "provided"}], "errors": [], "fallback": False}

    # Weather time slicing
    sim_mask = wx.index >= start
    wx_s = wx[sim_mask].iloc[:hours]
    if len(wx_s) != hours:
        raise ValueError(f"hourly weather must cover {hours} hours (got {len(wx_s)})")

    # Shared grid setup (matches physics_spread)
    mean_wind = float(wx_s["wind_kmh"].mean())
    max_run_km = min(80.0, max(5.0, mean_wind * hours * 0.08))
    half_m = min(max(max_run_km * 1000.0, 3000.0), 80_000.0)
    cell_m = max(250.0, 2 * half_m / (161 - 1))
    grid = ps.Grid(clat, clon, half_m, cell_m)

    # Map ignition cells to grid indices
    i_idx, j_idx = grid.ij(ilats, ilons)
    valid_ign = (i_idx >= 0) & (i_idx < grid.n) & (j_idx >= 0) & (j_idx < grid.n)
    if not np.any(valid_ign):
        ci, cj = grid.n // 2, grid.n // 2
        ign_cells = [int(ci * grid.n + cj)]
    else:
        ign_cells = [int(i * grid.n + j) for i, j in zip(i_idx[valid_ign], j_idx[valid_ign])]

    # Terrain
    tan, up, tinfo = ps.terrain(grid, getter=getter, elev_fn=elev_fn)

    # 2. Run ensemble members with perturbations
    rng = np.random.default_rng(seed)
    a_cell_km2 = (grid.cell * grid.cell) / 1e6

    member_areas_km2: List[float] = []
    burn_count = np.zeros((grid.n, grid.n), dtype=np.int32)

    member_runs_info: List[Dict[str, Any]] = []

    for m in range(members):
        # Perturb weather
        wx_m = _perturb_weather(wx, wind_noise_pct, wind_dir_noise_deg, rh_noise_pct, rng)
        wx_m_s = wx_m[sim_mask].iloc[:hours]
        fm_m = ps.fuel_moisture(wx_m)
        fm_m_s = fm_m[sim_mask].iloc[:hours]

        # Perturb ROS multiplier (fuel load / rate uncertainty)
        ros_mult_m = float(np.clip(rng.normal(1.0, ros_mult_noise_pct / 100.0), 0.5, 2.0))

        # Hourly Rothermel behaviour
        hb_m = ps.hourly_behaviour(fuel_class, wx_m_s, fm_m_s, ros_mult_m)

        # Dijkstra minimum travel time
        T_m, _, _ = ps.mtt_labelled(grid, ign_cells, hb_m, tan, up, fuel_class, hours)

        # Check burned cells at horizon
        burned_mask = (T_m <= hours + 1e-9)
        burn_count += burned_mask.astype(np.int32)

        area_km2 = float(burned_mask.sum() * a_cell_km2)
        member_areas_km2.append(round(area_km2, 3))

        if m < 5 or m == members - 1:
            member_runs_info.append({
                "member_id": m + 1,
                "mean_wind_kmh": round(float(wx_m_s["wind_kmh"].mean()), 1),
                "mean_wind_dir": round(float(wx_m_s["wind_dir"].mean()), 1),
                "mean_rh": round(float(wx_m_s["rh"].mean()), 1),
                "ros_mult": round(ros_mult_m, 2),
                "burned_km2": round(area_km2, 3),
            })

    # 3. Frequency raster & contour bands
    freq_grid = burn_count / float(members)

    contour_bands: List[Dict[str, Any]] = []
    color_map = {
        0.20: "#fed7aa",  # Light peach/orange (20% extent)
        0.50: "#fb923c",  # Amber/orange (50% median)
        0.80: "#ea580c",  # Deep orange (80% core agreement)
        0.95: "#991b1b",  # Dark red (95% almost certain in ensemble)
    }

    for thresh in thresholds:
        band_mask = freq_grid >= thresh
        cell_count = int(band_mask.sum())
        area_ha = round(float(cell_count * a_cell_km2 * 100.0), 1)
        area_km2 = round(float(cell_count * a_cell_km2), 3)

        geojson_poly = None
        if cell_count > 0:
            geojson_poly = ps.mask_to_geojson(band_mask, grid)

        contour_bands.append({
            "threshold_frequency": thresh,
            "threshold_percent": int(thresh * 100),
            "label": f"≥ {int(thresh * 100)}% częstości w zespole",
            "color": color_map.get(thresh, "#f97316"),
            "cell_count": cell_count,
            "area_ha": area_ha,
            "area_km2": area_km2,
            "polygon": geojson_poly,
        })

    # 4. Statistical distribution across ensemble
    areas_arr = np.array(member_areas_km2)
    percentiles = {
        "p10": round(float(np.percentile(areas_arr, 10)), 3),
        "p25": round(float(np.percentile(areas_arr, 25)), 3),
        "p50_median": round(float(np.percentile(areas_arr, 50)), 3),
        "p75": round(float(np.percentile(areas_arr, 75)), 3),
        "p90": round(float(np.percentile(areas_arr, 90)), 3),
        "min": round(float(np.min(areas_arr)), 3),
        "max": round(float(np.max(areas_arr)), 3),
        "std_km2": round(float(np.std(areas_arr)), 3),
    }

    elapsed_s = round(time.time() - t0, 3)

    return {
        "status": "success",
        "query": {
            "lat": lat,
            "lon": lon,
            "start": str(start),
            "hours": hours,
            "members": members,
            "fuel": fuel_class,
            "seed": seed,
        },
        "perturbation_specs": {
            "wind_noise_pct": wind_noise_pct,
            "wind_dir_noise_deg": wind_dir_noise_deg,
            "rh_noise_pct": rh_noise_pct,
            "ros_mult_noise_pct": ros_mult_noise_pct,
        },
        "reproducibility": {
            "seed": seed,
            "members": members,
            "rng": "numpy.random.default_rng",
            "perturbation_ranges": {
                "wind_speed_lognormal_sigma": round(wind_noise_pct / 100.0, 4),
                "wind_direction_normal_std_deg": wind_dir_noise_deg,
                "rh_additive_normal_std_pct": rh_noise_pct,
                "ros_multiplier_normal_std": round(ros_mult_noise_pct / 100.0, 4),
            },
        },
        "ensemble_stats": {
            "members_run": members,
            "runtime_seconds": elapsed_s,
            "area_distribution_km2": percentiles,
            "sample_members": member_runs_info,
        },
        "frequency_bands": contour_bands,
        "grid": {
            "cell_m": grid.cell,
            "size": grid.n,
            "half_width_km": round((grid.n // 2) * grid.cell / 1000.0, 2),
            "center": [clat, clon],
        },
        "wording_and_disclaimer": {
            "term_used": "Częstość w zespole symulacji fizycznej (Ensemble simulation frequency)",
            "definition": "Częstość (odsetek) przebiegów z perturbacjami warunków pogodowych, w których ogień osiągnął daną komórkę.",
            "uncalibrated_notice": "Wskaźnik ten NIE jest empirycznie skalibrowanym prawdopodobieństwem pożaru w świecie rzeczywistym. Przedstawia czułość modelu fizycznego na losowe odchylenia prognozy numerycznej.",
        },
        "warnings": [
            "Częstość w zespole odzwierciedla wariancję wejściowych parametrów numerycznych (wiatr, wilgotność), a nie zmierzone prawdopodobieństwo zapłonu.",
            "Pojedyncze ekstremalne porywy wiatru lub lokalne zjawiska mikroklimatyczne mogą wykraczać poza wygenerowany zespół.",
            "Brak uwzględnienia działań gaśniczych (brak gaszenia).",
        ],
    }


# ------------------------------------------------------------------ API Routes
@router.get("/presets")
def get_presets():
    """Returns available Monte Carlo ensemble configuration templates."""
    return {"presets": list(PRESETS.values())}


@router.post("/run")
def post_run(
    body: Dict[str, Any] = Body(..., description="Monte Carlo run parameters and optional custom ignition"),
    lat: Optional[float] = Query(None),
    lon: Optional[float] = Query(None),
    start: Optional[str] = Query(None),
    hours: int = Query(24, ge=1, le=72),
    members: int = Query(30, ge=5, le=100),
    fuel: str = Query("auto"),
    preset: Optional[str] = Query(None),
    seed: Optional[int] = Query(None),
):
    """Executes Monte Carlo simulation over perturbed weather and returns frequency contours."""
    # Resolve parameters from body or query
    req_lat = float(body.get("lat", lat if lat is not None else 54.975))
    req_lon = float(body.get("lon", lon if lon is not None else -101.725))
    req_start = body.get("start", start)
    req_hours = int(body.get("hours", hours))
    req_members = int(body.get("members", members))
    req_fuel = str(body.get("fuel", fuel))
    req_seed = body.get("seed", seed if seed is not None else 42)
    if req_seed is not None:
        req_seed = int(req_seed)

    ignition = body.get("ignition")

    # Resolve preset perturbation levels
    preset_data = PRESETS.get(preset or body.get("preset", "standard"), PRESETS["standard"])
    wind_noise = float(body.get("wind_noise_pct", preset_data["wind_noise_pct"]))
    wind_dir_noise = float(body.get("wind_dir_noise_deg", preset_data["wind_dir_noise_deg"]))
    rh_noise = float(body.get("rh_noise_pct", preset_data["rh_noise_pct"]))
    ros_mult_noise = float(body.get("ros_mult_noise_pct", preset_data["ros_mult_noise_pct"]))

    try:
        res = run_ensemble(
            lat=req_lat,
            lon=req_lon,
            start=req_start,
            hours=req_hours,
            members=req_members,
            fuel=req_fuel,
            ignition=ignition,
            wind_noise_pct=wind_noise,
            wind_dir_noise_deg=wind_dir_noise,
            rh_noise_pct=rh_noise,
            ros_mult_noise_pct=ros_mult_noise,
            seed=req_seed,
        )
        return res
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Monte Carlo ensemble error: {str(exc)}")


@router.get("/run")
def get_run(
    lat: float = Query(54.975, ge=-90.0, le=90.0),
    lon: float = Query(-101.725, ge=-180.0, le=180.0),
    start: Optional[str] = Query(None),
    hours: int = Query(24, ge=1, le=72),
    members: int = Query(25, ge=5, le=50),
    fuel: str = Query("auto"),
    preset: Optional[str] = Query("standard"),
    seed: Optional[int] = Query(42),
):
    """GET endpoint for fast evaluation of Monte Carlo ensemble."""
    preset_data = PRESETS.get(preset, PRESETS["standard"])
    try:
        res = run_ensemble(
            lat=lat,
            lon=lon,
            start=start,
            hours=hours,
            members=members,
            fuel=fuel,
            wind_noise_pct=preset_data["wind_noise_pct"],
            wind_dir_noise_deg=preset_data["wind_dir_noise_deg"],
            rh_noise_pct=preset_data["rh_noise_pct"],
            ros_mult_noise_pct=preset_data["ros_mult_noise_pct"],
            seed=seed,
        )
        return res
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Monte Carlo error: {str(exc)}")
