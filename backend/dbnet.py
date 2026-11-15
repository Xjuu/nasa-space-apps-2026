"""NASA Direct Broadcast / Direct Readout geometry and sun-satellite illumination geometry.

Both modules are pure orbit geometry (SGP4 + TLE, no network). They do not receive any satellite signal.

1. Direct Broadcast (DB) coverage:
   Terra, Aqua, Suomi NPP and NOAA-20/21 transmit MODIS/VIIRS data live in X-band. A receiving station that has the
   satellite above its horizon (elevation >= mask) at the moment of observation can get the data within minutes,
   instead of waiting for the stored-data downlink and the central processing (FIRMS says NRT data arrive within
   about 3 hours of observation). FireSpot does NOT operate a receiver and does NOT process L0/L1/L2; it shows when
   and where such a receiver could have the data, computed from orbits. The station list is a sample of commonly
   cited NASA / partner sites with approximate coordinates; check NASA DRL for the official list.

2. Sun-satellite illumination geometry at the fire (hot-spot / sun-glint risk):
   Daytime fire detection uses the 3.9 um band, which contains reflected sunlight. A small sun-glint angle (the
   satellite looks along the specular reflection of the sun) is a known source of false alarms; a small
   backscatter angle is the "hot-spot" geometry of land reflectance (BRDF). We compute the angles and a risk tier.
   We do NOT apply a radiometric BRDF correction (no MCD43 data used) and we do not claim a fixed percentage.

Endpoints: GET /dbnet/live, GET /dbnet/race?lat&lon, GET /illumination?lat&lon
"""
from __future__ import annotations

import datetime as dt
import math

import numpy as np
from fastapi import APIRouter, HTTPException, Query
from sgp4.api import Satrec

from . import orbits as ob

R_E = 6371.0
STATIONS = [
    {"id": "fairbanks", "name": "Fairbanks, Alaska", "lat": 64.86, "lon": -147.85},
    {"id": "svalbard", "name": "Svalbard (SvalSat)", "lat": 78.23, "lon": 15.40},
    {"id": "whitesands", "name": "White Sands, New Mexico", "lat": 32.50, "lon": -106.61},
    {"id": "mcmurdo", "name": "McMurdo, Antarctica", "lat": -77.85, "lon": 166.67},
    {"id": "greenbelt", "name": "NASA GSFC, Greenbelt, Maryland", "lat": 38.99, "lon": -76.84},
]
FIRMS_NRT_NOTE = "FIRMS: near-real-time data are available within about 3 hours of the satellite observation (published target)."
DB_LIMITS = [
    "Geometry only: FireSpot has no X-band receiver and does not process L0/L1/L2 data. The detections you see here come from the public FIRMS feed.",
    "Station list is a sample with approximate coordinates; a given station may not be configured for every satellite. See NASA Direct Readout Laboratory for the official list.",
    "Real direct-broadcast latency (reception + processing) depends on the receiving centre and is not measured here.",
]


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def _sat_ecef(sat: Satrec, jd, fr):
    err, r, _ = sat.sgp4_array(jd, fr)
    g = ob._gmst(jd + fr)
    c, s = np.cos(g), np.sin(g)
    out = np.c_[c * r[:, 0] + s * r[:, 1], -s * r[:, 0] + c * r[:, 1], r[:, 2]]
    out[err != 0] = np.nan
    return out


def _geodetic(p: np.ndarray) -> tuple[float, float, float]:
    x, y, z = p
    rr = math.sqrt(x * x + y * y + z * z)
    return math.degrees(math.asin(z / rr)), math.degrees(math.atan2(y, x)), rr - R_E


def _el_range(sat_ecef: np.ndarray, lat: float, lon: float) -> tuple[np.ndarray, np.ndarray]:
    obs, up = ob._observer_ecef(lat, lon)
    rho = sat_ecef - obs
    rng = np.linalg.norm(rho, axis=1)
    return np.degrees(np.arcsin(np.clip(rho @ up / rng, -1, 1))), rng


def _station_radius_km(alt_km: float, mask_deg: float) -> float:
    el = math.radians(mask_deg)
    return R_E * (math.acos(R_E * math.cos(el) / (R_E + alt_km)) - el)


def _sats(tles: dict):
    for sn in ob.SENSORS:
        if sn.norad in tles:
            yield sn, Satrec.twoline2rv(*tles[sn.norad])


def _dt_arrays(times):
    return ob.julian(times)


