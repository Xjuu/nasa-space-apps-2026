"""Extra Data: Static Thermal Sources Mask (#13) & Observation Quality Index (#15).

Auxiliary diagnostic modules for the EquiFire / FireSpot platform.
Explicitly separated from the primary ML/physics forecast per coordinator guidelines.

Capabilities:
  1. Static Thermal Sources Mask (#13):
     Screening filter identifying persistent industrial flaring, metallurgical furnaces,
     petrochemical plants, and volcanic craters to prevent false wildfire alarms.
  2. Observation Quality Index (#15):
     Composite metric of sensor footprint geometry (Wolfe bow-tie model), atmospheric cloud
     attenuation (Open-Meteo), instrument resolution (VIIRS vs MODIS), and solar illumination.
     STRICT WORDING: Termed 'Wskaźnik jakości obserwacji' (Observation Quality Index).
     NOT an empirically calibrated confidence interval.
"""
from __future__ import annotations

import datetime as dt
import math
from typing import Any, Dict, List, Optional, Tuple, Union

from fastapi import APIRouter, Body, HTTPException, Query
import numpy as np

router = APIRouter(prefix="/extra-sq", tags=["extra-sq"])

# -----------------------------------------------------------------------------
# 1. Curated Reference Catalog of Persistent Static Thermal Sources
# -----------------------------------------------------------------------------
# Types: gas_flare, industrial_metallurgy, petrochemical_refinery, volcanic_thermal
STATIC_CATALOG: List[Dict[str, Any]] = [
    # Major Global Oil & Gas Flaring Regions
    {"id": "flare_basra", "name": "Basra Oil Fields Flare Complex", "lat": 30.52, "lon": 47.45, "type": "gas_flare", "country": "Iraq", "radius_km": 25.0},
    {"id": "flare_permian_midland", "name": "Permian Basin Flaring Cluster", "lat": 31.95, "lon": -102.15, "type": "gas_flare", "country": "USA", "radius_km": 30.0},
    {"id": "flare_bakken", "name": "Bakken Formation Flaring Cluster", "lat": 48.05, "lon": -103.10, "type": "gas_flare", "country": "USA", "radius_km": 25.0},
    {"id": "flare_samotlor", "name": "Samotlor Oil Field Flare Complex", "lat": 61.35, "lon": 76.70, "type": "gas_flare", "country": "Russia", "radius_km": 20.0},
    {"id": "flare_niger_delta", "name": "Niger Delta Flare Cluster", "lat": 4.85, "lon": 7.05, "type": "gas_flare", "country": "Nigeria", "radius_km": 35.0},
    {"id": "flare_monagas", "name": "Monagas Gas Flaring Complex", "lat": 9.80, "lon": -63.50, "type": "gas_flare", "country": "Venezuela", "radius_km": 20.0},
    {"id": "flare_khuzestan", "name": "Ahvaz / Khuzestan Flaring Field", "lat": 31.30, "lon": 49.00, "type": "gas_flare", "country": "Iran", "radius_km": 25.0},
    {"id": "flare_hassi_messaoud", "name": "Hassi Messaoud Flaring Field", "lat": 31.68, "lon": 6.05, "type": "gas_flare", "country": "Algeria", "radius_km": 20.0},
    {"id": "flare_bohai", "name": "Bohai Bay Offshore Platform Flares", "lat": 38.30, "lon": 119.20, "type": "gas_flare", "country": "China", "radius_km": 15.0},

    # Major Metallurgical & Industrial Smelting Facilities
    {"id": "ind_magnitogorsk", "name": "Magnitogorsk Iron & Steel Works", "lat": 53.42, "lon": 59.05, "type": "industrial_metallurgy", "country": "Russia", "radius_km": 5.0},
    {"id": "ind_gary_works", "name": "Gary Works Steel Plant", "lat": 41.61, "lon": -87.34, "type": "industrial_metallurgy", "country": "USA", "radius_km": 4.0},
    {"id": "ind_katowice", "name": "Upper Silesian Metallurgical Complex", "lat": 50.32, "lon": 19.12, "type": "industrial_metallurgy", "country": "Poland", "radius_km": 6.0},
    {"id": "ind_taranto", "name": "Taranto Acciaierie d'Italia", "lat": 40.50, "lon": 17.22, "type": "industrial_metallurgy", "country": "Italy", "radius_km": 4.5},
    {"id": "ind_jubail", "name": "Jubail Industrial Petrochemical City", "lat": 27.02, "lon": 49.62, "type": "petrochemical_refinery", "country": "Saudi Arabia", "radius_km": 12.0},
    {"id": "ind_dunkirk", "name": "Dunkirk ArcelorMittal Steelworks", "lat": 51.04, "lon": 2.30, "type": "industrial_metallurgy", "country": "France", "radius_km": 4.0},
    {"id": "ind_flin_flon", "name": "Flin Flon Zinc/Copper Smelter Complex", "lat": 54.76, "lon": -101.88, "type": "industrial_metallurgy", "country": "Canada", "radius_km": 3.0},

    # Persistent Active Volcanic Heat Centers
    {"id": "volc_kilauea", "name": "Kilauea Caldera Thermal Vent", "lat": 19.42, "lon": -155.29, "type": "volcanic_thermal", "country": "USA", "radius_km": 6.0},
    {"id": "volc_erta_ale", "name": "Erta Ale Persistent Lava Lake", "lat": 13.60, "lon": 40.67, "type": "volcanic_thermal", "country": "Ethiopia", "radius_km": 4.0},
    {"id": "volc_etna", "name": "Mount Etna Active Summit Craters", "lat": 37.75, "lon": 14.99, "type": "volcanic_thermal", "country": "Italy", "radius_km": 5.0},
    {"id": "volc_stromboli", "name": "Stromboli Continuous Eruptive Vent", "lat": 38.79, "lon": 15.21, "type": "volcanic_thermal", "country": "Italy", "radius_km": 3.0},
    {"id": "volc_nyiragongo", "name": "Mount Nyiragongo Lava Lake", "lat": -1.52, "lon": 29.25, "type": "volcanic_thermal", "country": "DRC", "radius_km": 5.0},
    {"id": "volc_masaya", "name": "Masaya Active Degassing Crater", "lat": 11.98, "lon": -86.16, "type": "volcanic_thermal", "country": "Nicaragua", "radius_km": 3.5},
]


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculates great-circle distance between two coordinates in kilometers."""
    r = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2.0) ** 2
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return r * c


# -----------------------------------------------------------------------------
# Static Sources Screening Filter (#13)
# -----------------------------------------------------------------------------
def screen_static_sources(
    lat: float,
    lon: float,
    radius_km: float = 25.0,
    detections: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Screens geographic location and optional detections against known persistent heat sources."""
    lat, lon = float(lat), float(lon)
    radius_km = float(min(100.0, max(1.0, radius_km)))

    nearby_sources = []
    for s in STATIC_CATALOG:
        dist = _haversine_km(lat, lon, s["lat"], s["lon"])
        if dist <= (radius_km + s["radius_km"]):
            nearby_sources.append({
                "id": s["id"],
                "name": s["name"],
                "type": s["type"],
                "country": s["country"],
                "center_lat": s["lat"],
                "center_lon": s["lon"],
                "distance_km": round(dist, 2),
                "site_buffer_km": s["radius_km"],
                "inside_static_buffer": bool(dist <= s["radius_km"]),
            })

    # Sort by closest first
    nearby_sources.sort(key=lambda x: x["distance_km"])

    # If list of hot-spot detections is provided, classify and filter them
    filtered_hotspots: List[Dict[str, Any]] = []
    flagged_static: List[Dict[str, Any]] = []

    if detections:
        for idx, pt in enumerate(detections):
            val_lat = pt.get("latitude") if pt.get("latitude") is not None else pt.get("lat")
            p_lat = float(lat if val_lat is None else val_lat)
            val_lon = pt.get("longitude") if pt.get("longitude") is not None else pt.get("lon")
            p_lon = float(lon if val_lon is None else val_lon)
            frp = float(pt.get("frp", 10.0))

            matched_source = None
            for s in nearby_sources:
                d_pt = _haversine_km(p_lat, p_lon, s["center_lat"], s["center_lon"])
                if d_pt <= s["site_buffer_km"]:
                    matched_source = {**s, "dist_from_hotspot_km": round(d_pt, 2)}
                    break

            item = {
                "index": idx,
                "lat": p_lat,
                "lon": p_lon,
                "frp": frp,
                "instrument": pt.get("instrument", "VIIRS"),
            }

            if matched_source:
                flagged_static.append({
                    **item,
                    "flagged_as_static": True,
                    "static_reason": f"Within {matched_source['site_buffer_km']} km of {matched_source['name']} ({matched_source['type']})",
                    "source_id": matched_source["id"],
                    "source_type": matched_source["type"],
                })
            else:
                filtered_hotspots.append({
                    **item,
                    "flagged_as_static": False,
                    "candidate_wildfire": True,
                })

    total_in = len(detections) if detections is not None else 0
    flagged_count = len(flagged_static)
    unflagged_count = len(filtered_hotspots)
    flag_rate_pct = round((flagged_count / total_in * 100.0), 1) if total_in > 0 else 0.0

    in_known_static_zone = any(s["inside_static_buffer"] for s in nearby_sources)

    return {
        "status": "success",
        "query": {
            "lat": lat,
            "lon": lon,
            "radius_km": radius_km,
            "detections_evaluated": total_in,
        },
        "static_zone_assessment": {
            "in_known_static_buffer": in_known_static_zone,
            "nearby_static_sources_count": len(nearby_sources),
            "closest_source": nearby_sources[0] if nearby_sources else None,
            "all_nearby_sources": nearby_sources,
            "status_label": "known_static_buffer" if in_known_static_zone else "negative_screening_no_cataloged_sources",
            "status_description": (
                "Location is within buffer of a known persistent thermal source." if in_known_static_zone
                else "Negative screening: No cataloged static thermal sources within screening radius (does not guarantee absence of uncataloged local sources)."
            ),
        },
        "screening_results": {
            "total_detections": total_in,
            "flagged_static_count": flagged_count,
            "unflagged_candidates_count": unflagged_count,
            "verified_wildfire_candidates_count": unflagged_count,  # backward compatibility alias
            "static_source_flag_rate_pct": flag_rate_pct,
            "heuristic_static_flag_rate_pct": flag_rate_pct,
            "false_alarm_reduction_pct": flag_rate_pct,  # backward compatibility alias
            "flagged_static_detections": flagged_static[:50],
            "sample_wildfire_candidates": filtered_hotspots[:50],
        },
        "disclaimer_and_notes": {
            "role": "Auxiliary data (Extra data) — not mixed into core spread prediction",
            "metric_interpretation": (
                "static_source_flag_rate_pct indicates the proportion of detections matching "
                "the static industrial/volcanic catalog within spatial buffers. "
                "It is a heuristic catalog screening match rate, NOT an empirically calibrated false alarm reduction against ground-truth wildfire perimeters."
            ),
            "guidance": "Hotspots within static thermal buffers should be cross-referenced with industrial flare records and local permits before wildfire dispatch.",
            "negative_screening_note": (
                "Absence of cataloged static sources indicates negative screening against the reference catalog, "
                "not a guarantee of absence of uncataloged local industrial or agricultural sources."
            ),
            "basis": "Curated catalog of high-persistence global flaring fields, metallurgy, and volcanic centers (Schroeder et al. 2014; Elvidge et al. 2016).",
        },
    }


