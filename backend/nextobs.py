"""'What might happen before we can observe this fire again?'  Three keyless endpoints.

    GET /nextobs?lat=&lon=[&time=ISO][&radius_km=15]
        last detection + observation age, next MODIS / VIIRS overpasses of the point (SGP4 on aux/tle),
        gap to the next observation, scan angle / pixel size tier, Open-Meteo cloud cover at each pass time
        (a VISIBILITY INDICATOR, not proof that a sensor misses a fire) and `simulate_until`, the horizon in
        hours (now -> next usable pass) to feed /simulate (hours) and /predict (days).
    GET /blindspots?bbox=minlon,minlat,maxlon,maxlat[&time=ISO][&hours=24|48]
        grid classification: fire_detected | no_fire_detected_good_observation | insufficient_observation |
        recently_burned_not_seen.  Observation quality is computed on a coarse tile grid (0.25 deg, coarser for
        big boxes) from overpass geometry + tile-centre cloud cover; fire cells are 0.05 deg (FIRMS grid).
    GET /scenarios?lat=&lon=[&start=][&hours=24][&ignition=live|point][&scenarios=baseline,wind_x1.5,...]
        the physics simulator (backend.physics_spread, imported read-only) under HYPOTHETICAL weather edits
        (wind speed / direction, rain, humidity). Not the issued forecast.

Everything uses public data (FIRMS NRT feeds via backend.live, Open-Meteo, aux/tle). Nothing here is a
calibrated probability; thresholds are labelled heuristics.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import math
import re
import threading
import time as _time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd
from fastapi import APIRouter, HTTPException, Query
from sgp4.api import Satrec

from . import orbits as _orb

router = APIRouter(tags=["nextobs"])
UTC = dt.timezone.utc

# ------------------------------------------------------------------ constants / heuristics
LIVE_WINDOW_DAYS = 6.5            # FIRMS NRT 7-day feeds; older times use the daily archive
TLE_FRESH_DAYS = 7.0              # SGP4 along-track error grows with TLE age; beyond this passes are rough
EXCLUDED_SATS = {"snpp": "Suomi NPP: FIRMS NRT S-NPP feed is empty (instrument not delivering fire data)"}
SCAN_GOOD_DEG, SCAN_MAX_USABLE_DEG = 25.0, 45.0   # heuristic tiers (app choice, not a sensor spec)
CLOUD_CLEAR_PCT, CLOUD_OBSCURED_PCT = 30.0, 70.0  # heuristic visibility tiers on Open-Meteo total cloud cover
LOOKAHEAD_H = 48
NOMINAL_LST = {   # approximate nominal equator-crossing local solar times (hours); estimate, +-~1.5 h
    "terra": (10.5, 22.5), "aqua": (13.5, 1.5), "noaa20": (13.4, 1.4), "noaa21": (13.2, 1.2)}
OPEN_METEO = {"forecast": "https://api.open-meteo.com/v1/forecast",
              "archive": "https://archive-api.open-meteo.com/v1/archive"}
CLOUD_VARS = "cloud_cover,cloud_cover_low,cloud_cover_mid,cloud_cover_high"
VIS_NOTE = ("Cloud cover is an Open-Meteo model value at the pass time. It is a visibility indicator only: "
            "thin or partial cloud does not always hide a fire and clear sky does not guarantee detection.")


# ------------------------------------------------------------------ injectable data sources (tests patch these)
def _http_json(url: str, params: dict, timeout: float = 25.0):
    import httpx
    last = None
    for attempt in range(3):
        try:
            r = httpx.get(url, params=params, timeout=timeout, headers={"User-Agent": "equifire-demo"})
            if r.status_code == 429:
                _time.sleep(1.5 * (attempt + 1))
                continue
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa: BLE001
            last = e
            _time.sleep(0.5)
    raise RuntimeError(f"{type(last).__name__}: {str(last)[:120]}")


def _load_feeds():
    from . import live
    return live.load_feeds("7d")


def _get_state():
    try:
        from . import app as _app
        return _app.store.state
    except Exception:  # noqa: BLE001
        return None


def _tles() -> dict:
    return _orb.read_tles()


def _now() -> dt.datetime:
    return dt.datetime.now(UTC).replace(microsecond=0)


# ------------------------------------------------------------------ small helpers
def parse_time(s: str | None, default: dt.datetime | None = None) -> dt.datetime:
    if not s:
        return default or _now()
    try:
        t = dt.datetime.fromisoformat(s.strip().replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(400, f"bad time {s!r}: expected ISO 8601 UTC, e.g. 2026-11-14T22:00:00Z") from None
    return (t.replace(tzinfo=UTC) if t.tzinfo is None else t.astimezone(UTC)).replace(microsecond=0)


def iso(t) -> str:
    """Timestamp / datetime (naive = UTC) -> 'YYYY-MM-DDTHH:MM:SSZ'."""
    ts = pd.Timestamp(t)
    ts = ts.tz_localize(None) if ts.tzinfo is None else ts.tz_convert("UTC").tz_localize(None)
    return ts.floor("s").isoformat() + "Z"


def km_between(lat1, lon1, lat2, lon2):
    la = np.radians((np.asarray(lat1) + np.asarray(lat2)) / 2)
    dy = (np.asarray(lat2) - np.asarray(lat1)) * 111.195
    dx = (np.asarray(lon2) - np.asarray(lon1)) * 111.195 * np.cos(la)
    return np.hypot(dx, dy)


def cloud_tier(total: float | None) -> str:
    if total is None or not np.isfinite(total):
        return "unknown"
    return "clear" if total < CLOUD_CLEAR_PCT else ("partly" if total < CLOUD_OBSCURED_PCT else "obscured")


def scan_tier(scan_deg: float) -> str:
    return "good" if scan_deg <= SCAN_GOOD_DEG else ("moderate" if scan_deg <= SCAN_MAX_USABLE_DEG else "degraded")


# ------------------------------------------------------------------ Open-Meteo cloud cover (keyless)
_cloud_cache: dict = {}
_cloud_lock = threading.Lock()


def fetch_cloud(points: list[tuple[float, float]], t0: dt.datetime, t1: dt.datetime) -> list[dict | None]:
    """Hourly total/low/mid/high cloud cover (%) at each point for [t0, t1] (UTC). One request per <=50 points.

    Returns a list aligned with `points`: {"t": epoch seconds array, "total", "low", "mid", "high"} or None on
    failure (callers then report visibility 'unknown')."""
    now = _now()
    api = "archive" if t1 < now - dt.timedelta(days=6) else "forecast"
    sd, ed = t0.date().isoformat(), t1.date().isoformat()
    out: list[dict | None] = [None] * len(points)
    todo = []
    with _cloud_lock:
        for i, (la, lo) in enumerate(points):
            key = (api, round(la, 2), round(lo, 2), sd, ed)
            hit = _cloud_cache.get(key)
            if hit and _time.time() - hit[0] < 1800:
                out[i] = hit[1]
            else:
                todo.append(i)
    for k in range(0, len(todo), 50):
        chunk = todo[k:k + 50]
        params = {"latitude": ",".join(f"{points[i][0]:.3f}" for i in chunk),
                  "longitude": ",".join(f"{points[i][1]:.3f}" for i in chunk),
                  "hourly": CLOUD_VARS, "start_date": sd, "end_date": ed, "timezone": "GMT"}
        try:
            js = _http_json(OPEN_METEO[api], params)
        except Exception:  # noqa: BLE001
            continue
        js = js if isinstance(js, list) else [js]
        for i, j in zip(chunk, js):
            try:
                h = j["hourly"]
                tt = (pd.to_datetime(h["time"]).astype("datetime64[s]").astype(np.int64)).astype(float)

                def arr(name):
                    return np.array([np.nan if v is None else float(v) for v in h.get(name, [None] * len(tt))])
                rec = {"t": tt, "total": arr("cloud_cover"), "low": arr("cloud_cover_low"),
                       "mid": arr("cloud_cover_mid"), "high": arr("cloud_cover_high")}
            except Exception:  # noqa: BLE001
                continue
            out[i] = rec
            with _cloud_lock:
                _cloud_cache[(api, round(points[i][0], 2), round(points[i][1], 2), sd, ed)] = (_time.time(), rec)
                if len(_cloud_cache) > 600:
                    _cloud_cache.pop(next(iter(_cloud_cache)))
    return out


def cloud_at(rec: dict | None, t: dt.datetime) -> dict:
    if rec is None:
        return {"cover_pct": None, "low_pct": None, "mid_pct": None, "high_pct": None, "visibility": "unknown",
                "source": "Open-Meteo unavailable"}
    x = t.timestamp()

    def at(name):
        a = rec[name]
        m = np.isfinite(a)
        return float(np.interp(x, rec["t"][m], a[m])) if m.sum() else float("nan")
    tot = at("total")
    r = lambda v: None if not np.isfinite(v) else round(v)  # noqa: E731
    return {"cover_pct": r(tot), "low_pct": r(at("low")), "mid_pct": r(at("mid")), "high_pct": r(at("high")),
            "visibility": cloud_tier(tot), "source": "Open-Meteo hourly cloud_cover (model)"}


# ------------------------------------------------------------------ overpasses with quality + cloud
def _pass_record(sat_info: dict, p: dict, rec, t_ref: dt.datetime) -> dict:
    best = dt.datetime.fromisoformat(p["best"])
    best = best if best.tzinfo else best.replace(tzinfo=UTC)
    scan = float(p["min_scan_deg"])
    at, asc = p["pixel_km"]
    nadir = 0.375 if sat_info["instrument"] == "VIIRS" else 1.0
    key = sat_info["key"]
    cl = cloud_at(rec, best)
    excluded = EXCLUDED_SATS.get(key)
    geom_ok = scan <= SCAN_MAX_USABLE_DEG and excluded is None
    return {"satellite": sat_info["satellite"], "key": key, "instrument": sat_info["instrument"],
            "time_utc": best.isoformat().replace("+00:00", "Z"),
            "hours_from_ref": round((best - t_ref).total_seconds() / 3600, 2),
            "scan_angle_deg": round(scan, 1), "quality_tier": scan_tier(scan),
            "pixel_km": [at, asc], "pixel_area_vs_nadir": round(at * asc / nadir ** 2, 2),
            "max_elevation_deg": p["max_elevation_deg"], "day_pass": p["day_pass"],
            "cloud": cl, "geometry_ok": geom_ok, "excluded_reason": excluded,
            "usable": bool(geom_ok and cl["visibility"] != "obscured"),
            "in_progress": bool(p.get("in_progress"))}


def point_passes(lat: float, lon: float, start: dt.datetime, hours: float, tles: dict) -> tuple[list[dict], list[dict]]:
    """(flat pass list, per-satellite TLE info) over [start, start + hours]."""
    sats = _orb.passes(lat, lon, start, hours, tles)
    info = []
    for s in sats:
        info.append({"satellite": s["satellite"], "norad": s["norad"], "tle_epoch": s.get("tle_epoch"),
                     "tle_age_days": s.get("tle_age_days"), "error": s.get("error"),
                     "fresh": (s.get("tle_age_days") is not None and abs(s["tle_age_days"]) <= TLE_FRESH_DAYS)})
    return sats, info


def _dedupe_detections(d: pd.DataFrame) -> pd.DataFrame:
    return d.drop_duplicates(["lat", "lon", "t", "sat"]) if len(d) else d


# ------------------------------------------------------------------ /nextobs
def _nominal_passes(lon: float, t: dt.datetime, hours: float) -> list[dict]:
    """Archive dates: TLEs of today are useless for a past date, so only nominal local-time estimates."""
    out = []
    end = t + dt.timedelta(hours=hours)
    for key, times in NOMINAL_LST.items():
        sn = next(s for s in _orb.SENSORS if s.key == key)
        for lst in times:
            for day in range(-1, int(hours / 24) + 2):
                utc_h = (lst - lon / 15) % 24
                base = dt.datetime(t.year, t.month, t.day, tzinfo=UTC) + dt.timedelta(days=day, hours=utc_h)
                if t <= base <= end:
                    out.append({"satellite": sn.name, "key": key, "instrument": sn.instrument, "best": base, "estimate": True})
    return sorted(out, key=lambda p: p["best"])


def _simulate_until(passes: list[dict], t: dt.datetime) -> dict:
    cand = [p for p in passes if p["usable"] and p["hours_from_ref"] > 0]
    basis = "next pass with scan angle <= 45 deg, not S-NPP, and cloud cover below 70% (visibility not 'obscured')"
    if not cand:
        cand = [p for p in passes if p["geometry_ok"] and p["hours_from_ref"] > 0]
        basis = ("no pass in the next 48 h with usable cloud visibility: horizon = next pass with scan angle "
                 "<= 45 deg regardless of cloud")
    if not cand:
        return {"hours": None, "exact_hours": None, "until_utc": None, "basis": "no usable pass within 48 h",
                "ml_predict_days": None, "start_utc": iso(t.replace(tzinfo=None))}
    p = min(cand, key=lambda q: q["hours_from_ref"])
    h = max(p["hours_from_ref"], 0.0)
    return {"hours": max(1, min(168, math.ceil(h))), "exact_hours": round(h, 2), "until_utc": p["time_utc"],
            "satellite": p["satellite"], "start_utc": iso(t.replace(tzinfo=None)),
            "ml_predict_days": max(1, min(3, math.ceil(h / 24))),
            "basis": basis,
            "how_to_use": "hours -> /simulate?hours= (1..168) or /scenarios?hours=; ml_predict_days -> /predict?days="}


def _last_detection_live(lat, lon, t, radius_km, window_h=168):
    df, info = _load_feeds()
    t_naive = pd.Timestamp(t).tz_localize(None) if pd.Timestamp(t).tzinfo is None else pd.Timestamp(t).tz_convert("UTC").tz_localize(None)
    if len(df) == 0:
        return df, info, t_naive
    d = df[(df["t"] <= t_naive) & (df["t"] >= t_naive - pd.Timedelta(hours=window_h))]
    d = d[(d["lat"].between(lat - radius_km / 111.0, lat + radius_km / 111.0))]
    if len(d):
        d = d[km_between(lat, lon, d["lat"].to_numpy(), d["lon"].to_numpy()) <= radius_km]
    return _dedupe_detections(d), info, t_naive


@router.get("/nextobs")
def nextobs(lat: float = Query(..., ge=-90, le=90), lon: float = Query(..., ge=-180, le=180),
            time: str | None = Query(None, description="reference time, ISO 8601 UTC (default now)"),
            radius_km: float = Query(15.0, gt=0, le=60, description="detections within this distance = 'the fire'")) -> dict:
    now = _now()
    t = parse_time(time, now)
    notes = []
    if t > now + dt.timedelta(minutes=5):
        notes.append("time is in the future: detections are those known now; passes counted from the requested time")
    historical = t < now - dt.timedelta(days=LIVE_WINDOW_DAYS)
    t_naive = pd.Timestamp(t).tz_localize(None)
    fire: dict = {"found": False}
    feed_meta = None
    recent_det = None
    if not historical:
        try:
            d, info, _ = _last_detection_live(lat, lon, t, radius_km)
            feed_meta = [{k: i.get(k) for k in ("feed", "satellite", "latest", "stale", "error")} for i in info]
        except Exception as e:  # noqa: BLE001
            d, notes = None, notes + [f"FIRMS NRT feed unavailable: {type(e).__name__}"]
        if d is not None and len(d):
            last = d.loc[d["t"].idxmax()]
            lt = pd.Timestamp(last["t"])
            by = d.groupby("sat")["t"].max()
            fire = {"found": True, "mode": "live_nrt", "n_detections_7d": int(len(d)),
                    "n_detections_24h": int((d["t"] >= t_naive - pd.Timedelta(hours=24)).sum()),
                    "last_detection_utc": iso(lt), "observation_age_hours": round((t_naive - lt).total_seconds() / 3600, 2),
                    "last_detection_feed": str(last["sat"]),
                    "last_detection_instrument": "MODIS" if "MODIS" in str(last["sat"]).upper() else "VIIRS",
                    "last_by_feed": {str(k): iso(v) for k, v in by.items()},
                    "centroid": [round(float(d["lon"].mean()), 4), round(float(d["lat"].mean()), 4)],
                    "max_frp_mw": round(float(d["frp"].max()), 1), "radius_km": radius_km,
                    "note": "FIRMS NRT detections lag the overpass by roughly 3 h or less (US/Canada faster); "
                            "the last detection time is the satellite acquisition time"}
            recent_det = d
        else:
            fire = {"found": False, "radius_km": radius_km, "mode": "live_nrt",
                    "note": f"no FIRMS NRT detection within {radius_km:g} km in the last 7 days before this time"}
    else:
        st = _get_state()
        fire = {"found": False, "mode": "archive_daily", "radius_km": radius_km}
        if st is None:
            fire["note"] = "archive not loaded on this server (EQUIFIRE_LITE or still loading)"
        else:
            from equifire.common import cell_center
            from .data import day_of
            day = day_of(t.date().isoformat())
            for back in range(0, 4):
                try:
                    sl = st.viirs_slice(day - back)
                except Exception:  # noqa: BLE001
                    sl = None
                if sl is None or len(sl) == 0:
                    continue
                clat, clon = cell_center(sl["gx"].to_numpy(), sl["gy"].to_numpy())
                sel = km_between(lat, lon, clat, clon) <= radius_km
                if sel.any():
                    day_d = (t - dt.timedelta(days=back)).date()
                    fire = {"found": True, "mode": "archive_daily", "n_cells": int(sel.sum()),
                            "last_detection_date": day_d.isoformat(), "last_detection_utc": None,
                            "observation_age_hours": None,
                            "observation_age_days": back,
                            "note": "archive keeps one record per 0.05 deg cell and day (no acquisition time): "
                                    "age is given in whole days; passes below are nominal local-time estimates",
                            "radius_km": radius_km}
                    break
            if not fire["found"]:
                fire["note"] = f"no burning VIIRS cell within {radius_km:g} km in the 4 days up to this date"
    # ---- passes
    tles = _tles()
    passes_rec: list[dict] = []
    recent_rec: list[dict] = []
    tle_info: list[dict] = []
    cloud_error = None
    if not historical:
        t0p = t - dt.timedelta(hours=24)
        rec = fetch_cloud([(lat, lon)], t0p, t + dt.timedelta(hours=LOOKAHEAD_H + 1))[0]
        if rec is None:
            cloud_error = "Open-Meteo cloud cover unavailable: visibility = unknown (not assumed clear)"
        nxt, tle_info = point_passes(lat, lon, t, LOOKAHEAD_H, tles)
        prv, _ = point_passes(lat, lon, t0p, 24, tles)
        for s in nxt:
            for p in s["passes"]:
                passes_rec.append(_pass_record(s, p, rec, t))
        for s in prv:
            for p in s["passes"]:
                r = _pass_record(s, p, rec, t)
                if r["hours_from_ref"] >= 0:
                    continue
                r["fire_detected_in_pass"] = None
                r["detection_may_be_pending"] = r["hours_from_ref"] > -3.5
                if recent_det is not None and not r["excluded_reason"]:
                    bt = pd.Timestamp(r["time_utc"][:-1])
                    dd = recent_det[(recent_det["t"] - bt).abs() <= pd.Timedelta(minutes=20)]
                    want = "MODIS" if r["instrument"] == "MODIS" else {"noaa20": "N20", "noaa21": "N21"}.get(r["key"])
                    dd = dd[dd["sat"].astype(str).str.upper() == want.upper()] if want else dd.iloc[0:0]
                    r["fire_detected_in_pass"] = bool(len(dd))
                recent_rec.append(r)
        passes_rec.sort(key=lambda p: p["hours_from_ref"])
        recent_rec.sort(key=lambda p: -p["hours_from_ref"])
        stale = [i["satellite"] for i in tle_info if i.get("fresh") is False]
        if stale:
            notes.append(f"TLE older than {TLE_FRESH_DAYS:g} days for {', '.join(stale)}: pass times may be off by minutes or more")
    else:
        est = _nominal_passes(lon, t, LOOKAHEAD_H)
        rec = fetch_cloud([(lat, lon)], t, t + dt.timedelta(hours=LOOKAHEAD_H + 1))[0]
        for p in est:
            cl = cloud_at(rec, p["best"])
            passes_rec.append({"satellite": p["satellite"], "key": p["key"], "instrument": p["instrument"],
                               "time_utc": p["best"].isoformat().replace("+00:00", "Z"),
                               "hours_from_ref": round((p["best"] - t).total_seconds() / 3600, 2),
                               "scan_angle_deg": None, "quality_tier": "unknown", "pixel_km": None,
                               "pixel_area_vs_nadir": None, "day_pass": None, "cloud": cl,
                               "geometry_ok": True, "excluded_reason": None, "estimate": True,
                               "usable": cl["visibility"] != "obscured", "in_progress": False})
        notes.append("historical date: today's TLEs cannot propagate to a past date, so pass times are nominal "
                     "local-time ESTIMATES (+-~1.5 h) and scan angle/pixel size are not computed")
    inst = {}
    for k in ("VIIRS", "MODIS"):
        c = [p for p in passes_rec if p["instrument"] == k and p["hours_from_ref"] > 0 and not p.get("excluded_reason")]
        inst[k] = min(c, key=lambda p: p["hours_from_ref"]) if c else None
    nxt_any = min((p for p in passes_rec if p["hours_from_ref"] > 0 and not p.get("excluded_reason")),
                  key=lambda p: p["hours_from_ref"], default=None)
    nxt_use = min((p for p in passes_rec if p["usable"] and p["hours_from_ref"] > 0),
                  key=lambda p: p["hours_from_ref"], default=None)
    age = fire.get("observation_age_hours")

    def gap(p):
        return None if p is None else p["hours_from_ref"]
    out = {
        "query": {"lat": lat, "lon": lon, "time_utc": t.isoformat().replace("+00:00", "Z"),
                  "mode": "archive_daily" if historical else "live_nrt", "radius_km": radius_km},
        "fire": fire,
        "feeds": feed_meta,
        "tle": tle_info,
        "next_observation": nxt_any,
        "next_usable_observation": nxt_use,
        "next_by_instrument": {"VIIRS (NOAA-20/21)": inst["VIIRS"], "MODIS (Terra/Aqua)": inst["MODIS"]},
        "gap_hours": {"since_last_detection": age, "to_next_viirs": gap(inst["VIIRS"]),
                      "to_next_modis": gap(inst["MODIS"]), "to_next_observation": gap(nxt_any),
                      "to_next_usable_observation": gap(nxt_use),
                      "last_detection_to_next_usable": (round(age + nxt_use["hours_from_ref"], 2)
                                                        if age is not None and nxt_use else None)},
        "simulate_until": _simulate_until(passes_rec, t),
        "passes": passes_rec,
        "recent_passes": recent_rec,
        "quality_legend": {"good": f"scan angle <= {SCAN_GOOD_DEG:g} deg (pixels near nominal size)",
                           "moderate": f"{SCAN_GOOD_DEG:g}-{SCAN_MAX_USABLE_DEG:g} deg (pixels larger, small fires easier to miss)",
                           "degraded": f"> {SCAN_MAX_USABLE_DEG:g} deg swath edge (bow-tie, pixel several times nominal)",
                           "note": "tier cut-offs are heuristics of this app; pixel size is the geometric estimate"},
        "visibility_legend": {"clear": f"< {CLOUD_CLEAR_PCT:g}% cloud cover", "partly": f"{CLOUD_CLEAR_PCT:g}-{CLOUD_OBSCURED_PCT:g}%",
                              "obscured": f">= {CLOUD_OBSCURED_PCT:g}%", "unknown": "no cloud data",
                              "note": VIS_NOTE},
        "excluded_satellites": EXCLUDED_SATS,
        "notes": notes + ([cloud_error] if cloud_error else []),
        "generated_utc": now.isoformat().replace("+00:00", "Z"),
        "estimate": True,
    }
    return out


# ------------------------------------------------------------------ /blindspots
CELL_DEG = 0.05
LEGEND = {
    "fire_detected": "A satellite detected fire in this 0.05 deg cell during the window.",
    "no_fire_detected_good_observation": "No fire detected AND a clear-enough, usable overpass within the last "
                                         "`fresh_hours` (default 12 h). This is the closest the data gets to 'probably no active fire', "
                                         "not a guarantee (small or smouldering fires can be missed).",
    "insufficient_observation": "No fire detected, but the area was NOT well observed (no overpass, only swath-edge "
                                "overpasses, or heavy cloud at every overpass). 'No detection' here means unknown, "
                                "NOT 'no fire'.",
    "recently_burned_not_seen": "Fire was detected in this cell earlier (before the window) but not now, and the area "
                                "is poorly observed (cloud/swath gap): the fire may still be burning unseen."}


def _satellite_ecef(sat: Satrec, jd, fr):
    err, r, _ = sat.sgp4_array(jd, fr)
    g = _orb._gmst(jd + fr)
    c, s = np.cos(g), np.sin(g)
    ecef = np.c_[c * r[:, 0] + s * r[:, 1], -s * r[:, 0] + c * r[:, 1], r[:, 2]]
    ecef[err != 0] = np.nan
    return ecef


def tile_passes(centres: np.ndarray, t0: dt.datetime, hours: float, tles: dict, step_s: int = 60) -> list[list[dict]]:
    """Per tile centre (K, 2) -> list of passes {key, instrument, best(datetime), scan, elev} in [t0, t0+hours].
    Satellite positions are computed once and shared by all tiles."""
    n = int(hours * 3600 / step_s) + 1
    times = [t0 + dt.timedelta(seconds=step_s * i) for i in range(n)]
    jd, fr = _orb.julian(times)
    K = len(centres)
    obs = np.array([_orb._observer_ecef(la, lo)[0] for la, lo in centres])      # (K, 3)
    out: list[list[dict]] = [[] for _ in range(K)]
    for sn in _orb.SENSORS:
        if sn.norad not in tles:
            continue
        sat = Satrec.twoline2rv(*tles[sn.norad])
        se = _satellite_ecef(sat, jd, fr)                                       # (T, 3)
        rho = se[None, :, :] - obs[:, None, :]                                  # (K, T, 3)
        rng = np.linalg.norm(rho, axis=2)
        up = obs / np.linalg.norm(obs, axis=1)[:, None]
        elev = np.degrees(np.arcsin(np.clip(np.einsum("ktc,kc->kt", rho, up) / rng, -1, 1)))
        to_ctr = -se
        cosang = np.einsum("ktc,tc->kt", -rho, to_ctr) / (rng * np.linalg.norm(to_ctr, axis=1)[None, :])
        scan = np.degrees(np.arccos(np.clip(cosang, -1, 1)))
        inside = (np.nan_to_num(scan, nan=180) <= sn.max_scan_deg) & (np.nan_to_num(elev, nan=-90) > 0)
        for k in range(K):
            idx = np.flatnonzero(np.diff(np.r_[0, inside[k].astype(np.int8), 0]))
            for a, b in zip(idx[::2], idx[1::2]):
                i = a + int(np.nanargmin(scan[k, a:b]))
                out[k].append({"key": sn.key, "satellite": sn.name, "instrument": sn.instrument, "best": times[i],
                               "scan": float(scan[k, i]), "elev": float(elev[k, i])})
    return out


def _tile_step(box) -> float:
    """Smallest tile size (deg) that keeps the cloud request at <= 100 points."""
    for step in (0.25, 0.5):
        if math.ceil((box[2] - box[0]) / step + 1) * math.ceil((box[3] - box[1]) / step + 1) <= 100:
            return step
    return 1.0


@router.get("/blindspots")
def blindspots(bbox: str = Query(..., description="minlon,minlat,maxlon,maxlat (max 10 x 10 deg)"),
               time: str | None = Query(None, description="end of the window, ISO 8601 UTC (default now)"),
               hours: int = Query(24, ge=6, le=48, description="detection / pass window length, hours (24 or 48 typical)"),
               fresh_hours: int = Query(12, ge=3, le=48, description="a tile counts as well observed only if its last "
                                        "usable (geometry + cloud) pass was at most this many hours before `time`")) -> dict:
    try:
        box = [float(v) for v in bbox.split(",")]
        assert len(box) == 4 and box[0] < box[2] and box[1] < box[3]
        assert -180 <= box[0] and box[2] <= 180 and -90 <= box[1] and box[3] <= 90
    except (ValueError, AssertionError):
        raise HTTPException(400, "bbox must be minlon,minlat,maxlon,maxlat") from None
    if box[2] - box[0] > 10 or box[3] - box[1] > 10:
        raise HTTPException(400, "bbox larger than 10 x 10 deg")
    now = _now()
    t = parse_time(time, now)
    if t > now + dt.timedelta(minutes=5):
        raise HTTPException(400, "blind spots describe the past window; time must not be in the future")
    if t < now - dt.timedelta(days=LIVE_WINDOW_DAYS - 3):
        raise HTTPException(400, "blind spots are computed from the live FIRMS NRT feeds (last ~7 days); "
                                 "choose a time within the last 3 days")
    t0 = t - dt.timedelta(hours=hours)
    tn, t0n = pd.Timestamp(t).tz_localize(None), pd.Timestamp(t0).tz_localize(None)
    notes: list[str] = []
    # ---- detections
    try:
        df, info = _load_feeds()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(503, f"FIRMS NRT feed unavailable: {type(e).__name__}") from None
    pad = 0.0
    if len(df):
        df = df[(df["t"] <= tn) & (df["t"] >= tn - pd.Timedelta(hours=72 + hours)) &
                df["lon"].between(box[0] - pad, box[2] + pad) & df["lat"].between(box[1] - pad, box[3] + pad)]
        df = _dedupe_detections(df)
    # ---- tiles
    step = _tile_step(box)
    tx0, tx1 = math.floor(box[0] / step), math.ceil(box[2] / step)
    ty0, ty1 = math.floor(box[1] / step), math.ceil(box[3] / step)
    ntx, nty = tx1 - tx0, ty1 - ty0
    centres = np.array([[(ty0 + j + 0.5) * step, (tx0 + i + 0.5) * step] for j in range(nty) for i in range(ntx)])
    centres[:, 0] = np.clip(centres[:, 0], -89.9, 89.9)
    centres[:, 1] = np.clip(centres[:, 1], -179.9, 179.9)
    tles = _tles()
    tp = tile_passes(centres, t0, hours, tles)
    clouds = fetch_cloud([(float(a), float(b)) for a, b in centres], t0 - dt.timedelta(hours=1), t + dt.timedelta(hours=1))
    n_cloud_missing = sum(c is None for c in clouds)
    if n_cloud_missing:
        notes.append(f"cloud data missing for {n_cloud_missing} of {len(clouds)} tiles: those tiles are treated as "
                     "insufficient (visibility unknown), not as clear")
    tiles = []
    for k, (c, plist) in enumerate(zip(centres, tp)):
        good, any_geom, details = [], 0, []
        for p in plist:
            if p["key"] in EXCLUDED_SATS or p["scan"] > SCAN_MAX_USABLE_DEG:
                continue
            any_geom += 1
            cl = cloud_at(clouds[k], p["best"])
            ok = cl["visibility"] in ("clear", "partly")
            details.append({"satellite": p["satellite"], "time_utc": p["best"].isoformat().replace("+00:00", "Z"),
                            "scan_angle_deg": round(p["scan"], 1), "cloud_pct": cl["cover_pct"],
                            "visibility": cl["visibility"]})
            if ok:
                good.append(p)
        lastp = max(good, key=lambda p: p["best"]) if good else None
        if lastp is not None and (t - lastp["best"]).total_seconds() / 3600 <= fresh_hours:
            cls, why = "good_observation", []
        elif lastp is not None:
            cls = "insufficient_observation"
            why = [f"last usable overpass was {(t - lastp['best']).total_seconds() / 3600:.1f} h ago (> {fresh_hours} h); "
                   f"{any_geom - len(good)} later usable-geometry pass(es) had cloud cover >= {CLOUD_OBSCURED_PCT:g}%"
                   if any_geom > len(good) else
                   f"last usable overpass was {(t - lastp['best']).total_seconds() / 3600:.1f} h ago (> {fresh_hours} h) "
                   "and no later pass reached the area"]
        else:
            cls, lastp = "insufficient_observation", None
            if not plist:
                why = ["no overpass of any fire sensor during the window"]
            elif not any_geom:
                why = [f"only swath-edge overpasses (scan angle > {SCAN_MAX_USABLE_DEG:g} deg) or an excluded sensor"]
            elif clouds[k] is None:
                why = ["cloud data unavailable for the overpass times"]
            else:
                why = [f"cloud cover >= {CLOUD_OBSCURED_PCT:g}% at all {any_geom} usable-geometry overpass(es)"]
        tiles.append({"class": cls, "reasons": why, "n_good_passes": len(good), "n_geometry_ok_passes": any_geom,
                      "n_passes_total": len(plist), "passes": details[:8],
                      "hours_since_last_good_pass": None if lastp is None else round((t - lastp["best"]).total_seconds() / 3600, 1)})
    # ---- fire cells (0.05 deg)
    cx = lambda lon: np.floor((np.asarray(lon) + 180) / CELL_DEG).astype(np.int64)  # noqa: E731
    cy = lambda lat: np.floor((np.asarray(lat) + 90) / CELL_DEG).astype(np.int64)  # noqa: E731
    x0, x1 = int(math.floor((box[0] + 180) / CELL_DEG + 1e-9)), int(math.ceil((box[2] + 180) / CELL_DEG - 1e-9))
    y0, y1 = int(math.floor((box[1] + 90) / CELL_DEG + 1e-9)), int(math.ceil((box[3] + 90) / CELL_DEG - 1e-9))
    nx, ny = x1 - x0, y1 - y0
    now_cells, earlier_cells = {}, {}
    if len(df):
        gx, gy = cx(df["lon"]), cy(df["lat"])
        in_win = (df["t"] > t0n).to_numpy()
        for mask, store in ((in_win, now_cells), (~in_win, earlier_cells)):
            if mask.any():
                sub = pd.DataFrame({"gx": gx[mask], "gy": gy[mask], "frp": df["frp"].to_numpy()[mask],
                                    "t": df["t"].to_numpy()[mask]})
                for (a, b), g in sub.groupby(["gx", "gy"]):
                    store[(int(a), int(b))] = (int(len(g)), float(g["frp"].sum()), pd.Timestamp(g["t"].max()))
    # tile index of each 0.05 cell
    ci = (np.arange(x0, x1) * CELL_DEG - 180 + CELL_DEG / 2)
    cj = (np.arange(y0, y1) * CELL_DEG - 90 + CELL_DEG / 2)
    ti = np.clip(np.floor(ci / step).astype(int) - tx0, 0, ntx - 1)
    tj = np.clip(np.floor(cj / step).astype(int) - ty0, 0, nty - 1)
    tile_good = np.array([tl["class"] == "good_observation" for tl in tiles]).reshape(nty, ntx)
    good_grid = tile_good[np.ix_(tj, ti)]                    # (ny, nx)
    fire_grid = np.zeros((ny, nx), bool)
    for (a, b) in now_cells:
        if x0 <= a < x1 and y0 <= b < y1:
            fire_grid[b - y0, a - x0] = True
    prev_grid = np.zeros((ny, nx), bool)
    for (a, b) in earlier_cells:
        if x0 <= a < x1 and y0 <= b < y1 and not fire_grid[b - y0, a - x0]:
            prev_grid[b - y0, a - x0] = True
    counts = {"fire_detected": int(fire_grid.sum()),
              "recently_burned_not_seen": int((prev_grid & ~good_grid).sum()),
              "no_fire_detected_good_observation": int((~fire_grid & good_grid).sum()),
              "insufficient_observation": int((~fire_grid & ~good_grid & ~prev_grid).sum())}
    counts["previously_detected_now_quiet_good_observation"] = int((prev_grid & good_grid).sum())
    counts["total_cells"] = int(nx * ny)
    # ---- GeoJSON
    feats = []
    for k, tl in enumerate(tiles):
        j, i = divmod(k, ntx)
        w, s = max((tx0 + i) * step, box[0]), max((ty0 + j) * step, box[1])
        e, n_ = min((tx0 + i + 1) * step, box[2]), min((ty0 + j + 1) * step, box[3])
        feats.append({"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[[w, s], [e, s], [e, n_], [w, n_], [w, s]]]},
                      "properties": {"layer": "observation", "class": "good_observation" if tl["class"] == "good_observation"
                                     else "insufficient_observation", "tile_deg": step,
                                     "centre": [round(float(centres[k][1]), 4), round(float(centres[k][0]), 4)], **{kk: tl[kk] for kk in
                                         ("reasons", "n_good_passes", "n_passes_total", "hours_since_last_good_pass", "passes")}}})

    def cell_poly(a, b):
        w, s = a * CELL_DEG - 180, b * CELL_DEG - 90
        return {"type": "Polygon", "coordinates": [[[round(w, 4), round(s, 4)], [round(w + CELL_DEG, 4), round(s, 4)],
                                                   [round(w + CELL_DEG, 4), round(s + CELL_DEG, 4)],
                                                   [round(w, 4), round(s + CELL_DEG, 4)], [round(w, 4), round(s, 4)]]]}
    MAXC = 6000
    nfire = 0
    for (a, b), (n, frp, lt) in now_cells.items():
        if x0 <= a < x1 and y0 <= b < y1 and nfire < MAXC:
            nfire += 1
            feats.append({"type": "Feature", "geometry": cell_poly(a, b), "properties": {
                "layer": "fire", "class": "fire_detected", "n_detections": n, "frp_mw": round(frp, 1),
                "last_detection_utc": iso(lt), "age_hours": round((tn - lt).total_seconds() / 3600, 1)}})
    for (a, b), (n, frp, lt) in earlier_cells.items():
        if x0 <= a < x1 and y0 <= b < y1 and not fire_grid[b - y0, a - x0] and nfire < MAXC:
            ok = bool(good_grid[b - y0, a - x0])
            nfire += 1
            feats.append({"type": "Feature", "geometry": cell_poly(a, b), "properties": {
                "layer": "fire", "class": "no_fire_detected_good_observation" if ok else "recently_burned_not_seen",
                "previously_detected": True, "last_detection_utc": iso(lt),
                "age_hours": round((tn - lt).total_seconds() / 3600, 1), "n_detections_earlier": n}})
    if nfire >= MAXC:
        notes.append(f"fire-cell polygons capped at {MAXC}; counts are complete")
    notes += ["Observation quality is evaluated per coarse tile centre (overpass geometry and Open-Meteo cloud cover "
              "at that point); all 0.05 deg cells inside a tile share its class.",
              "Cloud is a model value; smoke, thin cirrus, fire size and pixel geometry are not modelled. "
              "'Good observation' is not proof that no fire exists.",
              "S-NPP is excluded (its FIRMS NRT feed is empty). Swath-edge passes (> 45 deg) are not counted as good."]
    stale = [i["satellite"] for i in (_orb.passes(0, 0, t0, 0.01, tles)) if i.get("tle_age_days") is not None
             and abs(i["tle_age_days"]) > TLE_FRESH_DAYS]
    if stale:
        notes.append(f"TLE older than {TLE_FRESH_DAYS:g} days for {', '.join(stale)}: pass times less reliable")
    return {"type": "FeatureCollection", "features": feats,
            "counts": counts, "legend": LEGEND,
            "query": {"bbox": box, "time_utc": t.isoformat().replace("+00:00", "Z"), "window_hours": hours,
                      "window_start_utc": t0.isoformat().replace("+00:00", "Z"), "fresh_hours": fresh_hours, "tile_deg": step,
                      "n_tiles": len(tiles), "cell_deg": CELL_DEG, "history_hours": 72},
            "style_hint": {"fire_detected": "red", "recently_burned_not_seen": "orange hatched",
                           "no_fire_detected_good_observation": "none / light green",
                           "insufficient_observation": "grey hatched (NOT green)"},
            "feeds": [{k: i.get(k) for k in ("feed", "latest", "stale", "error")} for i in info],
            "notes": notes, "estimate": True}


# ------------------------------------------------------------------ /scenarios
SCENARIO_RE = [
    (re.compile(r"^baseline$"), "baseline"),
    (re.compile(r"^wind_x(\d+(?:\.\d+)?)$"), "wind_scale"),
    (re.compile(r"^wind_rot([+-]\d+(?:\.\d+)?)$"), "wind_rot"),
    (re.compile(r"^rain_(\d+(?:\.\d+)?)mm$"), "rain"),
    (re.compile(r"^(?:dry|wet)_rh([+-]?\d+(?:\.\d+)?)$"), "rh"),
]
DEFAULT_SCENARIOS = "baseline,wind_x1.5,wind_x0.5,wind_rot+45,wind_rot-45,rain_6mm,dry_rh-15"
HYPO = "hypothetical weather — not the issued forecast"
RAIN_HOURS = 6
COMPASS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]


def parse_scenario(name: str) -> dict:
    n = name.strip().replace(" ", "+")      # '+' arrives as a space in query strings
    for rx, kind in SCENARIO_RE:
        m = rx.match(n)
        if m:
            v = float(m.group(1)) if m.groups() else None
            if kind == "wind_scale" and not 0 <= v <= 5:
                break
            if kind == "wind_rot" and abs(v) > 180:
                break
            if kind == "rain" and v > 200:
                break
            if kind == "rh" and abs(v) > 60:
                break
            if kind == "rh" and n.startswith("dry_rh") and v > 0:
                v = -v
            return {"name": n, "kind": kind, "value": v}
    raise HTTPException(400, f"unknown scenario {name!r}: use baseline, wind_x<factor 0-5>, wind_rot<+-deg>, "
                             "rain_<mm>mm, dry_rh-<points> or wet_rh+<points>")


def apply_scenario(wg, sc: dict, t0: pd.Timestamp):
    """Edited copy of a WeatherGrid (equifire.fireweather) from the simulation start onward. Spin-up (past) hours
    are untouched so initial fuel moisture is the real one."""
    arrays = {k: np.array(v, copy=True) for k, v in wg.arrays.items()}
    sim = np.asarray(wg.times).astype("datetime64[s]") >= np.datetime64(t0.to_datetime64(), "s")
    kind, v = sc["kind"], sc["value"]
    if kind == "wind_scale":
        for k in ("wind_speed_10m", "wind_gusts_10m"):
            if k in arrays:
                arrays[k][sim] = arrays[k][sim] * v
    elif kind == "wind_rot":
        arrays["wind_direction_10m"][sim] = (arrays["wind_direction_10m"][sim] + v) % 360
    elif kind == "rh":
        arrays["relative_humidity_2m"][sim] = np.clip(arrays["relative_humidity_2m"][sim] + v, 3, 100)
    elif kind == "rain":
        idx = np.flatnonzero(sim)[:RAIN_HOURS]
        arrays["precipitation"][idx] = np.nan_to_num(arrays["precipitation"][idx]) + v / max(len(idx), 1)
        arrays["relative_humidity_2m"][idx] = np.maximum(arrays["relative_humidity_2m"][idx], 85)
    return dataclasses.replace(wg, arrays=arrays)


def apply_scenario_frame(wx: pd.DataFrame, sc: dict, t0: pd.Timestamp) -> pd.DataFrame:
    """Same edits for the single-point fallback weather frame (columns temp_c, rh, wind_kmh, wind_dir, gust_kmh, rain_mm)."""
    w = wx.copy()
    sim = w.index >= t0
    kind, v = sc["kind"], sc["value"]
    if kind == "wind_scale":
        w.loc[sim, ["wind_kmh", "gust_kmh"]] = w.loc[sim, ["wind_kmh", "gust_kmh"]] * v
    elif kind == "wind_rot":
        w.loc[sim, "wind_dir"] = (w.loc[sim, "wind_dir"] + v) % 360
    elif kind == "rh":
        w.loc[sim, "rh"] = (w.loc[sim, "rh"] + v).clip(3, 100)
    elif kind == "rain":
        ix = w.index[sim][:RAIN_HOURS]
        w.loc[ix, "rain_mm"] = w.loc[ix, "rain_mm"].fillna(0) + v / max(len(ix), 1)
        w.loc[ix, "rh"] = w.loc[ix, "rh"].clip(lower=85)
    return w


def describe_weather_change(sc: dict) -> str:
    k, v = sc["kind"], sc["value"]
    if k == "baseline":
        return "Weather as forecast by Open-Meteo (the same weather /simulate uses)."
    if k == "wind_scale":
        return f"Wind speed and gusts multiplied by {v:g} for every simulated hour (direction unchanged)."
    if k == "wind_rot":
        return f"Wind direction turned {v:+g} deg (clockwise positive) in every simulated hour (speed unchanged)."
    if k == "rain":
        return (f"{v:g} mm of rain added evenly over the first {RAIN_HOURS} simulated hours; humidity raised to at least "
                "85% in those hours; fuel moisture then recovers by the model's drying rules.")
    return f"Relative humidity {v:+g} percentage points in every simulated hour."


def _extent(final, initial, grid) -> dict:
    """Spread beyond the initial fire footprint. For every newly burned cell: distance and bearing from the nearest
    initially burning cell. Returns the farthest reach per compass sector and the mean displacement vector (km)."""
    from scipy import ndimage
    new = final & ~initial
    if not new.any() or not initial.any():
        return {"sector_km": [0.0] * 8, "mean_disp_km": [0.0, 0.0], "new_cells": 0}
    ck = grid.cell / 1000.0
    _, (iy, ix) = ndimage.distance_transform_edt(~initial, return_indices=True)
    ii, jj = np.nonzero(new)
    dy, dx = (ii - iy[ii, jj]) * ck, (jj - ix[ii, jj]) * ck      # north, east
    d = np.hypot(dx, dy)
    sec = np.round(((np.degrees(np.arctan2(dx, dy)) + 360) % 360) / 45).astype(int) % 8
    ext = [round(float(d[sec == k].max() + ck / 2), 2) if (sec == k).any() else 0.0 for k in range(8)]
    return {"sector_km": ext, "mean_disp_km": [float(dx.mean()), float(dy.mean())], "new_cells": int(new.sum())}


def _bearing(dx, dy):
    return (math.degrees(math.atan2(dx, dy)) + 360) % 360


def _wx_summary(timeline: list[dict]) -> dict:
    if not timeline:
        return {}
    ws = np.array([r["wind_kmh"] for r in timeline], float)
    wd = np.radians([r["wind_dir_deg"] for r in timeline])
    # vector-mean wind direction (direction the wind blows FROM)
    mdir = (math.degrees(math.atan2(np.mean(ws * np.sin(wd)), np.mean(ws * np.cos(wd)))) + 360) % 360
    return {"mean_wind_kmh": round(float(ws.mean()), 1), "max_wind_kmh": round(float(ws.max()), 1),
            "mean_wind_from_deg": round(mdir), "wind_blows_toward": COMPASS[int(round(((mdir + 180) % 360) / 45)) % 8],
            "mean_rh_pct": round(float(np.mean([r["rh"] for r in timeline]))),
            "total_rain_mm": round(float(np.sum([r["rain_mm"] for r in timeline])), 1)}


def _run_one(sc, ctx, perimeters: bool, min_cell_m: float = 250.0) -> dict:
    from . import physics_spread as P
    t_run = _time.time()
    kw = {}
    if ctx["wg"] is not None:
        kw["wx_grid"] = apply_scenario(ctx["wg"], sc, ctx["t0"]) if sc["kind"] != "baseline" else ctx["wg"]
    else:
        kw["wx"] = apply_scenario_frame(ctx["wx"], sc, ctx["t0"]) if sc["kind"] != "baseline" else ctx["wx"]
        kw["wx_info"] = ctx["wx_info"]
    out = P.simulate(ctx["lat"], ctx["lon"], ctx["t0"], ctx["hours"], 60, ignition=ctx["ign"], fuel=ctx["fuel"],
                     envelope=False, frames=False, keep_masks=True, min_cell_m=min_cell_m, ros_mult=ctx["m"], landscape=ctx["lp"],
                     calibration=ctx["cal"], **kw)
    burned, grid = out["frames"][-1]["_mask"]
    ext = _extent(burned, out["frames"][0]["_mask"][0], grid)
    series = [{"t_hours": f["t_hours"], "burned_km2": f["burned_km2"]} for f in out["frames"]]
    for f in out["frames"]:
        f.pop("_mask", None)
    s = out["summary"]
    return {"name": sc["name"], "kind": sc["kind"], "value": sc["value"], "weather_change": describe_weather_change(sc),
            "burned_km2": s["burned_km2"], "initial_km2": s["initial_km2"], "max_ros_m_min": s["max_ros_m_min"],
            "max_run_km": s["max_run_km"], "spread_hours": s["spread_hours"], "stalled_hours": s["stalled_hours"],
            "reached_domain_edge": s["reached_domain_edge"], "timeline": series,
            "cell_m": out["grid"]["cell_m"], "blocked_cells": (out.get("landscape") or {}).get("blocked_cells"), "growth_km2": round(s["burned_km2"] - s["initial_km2"], 3), "ignition_cells": out["ignition"]["cells"],
            "sector_reach_km": dict(zip(COMPASS, ext["sector_km"])), "mean_displacement_km": ext["mean_disp_km"],
            "weather": _wx_summary(out["weather_timeline"]),
            "perimeter": s["final_perimeter"] if perimeters else None,
            "runtime_s": round(_time.time() - t_run, 2)}


def compare(base: dict, r: dict) -> dict:
    extra = round(r["growth_km2"] - base["growth_km2"], 2)
    pct = None if base["growth_km2"] <= 0.05 else round(100 * extra / base["growth_km2"], 1)
    bx, by = base["mean_displacement_km"]
    rx, ry = r["mean_displacement_km"]
    shift = math.hypot(rx - bx, ry - by)
    dsec = {c: round(r["sector_reach_km"][c] - base["sector_reach_km"][c], 2) for c in COMPASS}
    big = max(COMPASS, key=lambda c: abs(dsec[c]))
    return {"extra_km2": extra, "extra_pct": pct, "total_extra_km2": round(r["burned_km2"] - base["burned_km2"], 2),
            "drift_shift_km": round(shift, 2),
            "drift_shift_toward": COMPASS[int(round(_bearing(rx - bx, ry - by) / 45)) % 8] if shift >= 0.2 else None,
            "sector_reach_change_km": dsec, "largest_sector_change": {"sector": big, "km": dsec[big]},
            "max_run_change_km": round(r["max_run_km"] - base["max_run_km"], 2),
            "ignition_cell_diff": r["ignition_cells"] - base["ignition_cells"]}


def explain(r: dict, d: dict | None) -> str:
    if d is None:
        return (f"Baseline: {r['burned_km2']:.1f} km² burned by the end (growth {r['growth_km2']:.1f} km²), fastest head about "
                f"{r['max_ros_m_min']:.1f} m/min, forecast wind averaging {r['weather'].get('mean_wind_kmh', '?')} km/h "
                f"from {r['weather'].get('mean_wind_from_deg', '?')}°.")
    ch = r["weather_change"].split(" for every")[0].rstrip(".")
    ab = abs(d["extra_km2"])
    if ab < 0.05 and abs(d["largest_sector_change"]["km"]) < 0.3 and d["drift_shift_km"] < 0.3:
        eff = "makes almost no difference to the burned area or shape"
    else:
        eff = (f"{'adds' if d['extra_km2'] >= 0 else 'removes'} {ab:.1f} km² of spread "
               f"({d['extra_pct']:+.0f}% vs baseline growth)" if d["extra_pct"] is not None else f"changes spread area by {d['extra_km2']:+.1f} km²")
        ls = d["largest_sector_change"]
        if abs(ls["km"]) >= 0.3:
            eff += f"; largest reach change toward {ls['sector']}: front {'further' if ls['km'] > 0 else 'shorter'} by {abs(ls['km']):.1f} km"
        if d["drift_shift_toward"]:
            eff += f"; net spread drifts {d['drift_shift_km']:.1f} km toward {d['drift_shift_toward']}"
        if d["ignition_cell_diff"]:
            eff += f" (note: the model domain held {abs(d['ignition_cell_diff'])} {'more' if d['ignition_cell_diff'] > 0 else 'fewer'} ignition cell(s) than baseline)"
    return f"{ch}: {eff}."


_ctx_cache: dict = {}
_ctx_lock = threading.Lock()


def _build_context(lat, lon, t0, hours, ignition, radius_km, fuel, landscape, calibration):
    from . import physics_spread as P
    state = _get_state()
    ign = P.resolve_ignition(ignition, lat, lon, t0, state=state, radius_km=radius_km)
    clat, clon = float(np.mean(ign["lats"])), float(np.mean(ign["lons"]))
    fuel_key, _ = P.choose_fuel(clat, fuel)
    lp = P.LandscapeProvider(clat, clon, min(80.0, radius_km + 20), date=t0) if landscape == "on" else None
    notes = []
    with ThreadPoolExecutor(max_workers=2) as ex:
        f_land = ex.submit(lp.get) if lp is not None else None
        f_wx = ex.submit(P.fetch_weather_grid, clat, clon, t0, hours)
        wg, wg_info = f_wx.result()
        if f_land is not None:
            f_land.result()
    wx = wx_info = None
    if wg is None:
        wx, wx_info = P.fetch_weather(clat, clon, t0 - pd.Timedelta(hours=P.SPINUP_H), hours)
        notes.append("weather grid unavailable: single-point weather used for all scenarios")
    if lp is not None and lp.error:
        notes.append(f"landscape layers unavailable ({lp.error[:80]}): fuel/terrain fallbacks used")
    m, cal = 1.0, {"applied": False, "m": 1.0, "requested": calibration, "estimate": True, "reason": "calibration=off or point ignition"}
    if calibration == "on" and ign["mode"] != "point":
        try:
            fc = P.forecast(lat, lon, t0, 1, 60, ignition=ignition, fuel=fuel, envelope=False, frames=False,
                            radius_km=radius_km, calibration="on", state=state, weather="grid", goes="off", fwi=False,
                            landscape=landscape)
            m = float(fc["ros_multiplier"])
            cal = fc["calibration"]
        except Exception as e:  # noqa: BLE001
            notes.append(f"calibration failed ({type(e).__name__}): pure physics m = 1 used")
    return {"lat": lat, "lon": lon, "t0": t0, "hours": hours, "ign": ign, "fuel": fuel, "lp": lp, "wg": wg,
            "wx": wx, "wx_info": wx_info, "m": m, "cal": cal, "notes": notes,
            "ignition_note": {"requested": ignition, "mode": ign["mode"], "fallback_reason": ign.get("fallback_reason"),
                              "n_detections": ign.get("n_detections"), "cells": int(len(ign["lats"]))},
            "fuel_class": fuel_key}


@router.get("/scenarios")
def scenarios(lat: float = Query(..., ge=-90, le=90), lon: float = Query(..., ge=-180, le=180),
              start: str | None = Query(None, description="YYYY-MM-DDTHH:MM UTC, default now"),
              hours: int = Query(24, ge=1, le=72, description="simulated hours (see /nextobs simulate_until.hours)"),
              until_next_observation: bool = Query(False, description="override hours with /nextobs simulate_until.hours"),
              ignition: str = Query("live", description="live | point"),
              scenarios: str = Query(DEFAULT_SCENARIOS, description="comma list: baseline, wind_x<f>, wind_rot<+-deg>, "
                                     "rain_<mm>mm, dry_rh-<pts>, wet_rh+<pts> (max 8)"),
              radius_km: float = Query(30.0, ge=0.5, le=60.0),
              fuel: str = Query("auto"), landscape: str = Query("on", pattern="^(on|off)$"),
              calibration: str = Query("on", pattern="^(on|off)$"),
              perimeters: bool = Query(True, description="include the final perimeter GeoJSON of each scenario")) -> dict:
    from . import physics_spread as P
    if ignition not in ("live", "point"):
        raise HTTPException(400, "ignition must be live or point")
    if fuel not in ("auto", *P.FUEL_MODELS):
        raise HTTPException(400, "fuel must be auto, grass, savanna, shrub or forest")
    try:
        t0 = P._parse_start(start)
    except Exception:  # noqa: BLE001
        raise HTTPException(400, f"bad start {start!r}: expected YYYY-MM-DDTHH:MM (UTC)") from None
    scs = [parse_scenario(s) for s in scenarios.split(",") if s.strip()]
    if len(scs) > 8:
        raise HTTPException(400, "at most 8 scenarios")
    if not any(s["kind"] == "baseline" for s in scs):
        scs.insert(0, {"name": "baseline", "kind": "baseline", "value": None})
    scs.sort(key=lambda s: s["kind"] != "baseline")
    seen, uniq = set(), []
    for s in scs:
        if s["name"] not in seen:
            seen.add(s["name"])
            uniq.append(s)
    scs = uniq
    horizon = None
    if until_next_observation:
        horizon = nextobs(lat, lon, None, min(radius_km, 60.0))["simulate_until"]
        if not horizon.get("hours"):
            raise HTTPException(404, "no usable observation within 48 h: pass an explicit hours")
        hours = min(72, int(horizon["hours"]))
    key = (round(lat, 4), round(lon, 4), t0.isoformat(), hours, ignition, round(radius_km, 2), fuel, landscape, calibration)
    t_ctx = _time.time()
    with _ctx_lock:
        hit = _ctx_cache.get(key)
        if not (hit and _time.time() - hit[0] < 600):
            ctx = _build_context(lat, lon, t0, hours, ignition, radius_km, fuel, landscape, calibration)
            if len(_ctx_cache) >= 6:
                _ctx_cache.pop(next(iter(_ctx_cache)))
            _ctx_cache[key] = (_time.time(), ctx)
        else:
            ctx = hit[1]
    t_ctx = _time.time() - t_ctx
    t_all = _time.time()
    with ThreadPoolExecutor(max_workers=min(4, len(scs))) as ex:
        runs = list(ex.map(lambda s: _run_one(s, ctx, perimeters), scs))
        # The simulator sizes its raster from the weather (stronger wind -> bigger domain -> coarser cells). Areas on
        # different cell sizes are not comparable, so re-run the finer ones on the coarsest cell size.
        mx = max(r["cell_m"] for r in runs)
        redo = [i for i, r in enumerate(runs) if r["cell_m"] < mx - 0.5]
        if redo:
            again = list(ex.map(lambda i: _run_one(scs[i], ctx, perimeters, mx), redo))
            for i, r in zip(redo, again):
                runs[i] = r
    base = runs[0]
    no_spread = base["growth_km2"] < 0.05
    for i, r in enumerate(runs):
        r["vs_baseline"] = None if i == 0 else compare(base, r)
        r["explanation"] = explain(r, r["vs_baseline"])
        if no_spread and r["growth_km2"] < 0.05 and i > 0:
            r["explanation"] = (f"{describe_weather_change(r).rstrip('.')}: still no spread beyond the initial footprint, "
                                "so this weather change cannot be compared here (see warnings).")
    if no_spread:
        ctx["notes"] = ctx["notes"] + [
            "WARNING: the baseline simulation shows no spread beyond the initial footprint "
            f"(blocked cells in the domain: {base.get('blocked_cells')}). Likely the land around the detections is mapped "
            "non-burnable (e.g. cropland, water, built-up) or fuels are too wet; weather scenarios then show no difference. "
            "This is a model/landscape result, not evidence that the fire cannot spread."]
    return {
        "label": HYPO.capitalize(),
        "disclaimer": ("Every scenario except 'baseline' edits the forecast weather by a fixed amount to show "
                       "sensitivity. These are NOT forecasts, NOT probabilities and NOT confidence intervals."),
        "query": {"lat": lat, "lon": lon, "start_utc": t0.isoformat() + "Z", "hours": hours, "ignition": ignition,
                  "radius_km": radius_km, "fuel": ctx["fuel_class"], "landscape": landscape},
        "simulate_until": horizon,
        "ignition": ctx["ignition_note"], "baseline_no_spread": bool(no_spread),
        "ros_multiplier": round(ctx["m"], 4),
        "calibration": {k: ctx["cal"].get(k) for k in ("applied", "m", "requested", "confidence", "reason") if k in ctx["cal"]},
        "scenarios": runs,
        "ranking_by_growth": [r["name"] for r in sorted(runs, key=lambda r: -r["growth_km2"])],
        "common_cell_m": max(r["cell_m"] for r in runs),
        "weather_mode": "gridded 5x5 Open-Meteo/NOAA points" if ctx["wg"] is not None else "single point",
        "notes": ctx["notes"] + ["All scenarios use the same raster cell size so areas are comparable; it can differ slightly "
                                 "from a plain /simulate run.",
                                 "Growth = burned area minus the initial footprint; reach per compass sector = farthest new burn beyond the initial footprint.",
                                 "Physics model: experimental screening estimate (see /simulate method and uncertainty)."],
        "runtime_s": round(_time.time() - t_all, 2), "input_fetch_s": round(t_ctx, 2),
        "estimate": True,
    }