# ------------------------------------------------------------------ live links for the globe
def live_links(mask_deg: float = 5.0, when: dt.datetime | None = None) -> dict:
    t = when or _now()
    tles = ob.read_tles()
    jd, fr = _dt_arrays([t])
    links, sats = [], []
    for sn, sat in _sats(tles):
        e = _sat_ecef(sat, jd, fr)[0]
        if np.isnan(e).any():
            continue
        slat, slon, salt = _geodetic(e)
        best = None
        for st in STATIONS:
            el, rng = _el_range(e[None, :], st["lat"], st["lon"])
            if el[0] >= mask_deg and (best is None or el[0] > best[1]):
                best = (st, float(el[0]), float(rng[0]))
        sats.append({"key": sn.key, "name": sn.name, "norad": sn.norad, "instrument": sn.instrument,
                     "lat": round(slat, 3), "lon": round(slon, 3), "alt_km": round(salt, 1), "linked": best is not None})
        if best:
            links.append({"satellite": sn.name, "norad": sn.norad, "station": best[0]["name"], "station_id": best[0]["id"],
                          "station_lat": best[0]["lat"], "station_lon": best[0]["lon"], "sat_lat": round(slat, 3),
                          "sat_lon": round(slon, 3), "sat_alt_km": round(salt, 1), "elevation_deg": round(best[1], 1),
                          "range_km": round(best[2])})
    alt = 830.0
    return {"time_utc": t.strftime("%Y-%m-%dT%H:%M:%SZ"), "mask_deg": mask_deg, "satellites": sats, "links": links,
            "stations": [dict(s, radius_km=round(_station_radius_km(alt, mask_deg))) for s in STATIONS],
            "limits": DB_LIMITS, "firms_nrt_note": FIRMS_NRT_NOTE}


# ------------------------------------------------------------------ the race for one fire
def _contact_windows(sat: Satrec, t0: dt.datetime, hours: float, mask_deg: float, step_s: int = 20) -> list[dict]:
    n = int(hours * 3600 / step_s) + 1
    times = [t0 + dt.timedelta(seconds=step_s * i) for i in range(n)]
    jd, fr = _dt_arrays(times)
    e = _sat_ecef(sat, jd, fr)
    out = []
    for st in STATIONS:
        el, _ = _el_range(e, st["lat"], st["lon"])
        up = np.nan_to_num(el, nan=-90) >= mask_deg
        idx = np.flatnonzero(np.diff(np.r_[0, up.astype(np.int8), 0]))
        for a, b in zip(idx[::2], idx[1::2]):
            out.append({"station": st["name"], "station_id": st["id"], "aos": times[a], "los": times[b - 1],
                        "max_el": float(np.nanmax(el[a:b]))})
    out.sort(key=lambda w: w["aos"])
    return out


def race(lat: float, lon: float, hours: float = 24.0, mask_deg: float = 5.0) -> dict:
    t0 = _now()
    tles = ob.read_tles()
    pas = ob.passes(lat, lon, t0, hours, tles)
    items = []
    for info in pas:
        sn = next((s for s in ob.SENSORS if s.key == info["key"]), None)
        if sn is None or sn.norad not in tles or not info.get("passes"):
            continue
        sat = Satrec.twoline2rv(*tles[sn.norad])
        wins = _contact_windows(sat, t0, hours + 3.0, mask_deg)
        for p in info["passes"]:
            tb = dt.datetime.fromisoformat(p["best"])
            # direct broadcast: a station sees the satellite at the moment it observes the fire
            live = [w for w in wins if w["aos"] <= tb <= w["los"]]
            nxt = next((w for w in wins if w["aos"] > tb), None)
            rec = {"satellite": info["satellite"], "instrument": info["instrument"], "observation_utc": tb.strftime("%Y-%m-%dT%H:%M:%SZ"),
                   "minutes_until": p["minutes_until"], "day_pass": p["day_pass"],
                   "direct_broadcast": None, "next_station_contact": None}
            if live:
                w = max(live, key=lambda w: w["max_el"])
                rec["direct_broadcast"] = {"station": w["station"], "in_view_until_utc": w["los"].strftime("%Y-%m-%dT%H:%M:%SZ"),
                                           "note": "A station could receive this pass live (if configured for this satellite)."}
            if nxt:
                rec["next_station_contact"] = {"station": nxt["station"], "aos_utc": nxt["aos"].strftime("%Y-%m-%dT%H:%M:%SZ"),
                                               "minutes_after_observation": round((nxt["aos"] - tb).total_seconds() / 60, 1)}
            items.append(rec)
    items.sort(key=lambda r: r["observation_utc"])
    return {"time_utc": t0.strftime("%Y-%m-%dT%H:%M:%SZ"), "lat": lat, "lon": lon, "mask_deg": mask_deg, "passes": items[:12],
            "firms_nrt_note": FIRMS_NRT_NOTE, "limits": DB_LIMITS}


# ------------------------------------------------------------------ sun-satellite geometry
def _sun_ecef_unit(t: dt.datetime) -> np.ndarray:
    jd, fr = _dt_arrays([t])
    d = (jd[0] + fr[0]) - 2451545.0
    L = math.radians((280.460 + 0.9856474 * d) % 360)
    gm = math.radians((357.528 + 0.9856003 * d) % 360)
    lam = L + math.radians(1.915) * math.sin(gm) + math.radians(0.020) * math.sin(2 * gm)
    eps = math.radians(23.439 - 0.0000004 * d)
    x, y, z = math.cos(lam), math.cos(eps) * math.sin(lam), math.sin(eps) * math.sin(lam)
    g = float(ob._gmst(np.array([jd[0] + fr[0]]))[0])
    c, s = math.cos(g), math.sin(g)
    return np.array([c * x + s * y, -s * x + c * y, z])