# -----------------------------------------------------------------------------
# Observation Quality Index (#15)
# -----------------------------------------------------------------------------
def compute_observation_quality(
    lat: float,
    lon: float,
    scan_angle_deg: float = 15.0,
    cloud_cover_pct: Optional[float] = None,
    cloud_tier: Optional[str] = None,
    instrument: str = "viirs",
    is_day: bool = False,
    solar_zenith_deg: Optional[float] = None,
) -> Dict[str, Any]:
    """Computes a multi-factor Observation Quality Index [0.0 - 1.0] for satellite active fire detection."""
    scan_deg = float(min(65.0, max(0.0, scan_angle_deg)))
    scan_rad = math.radians(scan_deg)

    # 1. Geometry Score: Wolfe et al. (2002) bow-tie degradation
    # Nadir (0 deg) = 1.0; 30 deg = ~0.75; 55 deg = ~0.35
    geom_score = float(np.clip(math.cos(scan_rad) ** 2 * (1.0 - 0.25 * (scan_deg / 60.0)), 0.15, 1.0))

    # 2. Cloud Clarity Score: Non-linear optical attenuation proxy
    has_cloud_data = cloud_cover_pct is not None
    if has_cloud_data:
        c_pct = float(np.clip(cloud_cover_pct, 0.0, 100.0))
        cloud_score = float(np.clip(1.0 - (c_pct / 100.0) ** 1.3, 0.02, 1.0))
    else:
        c_pct = None
        cloud_score = None

    # 3. Instrument Spatial Sampling Factor
    inst_lower = instrument.lower()
    if "viirs" in inst_lower or "375" in inst_lower:
        inst_score = 1.00  # VIIRS 375 m I-band: superior spatial resolution
        inst_name = "VIIRS (375 m nominal nadir)"
    elif "modis" in inst_lower or "1km" in inst_lower:
        inst_score = 0.72  # MODIS 1 km nominal: coarser resolution, higher minimum FRP threshold
        inst_name = "MODIS (1 km nominal nadir)"
    else:
        inst_score = 0.85
        inst_name = f"{instrument} (generic)"

    # 4. Illumination & Solar Glint Factor
    if not is_day:
        illum_score = 1.00  # Night pass: zero solar glint, optimal thermal contrast
        illum_desc = "Night observation — maximum infrared thermal contrast; zero solar glint."
    else:
        if solar_zenith_deg is not None:
            sz = float(solar_zenith_deg)
            if sz > 85.0:
                illum_score = 1.00
                illum_desc = "Twilight / low sun angle — minimal glint risk."
            elif sz < 30.0:
                illum_score = 0.70
                illum_desc = "High overhead sun — elevated risk of solar glint off water or specular surfaces."
            else:
                illum_score = 0.85
                illum_desc = "Daytime observation — moderate solar background illumination."
        else:
            illum_score = 0.85
            illum_desc = "Daytime pass — nominal solar illumination assumed."

    # 5. Composite Quality Index Calculation
    if has_cloud_data and cloud_score is not None:
        w_geom, w_cloud, w_illum = 0.40, 0.45, 0.15
        composite_raw = inst_score * (w_geom * geom_score + w_cloud * cloud_score + w_illum * illum_score)
    else:
        # Without cloud data, geometry and illumination describe sensor observation condition,
        # but atmospheric transparency remains unverified.
        w_geom, w_illum = 0.70, 0.30
        composite_raw = inst_score * (w_geom * geom_score + w_illum * illum_score)

    composite_index = round(float(np.clip(composite_raw, 0.05, 1.0)), 3)

    # 6. Qualitative Tiering
    if has_cloud_data:
        if composite_index >= 0.80:
            quality_tier = "optimal"
            tier_label_en = "Optimal detection quality"
            tier_label_pl = "Optymalna jakość detekcji"
            tier_desc = "Excellent sensor geometry and clear atmosphere. High sensitivity to small and low-intensity fires."
            tier_desc_pl = "Doskonała geometria i czysta atmosfera. Bardzo wysoka czułość na małe i niskoenergetyczne pożary."
        elif composite_index >= 0.60:
            quality_tier = "good"
            tier_label_en = "Good / usable quality"
            tier_label_pl = "Dobra / użyteczna jakość"
            tier_desc = "Good visibility; minor scan angle distortion or moderate solar background."
            tier_desc_pl = "Dobra widoczność; nieznaczne zniekształcenie geometrii lub umiarkowane tło słoneczne."
        elif composite_index >= 0.40:
            quality_tier = "degraded"
            tier_label_en = "Degraded / restricted quality"
            tier_label_pl = "Zdegradowana / ograniczona"
            tier_desc = "Pronounced bow-tie distortion near scan edge or partial cloud cover. Risk of missing small fires."
            tier_desc_pl = "Wyraźne zniekształcenie bow-tie przy skraju skanu lub częściowe zachmurzenie. Ryzyko pominięcia małych pożarów."
        else:
            quality_tier = "compromised"
            tier_label_en = "Low quality / high miss risk"
            tier_label_pl = "Niska jakość / wysokie ryzyko pominięcia"
            tier_desc = "Dense cloud cover or extreme scan edge. Sensor has severely degraded ability to detect ground fires."
            tier_desc_pl = "Gęste zachmurzenie lub skrajny kąt skanu. Sensor ma silnie ograniczoną możliwość detekcji pożarów gruntowych."
    else:
        # Atmosphere unverified: cap tier at "good" (never "optimal") and clarify in descriptions
        if composite_index >= 0.60:
            quality_tier = "good"
            tier_label_en = "Good geometry (unverified atmosphere)"
            tier_label_pl = "Dobra geometria (atmosfera nieweryfikowana)"
            tier_desc = "Optimal or good observation geometry, but atmospheric transparency and cloud cover were unverified."
            tier_desc_pl = "Optymalna lub dobra geometria obserwacji, lecz stan atmosfery i zachmurzenie nie były weryfikowane."
        elif composite_index >= 0.40:
            quality_tier = "degraded"
            tier_label_en = "Degraded geometry (unverified atmosphere)"
            tier_label_pl = "Zdegradowana geometria (atmosfera nieweryfikowana)"
            tier_desc = "Noticeable bow-tie distortion or solar background; cloud cover unverified."
            tier_desc_pl = "Zniekształcenie geometrii lub tło słoneczne; zachmurzenie nieweryfikowane."
        else:
            quality_tier = "compromised"
            tier_label_en = "Compromised geometry (unverified atmosphere)"
            tier_label_pl = "Niska jakość geometryczna (atmosfera nieweryfikowana)"
            tier_desc = "Extreme scan edge distortion; cloud cover unverified."
            tier_desc_pl = "Skrajny kąt skanu; zachmurzenie nieweryfikowane."

    return {
        "status": "success",
        "query": {
            "lat": lat,
            "lon": lon,
            "instrument": inst_name,
            "scan_angle_deg": scan_deg,
            "cloud_cover_pct": c_pct,
            "is_day": is_day,
            "solar_zenith_deg": solar_zenith_deg,
        },
        "observation_quality_index": composite_index,
        "quality_tier": quality_tier,
        "quality_tier_pl": tier_label_pl,
        "quality_tier_en": tier_label_en,
        "tier_description": tier_desc,
        "tier_description_pl": tier_desc_pl,
        "tier_description_en": tier_desc,
        "sub_scores": {
            "geometry_score": round(geom_score, 3),
            "cloud_clarity_score": round(cloud_score, 3) if has_cloud_data and cloud_score is not None else None,
            "instrument_resolution_weight": round(inst_score, 3),
            "illumination_score": round(illum_score, 3),
        },
        "cloud_accounted": has_cloud_data,
        "illumination_notes": illum_desc,
        "terminology_disclaimer": {
            "term_used": "Wskaźnik jakości obserwacji (Observation Quality Index)",
            "warning": "Wskaźnik ten NIE jest skalibrowanym empirycznie przedziałem ufności. Służy do diagnostyki wiarygodności satelitarnej detekcji pożaru.",
            "extra_data_role": "Pomocniczy moduł jakościowy — nie zastępuje walidacji naziemnej ani prognozy fizycznej.",
        },
    }


