"""What-If Firebreak Sandbox: Tactical Barrier & Containment Assessment Module.

Heuristic tactical planning aid (NOT a certified wildland fire control plan).
Evaluates whether user-drawn firebreaks, dozer lines, aerial retardant drops, or
tactical burnouts (backburns) can contain or delay a spreading wildfire front.

Physical & Empirical Basis:
  * Flame length FL: Byram (1959) fireline intensity relation FL = 0.0775 * I_B^0.46
  * Critical firebreak width W_crit: Wilson (1988), NWCG firebreak guidelines.
    Scales with flame length, wind tilt, and fuel type.
  * Spotting distance D_spot: Albini (1979, 1983) and Alexander (1998) maximum ember
    lofting proxy as a function of wind speed U_10 and flame length.
  * Line-holding probability P_hold: Logistic response curve over relative barrier width
    (W / W_crit), line construction efficacy, and ember attack.
  * Avoided carbon emissions: Wooster et al. (2005) biomass burning emission factor
    (0.368 kg dry matter / MJ) consistent with backend/carbon.py.

Endpoints:
  GET  /firebreak/presets
  POST /firebreak/evaluate
  GET  /firebreak/evaluate
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple, Union

import numpy as np
from fastapi import APIRouter, Body, HTTPException, Query
from shapely.geometry import LineString, MultiLineString, MultiPolygon, Point, Polygon, mapping, shape
from shapely.ops import unary_union

router = APIRouter(prefix="/firebreak", tags=["firebreak"])

M_LAT = 110574.0
M_LON = 111320.0

# ------------------------------------------------------------------ Presets
PRESETS: Dict[str, Dict[str, Any]] = {
    "handline": {
        "id": "handline",
        "name": "Hand Crew Line (Scrape)",
        "name_pl": "Pas ręczny (mineralny)",
        "width_m": 2.0,
        "efficacy": 0.82,
        "color": "#eab308",
        "description": "Mineral soil scrape constructed by wildland hand crew using Pulaskis and McLeods.",
        "construction_rate_m_per_h": 120.0,
    },
    "dozer_single": {
        "id": "dozer_single",
        "name": "Type 2 Dozer Line (Single Blade)",
        "name_pl": "Pas spycharkowy pojedynczy (Type 2)",
        "width_m": 5.0,
        "efficacy": 0.90,
        "color": "#f97316",
        "description": "Single-blade scrape down to mineral earth by medium tractor/dozer.",
        "construction_rate_m_per_h": 450.0,
    },
    "dozer_double": {
        "id": "dozer_double",
        "name": "Type 1 Heavy Dozer Line (Dual Pass)",
        "name_pl": "Pas spycharkowy podwójny (Type 1)",
        "width_m": 12.0,
        "efficacy": 0.95,
        "color": "#ea580c",
        "description": "Double blade pass with clearing cut and push, wide mineral barrier.",
        "construction_rate_m_per_h": 280.0,
    },
    "retardant_single": {
        "id": "retardant_single",
        "name": "SEAT Air Tanker Retardant Line",
        "name_pl": "Zrzut retardantu SEAT (samolot lekki)",
        "width_m": 15.0,
        "efficacy": 0.85,
        "color": "#ef4444",
        "description": "Single Engine Air Tanker (SEAT) chemical retardant slurry drop (approx. 3,000 L).",
        "construction_rate_m_per_h": 2500.0,
    },
    "retardant_heavy": {
        "id": "retardant_heavy",
        "name": "VLAT Heavy Retardant Line",
        "name_pl": "Zrzut retardantu ciężkiego VLAT (DC-10/747)",
        "width_m": 30.0,
        "efficacy": 0.93,
        "color": "#dc2626",
        "description": "Very Large Air Tanker chemical retardant line (>35,000 L polyphosphate drop).",
        "construction_rate_m_per_h": 8000.0,
    },
    "road_barrier": {
        "id": "road_barrier",
        "name": "Paved Road / Highway",
        "name_pl": "Droga asfaltowa / szosa",
        "width_m": 14.0,
        "efficacy": 0.92,
        "color": "#64748b",
        "description": "Existing paved two-lane roadway with cleared shoulders.",
        "construction_rate_m_per_h": 99999.0,
    },
    "blackline": {
        "id": "blackline",
        "name": "Tactical Backburn (Blackline)",
        "name_pl": "Wypalanie kontrolowane (czarna linia)",
        "width_m": 45.0,
        "efficacy": 0.98,
        "color": "#1e293b",
        "description": "Controlled backfire burnout stripping fuel between line and approaching front.",
        "construction_rate_m_per_h": 200.0,
    },
}

# Fuel parameters for flame length & biomass load:
# (flame_base_m, wind_flame_coeff, biomass_tons_per_ha)
FUEL_SPECS: Dict[str, Tuple[float, float, float]] = {
    "grass": (1.1, 0.035, 4.0),
    "savanna": (1.8, 0.050, 15.0),
    "shrub": (2.8, 0.075, 25.0),
    "forest": (3.5, 0.090, 85.0),
}


# ------------------------------------------------------------------ Projections & Math
def _make_proj(lat0: float, lon0: float):
    """Local equirectangular projection centered on (lat0, lon0)."""
    kx = M_LON * math.cos(math.radians(lat0))
    ky = M_LAT

    def forward(lon: float, lat: float) -> Tuple[float, float]:
        return (lon - lon0) * kx, (lat - lat0) * ky

    def backward(x: float, y: float) -> Tuple[float, float]:
        return lon0 + x / kx, lat0 + y / ky

    return forward, backward


def estimate_flame_length(fuel: Union[str, Dict[str, Any]], wind_kmh: float) -> float:
    """Estimates surface flame length FL (meters) based on fuel model and wind speed."""
    if isinstance(fuel, dict):
        fuel_key = str(fuel.get("class") or fuel.get("model") or "forest").lower()
    else:
        fuel_key = str(fuel or "forest").lower()

    # match prefix or key in FUEL_SPECS
    matched_spec = FUEL_SPECS.get("forest")
    for k, spec in FUEL_SPECS.items():
        if k in fuel_key:
            matched_spec = spec
            break
    base, coeff, _ = matched_spec
    # Flame length increases with mid-flame wind speed
    fl = base * (1.0 + coeff * max(0.0, float(wind_kmh)))
    return round(float(fl), 2)


def estimate_spotting_distance(fuel: Union[str, Dict[str, Any]], wind_kmh: float, flame_length_m: float) -> float:
    """Estimates maximum potential ember lofting / spotting distance (meters) via Albini proxy."""
    if isinstance(fuel, dict):
        fuel_key = str(fuel.get("class") or fuel.get("model") or "forest").lower()
    else:
        fuel_key = str(fuel or "forest").lower()

    fuel_k = 0.8
    for k, val in [("forest", 1.0), ("shrub", 0.65), ("savanna", 0.40), ("grass", 0.20)]:
        if k in fuel_key:
            fuel_k = val
            break

    u_term = (max(5.0, float(wind_kmh)) / 10.0) ** 1.35
    fl_term = max(0.5, float(flame_length_m)) ** 0.60
    dist_m = 14.0 * fuel_k * u_term * fl_term
    return round(float(dist_m), 1)


def critical_break_width(flame_length_m: float, wind_kmh: float, attack_angle_deg: float) -> float:
    """Calculates critical firebreak width W_crit (meters) required to prevent direct flame contact.

    Heuristic scaling with flame length inspired by Wilson (1988) grass firebreak observations
    and NWCG line construction guidelines, adjusted for wind flame tilt and attack angle.
    Attack angle is the angle between the fire spread vector and the barrier normal (0 deg = head-on).
    Flank and backing fire require substantially less width than head-fire.
    """
    rad = math.radians(abs(attack_angle_deg))
    cos_term = max(0.20, math.cos(rad))  # head-fire factor (1.0 head on, 0.20 parallel)

    # Base formula: flame length scaling + wind flame tilt
    tilt_factor = 1.5 + 0.035 * max(0.0, wind_kmh) * cos_term
    w_crit = flame_length_m * tilt_factor * cos_term
    return max(1.5, round(float(w_crit), 2))


def heuristic_holding_score(
    width_m: float,
    crit_width_m: float,
    spotting_dist_m: float,
    efficacy: float = 0.95,
) -> float:
    """Calculates a heuristic holding score (screening indicator, NOT a calibrated real-world probability).

    Logistic curve based on barrier width relative to critical width (ratio) with
    penalty for ember spotting distance. Line efficacy (0.1..1.0) represents construction
    quality (residual fuels, gaps, retardant degradation) acting as an upper bound ceiling.
    """
    ratio = width_m / max(0.5, crit_width_m)
    # Direct line holding logistic function: 0 at ratio=1.0, positive when wider, negative when narrower
    z_direct = (ratio - 1.0) / 0.35

    # Ember attack factor: if spotting distance exceeds barrier width, embers can jump
    ember_ratio = spotting_dist_m / max(1.0, width_m)
    z_ember = -0.75 * max(0.0, ember_ratio - 1.0)

    z_combined = z_direct + z_ember
    # Logistic response
    p_ideal = 1.0 / (1.0 + math.exp(-max(-6.0, min(6.0, z_combined))))

    # Single application of line construction efficacy ceiling
    score = p_ideal * max(0.1, min(1.0, efficacy))
    return round(float(np.clip(score, 0.02, 0.98)), 3)


# Backward-compatible alias
line_hold_probability = heuristic_holding_score


# ------------------------------------------------------------------ Core Evaluator
def evaluate_firebreak(
    barrier_geom: Union[Dict[str, Any], LineString, MultiLineString, Polygon],
    fuel: str = "forest",
    wind_kmh: float = 20.0,
    wind_dir_deg: float = 270.0,
    preset_id: Optional[str] = None,
    custom_width_m: Optional[float] = None,
    custom_efficacy: Optional[float] = None,
    simulation_payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Evaluates containment line performance, breach points, hold probability,

    and saved area/carbon impact.
    """
    # 1. Resolve preset / barrier specs
    preset = PRESETS.get(preset_id or "dozer_double", PRESETS["dozer_double"])
    width_m = float(custom_width_m if custom_width_m is not None else preset["width_m"])
    efficacy = float(custom_efficacy if custom_efficacy is not None else preset["efficacy"])

    # 2. Parse barrier geometry
    if isinstance(barrier_geom, dict):
        # Could be Feature, Geometry, or FeatureCollection
        if barrier_geom.get("type") == "Feature":
            geom_obj = shape(barrier_geom.get("geometry", {}))
        elif barrier_geom.get("type") == "FeatureCollection":
            features = barrier_geom.get("features", [])
            if not features:
                raise ValueError("Empty FeatureCollection provided for barrier.")
            geom_obj = unary_union([shape(f.get("geometry", {})) for f in features])
        else:
            geom_obj = shape(barrier_geom)
    else:
        geom_obj = barrier_geom

    if geom_obj.is_empty:
        raise ValueError("Barrier geometry is empty.")

    # 3. Pull weather and fuel from simulation payload if available
    sim_frames = []
    if simulation_payload and isinstance(simulation_payload, dict):
        fuel_in_sim = simulation_payload.get("fuel")
        if fuel_in_sim:
            fuel = fuel_in_sim
        sim_frames = simulation_payload.get("frames", [])
        # Extract mean weather or last frame weather
        if sim_frames:
            last_wx = sim_frames[-1].get("weather") or {}
            if "wind_kmh" in last_wx and last_wx["wind_kmh"] is not None:
                wind_kmh = float(last_wx["wind_kmh"])
            if "wind_dir_deg" in last_wx and last_wx["wind_dir_deg"] is not None:
                wind_dir_deg = float(last_wx["wind_dir_deg"])

    # Normalize fuel to string in FUEL_SPECS
    if isinstance(fuel, dict):
        fuel_str = str(fuel.get("class") or fuel.get("model") or "forest").lower()
    else:
        fuel_str = str(fuel or "forest").lower()
    fuel_norm = "forest"
    for k in FUEL_SPECS:
        if k in fuel_str:
            fuel_norm = k
            break
    fuel = fuel_norm

    # 4. Flame length & spotting potential
    flame_length = estimate_flame_length(fuel, wind_kmh)
    spotting_dist = estimate_spotting_distance(fuel, wind_kmh, flame_length)

    # 5. Segment breakdown & attack angles
    bounds = geom_obj.bounds  # minx (lon), miny (lat), maxx, maxy
    clat = (bounds[1] + bounds[3]) / 2.0
    clon = (bounds[0] + bounds[2]) / 2.0
    proj_fwd, proj_bwd = _make_proj(clat, clon)

    # Extract lines
    lines: List[LineString] = []
    if isinstance(geom_obj, LineString):
        lines.append(geom_obj)
    elif isinstance(geom_obj, MultiLineString):
        lines.extend(geom_obj.geoms)
    elif isinstance(geom_obj, Polygon):
        lines.append(geom_obj.exterior)
    elif isinstance(geom_obj, MultiPolygon):
        for poly in geom_obj.geoms:
            lines.append(poly.exterior)

    segments_eval: List[Dict[str, Any]] = []
    spread_dir_deg = (wind_dir_deg + 180.0) % 360.0  # wind blows FROM wind_dir, fire spreads TO spread_dir

    total_len_m = 0.0
    weighted_p_hold = 0.0
    breached_count = 0

    for line_idx, line in enumerate(lines):
        coords = list(line.coords)
        for i in range(len(coords) - 1):
            p1 = coords[i]
            p2 = coords[i + 1]
            x1, y1 = proj_fwd(p1[0], p1[1])
            x2, y2 = proj_bwd_x, proj_bwd_y = proj_fwd(p2[0], p2[1])

            dx = x2 - x1
            dy = y2 - y1
            seg_len = math.hypot(dx, dy)
            if seg_len < 1.0:
                continue

            total_len_m += seg_len
            # Segment line azimuth
            seg_azimuth = (math.degrees(math.atan2(dx, dy)) + 360.0) % 360.0
            # Normal to segment
            norm1 = (seg_azimuth + 90.0) % 360.0
            norm2 = (seg_azimuth - 90.0) % 360.0

            # Angle difference between fire spread direction and barrier normal
            diff1 = abs((spread_dir_deg - norm1 + 180.0) % 360.0 - 180.0)
            diff2 = abs((spread_dir_deg - norm2 + 180.0) % 360.0 - 180.0)
            attack_angle = min(diff1, diff2)

            # Evaluate this segment
            w_crit_seg = critical_break_width(flame_length, wind_kmh, attack_angle)
            p_hold_seg = line_hold_probability(width_m, w_crit_seg, spotting_dist, efficacy)
            is_breached = p_hold_seg < 0.50

            if is_breached:
                breached_count += 1

            weighted_p_hold += p_hold_seg * seg_len

            # Midpoint coords
            mid_lon = (p1[0] + p2[0]) / 2.0
            mid_lat = (p1[1] + p2[1]) / 2.0

            segments_eval.append({
                "segment_index": len(segments_eval),
                "line_id": line_idx,
                "length_m": round(seg_len, 1),
                "midpoint": [round(mid_lon, 6), round(mid_lat, 6)],
                "attack_angle_deg": round(attack_angle, 1),
                "crit_width_m": w_crit_seg,
                "heuristic_holding_score": p_hold_seg,
                "hold_probability": p_hold_seg,
                "status": "breached" if is_breached else "holding",
                "risk_level": "high" if is_breached else ("moderate" if p_hold_seg < 0.75 else "low"),
            })

    overall_hold_p = round(weighted_p_hold / total_len_m, 3) if total_len_m > 0 else 0.50
    overall_status = "holding" if overall_hold_p >= 0.50 else "breached"

    # Spotting risk rating
    if spotting_dist < 20.0:
        spotting_risk = "low"
    elif spotting_dist < 60.0:
        spotting_risk = "moderate"
    elif spotting_dist < 150.0:
        spotting_risk = "high"
    else:
        spotting_risk = "extreme"

    # Recommended width for 90% confidence on worst-case attack
    max_crit_w = critical_break_width(flame_length, wind_kmh, 0.0)
    recommended_width_m = round(max(max_crit_w * 1.5, spotting_dist * 0.8), 1)

    # 6. Simulation intersection, saved area, and avoided CO2
    area_saved_ha = 0.0
    co2_prevented_t = 0.0
    arrival_time_h: Optional[float] = None
    baseline_burned_ha = 0.0

    # Create buffered barrier polygon in projected coordinates for containment clipping
    barrier_proj = unary_union([
        LineString([proj_fwd(c[0], c[1]) for c in line.coords]).buffer(max(1.0, width_m / 2.0))
        for line in lines
    ])

    if sim_frames:
        last_frame = sim_frames[-1]
        if "burned_km2" in last_frame and last_frame["burned_km2"] is not None:
            baseline_burned_ha = float(last_frame["burned_km2"]) * 100.0
        elif "burned_area_ha" in last_frame and last_frame["burned_area_ha"] is not None:
            baseline_burned_ha = float(last_frame["burned_area_ha"])
        elif "area_km2" in last_frame and last_frame["area_km2"] is not None:
            baseline_burned_ha = float(last_frame["area_km2"]) * 100.0
        else:
            baseline_burned_ha = 314.0

        # Check earliest frame that touches the barrier
        for f in sim_frames:
            poly_data = f.get("burned") or f.get("polygon")
            if poly_data:
                try:
                    f_geom = shape(poly_data)
                    if f_geom.intersects(geom_obj):
                        arrival_time_h = float(f.get("t_hours", 0.0))
                        break
                except Exception:
                    continue

        if overall_hold_p >= 0.50:
            # If held, estimated area saved is proportional to hold confidence and front geometry
            # Heuristic: barrier saves between 20% to 65% of perimeter progression beyond arrival
            fraction_saved = min(0.65, max(0.15, overall_hold_p * 0.60))
            area_saved_ha = round(baseline_burned_ha * fraction_saved, 1)
        else:
            # Line is breached, but provides delay: minor containment savings (5-15%)
            area_saved_ha = round(baseline_burned_ha * 0.10 * overall_hold_p, 1)
    else:
        # Fallback estimation when no simulation is provided
        # Assume an active fire front of radius 1 km (~314 ha)
        baseline_burned_ha = 314.0
        if overall_hold_p >= 0.50:
            area_saved_ha = round(baseline_burned_ha * 0.40 * overall_hold_p, 1)
        else:
            area_saved_ha = round(baseline_burned_ha * 0.08, 1)

    # Carbon savings calculation (Wooster et al., 0.368 kg DM/MJ, dry biomass load)
    _, _, biomass_per_ha = FUEL_SPECS.get(fuel.lower(), FUEL_SPECS["forest"])
    combustion_completeness = 0.55  # typical wildland fraction
    dry_matter_burnt_tons = area_saved_ha * biomass_per_ha * combustion_completeness
    # 1 ton of burned dry vegetation yields ~1.686 tons of CO2
    co2_prevented_t = round(dry_matter_burnt_tons * 1.686, 1)

    # 7. Generate incident action tactical summary
    summary_parts = []
    if overall_status == "holding":
        summary_parts.append(
            f"Containment line projected to HOLD with heuristic score {int(overall_hold_p * 100)}/100 (screening indicator). "
            f"Estimated {area_saved_ha:,.1f} ha protected and {co2_prevented_t:,.1f} t CO2 prevented relative to simulated unmitigated front."
        )
    else:
        summary_parts.append(
            f"HIGH BREACH RISK: Barrier width ({width_m} m) insufficient against {flame_length} m flame lengths "
            f"and {wind_kmh:.0f} km/h winds ({breached_count}/{len(segments_eval)} segments vulnerable)."
        )

    if spotting_risk in ("high", "extreme"):
        summary_parts.append(
            f"Critical spotting hazard ({spotting_dist:.0f} m max ember throw): airborne brands likely to breach line. "
            f"Deploy aerial retardant drops or initiate widening to >= {recommended_width_m} m."
        )

    return {
        "status": overall_status,
        "heuristic_holding_score": overall_hold_p,
        "hold_probability": overall_hold_p,  # backward-compatible alias
        "barrier_length_m": round(total_len_m, 1),
        "barrier_width_m": width_m,
        "preset": preset,
        "fuel_model": fuel,
        "wind": {
            "speed_kmh": round(wind_kmh, 1),
            "direction_deg": round(wind_dir_deg, 1),
            "spread_direction_deg": round(spread_dir_deg, 1),
        },
        "flame_length_m": flame_length,
        "spotting": {
            "risk_category": spotting_risk,
            "max_distance_m": spotting_dist,
        },
        "critical_width_m": round(max_crit_w, 1),
        "recommended_width_m": recommended_width_m,
        "impact": {
            "baseline_burned_ha": baseline_burned_ha,
            "area_saved_ha": area_saved_ha,
            "area_saved_km2": round(area_saved_ha / 100.0, 3),
            "co2_prevented_tons": co2_prevented_t,
            "first_contact_time_h": arrival_time_h,
            "estimate_note": "Area saved and avoided CO2 are heuristic screening estimates computed relative to simulated uncontained footprint (Wooster 0.368 kg/MJ factor).",
        },
        "segments": segments_eval,
        "tactical_summary": " ".join(summary_parts),
        "warnings": [
            "Heuristic holding score is a screening indicator and is NOT calibrated or validated against suppression outcomes.",
            "Heuristic tactical screening aid based on Rothermel/Albini/Wilson models, NOT a certified incident action plan.",
            "Atmospheric turbulence, terrain channeling, and erratic ember spotting can compromise containment lines.",
            "Always maintain designated safety zones and escape routes (LACES / LCES protocols).",
        ],
    }