def _tier(glint: float) -> str:
    return "high" if glint < 10 else ("elevated" if glint < 25 else "low")


def illumination(lat: float, lon: float, hours: float = 24.0) -> dict:
    t0 = _now()
    tles = ob.read_tles()
    obs, up = ob._observer_ecef(lat, lon)
    east = np.array([-math.sin(math.radians(lon)), math.cos(math.radians(lon)), 0.0])
    north = np.cross(up, east)
    pas = ob.passes(lat, lon, t0, hours, tles)
    out = []
    for info in pas:
        sn = next((s for s in ob.SENSORS if s.key == info["key"]), None)
        if sn is None or sn.norad not in tles:
            continue
        sat = Satrec.twoline2rv(*tles[sn.norad])
        for p in info.get("passes", []):
            tb = dt.datetime.fromisoformat(p["best"])
            jd, fr = _dt_arrays([tb])
            e = _sat_ecef(sat, jd, fr)[0]
            v = e - obs
            v /= np.linalg.norm(v)
            s = _sun_ecef_unit(tb)
            sza = math.degrees(math.acos(float(np.clip(s @ up, -1, 1))))
            vza = math.degrees(math.acos(float(np.clip(v @ up, -1, 1))))
            rec = {"satellite": info["satellite"], "instrument": info["instrument"], "time_utc": tb.strftime("%Y-%m-%dT%H:%M:%SZ"),
                   "solar_zenith_deg": round(sza, 1), "view_zenith_deg": round(vza, 1), "day": sza < 90}
            if sza < 90:
                refl = 2.0 * float(s @ up) * up - s                       # specular direction of the sun
                glint = math.degrees(math.acos(float(np.clip(refl @ v, -1, 1))))
                back = math.degrees(math.acos(float(np.clip(s @ v, -1, 1))))   # 0 = hot-spot (sun behind the satellite)
                scat = 180.0 - back
                az = lambda a: math.degrees(math.atan2(a @ east, a @ north)) % 360
                rel = abs((az(s - (s @ up) * up) - az(v - (v @ up) * up) + 180) % 360 - 180)
                rec.update({"relative_azimuth_deg": round(rel, 1), "backscatter_angle_deg": round(back, 1), "scattering_angle_deg": round(scat, 1),
                            "sun_glint_angle_deg": round(glint, 1), "glint_risk": _tier(glint),
                            "hotspot_geometry": back < 15.0})
            else:
                rec.update({"glint_risk": "none (night)", "hotspot_geometry": False})
            out.append(rec)
    out.sort(key=lambda r: r["time_utc"])
    return {"time_utc": t0.strftime("%Y-%m-%dT%H:%M:%SZ"), "lat": lat, "lon": lon, "passes": out[:12],
            "what_this_is": "Sun and satellite angles at the fire for each upcoming pass. A small sun-glint angle is a known source of daytime false alarms (the 3.9 um band contains reflected sunlight). Backscatter below ~15 degrees is the hot-spot geometry of land reflectance.",
            "what_this_is_not": "No radiometric BRDF correction is applied (MCD43 is not used) and no fixed percentage is claimed. Fire detection itself relies on thermal bands. Risk tiers use rule-of-thumb thresholds (<10 degrees high, <25 degrees elevated), not instrument-specific ones.",
            "references": ["Giglio et al. 2003/2016, MODIS active fire algorithm (sun-glint rejection)", "Schaaf et al. 2002, MCD43 BRDF/albedo", "Wooster et al. 2005"]}


# ------------------------------------------------------------------ router
router = APIRouter(tags=["directreadout"])


@router.get("/dbnet/live", summary="Satellites, Direct Broadcast station coverage circles and live line-of-sight links (geometry only)")
def dbnet_live(mask_deg: float = Query(5.0, ge=0, le=20)) -> dict:
    return live_links(mask_deg)


@router.get("/dbnet/race", summary="For a fire: which upcoming passes a Direct Broadcast station could receive live, and when the satellite next meets a station")
def dbnet_race(lat: float = Query(..., ge=-90, le=90), lon: float = Query(..., ge=-180, le=180),
               hours: float = Query(24, gt=1, le=48), mask_deg: float = Query(5.0, ge=0, le=20)) -> dict:
    try:
        return race(lat, lon, hours, mask_deg)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"orbit computation failed: {e}") from None


@router.get("/illumination", summary="Sun-satellite geometry (sun-glint / hot-spot risk) for the next passes over a point")
def illum(lat: float = Query(..., ge=-90, le=90), lon: float = Query(..., ge=-180, le=180),
          hours: float = Query(24, gt=1, le=48)) -> dict:
    try:
        return illumination(lat, lon, hours)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"orbit computation failed: {e}") from None