# -----------------------------------------------------------------------------
# FastAPI Endpoints
# -----------------------------------------------------------------------------
@router.get("/static-sources")
def get_static_sources(
    lat: float = Query(54.975, ge=-90.0, le=90.0),
    lon: float = Query(-101.725, ge=-180.0, le=180.0),
    radius_km: float = Query(25.0, ge=1.0, le=100.0),
):
    """Checks proximity to known persistent industrial flaring, metallurgy, or volcanic heat sources."""
    try:
        return screen_static_sources(lat=lat, lon=lon, radius_km=radius_km)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Static sources evaluation error: {str(exc)}")


@router.post("/static-sources")
def post_static_sources(
    body: Dict[str, Any] = Body(...),
    lat: Optional[float] = Query(None),
    lon: Optional[float] = Query(None),
    radius_km: float = Query(25.0, ge=1.0, le=100.0),
):
    """Filters a provided array of hotspot detections against static persistent thermal sources."""
    req_lat = float(body.get("lat", lat if lat is not None else 54.975))
    req_lon = float(body.get("lon", lon if lon is not None else -101.725))
    req_radius = float(body.get("radius_km", radius_km))
    detections = body.get("detections") or body.get("hotspots")

    try:
        return screen_static_sources(lat=req_lat, lon=req_lon, radius_km=req_radius, detections=detections)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Static sources screening error: {str(exc)}")


@router.get("/quality-index")
def get_quality_index(
    lat: float = Query(54.975, ge=-90.0, le=90.0),
    lon: float = Query(-101.725, ge=-180.0, le=180.0),
    scan_angle_deg: float = Query(15.0, ge=0.0, le=65.0),
    cloud_cover_pct: Optional[float] = Query(None, ge=0.0, le=100.0),
    instrument: str = Query("viirs"),
    is_day: bool = Query(False),
    solar_zenith_deg: Optional[float] = Query(None, ge=0.0, le=180.0),
):
    """Computes the Observation Quality Index for satellite active fire detection."""
    try:
        return compute_observation_quality(
            lat=lat,
            lon=lon,
            scan_angle_deg=scan_angle_deg,
            cloud_cover_pct=cloud_cover_pct,
            instrument=instrument,
            is_day=is_day,
            solar_zenith_deg=solar_zenith_deg,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Observation quality calculation error: {str(exc)}")
