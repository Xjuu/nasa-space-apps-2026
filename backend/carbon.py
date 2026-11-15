"""CO2 emissions and carbon-value ticker from fire radiative power (FRP) or a simulated burned area.

Physics (published, simple):
  * Wooster et al. (2005): biomass burned [kg dry matter] ~= 0.368 x FRE [MJ]; FRP [MW] = MJ/s, so
    burned biomass rate [kg/s] ~= 0.368 x FRP [MW].
  * CO2 = dry matter x emission factor (g CO2 per kg dry matter, Akagi et al. 2011 / Andreae 2019 by biome).
  * Value = tonnes CO2 x an ASSUMED carbon price (default 70 EUR/t, the order of magnitude of EU ETS; NOT a live
    price, there is no keyless live feed) and an assumed USD/EUR rate.

What this number is: the market value of the CO2 emitted at an assumed price. It is NOT an insured loss, NOT damage
to property or ecosystems, NOT a social cost of carbon.

Honest limits: satellites sample a fire a few times a day, so the integrated total is a coarse estimate
(plausibly a factor 2-3 either way); the biome comes from a coarse lat/lon map; fuel loads are typical literature
values. Meant for orders of magnitude, not accounting.

Endpoints:  GET  /carbon/live?bbox=minlon,minlat,maxlon,maxlat | lat,lon,radius_km  &hours&price_eur_t&usd_per_eur&biome
            POST /carbon/forecast   (body: finished simulation JSON)
"""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Body, HTTPException, Query

WOOSTER_KG_PER_MJ = 0.368   # Wooster et al. 2005, dry matter

# g CO2 per kg dry matter burned (Akagi et al. 2011; Andreae 2019, rounded)
EF_CO2 = {"boreal": 1489, "temperate": 1509, "tropical_forest": 1643, "savanna": 1686, "cropland": 1585, "peat": 1703}
# typical dry matter consumed per hectare of fire, t/ha. ORDER OF MAGNITUDE only (varies by factor 2-5 with fuel and fire severity)
FUEL_T_HA = {"boreal": 20.0, "temperate": 15.0, "tropical_forest": 10.0, "savanna": 4.0, "cropland": 4.0, "peat": 50.0}
LABEL = {"boreal": "Boreal forest", "temperate": "Temperate forest / mixed", "tropical_forest": "Tropical forest",
         "savanna": "Savanna / grassland", "cropland": "Cropland (residue burning)", "peat": "Peatland"}
FOREST = {"boreal", "temperate", "tropical_forest", "peat"}


def guess_biome(lat: float, lon: float) -> str:
    """Very coarse biome map. Use the biome parameter to override."""
    if -8 <= lat <= 6 and 95 <= lon <= 120:
        return "peat"                               # Sumatra / Borneo peat belts
    if (-20 <= lat <= 8 and -80 <= lon <= -45) or (-6 <= lat <= 6 and 8 <= lon <= 30):
        return "tropical_forest"                    # Amazon, Congo basin
    if 20 <= lat <= 33 and 70 <= lon <= 90:
        return "cropland"                           # Indo-Gangetic plain (stubble burning)
    if lat >= 50:
        return "boreal"
    if abs(lat) <= 25:
        return "savanna"
    return "temperate"


def _prices(price_eur_t: float, usd_per_eur: float, co2_t: float) -> dict:
    eur = co2_t * price_eur_t
    return {"eur": round(eur), "usd": round(eur * usd_per_eur), "price_eur_t": price_eur_t, "usd_per_eur": usd_per_eur}


def assumptions(price_eur_t: float, usd_per_eur: float, biome: str, how: str) -> dict:
    return {
        "biomass_formula": "burned dry matter [kg/s] = 0.368 x FRP [MW] (Wooster et al. 2005)",
        "co2_emission_factor_g_per_kg": EF_CO2[biome], "biome": biome, "biome_label": LABEL[biome],
        "biome_source": how,
        "fuel_consumption_t_per_ha": FUEL_T_HA[biome],
        "carbon_price": "ASSUMED %.0f EUR/t CO2 (order of magnitude of EU ETS), not a live price; USD at an assumed %.2f per EUR" % (price_eur_t, usd_per_eur),
        "meaning": "Market value of the emitted CO2 at the assumed price. Not insured loss, not damage, not a social cost of carbon.",
        "uncertainty": "Coarse: satellites sample a fire a few times a day; expect a factor of 2-3 either way. Orders of magnitude only.",
    }


