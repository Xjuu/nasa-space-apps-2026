"""Post-Replay Biome Error Analysis (Idea #27).

Systematic error decomposition across 6 distinct terrestrial biomes using the curated
demonstration replay benchmarks (Flin Flon, Kalimantan, Pantanal, Punjab, Roraima, Smokehouse Creek).

Methodology:
- Strictly diagnostic: analyzes existing replay records from cache/replay/ without online learning,
  weight tuning, or test-set leakage.
- Dissects spatial errors into Hits (TP), Misses (FN), and False Alarms (FP).
- Compares Raw Strict IoU, Tolerant Within-5km Matching, and Observable / Cloud-Adjusted scores.
- Classifies structural failure modes by biome (e.g. cloud masking in tropical peatlands,
  point-source scatter in agricultural burning, ignition latency in boreal crown fires).

Endpoints:
- GET /replay/biome-errors
- GET /replay/biome-errors/summary
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / "cache" / "replay"
FIXTURE_PATH = ROOT / "data" / "biome_benchmarks_reference.json"

_FIXTURE_CACHE: Optional[Dict[str, Any]] = None


def _load_reference_fixture() -> Dict[str, Any]:
    global _FIXTURE_CACHE
    if _FIXTURE_CACHE is not None:
        return _FIXTURE_CACHE
    if FIXTURE_PATH.is_file():
        try:
            _FIXTURE_CACHE = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
            return _FIXTURE_CACHE
        except Exception as exc:
            logger.warning(f"Failed to load fixture {FIXTURE_PATH}: {exc}")
    return {}

# -----------------------------------------------------------------------------
# Curated Biome Ecological & Fire Regime Metadata
# -----------------------------------------------------------------------------
BIOME_PROFILES: Dict[str, Dict[str, Any]] = {
    "flin_flon_2025": {
        "incident_id": "flin_flon_2025",
        "incident_name": "Flin Flon Wildfire Complex",
        "incident_name_pl": "Kompleks pożarów Flin Flon",
        "biome_id": "boreal_forest",
        "biome_name": "Boreal Forest (Taiga)",
        "biome_name_pl": "Las borealny (Tajga)",
        "region": "Manitoba / Saskatchewan border, Canada",
        "region_pl": "Pogranicze Manitoba / Saskatchewan, Kanada",
        "fuel_regime": "Spruce/pine crown fire and deep organic duff smoldering",
        "fuel_regime_pl": "Pożar wierzchołkowy świerka/sosny i tlenie głębokiej ściółki",
        "dominant_failure_mode": "delayed_crown_ignition",
        "failure_mode_label": "Delayed Crown-Fire Ignition & Lateral Fuel Overprediction",
        "failure_mode_label_pl": "Opóźnione modelowanie przejścia w koronę i boczny nadmiar prognozy",
        "diagnostic_insight": (
            "Model starts conservatively on Day 1 (strict IoU 9.1%) as physics propagation requires warm-up time "
            "to bridge surface fire to crown spread. By Day 3, tolerant within-5km agreement reaches 77.3% (IoU 63.0%), "
            "with false alarms concentrated in contiguous unburned spruce stands."
        ),
        "diagnostic_insight_pl": (
            "Model startuje zachowawczo w Dniu 1 (strict IoU 9,1%), gdyż przejście z pożaru ściółkowego w koronowy "
            "wymaga czasu rozgrzania. W Dniu 3 dopasowanie w promieniu 5 km osiąga 77,3% (IoU 63,0%), a fałszywe "
            "alarmy grupują się na przyległych, niewypalonych drzewostanach świerkowych."
        ),
    },
    "kalimantan_2023": {
        "incident_id": "kalimantan_2023",
        "incident_name": "South Kalimantan Peatland Outbreak",
        "incident_name_pl": "Pożary torfowisk w południowym Kalimantanie",
        "biome_id": "tropical_peatland",
        "biome_name": "Tropical Peatland & Swamp Scrub",
        "biome_name_pl": "Torfowiska tropikalne i bagienne zarośla",
        "region": "South Kalimantan, Borneo, Indonesia",
        "region_pl": "Południowy Kalimantan, Borneo, Indonezja",
        "fuel_regime": "Underground drained peat beds and degraded tropical heath",
        "fuel_regime_pl": "Odwodnione podpowierzchniowe pokłady torfu i zdegradowane zarośla",
        "dominant_failure_mode": "cloud_obscuration_blind_spot",
        "failure_mode_label": "Severe Cloud & Smoke Masking Blind Spot",
        "failure_mode_label_pl": "Martwe pole widzenia przez chmury konwekcyjne i smog",
        "diagnostic_insight": (
            "Persistent tropical cumulus and thick haze obscure satellite overpasses. "
            "On Day 1, 45.3% of false alarms (24 cells) fall under cloud cover, while across all 3 days 23.7% of false alarms "
            "(67 cells) are unverifiable. Filtering unverified false alarms (asymmetric cloud adjustment) increases Day 2 strict "
            "IoU from 19.7% to 25.3% (Day 1: 15.8% to 21.1%), with Day 3 1-cell neighborhood matching reaching 87.2%. "
            "Note: this is an unverified-FP filter, not a symmetric observable ground-truth mask."
        ),
        "diagnostic_insight_pl": (
            "Ciągłe chmury tropikalne i gęsty dym utrudniają obserwacje satelitarne. "
            "W Dniu 1 aż 45,3% fałszywych alarmów (24 komórki) leży pod chmurami, a łącznie w 3 dniach udział ten wynosi 23,7% "
            "(67 komórek). Po odrzuceniu nieweryfikowalnych fałszywych alarmów (korekta asymetryczna) strict IoU w Dniu 2 rośnie "
            "z 19,7% do 25,3% (w Dniu 1 z 15,8% do 21,1%), a dopasowanie sąsiedztwa 1 komórki w Dniu 3 osiąga 87,2%. "
            "Uwaga: jest to filtr prognozowanych FP, a nie symetryczna maska obserwowalności."
        ),
    },
    "pantanal_2024": {
        "incident_id": "pantanal_2024",
        "incident_name": "Pantanal Wetland Grassland Fire",
        "incident_name_pl": "Pożary sawanny i mokradeł Pantanalu",
        "biome_id": "flooded_savanna",
        "biome_name": "Seasonally Flooded Savanna & Wetland",
        "biome_name_pl": "Zalewowa sawanna i mokradła",
        "region": "Mato Grosso do Sul, Brazil / Bolivia border",
        "region_pl": "Mato Grosso do Sul, granica Brazylii i Boliwii",
        "fuel_regime": "Dry season desiccated tall grasses and riparian scrub",
        "fuel_regime_pl": "Wysuszone trawy wysokopienne i zarośla nadrzeczne w porze suchej",
        "dominant_failure_mode": "continuous_surface_front",
        "failure_mode_label": "High-Fidelity Continuous Surface Front Tracking",
        "failure_mode_label_pl": "Wysokiej wierności śledzenie ciągłego frontu traw",
        "diagnostic_insight": (
            "Top-performing benchmark: flat terrain and open fuel create a well-defined continuous thermal front. "
            "Zero unverified cells (clear skies). Strict IoU reaches 45.5% on Day 3 with a 100% recall in the tolerant "
            "5-km band and an 87.5% within-5km score."
        ),
        "diagnostic_insight_pl": (
            "Najlepiej oceniony przypadek: płaski teren i jednorodne paliwo tworzą czytelny, ciągły front termiczny. "
            "Zero komórek przesłoniętych chmurami. Strict IoU sięga 45,5% w Dniu 3, z 100% czułości w paśmie 5 km "
            "oraz wynikiem within-5km równym 87,5%."
        ),
    },
    "punjab_stubble_2024": {
        "incident_id": "punjab_stubble_2024",
        "incident_name": "Punjab Agricultural Crop Residue Burning",
        "incident_name_pl": "Rolnicze wypalanie ściernisk w Pendżabie",
        "biome_id": "agricultural_cropland",
        "biome_name": "Agricultural Croplands (Intensive Residue Burning)",
        "biome_name_pl": "Rolnicze użytki uprawne (Wypalanie ściernisk)",
        "region": "Punjab & Haryana plains, India",
        "region_pl": "Nizina Pendżabu i Haryany, Indie",
        "fuel_regime": "Paddy rice stubble post-harvest straw beds",
        "fuel_regime_pl": "Słoma i ściernisko po zbiorach ryżu",
        "dominant_failure_mode": "scattered_point_ignitions",
        "failure_mode_label": "Discrete Anthropogenic Field Ignitions (Spatial Dispersal)",
        "failure_mode_label_pl": "Drobne rozproszone podpalenia rolnicze (rozproszenie przestrzenne)",
        "diagnostic_insight": (
            "Stress test: spread models assume perimeter propagation, but agricultural burning consists of thousands "
            "of separate human ignitions across plot boundaries. Recall is nearly perfect (92.1% to 98.2%), but strict "
            "precision stays low (~30-39%) producing large cell-level false alarm counts (509 cells on Day 3). In a 5-km "
            "neighborhood, within-5km score rebounds to 90.8%."
        ),
        "diagnostic_insight_pl": (
            "Test obciążeniowy: modele fizyczne zakładają ciągłe czoło, tymczasem rolnictwo to tysiące punktowych "
            "podpaleń przez rolników. Czułość jest niemal idealna (92,1% do 98,2%), lecz ścisła precyzja niska (~30-39%), "
            "powodując 509 fałszywych alarmów komórkowych w Dniu 3. W promieniu 5 km wskaźnik wzrasta do 90,8%."
        ),
    },
    "roraima_2024": {
        "incident_id": "roraima_2024",
        "incident_name": "Roraima Forest-Savanna Ecotone Fire",
        "incident_name_pl": "Pożary na pograniczu lasu i sawanny w Roraimie",
        "biome_id": "tropical_ecotone",
        "biome_name": "Tropical Forest / Savanna Ecotone (Amazon Basin)",
        "biome_name_pl": "Pogranicze wilgotnego lasu i sawanny (Amazonia)",
        "region": "Roraima State, Northern Brazil",
        "region_pl": "Stan Roraima, północna Brazylia",
        "fuel_regime": "Deforestation slash, transitional dry forest understory and savanna grass",
        "fuel_regime_pl": "Wycinki odleśnieniowe, podszyt lasu przejściowego i trawy sawanny",
        "dominant_failure_mode": "ecotone_cloud_interception",
        "failure_mode_label": "Convective Cloud Interception at Deforestation Frontier",
        "failure_mode_label_pl": "Przechwytywanie chmur konwekcyjnych na froncie wylesiania",
        "diagnostic_insight": (
            "Daily afternoon convection creates rapid cloud cover over the rainforest edge. 35 to 50 predicted cells "
            "per day cannot be verified by VIIRS overpasses due to overcast conditions. Filtering unobservable cells "
            "lifts Day 1 strict IoU from 20.6% to 27.0% and Day 2 from 24.0% to 30.7%."
        ),
        "diagnostic_insight_pl": (
            "Popołudniowa konwekcja tworzy chmury nad skrajem lasu deszczowego. Od 35 do 50 prognozowanych komórek dziennie "
            "nie ma potwierdzenia VIIRS przez zachmurzenie. Odfiltrowanie nieobserwowalnych komórek podnosi strict IoU "
            "Dnia 1 z 20,6% do 27,0%, a Dnia 2 z 24,0% do 30,7%."
        ),
    },
    "smokehouse_creek_2024": {
        "incident_id": "smokehouse_creek_2024",
        "incident_name": "Smokehouse Creek Prairie Fire",
        "incident_name_pl": "Pożar prerii Smokehouse Creek",
        "biome_id": "temperate_grassland",
        "biome_name": "Temperate Semi-Arid Grassland (Prairie)",
        "biome_name_pl": "Preria umiarkowana (Usa Great Plains)",
        "region": "Texas Panhandle / Oklahoma border, USA",
        "region_pl": "Texas Panhandle, Teksas / Oklahoma, USA",
        "fuel_regime": "Dormant fine grasses, drought-cured tumbleweed, strong synoptic winds",
        "fuel_regime_pl": "Wyschnięte trawy stepowe, suchorośla i porywisty wiatr synoptyczny",
        "dominant_failure_mode": "high_velocity_wind_shift",
        "failure_mode_label": "High-Velocity Linear Spread & Cold-Front Flank Shift",
        "failure_mode_label_pl": "Wysoka prędkość liniowa i nagły zwrot flanki po froncie chłodnym",
        "diagnostic_insight": (
            "Fastest rate of spread in the catalog (>6 km/h under 50 km/h gusts). Because the initial fire footprint "
            "was very small (19 cells), minor directional errors under synoptic front passage induce strict misses (FN=11 "
            "on Day 2). However, tolerant 5-km recall quickly reaches 94.1% by Day 3 with a 74.4% within-5km score."
        ),
        "diagnostic_insight_pl": (
            "Najwyższa prędkość rozprzestrzeniania w katalogu (>6 km/h przy porywach do 50 km/h). Ponieważ pierwotny obrys "
            "miał zaledwie 19 komórek, drobne odchylenia kierunku wiatru dają ścisłe pominięcia (FN=11 w Dniu 2). W ujęciu "
            "tolerancyjnym czułość 5 km szybko osiąga 94,1% w Dniu 3 przy wskaźniku 74,4%."
        ),
    },
}


# -----------------------------------------------------------------------------
# Data Extraction & Normalization
# -----------------------------------------------------------------------------
def load_replay_record(incident_id: str, objective: str = "iou") -> Optional[Dict[str, Any]]:
    """Loads a cached replay file, falling back to bundled benchmark fixtures if cache is empty."""
    # 1. Try local cache directory if available
    if CACHE_DIR.is_dir():
        path = CACHE_DIR / f"{incident_id}__{objective}.json"
        if not path.is_file():
            alt_path = CACHE_DIR / f"{incident_id}__iou.json"
            if alt_path.is_file():
                path = alt_path
        if path.is_file():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.warning(f"Failed to read replay cache {path}: {exc}")

    # 2. Fallback to deterministic reference fixture
    fixtures = _load_reference_fixture()
    if incident_id in fixtures:
        return fixtures[incident_id]

    logger.warning(f"Replay record unavailable in cache or fixtures for {incident_id}")
    return None


def extract_incident_biome_metrics(incident_id: str, profile: Dict[str, Any], objective: str = "iou") -> Dict[str, Any]:
    """Extracts daily and cumulative metrics for a single biome demonstration incident."""
    data = load_replay_record(incident_id, objective=objective)
    if not data:
        return {
            "status": "unavailable",
            "incident_id": incident_id,
            "biome_name": profile["biome_name"],
            "note": "Replay cache file and fixture unavailable",
        }

    reveal = data.get("reveal") or {}
    per_day_raw = reveal.get("per_day") or []

    days_metrics: List[Dict[str, Any]] = []
    cum_hits = 0
    cum_misses = 0
    cum_fps = 0
    cum_pred = 0
    cum_obs = 0
    cum_unverified = 0

    for pd_entry in per_day_raw:
        day_num = int(pd_entry.get("day", 1))
        methods = pd_entry.get("methods") or {}
        # Select best available method: ml_physics if fitted, otherwise ml
        m_name = "ml_physics" if "ml_physics" in methods else "ml"
        m_obj = methods.get(m_name) or {}

        strict = m_obj.get("strict") or {}
        tol = m_obj.get("tolerant") or {}
        adj = m_obj.get("adjusted") or {}
        adj_s = adj.get("strict") or {} if isinstance(adj, dict) else {}
        observable = m_obj.get("observable") or {}
        obs_s = observable.get("strict") or {} if isinstance(observable, dict) else {}
        obs_t = observable.get("tolerant") or {} if isinstance(observable, dict) else {}
        coverage = observable.get("coverage") or {} if isinstance(observable, dict) else {}

        pred = int(strict.get("predicted_cells", 0))
        obs = int(strict.get("observed_cells", 0))
        hits = int(strict.get("hits", 0))
        misses = int(strict.get("misses", 0))
        fps = int(strict.get("false_alarms", 0))
        unver = int(adj.get("unverifiable_predicted_cells_strict", 0)) if isinstance(adj, dict) else 0

        cum_hits += hits
        cum_misses += misses
        cum_fps += fps
        cum_pred += pred
        cum_obs += obs
        cum_unverified += unver

        strict_iou = round(float(strict.get("iou", 0.0) or 0.0) * 100.0, 1)
        strict_prec = round(float(strict.get("precision", 0.0) or 0.0) * 100.0, 1)
        strict_rec = round(float(strict.get("recall", 0.0) or 0.0) * 100.0, 1)

        tol_iou = round(float(tol.get("iou", 0.0) or 0.0) * 100.0, 1)
        tol_w5km = round(float(tol.get("within_5km_score", 0.0) or 0.0) * 100.0, 1)
        tol_prec = round(float(tol.get("precision", 0.0) or 0.0) * 100.0, 1)
        tol_rec = round(float(tol.get("recall", 0.0) or 0.0) * 100.0, 1)

        # Cloud adjustment effect (asymmetric filter of unverified false alarms)
        adj_iou = round(float(adj_s.get("iou", 0.0) or 0.0) * 100.0, 1) if adj_s else strict_iou
        obs_iou = round(float(obs_s.get("iou", 0.0) or 0.0) * 100.0, 1) if obs_s and obs_s.get("iou") is not None else None
        obs_w5km = round(float(obs_t.get("within_5km_score", 0.0) or 0.0) * 100.0, 1) if obs_t and obs_t.get("within_5km_score") is not None else None

        # Edge case fix: observable_fraction=0.0 must not fallback to 1.0 via `or`
        if coverage and "observable_fraction" in coverage and coverage["observable_fraction"] is not None:
            obs_frac = round(float(coverage["observable_fraction"]) * 100.0, 1)
        else:
            obs_frac = None

        unver_share = round((unver / max(1, fps)) * 100.0, 1) if fps > 0 else 0.0

        days_metrics.append({
            "day": day_num,
            "date": pd_entry.get("date"),
            "model_method": m_name,
            "cells": {
                "predicted": pred,
                "observed": obs,
                "hits": hits,
                "misses": misses,
                "false_alarms": fps,
                "unverifiable_under_clouds": unver,
            },
            "strict": {
                "iou_pct": strict_iou,
                "precision_pct": strict_prec,
                "recall_pct": strict_rec,
            },
            "tolerant_5km": {
                "iou_pct": tol_iou,
                "within_5km_pct": tol_w5km,
                "precision_pct": tol_prec,
                "recall_pct": tol_rec,
                "neighborhood_definition": "Chebyshev 1-cell (3x3 dilation, ~5.5 km) grid neighborhood match.",
            },
            "observability": {
                "observable_fraction_pct": obs_frac,
                "observable_strict_iou_pct": obs_iou,
                "observable_within_5km_pct": obs_w5km,
                "adjusted_strict_iou_pct": adj_iou,
                "unverifiable_false_alarm_share_pct": unver_share,
                "adjustment_nature": "Asymmetric unverified-FP filter (removes predicted false alarms under clouds; not a symmetric ground-truth mask).",
            },
        })

    # 3-Day Cumulative Synthesis
    cum_union = max(1, cum_pred + cum_obs - cum_hits)
    cum_strict_iou = round((cum_hits / cum_union) * 100.0, 1)
    cum_strict_prec = round((cum_hits / max(1, cum_pred)) * 100.0, 1)
    cum_strict_rec = round((cum_hits / max(1, cum_obs)) * 100.0, 1)

    # Tolerant scores across 3 days
    day_3_tol = days_metrics[-1]["tolerant_5km"]["within_5km_pct"] if days_metrics else 0.0
    day_3_strict = days_metrics[-1]["strict"]["iou_pct"] if days_metrics else 0.0
    day_1_strict = days_metrics[0]["strict"]["iou_pct"] if days_metrics else 0.0
    day_1_unver = days_metrics[0]["observability"]["unverifiable_false_alarm_share_pct"] if days_metrics else 0.0
    cum_unver_pct = round((cum_unverified / max(1, cum_fps)) * 100.0, 1) if cum_fps > 0 else 0.0
    m_primary = days_metrics[0]["model_method"] if days_metrics else "ml_physics"

    return {
        "status": "success",
        "incident_id": incident_id,
        "incident_name": profile["incident_name"],
        "incident_name_pl": profile["incident_name_pl"],
        "biome_id": profile["biome_id"],
        "biome_name": profile["biome_name"],
        "biome_name_pl": profile["biome_name_pl"],
        "region": profile["region"],
        "region_pl": profile["region_pl"],
        "model_method": m_primary,
        "model_method_label": "ML + Rothermel Physics" if m_primary == "ml_physics" else "ML-only (Direct XGBoost)",
        "fuel_regime": profile["fuel_regime"],
        "fuel_regime_pl": profile["fuel_regime_pl"],
        "dominant_failure_mode": profile["dominant_failure_mode"],
        "failure_mode_label": profile["failure_mode_label"],
        "failure_mode_label_pl": profile["failure_mode_label_pl"],
        "diagnostic_insight": profile["diagnostic_insight"],
        "diagnostic_insight_pl": profile["diagnostic_insight_pl"],
        "cumulative_summary": {
            "model_method": m_primary,
            "total_predicted_cells": cum_pred,
            "total_observed_cells": cum_obs,
            "total_hits": cum_hits,
            "total_misses": cum_misses,
            "total_false_alarms": cum_fps,
            "total_unverifiable_cells": cum_unverified,
            "cumulative_strict_iou_pct": cum_strict_iou,
            "cumulative_strict_precision_pct": cum_strict_prec,
            "cumulative_strict_recall_pct": cum_strict_rec,
            "day_1_strict_iou_pct": day_1_strict,
            "day_3_strict_iou_pct": day_3_strict,
            "day_3_tolerant_5km_pct": day_3_tol,
            "day_1_unverifiable_fp_share_pct": day_1_unver,
            "unverifiable_false_alarm_fraction_pct": cum_unver_pct,
        },
        "days": days_metrics,
    }


def compute_all_biome_errors(objective: str = "iou") -> Dict[str, Any]:
    """Aggregates and compares error characteristics across all 6 reference biomes."""
    biomes_data: List[Dict[str, Any]] = []

    for inc_id, profile in BIOME_PROFILES.items():
        res = extract_incident_biome_metrics(inc_id, profile, objective=objective)
        if res.get("status") == "success":
            biomes_data.append(res)

    if not biomes_data:
        raise HTTPException(status_code=404, detail="No replay benchmark files or fixtures available for biome error analysis.")

    # Cross-Biome Comparison & Rankings
    # 1. Rank by Day 3 Strict IoU
    ranked_by_strict = sorted(
        biomes_data,
        key=lambda b: b["cumulative_summary"]["day_3_strict_iou_pct"],
        reverse=True,
    )
    # 2. Rank by Day 3 Tolerant (Within-5km) Score
    ranked_by_tolerant = sorted(
        biomes_data,
        key=lambda b: b["cumulative_summary"]["day_3_tolerant_5km_pct"],
        reverse=True,
    )
    # 3. Rank by Cloud Obscuration Impact (unverifiable false alarm share)
    ranked_by_cloud_impact = sorted(
        biomes_data,
        key=lambda b: b["cumulative_summary"]["unverifiable_false_alarm_fraction_pct"],
        reverse=True,
    )

    # Method distribution
    method_dist = {}
    for b in biomes_data:
        m = b["cumulative_summary"].get("model_method", "ml_physics")
        method_dist[m] = method_dist.get(m, 0) + 1

    # Means
    n = len(biomes_data)
    avg_d1_strict = round(sum(b["cumulative_summary"]["day_1_strict_iou_pct"] for b in biomes_data) / n, 1)
    avg_d3_strict = round(sum(b["cumulative_summary"]["day_3_strict_iou_pct"] for b in biomes_data) / n, 1)
    avg_d3_tol = round(sum(b["cumulative_summary"]["day_3_tolerant_5km_pct"] for b in biomes_data) / n, 1)
    avg_cum_strict = round(sum(b["cumulative_summary"]["cumulative_strict_iou_pct"] for b in biomes_data) / n, 1)
    avg_cum_prec = round(sum(b["cumulative_summary"]["cumulative_strict_precision_pct"] for b in biomes_data) / n, 1)
    avg_cum_rec = round(sum(b["cumulative_summary"]["cumulative_strict_recall_pct"] for b in biomes_data) / n, 1)

    return {
        "status": "success",
        "objective_requested": objective,
        "biome_count": n,
        "model_method_distribution": {
            "counts": method_dist,
            "note": "Kalimantan uses ML-only due to deep peat duff fuel regime without surface crown spread physics. Other 5 biomes utilize coupled ML + Rothermel physics.",
        },
        "biomes": biomes_data,
        "comparative_rankings": {
            "by_day3_strict_iou": [
                {"rank": idx + 1, "biome_id": b["biome_id"], "biome_name": b["biome_name"], "score_pct": b["cumulative_summary"]["day_3_strict_iou_pct"], "model_method": b["model_method"]}
                for idx, b in enumerate(ranked_by_strict)
            ],
            "by_day3_tolerant_within_5km": [
                {"rank": idx + 1, "biome_id": b["biome_id"], "biome_name": b["biome_name"], "score_pct": b["cumulative_summary"]["day_3_tolerant_5km_pct"], "model_method": b["model_method"]}
                for idx, b in enumerate(ranked_by_tolerant)
            ],
            "by_cloud_masking_penalty": [
                {"rank": idx + 1, "biome_id": b["biome_id"], "biome_name": b["biome_name"], "unverifiable_fp_pct": b["cumulative_summary"]["unverifiable_false_alarm_fraction_pct"], "model_method": b["model_method"]}
                for idx, b in enumerate(ranked_by_cloud_impact)
            ],
        },
        "cross_biome_benchmarks": {
            "mean_day_1_strict_iou_pct": avg_d1_strict,
            "mean_day_3_strict_iou_pct": avg_d3_strict,
            "mean_day_3_tolerant_within_5km_pct": avg_d3_tol,
            "mean_cumulative_strict_iou_pct": avg_cum_strict,
            "mean_cumulative_precision_pct": avg_cum_prec,
            "mean_cumulative_recall_pct": avg_cum_rec,
        },
        "methodology_disclaimer": {
            "no_online_learning": (
                "This biome error analysis operates strictly on preserved historical replay records. "
                "No weights were updated, no hyperparameters tuned, and no online learning was performed on these tests."
            ),
            "model_methodology_note": (
                "Kalimantan operates on ML-only due to peat duff fuel characteristics (absence of surface crown spread equations). "
                "The remaining 5 biomes utilize coupled ML + Rothermel physics. Comparisons reflect the operational model available for each respective region."
            ),
            "neighborhood_tolerance_definition": (
                "Within-5km scores represent 1-cell 3x3 binary dilation on the 0.05 degree grid (~5.5 km orthogonal, ~7.8 km diagonal). "
                "It measures spatial neighborhood overlap, not an exact circular radius or containment guarantee."
            ),
            "cloud_adjustment_definition": (
                "Cloud-adjusted IoU (adjusted.strict) removes predicted false alarms falling under thick cloud/haze (ERA5 proxy > 80%). "
                "It is an asymmetric false alarm filter, not a symmetric observable ground-truth mask."
            ),
        },
    }


# -----------------------------------------------------------------------------
# FastAPI Router
# -----------------------------------------------------------------------------
router = APIRouter(prefix="/replay", tags=["biome_errors"])


@router.get("/biome-errors")
def get_biome_errors(
    objective: str = Query("iou", description="iou = exact cell match; tol = 1-cell tolerance rule"),
    day: Optional[int] = Query(None, ge=1, le=3, description="Optional filter for specific day (1, 2, or 3)"),
) -> Dict[str, Any]:
    """Returns the complete multi-biome error decomposition and failure mode attribution."""
    res = compute_all_biome_errors(objective=objective)
    if day is not None:
        # Filter days array in each biome
        for b in res["biomes"]:
            b["days"] = [d for d in b["days"] if d["day"] == day]
    return res


@router.get("/biome-errors/summary")
def get_biome_errors_summary(
    objective: str = Query("iou", description="iou = exact cell match; tol = 1-cell tolerance rule"),
) -> Dict[str, Any]:
    """Returns a compact matrix table of errors by biome for fast dashboard rendering."""
    full = compute_all_biome_errors(objective=objective)
    rows = []
    for b in full["biomes"]:
        s = b["cumulative_summary"]
        rows.append({
            "biome_id": b["biome_id"],
            "biome_name": b["biome_name"],
            "biome_name_pl": b["biome_name_pl"],
            "incident_name": b["incident_name"],
            "incident_name_pl": b["incident_name_pl"],
            "model_method": s.get("model_method", "ml_physics"),
            "model_method_label": b.get("model_method_label", "ML + Rothermel Physics"),
            "failure_mode": b["failure_mode_label"],
            "failure_mode_pl": b["failure_mode_label_pl"],
            "day_1_strict_iou": s["day_1_strict_iou_pct"],
            "day_3_strict_iou": s["day_3_strict_iou_pct"],
            "day_3_tolerant_5km": s["day_3_tolerant_5km_pct"],
            "cumulative_strict_iou": s["cumulative_strict_iou_pct"],
            "cumulative_precision": s["cumulative_strict_precision_pct"],
            "cumulative_recall": s["cumulative_strict_recall_pct"],
            "day_1_unverifiable_fp_pct": s.get("day_1_unverifiable_fp_share_pct", 0.0),
            "unverifiable_fp_pct": s["unverifiable_false_alarm_fraction_pct"],
        })
    return {
        "status": "success",
        "rows": rows,
        "benchmarks": full["cross_biome_benchmarks"],
        "rankings": full["comparative_rankings"],
    }
