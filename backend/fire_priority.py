"""Fire Priority Ranking & Multi-Incident Triage (#20).

Operational decision-support module for emergency dispatchers and incident commanders.
When multiple active fires are burning across a region, ranks them into priority tiers
based on physical fire behavior, settlement exposure, radiative power, and suppression urgency.

Criteria & Component Weighting:
  1. Settlement & Asset Exposure (40%):
     Exponential distance decay from nearest town, critical facilities, or evacuation corridors.
  2. Fire Spread Velocity (30%):
     Forward Rate of Spread (ROS, m/min) driven by fuel model and wind gusts.
  3. Radiative Power & Energy (18%):
     Total Fire Radiative Power (FRP, MW) and active flaming zone extent.
  4. Fuel Dryness & Volatility (12%):
     Fine fuel moisture deficit (RH) and atmospheric instability.

Outputs:
  - Sorted TOP-N incident ranking table
  - Multi-attribute score breakdown (Exposure, Velocity, Intensity, Volatility)
  - Clear priority tier (Critical P1, High P2, Elevated P3, Monitoring P4)
  - Action guidance recommendations for commanders
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, HTTPException, Query
import numpy as np
from pydantic import BaseModel, Field

router = APIRouter(prefix="/priority", tags=["priority"])

# -----------------------------------------------------------------------------
# Curated Regional Multi-Incident Benchmark Scenarios
# -----------------------------------------------------------------------------
REGIONAL_PRESETS: Dict[str, Dict[str, Any]] = {
    "western_canada_boreal": {
        "id": "western_canada_boreal",
        "name": "Western Canada Boreal Complex (6 incidents)",
        "name_pl": "Kompleks borealny zachodniej Kanady (6 pożarów)",
        "region": "Saskatchewan / Manitoba / Alberta",
        "preset_type": "illustrative_scenario",
        "provenance_note": "Illustrative multi-fire scenario modeled after May 2024 boreal fire outbreaks (Flin Flon / Cranberry Portage) for platform demonstration and triage training.",
        "source_reference": "Synthetic benchmark derived from FIRMS VIIRS hotspot clusters and CIFFC national situation reports.",
        "incidents": [
            {
                "id": "sk_flin_flon_north",
                "name": "Flin Flon North Complex",
                "lat": 54.975,
                "lon": -101.725,
                "area_ha": 3400.0,
                "frp_mw": 320.0,
                "ros_m_min": 5.8,
                "wind_kmh": 32.0,
                "rh_pct": 18.0,
                "nearest_settlement_name": "Creighton / Flin Flon",
                "dist_to_settlement_km": 4.2,
                "fuel_type": "conifer_forest",
            },
            {
                "id": "mb_cranberry_portage",
                "name": "Cranberry Portage South Flank",
                "lat": 54.580,
                "lon": -101.380,
                "area_ha": 1250.0,
                "frp_mw": 145.0,
                "ros_m_min": 3.4,
                "wind_kmh": 26.0,
                "rh_pct": 24.0,
                "nearest_settlement_name": "Cranberry Portage",
                "dist_to_settlement_km": 6.8,
                "fuel_type": "mixedwood",
            },
            {
                "id": "sk_athabasca_remote",
                "name": "Lake Athabasca Remote Wilderness",
                "lat": 59.200,
                "lon": -107.800,
                "area_ha": 11500.0,
                "frp_mw": 580.0,
                "ros_m_min": 7.2,
                "wind_kmh": 38.0,
                "rh_pct": 15.0,
                "nearest_settlement_name": "Fond-du-Lac Outpost",
                "dist_to_settlement_km": 48.0,
                "fuel_type": "boreal_spruce",
            },
            {
                "id": "ab_slave_lake_east",
                "name": "Lesser Slave Lake East Buffer",
                "lat": 55.280,
                "lon": -114.770,
                "area_ha": 480.0,
                "frp_mw": 75.0,
                "ros_m_min": 2.1,
                "wind_kmh": 18.0,
                "rh_pct": 32.0,
                "nearest_settlement_name": "Slave Lake Town",
                "dist_to_settlement_km": 11.5,
                "fuel_type": "mixed_forest",
            },
            {
                "id": "sk_lac_la_ronge",
                "name": "Lac La Ronge Highway Corridor",
                "lat": 55.110,
                "lon": -105.280,
                "area_ha": 820.0,
                "frp_mw": 110.0,
                "ros_m_min": 4.1,
                "wind_kmh": 28.0,
                "rh_pct": 22.0,
                "nearest_settlement_name": "Air Ronge Subdivisions",
                "dist_to_settlement_km": 3.1,
                "fuel_type": "pine_timber",
            },
            {
                "id": "mb_grass_river_park",
                "name": "Grass River Provincial Park Fire",
                "lat": 54.650,
                "lon": -100.850,
                "area_ha": 2100.0,
                "frp_mw": 210.0,
                "ros_m_min": 4.5,
                "wind_kmh": 30.0,
                "rh_pct": 20.0,
                "nearest_settlement_name": "Highway 10 Relay Camp",
                "dist_to_settlement_km": 18.0,
                "fuel_type": "taiga_peat",
            },
        ],
    },
    "mediterranean_basin": {
        "id": "mediterranean_basin",
        "name": "Mediterranean Fire Wave (5 incidents)",
        "name_pl": "Fala pożarowa basenu Morza Śródziemnego (5 pożarów)",
        "region": "Greece / Rhodes / Peloponnese",
        "preset_type": "illustrative_scenario",
        "provenance_note": "Illustrative multi-fire scenario modeled after July 2023 Greek heatwave outbreaks for platform demonstration and triage training.",
        "source_reference": "Synthetic benchmark derived from EFFIS fire danger index and MODIS/VIIRS thermal anomalies.",
        "incidents": [
            {
                "id": "gr_rhodes_central",
                "name": "Rhodes Island Ridge Fire",
                "lat": 36.220,
                "lon": 27.950,
                "area_ha": 4200.0,
                "frp_mw": 410.0,
                "ros_m_min": 6.5,
                "wind_kmh": 45.0,
                "rh_pct": 14.0,
                "nearest_settlement_name": "Kiotari Resorts",
                "dist_to_settlement_km": 2.5,
                "fuel_type": "pine_maquis",
            },
            {
                "id": "gr_evros_dadia",
                "name": "Dadia Forest Mega-Fire",
                "lat": 41.120,
                "lon": 26.210,
                "area_ha": 9400.0,
                "frp_mw": 620.0,
                "ros_m_min": 7.0,
                "wind_kmh": 36.0,
                "rh_pct": 16.0,
                "nearest_settlement_name": "Dadia National Village",
                "dist_to_settlement_km": 5.4,
                "fuel_type": "dense_pine",
            },
            {
                "id": "gr_attica_parnitha",
                "name": "Mount Parnitha Interface Fire",
                "lat": 38.160,
                "lon": 23.710,
                "area_ha": 1850.0,
                "frp_mw": 195.0,
                "ros_m_min": 5.1,
                "wind_kmh": 40.0,
                "rh_pct": 19.0,
                "nearest_settlement_name": "Menidi / Thrakomakedones",
                "dist_to_settlement_km": 1.8,
                "fuel_type": "pine_timber",
            },
            {
                "id": "gr_olympia_ancient",
                "name": "Peloponnese Olympia Foothills",
                "lat": 37.640,
                "lon": 21.630,
                "area_ha": 920.0,
                "frp_mw": 85.0,
                "ros_m_min": 3.2,
                "wind_kmh": 22.0,
                "rh_pct": 28.0,
                "nearest_settlement_name": "Archaeological Park",
                "dist_to_settlement_km": 3.8,
                "fuel_type": "olive_shrub",
            },
            {
                "id": "gr_pindus_remote",
                "name": "Pindus High Mountain Lightning Fire",
                "lat": 39.950,
                "lon": 21.150,
                "area_ha": 310.0,
                "frp_mw": 35.0,
                "ros_m_min": 1.5,
                "wind_kmh": 15.0,
                "rh_pct": 42.0,
                "nearest_settlement_name": "Metsovo Valley",
                "dist_to_settlement_km": 22.0,
                "fuel_type": "alpine_pasture",
            },
        ],
    },
}


# -----------------------------------------------------------------------------
# Core Ranking Algorithm
# -----------------------------------------------------------------------------
def calculate_incident_score(inc: Dict[str, Any]) -> Dict[str, Any]:
    """Computes normalized multi-attribute priority score for a single incident."""
    if not isinstance(inc, dict):
        raise ValueError(f"Incident item must be a dictionary, got {type(inc).__name__}")

    dist_km = float(inc.get("dist_to_settlement_km", 20.0))
    if dist_km < 0:
        raise ValueError(f"dist_to_settlement_km cannot be negative, got {dist_km}")
    ros = float(inc.get("ros_m_min", 2.0))
    if ros < 0:
        raise ValueError(f"ros_m_min cannot be negative, got {ros}")
    frp = float(inc.get("frp_mw", 50.0))
    if frp < 0:
        raise ValueError(f"frp_mw cannot be negative, got {frp}")
    rh = float(inc.get("rh_pct", 35.0))
    if not (0.0 <= rh <= 100.0):
        raise ValueError(f"rh_pct must be between 0 and 100, got {rh}")
    wind = float(inc.get("wind_kmh", 20.0))
    if wind < 0:
        raise ValueError(f"wind_kmh cannot be negative, got {wind}")
    area_ha = float(inc.get("area_ha", 500.0))
    if area_ha < 0:
        raise ValueError(f"area_ha cannot be negative, got {area_ha}")

    # 1. Exposure Score E in [0, 100]: exponential distance decay
    # d <= 2 km -> ~85-100; d = 6 km -> ~60; d = 15 km -> ~28; d >= 35 km -> ~5
    e_raw = 100.0 * math.exp(-max(0.1, dist_km) / 10.0)
    exposure_score = round(float(np.clip(e_raw, 4.0, 100.0)), 1)

    # 2. Velocity Hazard V in [0, 100]: based on Rate of Spread
    # ROS 1.0 m/min -> 18; ROS 3.5 m/min -> 58; ROS >= 6.5 m/min -> 100
    v_raw = min(100.0, ros * 15.5)
    velocity_score = round(float(np.clip(v_raw, 5.0, 100.0)), 1)

    # 3. Radiative Power & Intensity I in [0, 100]: logarithmic FRP scaling
    # FRP 10 MW -> 22; FRP 100 MW -> 44; FRP 500 MW -> 60; FRP 1500 MW -> 75
    i_raw = 22.0 * math.log10(max(1.0, frp)) + math.log10(max(10.0, area_ha)) * 8.0
    intensity_score = round(float(np.clip(i_raw, 8.0, 100.0)), 1)

    # 4. Volatility & Dryness U in [0, 100]: fuel moisture deficit + wind gusting
    u_raw = (100.0 - rh) * 0.75 + (wind * 0.8)
    volatility_score = round(float(np.clip(u_raw, 10.0, 100.0)), 1)

    # 5. Composite Priority Score S in [0, 100]
    # Weights: Exposure 40%, Velocity 30%, Intensity 18%, Volatility 12%
    composite = (
        0.40 * exposure_score +
        0.30 * velocity_score +
        0.18 * intensity_score +
        0.12 * volatility_score
    )
    priority_score = round(float(np.clip(composite, 5.0, 100.0)), 1)

    # 6. Priority Tier & Advisory Classification (NOT automated dispatch or evacuation directives)
    if priority_score >= 75.0:
        tier_code = "P1"
        tier_label_en = "P1 — Critical Relative Screening Tier"
        tier_label_pl = "P1 — Krytyczny względny poziom przesiewowy"
        badge_color = "#dc2626"
        advisory_en = "Proximity to settlement coupled with elevated spread velocity and radiative power. Requires immediate human commander verification; not an automated evacuation order."
        advisory_pl = "Bliska odległość od osady w połączeniu z podwyższonym tempem rozprzestrzeniania i mocą FRP. Wymaga niezwłocznej weryfikacji przez dowódcę akcji; nie stanowi automatycznego nakazu ewakuacji."
    elif priority_score >= 55.0:
        tier_code = "P2"
        tier_label_en = "P2 — High Relative Screening Tier"
        tier_label_pl = "P2 — Wysoki względny poziom przesiewowy"
        badge_color = "#ea580c"
        advisory_en = "Active spread front advancing toward regional buffer zones. Continuous tactical assessment recommended."
        advisory_pl = "Aktywny front pożaru zbliżający się do stref buforowych. Zalecana ciągła ocena taktyczna."
    elif priority_score >= 35.0:
        tier_code = "P3"
        tier_label_en = "P3 — Elevated Relative Screening Tier"
        tier_label_pl = "P3 — Podwyższony względny poziom przesiewowy"
        badge_color = "#eab308"
        advisory_en = "Moderate spread rate with remaining distance buffer. Periodic satellite pass monitoring advised."
        advisory_pl = "Umiarkowane tempo rozprzestrzeniania z zachowanym buforem odległości. Zalecany okresowy monitoring satelitarny."
    else:
        tier_code = "P4"
        tier_label_en = "P4 — Routine Monitoring Tier"
        tier_label_pl = "P4 — Poziom rutynowego monitoringu"
        badge_color = "#65a30d"
        advisory_en = "Remote or low-intensity incident. Ongoing orbital surveillance; local on-scene conditions still govern operational action."
        advisory_pl = "Odległy lub niskoenergetyczny pożar. Bieżący nadzór orbitalny; o działaniach decydują lokalne warunki na miejscu zdarzenia."

    return {
        "id": inc.get("id", "inc_unknown"),
        "name": inc.get("name", "Unnamed Fire"),
        "lat": inc.get("lat"),
        "lon": inc.get("lon"),
        "priority_rank": 0,  # Assigned after sorting
        "composite_score": priority_score,
        "priority_tier": tier_code,
        "priority_label": f"KRYTYCZNY ({tier_label_en})" if tier_code == "P1" else f"{tier_code} ({tier_label_en})",
        "priority_label_en": tier_label_en,
        "priority_label_pl": tier_label_pl,
        "badge_color": badge_color,
        "sub_scores": {
            "exposure_score": exposure_score,
            "velocity_score": velocity_score,
            "intensity_score": intensity_score,
            "volatility_score": volatility_score,
        },
        "raw_attributes": {
            "area_ha": area_ha,
            "frp_mw": frp,
            "ros_m_min": ros,
            "wind_kmh": wind,
            "rh_pct": rh,
            "dist_to_settlement_km": dist_km,
            "nearest_settlement": inc.get("nearest_settlement_name", "Unknown"),
            "fuel_type": inc.get("fuel_type", "forest"),
        },
        "advisory_notes": advisory_en,
        "advisory_notes_pl": advisory_pl,
        "operational_guidance": advisory_en,  # backward compatibility alias
    }


def rank_incidents(incidents: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Scores and sorts a list of fire incidents in descending priority order."""
    if not incidents:
        return {
            "status": "success",
            "incident_count": 0,
            "top_priority_incidents": [],
            "summary": {"p1_critical_count": 0, "p2_high_count": 0, "p3_elevated_count": 0, "p4_monitoring_count": 0},
            "advisory_disclaimer": {
                "nature": "Experimental multi-attribute triage heuristic — uncalibrated relative score.",
                "operational_warning": "DO NOT use as an automated evacuation order, tactical dispatch directive, or resource de-allocation tool.",
            },
        }

    evaluated = [calculate_incident_score(inc) for inc in incidents]
    # Sort descending by composite score
    evaluated.sort(key=lambda x: x["composite_score"], reverse=True)

    # Assign 1-based ranks
    p1_c, p2_c, p3_c, p4_c = 0, 0, 0, 0
    for rank_idx, item in enumerate(evaluated, start=1):
        item["priority_rank"] = rank_idx
        t = item["priority_tier"]
        if t == "P1": p1_c += 1
        elif t == "P2": p2_c += 1
        elif t == "P3": p3_c += 1
        else: p4_c += 1

    return {
        "status": "success",
        "incident_count": len(evaluated),
        "top_priority_incidents": evaluated,
        "summary": {
            "p1_critical_count": p1_c,
            "p2_high_count": p2_c,
            "p3_elevated_count": p3_c,
            "p4_monitoring_count": p4_c,
            "top_incident_name": evaluated[0]["name"] if evaluated else None,
            "top_incident_score": evaluated[0]["composite_score"] if evaluated else None,
        },
        "scoring_methodology": {
            "weights": {"exposure_40pct": 0.40, "velocity_30pct": 0.30, "intensity_18pct": 0.18, "volatility_12pct": 0.12},
            "purpose": "Screening triage support to help assess relative severity across multi-fire regional outbreaks.",
        },
        "advisory_disclaimer": {
            "nature": "Experimental multi-attribute triage heuristic — uncalibrated relative ranking.",
            "operational_warning": (
                "DO NOT use as an automated evacuation order, tactical dispatch directive, or resource de-allocation tool. "
                "Actual emergency dispatch must account for local terrain, real-time fire weather warnings, structural fuel breaks, "
                "and certified incident commander judgment."
            ),
            "unaccounted_factors": [
                "Real-time topography and slope-driven spread acceleration",
                "Actual population density and vulnerable infrastructure",
                "Current firefighting asset deployment and local containment lines",
                "Spotting and ember flight beyond simple ROS",
            ],
            "human_in_the_loop": "Incident triage scores are auxiliary diagnostic aids requiring qualified human verification.",
        },
    }