def from_detections(feats: list[dict], hours: float, lat: float | None, lon: float | None, biome: str = "auto",
                    price_eur_t: float = 70.0, usd_per_eur: float = 1.08) -> dict:
    """Integrate satellite FRP over the window. Detections are grouped into passes (same satellite, 30 min bin)."""
    how = "set by user"
    if biome == "auto":
        biome, how = guess_biome(lat if lat is not None else 0.0, lon if lon is not None else 0.0), "coarse lat/lon map"
    now = datetime.now(timezone.utc)
    passes: dict[tuple, float] = defaultdict(float)
    for f in feats:
        p = f["properties"]
        t = datetime.fromisoformat(p["time_utc"].replace("Z", "+00:00"))
        key = (p.get("satellite"), int(t.timestamp() // 1800))
        passes[key] += float(p["frp_mw"] or 0.0)
    if not passes:
        return {"type": "carbon_live", "estimate": True, "detections": 0, "passes": 0, "window_h": hours,
                "rate": {"frp_mw": 0.0, "co2_t_per_h": 0.0, "co2_kg_per_s": 0.0}, "total": {"co2_t": 0.0, "biomass_t": 0.0, "area_ha": 0.0, "forest_ha": 0.0, "value": _prices(price_eur_t, usd_per_eur, 0.0)},
                "assumptions": assumptions(price_eur_t, usd_per_eur, biome, how),
                "note": "No detections in this area and window."}
    totals = sorted(((k[1] * 1800, v) for k, v in passes.items()))
    mean_frp = sum(v for _, v in totals) / len(totals)
    # current rate: mean of pass totals in the last 6 h (or the latest pass if none)
    recent = [v for ts, v in totals if now.timestamp() - ts <= 6 * 3600] or [totals[-1][1]]
    rate_frp = sum(recent) / len(recent)
    ef = EF_CO2[biome] / 1000.0                                      # kg CO2 per kg DM
    kg_s = WOOSTER_KG_PER_MJ * rate_frp                              # kg DM / s
    window_s = hours * 3600.0
    biomass_t = WOOSTER_KG_PER_MJ * mean_frp * window_s / 1000.0     # mean pass FRP x window (diurnal cycle not modelled)
    co2_t = biomass_t * ef
    area_ha = biomass_t / FUEL_T_HA[biome]
    return {
        "type": "carbon_live", "estimate": True, "generated_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "window_h": hours, "detections": len(feats), "passes": len(totals), "biome": biome,
        "rate": {"frp_mw": round(rate_frp, 1), "biomass_kg_per_s": round(kg_s, 2), "co2_kg_per_s": round(kg_s * ef, 2),
                 "co2_t_per_h": round(kg_s * ef * 3.6, 2), "value_eur_per_h": round(kg_s * ef * 3.6 * price_eur_t)},
        "total": {"mean_pass_frp_mw": round(mean_frp, 1), "biomass_t": round(biomass_t), "co2_t": round(co2_t),
                  "area_ha": round(area_ha), "forest_ha": round(area_ha) if biome in FOREST else 0,
                  "value": _prices(price_eur_t, usd_per_eur, co2_t)},
        "assumptions": assumptions(price_eur_t, usd_per_eur, biome, how),
        "area_note": "Hectares are derived from the biomass and a typical fuel load, not measured from burned-area maps.",
    }


def from_simulation(sim: dict, biome: str = "auto", price_eur_t: float = 70.0, usd_per_eur: float = 1.08) -> dict:
    frames = sim.get("frames") or []
    if not frames:
        raise ValueError("simulation has no frames")
    q = sim.get("query") or {}
    how = "set by user"
    if biome == "auto":
        biome, how = guess_biome(float(q.get("lat") or 0.0), float(q.get("lon") or 0.0)), "coarse lat/lon map"
    ef = EF_CO2[biome] / 1000.0
    tl = []
    for f in frames:
        ha = float(f.get("burned_km2") or 0.0) * 100.0
        biomass_t = ha * FUEL_T_HA[biome]
        co2 = biomass_t * ef
        tl.append({"t_hours": f.get("t_hours"), "time_utc": f.get("time_utc"), "area_ha": round(ha), "forest_ha": round(ha) if biome in FOREST else 0,
                   "co2_t": round(co2), "value_eur": round(co2 * price_eur_t), "value_usd": round(co2 * price_eur_t * usd_per_eur)})
    last = tl[-1]
    return {"type": "carbon_forecast", "estimate": True, "biome": biome, "timeline": tl, "final": last,
            "assumptions": assumptions(price_eur_t, usd_per_eur, biome, how),
            "area_note": "Burned area is the simulated footprint (an uncalibrated estimate); CO2 = area x typical fuel load x emission factor."}


Fmt = Annotated[str, Query(pattern="^(auto|boreal|temperate|tropical_forest|savanna|cropland|peat)$")]


def _router() -> APIRouter:
    r = APIRouter(tags=["carbon"])

    @r.get("/carbon/live", summary="CO2 emitted and its value at an assumed carbon price, from live satellite FRP (rough estimate)")
    def carbon_live(bbox: str | None = Query(None, description="minlon,minlat,maxlon,maxlat"),
                    lat: float | None = Query(None, ge=-90, le=90), lon: float | None = Query(None, ge=-180, le=180),
                    radius_km: float = Query(30, gt=1, le=200), hours: float = Query(24, gt=0, le=168), biome: Fmt = "auto",
                    price_eur_t: float = Query(70, ge=1, le=1000), usd_per_eur: float = Query(1.08, ge=0.5, le=2.0)) -> dict:
        if bbox is None:
            if lat is None or lon is None:
                raise HTTPException(400, "give bbox or lat+lon")
            dlat = radius_km / 110.574
            dlon = radius_km / (111.32 * max(0.05, math.cos(math.radians(lat))))
            bbox = "%s,%s,%s,%s" % (lon - dlon, lat - dlat, lon + dlon, lat + dlat)
        try:
            a = [float(x) for x in bbox.split(",")]
            clat, clon = (a[1] + a[3]) / 2, (a[0] + a[2]) / 2
        except Exception:  # noqa: BLE001
            raise HTTPException(400, "bad bbox") from None
        from backend import app as _app
        d = _app.live_fires(bbox=bbox, hours=hours, limit=100000)
        out = from_detections(d["features"], hours, clat, clon, biome, price_eur_t, usd_per_eur)
        out["bbox"] = bbox
        return out

    @r.post("/carbon/forecast", summary="CO2 and value timeline for a finished simulation (rough estimate)")
    def carbon_forecast(body: dict = Body(...), biome: Fmt = "auto", price_eur_t: float = Query(70, ge=1, le=1000),
                        usd_per_eur: float = Query(1.08, ge=0.5, le=2.0)) -> dict:
        try:
            return from_simulation(body.get("simulation", body), biome, price_eur_t, usd_per_eur)
        except ValueError as e:
            raise HTTPException(400, str(e)) from None

    return r


router = _router()