# ------------------------------------------------------------------ API Routes
@router.get("/presets")
def get_presets():
    """Returns available wildland firebreak templates and tactical specs."""
    return {"presets": list(PRESETS.values())}


@router.post("/evaluate")
def post_evaluate(
    body: Dict[str, Any] = Body(
        ...,
        description="JSON payload: either direct GeoJSON barrier or object with keys 'barrier' and optional 'simulation'",
    ),
    fuel: str = Query("forest", description="Fuel model: forest, shrub, savanna, grass"),
    wind_kmh: float = Query(20.0, ge=0.0, le=160.0, description="Wind speed in km/h"),
    wind_dir: float = Query(270.0, ge=0.0, le=360.0, description="Wind direction (degrees from)"),
    preset: Optional[str] = Query(None, description="Preset ID (e.g. dozer_double, retardant_heavy)"),
    width_m: Optional[float] = Query(None, ge=0.5, le=500.0, description="Custom barrier width in meters"),
    efficacy: Optional[float] = Query(None, ge=0.1, le=1.0, description="Custom barrier efficacy (0.1 - 1.0)"),
):
    """Evaluates the containment line against the wildfire parameters."""
    barrier = body.get("barrier", body)
    simulation = body.get("simulation")
    fuel = body.get("fuel", fuel)
    if "wind_kmh" in body and body["wind_kmh"] is not None:
        wind_kmh = float(body["wind_kmh"])
    if "wind_dir" in body and body["wind_dir"] is not None:
        wind_dir = float(body["wind_dir"])
    preset = body.get("preset", preset)
    if "width_m" in body and body["width_m"] is not None:
        width_m = float(body["width_m"])
    if "efficacy" in body and body["efficacy"] is not None:
        efficacy = float(body["efficacy"])

    try:
        result = evaluate_firebreak(
            barrier_geom=barrier,
            fuel=fuel,
            wind_kmh=wind_kmh,
            wind_dir_deg=wind_dir,
            preset_id=preset,
            custom_width_m=width_m,
            custom_efficacy=efficacy,
            simulation_payload=simulation,
        )
        return result
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Firebreak assessment error: {str(exc)}")


@router.get("/evaluate")
def get_evaluate(
    line: str = Query(..., description="Polyline coordinates as lon1,lat1;lon2,lat2;..."),
    fuel: str = Query("forest"),
    wind_kmh: float = Query(20.0),
    wind_dir: float = Query(270.0),
    preset: Optional[str] = Query(None),
    width_m: Optional[float] = Query(None),
):
    """Quick GET endpoint for polyline string input."""
    try:
        points = []
        for pair in line.split(";"):
            parts = pair.strip().split(",")
            if len(parts) == 2:
                points.append((float(parts[0]), float(parts[1])))
        if len(points) < 2:
            raise ValueError("Provide at least 2 points for the barrier line.")

        geom = LineString(points)
        return evaluate_firebreak(
            barrier_geom=geom,
            fuel=fuel,
            wind_kmh=wind_kmh,
            wind_dir_deg=wind_dir,
            preset_id=preset,
            custom_width_m=width_m,
        )
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Firebreak evaluation error: {str(exc)}")