# -----------------------------------------------------------------------------
# Input Validation Models
# -----------------------------------------------------------------------------
class FireIncidentInput(BaseModel):
    model_config = {"allow_inf_nan": False}   # NaN / Infinity are rejected with 422
    id: Optional[str] = "inc_custom"
    name: Optional[str] = "Unnamed Fire"
    lat: Optional[float] = Field(default=None, ge=-90.0, le=90.0)
    lon: Optional[float] = Field(default=None, ge=-180.0, le=180.0)
    area_ha: float = Field(default=500.0, ge=0.0, le=5000000.0)
    frp_mw: float = Field(default=50.0, ge=0.0, le=50000.0)
    ros_m_min: float = Field(default=2.0, ge=0.0, le=100.0)
    wind_kmh: float = Field(default=20.0, ge=0.0, le=250.0)
    rh_pct: float = Field(default=35.0, ge=0.0, le=100.0)
    nearest_settlement_name: Optional[str] = "Unknown"
    dist_to_settlement_km: float = Field(default=20.0, ge=0.0, le=2000.0)
    fuel_type: Optional[str] = "forest"


class RankRequest(BaseModel):
    incidents: Optional[List[FireIncidentInput]] = None
    fires: Optional[List[FireIncidentInput]] = None


# -----------------------------------------------------------------------------
# FastAPI Routes
# -----------------------------------------------------------------------------
@router.get("/presets")
def get_presets():
    """Lists available regional benchmark multi-incident scenarios."""
    return {
        "presets": [
            {
                "id": k,
                "name": v["name"],
                "name_pl": v["name_pl"],
                "region": v["region"],
                "preset_type": v.get("preset_type", "illustrative_scenario"),
                "provenance_note": v.get("provenance_note", ""),
                "source_reference": v.get("source_reference", ""),
                "incident_count": len(v["incidents"]),
            }
            for k, v in REGIONAL_PRESETS.items()
        ]
    }


@router.get("/rank")
def get_rank(preset: str = Query("western_canada_boreal")):
    """Evaluates and ranks incidents from a pre-configured regional outbreak scenario."""
    scen = REGIONAL_PRESETS.get(preset)
    if not scen:
        raise HTTPException(status_code=404, detail=f"Scenario '{preset}' not found in presets.")
    res = rank_incidents(scen["incidents"])
    res["scenario_id"] = preset
    res["scenario_name"] = scen["name"]
    res["preset_type"] = scen.get("preset_type", "illustrative_scenario")
    return res


@router.post("/rank")
def post_rank(payload: dict = Body(...)):
    """Ranks a custom user-submitted list of wildfire incidents with strict schema validation."""
    from pydantic import ValidationError
    try:
        body = RankRequest.model_validate(payload)
    except ValidationError as e:     # built by hand: the default 422 body cannot serialise a NaN input value
        raise HTTPException(status_code=422, detail=[{"loc": list(x["loc"]), "msg": x["msg"], "type": x["type"]} for x in e.errors()]) from None
    raw_list = body.incidents if body.incidents is not None else body.fires
    if raw_list is None:
        raise HTTPException(status_code=422, detail="Request body must contain 'incidents' or 'fires' list.")
    incidents = [item.model_dump() for item in raw_list]
    return rank_incidents(incidents)
