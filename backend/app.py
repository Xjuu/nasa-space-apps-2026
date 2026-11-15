"""EquiFire API.

    .venv/bin/python -m uvicorn backend.app:app --host 0.0.0.0 --port 8000

Data (satellite cells + spread model) loads in a background thread at startup; endpoints that need it
answer 503 until GET /health says "ready". Set EQUIFIRE_NO_LOAD=1 to skip loading (tests).
"""
from __future__ import annotations

import datetime as dt
import math
import os
from contextlib import asynccontextmanager
from typing import Annotated

import numpy as np
from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware

from . import cap, firms, geo, orbits
from .data import COARSE, State, Store, date_of, day_of
from .spread import Forecaster, NoFireError, find_centre, to_geojson

store = Store()
forecaster = Forecaster(store)
ANOMALY_Z = 2.0
MAX_FIRES = 50_000


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # EQUIFIRE_LITE=1: live features only (live fires, alerts, overpasses, Orbital Fire Watch); the
    # archive (fires by date, forecast, stats, anomaly) is not loaded -> ~0.3 GB RAM instead of several GB.
    lite = os.environ.get("EQUIFIRE_LITE") == "1"
    if os.environ.get("EQUIFIRE_NO_LOAD") != "1" and not lite:
        store.start()
        if os.environ.get("EQUIFIRE_PRELOAD_CALENDAR") == "1":  # otherwise loaded on first /classify call
            try:
                _calendar()
            except HTTPException:
                pass
    yield


app = FastAPI(title="FireSpot API", version="0.1.0", lifespan=lifespan,
              description="Harmonized MODIS + VIIRS fire hotspots, anomaly scores, spread forecasts, "
                          "overpass countdown, GeoJSON and CAP export.")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

Lat = Annotated[float, Query(ge=-90, le=90, description="latitude, degrees")]
Lon = Annotated[float, Query(ge=-180, le=180, description="longitude, degrees")]
DateQ = Annotated[str, Query(description="YYYY-MM-DD (UTC)")]


# ------------------------------------------------------------------ helpers
def parse_date(s: str) -> int:
    try:
        d = dt.date.fromisoformat(s)
    except ValueError:
        raise HTTPException(400, f"bad date {s!r}: expected YYYY-MM-DD") from None
    return day_of(d.isoformat())


def parse_bbox(s: str | None) -> tuple[float, float, float, float] | None:
    if s is None or s == "":
        return None
    try:
        v = [float(p) for p in s.split(",")]
    except ValueError:
        raise HTTPException(400, "bbox must be 4 numbers: minlon,minlat,maxlon,maxlat") from None
    if len(v) != 4 or not all(map(math.isfinite, v)):
        raise HTTPException(400, "bbox must be 4 numbers: minlon,minlat,maxlon,maxlat")
    x0, y0, x1, y1 = v
    if not (-180 <= x0 < x1 <= 180 and -90 <= y0 < y1 <= 90):
        raise HTTPException(400, "bbox must satisfy -180<=minlon<maxlon<=180 and -90<=minlat<maxlat<=90")
    return x0, y0, x1, y1


def need_state() -> State:
    if store.state is None:
        if store.status == "error":
            raise HTTPException(500, f"data failed to load: {store.error}")
        raise HTTPException(503, "data is still loading, see GET /health", headers={"Retry-After": "10"})
    return store.state


def need_model():
    if store.model is None:
        raise HTTPException(503, f"spread model not available: {store.model_error or 'loading'}",
                            headers={"Retry-After": "10"})


def in_range(state: State, day: int, viirs: bool = False):
    lo, hi = (state.viirs_day_min, state.viirs_day_max) if viirs else (state.day_min, state.day_max)
    if not lo <= day <= hi:
        raise HTTPException(404, f"date {date_of(day)} outside the {'VIIRS ' if viirs else ''}data range "
                                 f"{date_of(lo)} .. {date_of(hi)}")


def r3(v) -> float:
    return round(float(v), 3)


# ------------------------------------------------------------------ endpoints
@app.get("/health")
def health() -> dict:
    st = store.state
    return {
        "status": store.status,
        "data_loaded": st is not None,
        "model_loaded": store.model is not None,
        "model_error": store.model_error,
        "error": store.error,
        "loading_seconds": round(dt.datetime.now().timestamp() - store.started, 1)
        if store.started and st is None else None,
        "load_time_s": round(store.loaded_s, 1) if store.loaded_s else None,
        "data": st.summary() if st is not None else None,
        "model": {k: store.meta[k] for k in ("test_from", "val_auc") if k in store.meta} if store.meta else None,
        "log": store.log_lines[-5:],
    }


@app.get("/fires")
def fires(date: DateQ, bbox: str | None = Query(None, description="minlon,minlat,maxlon,maxlat"),
          limit: int = Query(20_000, ge=1, le=MAX_FIRES),
          max_area: float | None = Query(None, description="Max allowed area in km² (e.g. 25000)"),
          gaps: bool = Query(False, description="add cells that burned the day before but are not seen "
                                                "today (possible cloud/smoke dead zone)")) -> dict:
    """Burning 0.05 deg cells on a day (any sensor) with raw and harmonized FRP."""
    box = parse_bbox(bbox)
    if box and max_area is not None:
        area_km2 = firms.geodesic_area_km2(*box)
        if area_km2 > max_area + 2.0:
            raise HTTPException(400, f"Selected area is {area_km2:.1f} km², which exceeds the maximum allowed limit of {max_area:.0f} km².")
    day = parse_date(date)
    st = need_state()
    if day < st.day_min:
        in_range(st, day)
    elif day > st.day_max:
        if firms.get_map_key():
            try:
                return firms.get_last24h_geojson(box, date_str=date, max_area=max_area or firms.MAX_AREA_KM2)
            except ValueError as e:
                raise HTTPException(400, str(e)) from None
            except Exception as e:
                raise HTTPException(502, f"Failed fetching FIRMS data for {date}: {e}") from None
        in_range(st, day)
    in_range(st, day)
    full = _clip(st.day_slice(day), box)
    total = len(full)
    cur = full.assign(equiv=_frp_equiv(full)).sort_values("equiv", ascending=False).head(limit)
    feats = []
    for r in cur.itertuples(index=False):
        lat, lon = geo.cell_center(r.gx, r.gy)
        feats.append(geo.feature(
            geo.cell_polygon(r.gx, r.gy), kind="burning", lat=lat, lon=lon,
            detections_viirs=int(r.n_v), detections_modis=int(r.n_m),
            overpasses_viirs=int(r.ov_v), overpasses_modis=int(r.ov_m),
            frp_viirs_mw=r3(r.frp_v), frp_modis_raw_mw=r3(r.frp_m), frp_modis_harmonized_mw=r3(r.frp_mh),
            frp_equiv_mw=r3(r.equiv), frp_max_mw=r3(r.frp_max),
            fire_area_km2=r3(r.area_v / max(r.ov_v, 1) if r.ov_v else r.area_mh / max(r.ov_m, 1))))
    n_gaps = 0
    if gaps and day - 1 >= st.day_min:
        prev = _clip(st.day_slice(day - 1), box)
        now_keys = set(zip(full["gx"].tolist(), full["gy"].tolist()))
        for r in prev.itertuples(index=False):
            if (r.gx, r.gy) not in now_keys:
                n_gaps += 1
                lat, lon = geo.cell_center(r.gx, r.gy)
                feats.append(geo.feature(geo.cell_polygon(r.gx, r.gy), kind="gap", lat=lat, lon=lon,
                                         last_seen=date_of(day - 1), frp_equiv_mw=r3(_frp_equiv_row(r)),
                                         hint="burned yesterday, not detected today: extinguished or hidden "
                                              "by cloud/smoke/orbit gap"))
    return geo.collection(feats, query={"date": date, "bbox": box, "limit": limit},
                          summary={"burning_cells": total, "returned": min(total, limit), "truncated": total > limit,
                                   "gap_cells": n_gaps,
                                   "frp_note": "frp_equiv_mw = mean VIIRS-equivalent FRP per overpass (VIIRS "
                                               "measured + MODIS harmonized by the size model)"})


@app.get("/firms/last24h")
def firms_last24h(
    bbox: str | None = Query(None, description="minlon,minlat,maxlon,maxlat (area must be <= 25000 km²)"),
    source: str = Query("VIIRS_NOAA20_NRT", description="FIRMS sensor source"),
    max_area_km2: float = Query(firms.MAX_AREA_KM2, description="Max allowed area in km²")
) -> dict:
    """Fires seen last in each spot over the last 24 hours from NASA FIRMS."""
    box = parse_bbox(bbox)
    try:
        return firms.get_last24h_geojson(box, source=source, max_area=max_area_km2)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    except RuntimeError as e:
        raise HTTPException(502, str(e)) from None


@app.get("/firms/date")
def firms_date(
    date: DateQ,
    bbox: str | None = Query(None, description="minlon,minlat,maxlon,maxlat (area must be <= 25000 km²)"),
    source: str = Query("VIIRS_NOAA20_NRT", description="FIRMS sensor source"),
    max_area_km2: float = Query(firms.MAX_AREA_KM2, description="Max allowed area in km²")
) -> dict:
    """Fires on a specific date from NASA FIRMS."""
    box = parse_bbox(bbox)
    try:
        return firms.get_last24h_geojson(box, source=source, max_area=max_area_km2, date_str=date)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    except RuntimeError as e:
        raise HTTPException(502, str(e)) from None


def _clip(df, box):
    if box is None:
        return df
    x0, y0, x1, y1 = box
    gx0, gy0 = geo.lonlat_to_cell(y0, x0)
    gx1, gy1 = geo.lonlat_to_cell(y1, x1)
    return df[(df["gx"] >= gx0) & (df["gx"] <= gx1) & (df["gy"] >= gy0) & (df["gy"] <= gy1)]


def _frp_equiv(df) -> np.ndarray:
    ov = (df["ov_v"].values + df["ov_m"].values).astype(np.float32)
    return (df["frp_v"].values + df["frp_mh"].values) / np.maximum(ov, 1)


def _frp_equiv_row(r) -> float:
    return (r.frp_v + r.frp_mh) / max(r.ov_v + r.ov_m, 1)


def _forecast(lat: float, lon: float, date: str, days: int, search_deg: float):
    t0 = parse_date(date)
    st = need_state()
    need_model()
    in_range(st, t0, viirs=True)
    gx, gy = geo.lonlat_to_cell(lat, lon)
    try:
        cx, cy = find_centre(st, t0, gx, gy, max(0, int(round(search_deg / 0.05))))
        return forecaster.run(t0, cx, cy, days)
    except NoFireError as e:
        raise HTTPException(404, str(e)) from None


ForecastDays = Annotated[int, Query(ge=1, le=28, description="forecast horizon in days (1-28)")]
SearchDeg = Annotated[float, Query(ge=0, le=1.0, description="snap to the nearest VIIRS fire within this distance")]


@app.get("/forecast")
def forecast(lat: Lat, lon: Lon, date: DateQ, days: ForecastDays = 3, search_deg: SearchDeg = 0.5) -> dict:
    """Cells predicted to newly burn by each day (GeoJSON), plus observed truth for historical dates."""
    fc = _forecast(lat, lon, date, days, search_deg)
    return to_geojson(fc, {"lat": lat, "lon": lon, "date": date, "days": days, "search_deg": search_deg})


@app.get("/forecast.cap", response_class=Response,
         responses={200: {"content": {"application/cap+xml": {}}}})
def forecast_cap(lat: Lat, lon: Lon, date: DateQ, days: ForecastDays = 3, search_deg: SearchDeg = 0.5) -> Response:
    """Same forecast as a CAP 1.2 alert (one <area> per forecast day)."""
    fc = _forecast(lat, lon, date, days, search_deg)
    return Response(cap.to_cap(fc), media_type="application/cap+xml")


@app.get("/anomaly")
def anomaly(lat: Lat, lon: Lon, date: DateQ, z_threshold: float = Query(ANOMALY_Z, gt=0, le=10)) -> dict:
    """Z-score of VIIRS fire activity in the 1 deg cell and ISO week vs the same week of other years."""
    day = parse_date(date)
    st = need_state()
    if not len(st.weekly):
        raise HTTPException(503, "no VIIRS data loaded")
    gx, gy = geo.lonlat_to_cell(lat, lon)
    cx, cy = gx // COARSE, gy // COARSE
    iso = dt.date.fromisoformat(date_of(day)).isocalendar()
    year, week = iso.year, iso.week
    years = _complete_iso_years(st, week)
    try:
        rows = st.weekly.loc[(cx, cy, week)]
        by_year = {int(y): (int(r.cell_days), int(r.detections), float(r.frp)) for y, r in rows.iterrows()}
    except KeyError:  # this cell never burned in this week
        by_year = {}
    base_years = [y for y in years if y != year]
    if len(base_years) < 2:
        raise HTTPException(404, f"not enough complete VIIRS years for a baseline of ISO week {week}")
    base = np.array([by_year.get(y, (0, 0, 0.0))[0] for y in base_years], float)  # missing year = 0
    observed = by_year.get(year, (0, 0, 0.0)) if year in years else None
    lon0, lat0 = cx * 1.0 - 180, cy * 1.0 - 90
    out = {"lat": lat, "lon": lon, "date": date_of(day), "iso_year": year, "iso_week": week,
           "cell_1deg": {"bbox": [lon0, lat0, lon0 + 1, lat0 + 1]},
           "metric": "VIIRS burning 0.05-deg cell-days in the 1-deg cell during the ISO week",
           "baseline_years": base_years, "baseline_values": base.astype(int).tolist()}
    mean, sd = float(base.mean()), float(base.std(ddof=1))
    sd_used = max(sd, math.sqrt(max(mean, 1.0)))  # Poisson floor: no infinite z for quiet cells
    out.update({"baseline_mean": round(mean, 2), "baseline_std": round(sd, 2), "std_used": round(sd_used, 2),
                "seasonal_frequency": round(float((base > 0).mean()), 2)})
    if observed is None:
        out.update({"observed": None, "z": None, "label": "no data for this week",
                    "note": f"VIIRS data covers {date_of(st.viirs_day_min)} .. {date_of(st.viirs_day_max)}; "
                            "baseline only"})
        return out
    x = observed[0]
    z = (x - mean) / sd_used
    if x == 0:
        label = "no fire activity"
    elif z >= z_threshold:
        label = "anomaly"
    else:
        label = "routine seasonal burning"
    out.update({"observed": x, "observed_detections": observed[1], "observed_frp_mw": round(observed[2], 1),
                "z": round(z, 2), "z_threshold": z_threshold, "label": label})
    return out


# ---------------------------------------------------------------- goal 4: routine vs anomaly
import threading as _threading

_CAL = {"cal": None, "status": "not loaded", "error": None}
_CAL_LOCK = _threading.Lock()


def _load_calendar_bg():
    from equifire.seasonal import load_calendar
    try:
        _CAL["cal"] = load_calendar(verbose=False)
        _CAL["status"] = "ready"
    except Exception as e:  # keep the API alive, report on /classify
        _CAL["status"], _CAL["error"] = "error", f"{type(e).__name__}: {e}"


def _calendar():
    with _CAL_LOCK:
        if _CAL["status"] == "not loaded":
            _CAL["status"] = "loading"
            _threading.Thread(target=_load_calendar_bg, daemon=True).start()
    if _CAL["status"] == "error":
        raise HTTPException(500, f"seasonal calendar failed: {_CAL['error']}")
    if _CAL["cal"] is None:
        raise HTTPException(503, "seasonal calendar is loading (about 1 minute), retry shortly",
                            headers={"Retry-After": "15"})
    return _CAL["cal"]


@app.get("/classify")
def classify(lat: Lat, lon: Lon, date: DateQ) -> dict:
    """Routine seasonal (agricultural / managed) burning vs fire anomaly for the 0.25 deg region.

    Deterministic engine (equifire/seasonal.py): calendar baseline from all other years (leave-one-year-out),
    plus season frequency, z-score, field recurrence, one-day spike, FRP per detection, cluster size and
    persistence. Returns label, routine_score, anomaly_score and human-readable reasons."""
    parse_date(date)
    cal = _calendar()
    r = cal.classify(lat, lon, date)
    r["calendar"] = [round(v, 2) for v in r["calendar"]]
    for k, v in list(r.items()):
        if isinstance(v, float) and not math.isfinite(v):
            r[k] = None
    return r


def _complete_iso_years(st: State, week: int) -> list[int]:
    """ISO years whose `week` lies fully inside the VIIRS data range."""
    lo, hi = date_of(st.viirs_day_min), date_of(st.viirs_day_max)
    out = []
    for y in range(int(lo[:4]) - 1, int(hi[:4]) + 2):
        try:
            mon = dt.date.fromisocalendar(y, week, 1)
        except ValueError:  # week 53 does not exist that year
            continue
        if mon.isoformat() >= lo and (mon + dt.timedelta(days=6)).isoformat() <= hi:
            out.append(y)
    return out


@app.get("/overpass")
def overpass(lat: Lat, lon: Lon, hours: float = Query(24, gt=0, le=72),
             at: str | None = Query(None, description="start time, ISO 8601 (default: now, UTC)")) -> dict:
    """Next passes of Terra, Aqua (MODIS) and Suomi NPP, NOAA-20, NOAA-21 (VIIRS) over a point."""
    if at:
        try:
            start = dt.datetime.fromisoformat(at.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(400, f"bad 'at' {at!r}: expected ISO 8601 datetime") from None
        if start.tzinfo is None:
            start = start.replace(tzinfo=dt.timezone.utc)
    else:
        start = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    sats = orbits.passes(lat, lon, start, hours)
    flat = sorted(({"satellite": s["satellite"], "instrument": s["instrument"], **p}
                   for s in sats for p in s["passes"]), key=lambda p: p["minutes_until"])
    return {"lat": lat, "lon": lon, "from": start.isoformat(), "hours": hours,
            "criterion": "point inside the instrument swath (scan angle <= max) and above the horizon",
            "next": flat[0] if flat else None, "passes": flat, "satellites": sats}


# ---------------------------------------------------------------- area statistics (calendar + charts)
from fastapi import Body  # noqa: E402

from . import stats as _stats  # noqa: E402

MetricQ = Annotated[str, Query(description="one of: " + ", ".join(_stats.METRICS))]


def _stats_answer(box, polygon, year, metric, z_threshold) -> dict:
    st = need_state()
    try:
        return _stats.compute(st, box, polygon=polygon, year=year, metric=metric, z_threshold=z_threshold)
    except _stats.StatsError as e:
        raise HTTPException(400, str(e)) from None


@app.get("/stats")
def stats_get(bbox: str | None = Query(None, description="minlon,minlat,maxlon,maxlat"),
              lat: float | None = Query(None, ge=-90, le=90, description="centre latitude (if no bbox)"),
              lon: float | None = Query(None, ge=-180, le=180, description="centre longitude (if no bbox)"),
              size_deg: float = Query(1.0, gt=0, le=40, description="side of the square around lat/lon, degrees"),
              year: int | None = Query(None, description="year to compare (default: latest with data)"),
              metric: MetricQ = "viirs_cell_days",
              z_threshold: float = Query(ANOMALY_Z, gt=0, le=10)) -> dict:
    """Burning calendar (ISO year x week) and per-year totals by sensor for an area, plus weekly z-scores
    and key figures of `year` against all other years (leave-one-year-out baseline)."""
    box = parse_bbox(bbox)
    if box is None:
        if lat is None or lon is None:
            raise HTTPException(400, "give bbox=minlon,minlat,maxlon,maxlat or lat & lon (& size_deg)")
        h = size_deg / 2
        box = (max(-180.0, lon - h), max(-90.0, lat - h), min(180.0, lon + h), min(90.0, lat + h))
    return _stats_answer(box, None, year, metric, z_threshold)


@app.post("/stats")
def stats_post(body: dict = Body(..., examples=[{"geometry": {"type": "Polygon", "coordinates": [
                   [[-52, -7], [-50, -7], [-50, -5], [-52, -7]]]}, "year": 2024, "metric": "viirs_cell_days"}]),
               ) -> dict:
    """Same as GET /stats for a GeoJSON Polygon / MultiPolygon / Feature (key `geometry`), optional
    `year`, `metric`, `z_threshold`."""
    try:
        polys = _stats._rings(body.get("geometry") or {})
        box = _stats.polygon_bbox(polys)
    except _stats.StatsError as e:
        raise HTTPException(400, str(e)) from None
    if not (-180 <= box[0] <= box[2] <= 180 and -90 <= box[1] <= box[3] <= 90):
        raise HTTPException(400, "polygon must lie within lon -180..180, lat -90..90")
    year = body.get("year")
    try:
        year = None if year in (None, "") else int(year)
        zt = float(body.get("z_threshold", ANOMALY_Z))
    except (TypeError, ValueError):
        raise HTTPException(400, "year must be an integer and z_threshold a number") from None
    if not 0 < zt <= 10:
        raise HTTPException(400, "z_threshold must be in (0, 10]")
    return _stats_answer(box, polys, year, str(body.get("metric", "viirs_cell_days")), zt)


# ------------------------------------------------------------------ live NRT anomaly alerts (goal 3)
from . import live as _live  # noqa: E402

LiveRes = Annotated[float, Query(description="region size in degrees: 1 or 0.25")]
LIVE_LEVELS = ("watch", "warning", "critical")


def _live_state(res: float) -> dict:
    if res not in _live.RES:
        raise HTTPException(400, "res must be 1 or 0.25")
    st = _live.get_live(res)
    if st is None:
        rs = _live.refresh_status()
        if rs["error"] and not rs["running"]:
            raise HTTPException(502, f"live feed / baseline failed: {rs['error']}")
        raise HTTPException(503, "downloading NASA FIRMS NRT feeds / building baseline, retry shortly",
                            headers={"Retry-After": "20"})
    return st


@app.get("/live/summary")
def live_summary(res: LiveRes = 1.0) -> dict:
    """Feed timestamps, detection counts, number of alert regions per level (last 7 days vs 2008-2025)."""
    return {"res": res, **_live.summary(_live_state(res))}


@app.get("/live/alerts")
def live_alerts(level: str = Query("watch", description="minimum level: watch, warning or critical"),
                bbox: str | None = Query(None, description="minlon,minlat,maxlon,maxlat"),
                res: LiveRes = 1.0, limit: int = Query(500, ge=1, le=5000),
                include_no_baseline: bool = Query(False, description="also return active regions without a baseline")
                ) -> dict:
    """GeoJSON of grid regions whose last-7-day NRT burning cell-days exceed the historical norm for this
    calendar week (z >= 2 watch, 3 warning, 5 critical), sorted by z."""
    if level not in LIVE_LEVELS:
        raise HTTPException(400, f"level must be one of {LIVE_LEVELS}")
    box = parse_bbox(bbox)
    st = _live_state(res)
    lo = _live.LEVEL_RANK[level]
    regs = [r for r in st["regions"] if (_live.LEVEL_RANK[r["level"]] >= lo
                                         or (include_no_baseline and r["baseline"] == "none"))
            and _live.in_bbox(r, box)][:limit]
    fc = _live.to_geojson(regs)
    fc["meta"] = {"res": res, "level": level, "window": st["window"], "week": st["week"],
                  "obs_satellite": st["obs_satellite"], "computed": st.get("computed"),
                  "feeds": [{k: f[k] for k in ("feed", "fetched", "latest", "detections", "stale")} for f in st["feeds"]],
                  "baseline": st["baseline"], "returned": len(regs)}
    return fc


@app.get("/live/alerts.cap", response_class=Response,
         responses={200: {"content": {"application/cap+xml": {}}}})
def live_alerts_cap(res: LiveRes = 1.0, bbox: str | None = Query(None, description="minlon,minlat,maxlon,maxlat"),
                    limit: int = Query(100, ge=1, le=1000)) -> Response:
    """CAP 1.2 alert with one <info>/<area> per warning-or-critical region."""
    box = parse_bbox(bbox)
    st = _live_state(res)
    regs = [r for r in st["regions"] if _live.in_bbox(r, box)][:limit * 20]
    regs = [r for r in regs if _live.LEVEL_RANK[r["level"]] >= _live.LEVEL_RANK["warning"]][:limit]
    return Response(_live.to_cap(regs, window=st["window"]), media_type="application/cap+xml",
                    headers={"Content-Disposition": 'attachment; filename="equifire-live-alerts.cap.xml"'})


# ------------------------------------------------------------------ goal 2: view geometry + adaptive clusters
from .geometry_api import router as _geometry_router  # noqa: E402

app.include_router(_geometry_router)   # GET /geometry, GET /clusters (see backend/geometry_api.py)


# ---------------------------------------------------------------- live fire detections (public NRT feed, no key)
_LIVE_FIRES = {"t": 0.0, "df": None, "info": None}


@app.get("/live/fires")
def live_fires(bbox: str = Query(..., description="minlon,minlat,maxlon,maxlat (max 40 x 40 deg)"),
               hours: float = Query(24, gt=0, le=168, description="detections from the last N hours"),
               limit: int = Query(20000, ge=1, le=100000)) -> dict:
    """Individual fire detections from the FIRMS near-real-time global feeds (VIIRS NOAA-20, NOAA-21,
    S-NPP and MODIS), any map area up to 40 x 40 deg, newest first, as GeoJSON points. No API key."""
    import time as _time
    import pandas as _pd
    box = parse_bbox(bbox)
    if box is None:
        raise HTTPException(400, "bbox required")
    if box[2] - box[0] > 40 or box[3] - box[1] > 40:
        raise HTTPException(400, "bbox larger than 40 x 40 deg")
    if _LIVE_FIRES["df"] is None or _time.time() - _LIVE_FIRES["t"] > _live.TTL:
        window = "7d" if hours > 48 else ("48h" if hours > 24 else "24h")
        df, info = _live.load_feeds(window)
        _LIVE_FIRES.update(t=_time.time(), df=df, info=info, window=window)
    df = _LIVE_FIRES["df"]
    if hours > 24 and _LIVE_FIRES.get("window") == "24h":  # cached window too short: refresh with a longer one
        df, info = _live.load_feeds("7d")
        _LIVE_FIRES.update(t=_time.time(), df=df, info=info, window="7d")
    now = _pd.Timestamp.utcnow().tz_localize(None)
    d = df[(df["lon"] >= box[0]) & (df["lon"] <= box[2]) & (df["lat"] >= box[1]) & (df["lat"] <= box[3])
           & (df["t"] >= now - _pd.Timedelta(hours=hours))].sort_values("t", ascending=False).head(limit)
    feats = [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [round(r.lon, 4), round(r.lat, 4)]},
              "properties": {"kind": "live_fire", "satellite": r.sat, "frp_mw": round(float(r.frp), 1),
                             "time_utc": _pd.Timestamp(r.t).isoformat() + "Z", "confidence": r.conf,
                             "last_seen_time": _pd.Timestamp(r.t).isoformat() + "Z",
                             "hours_ago": round((now - _pd.Timestamp(r.t)).total_seconds() / 3600, 1)}}
             for r in d.itertuples(index=False)]
    area = geo.bbox_area_km2(*box) if hasattr(geo, "bbox_area_km2") else None
    return {"type": "FeatureCollection", "features": feats,
            "summary": {"total_spots": len(feats), "max_frp_mw": float(d["frp"].max()) if len(d) else 0.0,
                        "area_km2": round(area, 0) if area else None, "source": "FIRMS NRT global feed"},
            "meta": {"hours": hours, "returned": len(feats), "window": _LIVE_FIRES.get("window"),
                     "feeds": [{k: f[k] for k in ("feed", "fetched", "latest", "detections", "stale")}
                               for f in _LIVE_FIRES["info"]]}}


# ---------------------------------------------------------------- Orbital Fire Watch (integrated)
# Its API (/api/fires, /api/overpasses, /api/satellites/*, /api/satellite-track, /api/fire-spread-cones)
# runs inside this server; fires come from EquiFire's live NRT feed. No tracking UI is served.
def _mount_orbital():
    import sys as _sys
    from pathlib import Path as _P
    root = _P(__file__).resolve().parent.parent / "orbital"
    try:
        _sys.path.insert(0, str(root / "backend"))
        import importlib
        ofw = importlib.import_module("orbital.backend.main")
        for r in ofw.app.router.routes:
            if getattr(r, "path", "").startswith("/api/"):
                app.router.routes.append(r)
    except Exception as e:  # never break EquiFire because of the add-on
        print(f"[orbital] not mounted: {type(e).__name__}: {e}")


_mount_orbital()


# ---------------------------------------------------------------- physics spread simulator (owner: physics sub-agent)
# GET /simulate (frames for play/pause/step animation) and GET /simulate/weather. Logic: backend/physics_spread.py
import time as _ptime  # noqa: E402

from . import physics_spread as _phys  # noqa: E402

_SIM_CACHE: dict = {}


@app.get("/simulate")
def simulate_get(lat: Lat, lon: Lon,
                 start: str | None = Query(None, description="YYYY-MM-DDTHH:MM UTC, default now"),
                 hours: int = Query(72, ge=1, le=168), step_min: int = Query(60, ge=10, le=720),
                 ignition: str = Query("point", description="point | live | archive"),
                 fuel: str = Query("auto", description="auto | grass | savanna | shrub | forest"),
                 envelope: bool = Query(True, description="also run low/high weather variants"),
                 frames: bool = Query(True, description="include per-frame polygons"),
                 radius_km: float = Query(30.0, ge=0.5, le=60.0,
                                          description="live/archive: detections within this radius = ignition"),
                 calibration: str = Query("on", description="on | off: fit a spread multiplier to the fire's "
                                                             "growth over the previous 1-3 days"),
                 weather: str = Query("grid", description="grid (5x5 Open-Meteo points, per-cell wind/RH) | point"),
                 goes: str = Query("off", description="on | off: add GOES-East/West fire pixels (Americas) to the "
                                                      "calibration; first call downloads ~2 MB per scan-hour"),
                 fwi: bool = Query(True, description="add the Canadian FWI System for the start date"),
                 landscape: str = Query("on", description="on | off: real fuel, terrain, firebreak and dryness layers")) -> dict:
    if ignition not in ("point", "live", "archive"):
        raise HTTPException(400, "ignition must be point, live or archive")
    if fuel not in ("auto", *_phys.FUEL_MODELS):
        raise HTTPException(400, "fuel must be auto, grass, savanna, shrub or forest")
    try:
        t0 = _phys._parse_start(start)
    except Exception:
        raise HTTPException(400, f"bad start {start!r}: expected YYYY-MM-DDTHH:MM (UTC)") from None
    if calibration not in ("on", "off"):
        raise HTTPException(400, "calibration must be on or off")
    if weather not in ("grid", "point") or goes not in ("on", "off"):
        raise HTTPException(400, "weather must be grid or point; goes must be on or off")
    if landscape not in ("on", "off"):
        raise HTTPException(400, "landscape must be on or off")
    key = (round(lat, 4), round(lon, 4), t0.isoformat(), hours, step_min, ignition, fuel, envelope, frames,
           round(radius_km, 2), calibration, weather, goes, fwi, landscape)
    hit = _SIM_CACHE.get(key)
    if hit and _ptime.time() - hit[0] < 1800:
        return hit[1]
    out = _phys.forecast(lat, lon, t0, hours, step_min, ignition=ignition, fuel=fuel, envelope=envelope,
                         frames=frames, radius_km=radius_km, calibration=calibration, state=store.state,
                         weather=weather, goes=goes, fwi=fwi, landscape=landscape)
    if len(_SIM_CACHE) > 16:
        _SIM_CACHE.pop(next(iter(_SIM_CACHE)))
    _SIM_CACHE[key] = (_ptime.time(), out)
    return out


@app.get("/simulate/weather")
def simulate_weather(lat: Lat, lon: Lon,
                     start: str | None = Query(None, description="YYYY-MM-DDTHH:MM UTC, default now"),
                     hours: int = Query(72, ge=1, le=168),
                     fuel: str = Query("auto", description="auto | grass | savanna | shrub | forest")) -> dict:
    if fuel not in ("auto", *_phys.FUEL_MODELS):
        raise HTTPException(400, "fuel must be auto, grass, savanna, shrub or forest")
    try:
        t0 = _phys._parse_start(start)
    except Exception:
        raise HTTPException(400, f"bad start {start!r}: expected YYYY-MM-DDTHH:MM (UTC)") from None
    return _phys.weather_table(lat, lon, t0, hours, fuel=fuel)


# Combined predictor, deliberately separate from the existing ML /forecast endpoint.
@app.get('/predict')
def predict_combined(lat: Lat, lon: Lon, date: DateQ,
                     days: int = Query(3, ge=1, le=3),
                     objective: str = Query('iou', description='iou = exact cells; tol = one-cell tolerance')) -> dict:
    from . import ensemble
    try:
        return ensemble.predict(lat, lon, date, days, objective)
    except ensemble.NoIgnition as e:
        raise HTTPException(404, str(e)) from None
    except ensemble.PredictorUnavailable as e:
        raise HTTPException(503, str(e)) from None
    except (ValueError, KeyError) as e:
        raise HTTPException(400, str(e)) from None


_COMBINED_SIM_CACHE: dict = {}


@app.get('/predict/simulate')
def predict_simulation(lat: Lat, lon: Lon,
                       start: str | None = Query(None, description='Forecast initialization in UTC'),
                       hours: int = Query(24, ge=1, le=72),
                       step_min: int = Query(60, ge=10, le=720),
                       ignition: str = Query('live', description='live | archive satellite ignition'),
                       fuel: str = Query('auto'),
                       radius_km: float = Query(30.0, ge=0.5, le=60),
                       calibration: str = Query('off', description="on | off: calibrate spread rate to the fire's recent growth (experimental)")) -> dict:
    from . import ensemble
    try:
        if calibration not in ('on', 'off'):
            raise ValueError('calibration must be on or off')
        t0 = _phys._parse_start(start)
        if fuel not in ('auto', *_phys.FUEL_MODELS):
            raise ValueError('Unknown fuel model')
        key = (round(lat, 4), round(lon, 4), t0.isoformat(), hours, step_min, ignition, fuel, radius_km, calibration)
        hit = _COMBINED_SIM_CACHE.get(key)
        if hit and _ptime.time() - hit[0] < 300:
            return hit[1]
        out = ensemble.simulate_combined(lat, lon, t0, hours, step_min, ignition, fuel,
                                         radius_km, state=store.state, calibration=calibration)
        if len(_COMBINED_SIM_CACHE) >= 16:
            _COMBINED_SIM_CACHE.pop(next(iter(_COMBINED_SIM_CACHE)))
        _COMBINED_SIM_CACHE[key] = (_ptime.time(), out)
        return out
    except ensemble.NoIgnition as e:
        raise HTTPException(404, str(e)) from None
    except ensemble.PredictorUnavailable as e:
        raise HTTPException(503, str(e)) from None
    except (ValueError, KeyError) as e:
        raise HTTPException(400, str(e)) from None


# ------------------------------------------------ 25-year raw vs harmonized series (must precede the "/" mount below)
from .harmonized_api import router as _harmonized_router  # noqa: E402

app.include_router(_harmonized_router)   # GET /harmonized/series (see backend/harmonized_api.py)


# Exposure + incident brief (backend/exposure.py, backend/brief.py): GET/POST /exposure, GET /brief[.html|.pdf|.json].
# Must precede the "/" static mount below.
from .exposure import router as _exposure_router  # noqa: E402
from .brief import router as _brief_router  # noqa: E402

app.include_router(_exposure_router)
app.include_router(_brief_router)


# Honest historical replay (backend/replay.py): GET /replay/incidents, GET /replay/run. Must precede the "/" mount.
from .replay import router as _replay_router  # noqa: E402

app.include_router(_replay_router)


# One origin for the app and API, including temporary public tunnels.
from pathlib import Path as _FrontendPath
from fastapi.staticfiles import StaticFiles as _FrontendFiles
from fastapi.responses import RedirectResponse as _RedirectResponse

@app.get("/orbital", include_in_schema=False)
@app.get("/orbital/{legacy_path:path}", include_in_schema=False)
def retired_tracking_page(legacy_path: str = ""):
    return _RedirectResponse("/", status_code=307)

import re as _re_v
from fastapi.responses import HTMLResponse as _HTMLResponse

_FRONTEND_DIR = _FrontendPath(__file__).resolve().parent.parent / "frontend"
_ASSET_RE = _re_v.compile(r'(?P<attr>(?:src|href)=")(?P<url>(?![a-z]+:|//|#)[^"?#]+\.(?:js|css))(?P<end>")')


def _versioned_index() -> str:
    """index.html with ?v=<file mtime> on every local .js/.css, so a changed file gets a new URL and Cloudflare's
    edge cache (and browsers) fetch it fresh without a manual purge."""
    html = (_FRONTEND_DIR / "index.html").read_text(encoding="utf-8")

    def stamp(m):
        f = _FRONTEND_DIR / m.group("url")
        v = int(f.stat().st_mtime) if f.is_file() else 0
        return f'{m.group("attr")}{m.group("url")}?v={v}{m.group("end")}'
    return _ASSET_RE.sub(stamp, html)


@app.get("/", include_in_schema=False)
@app.get("/index.html", include_in_schema=False)
def _index_page():
    return _HTMLResponse(_versioned_index(), headers={"Cache-Control": "no-cache, max-age=0, must-revalidate",
                                                      "CDN-Cache-Control": "no-store"})


_STATIC_EXT = (".html", ".js", ".css", ".svg", ".png", ".jpg", ".jpeg", ".ico", ".webp", ".woff", ".woff2", ".map")


@app.middleware("http")
async def _no_stale_frontend(request, call_next):
    """The site is deployed live behind a Cloudflare tunnel: edge and browser caches must always revalidate the
    frontend files (ETag gives cheap 304s), otherwise visitors keep seeing an old version after an edit."""
    response = await call_next(request)
    path = request.url.path
    if request.method == "GET" and (path == "/" or path.endswith(_STATIC_EXT)):
        response.headers["Cache-Control"] = "no-cache, max-age=0, must-revalidate"
        response.headers["CDN-Cache-Control"] = "no-store"
    return response


app.mount("/", _FrontendFiles(directory=_FrontendPath(__file__).resolve().parent.parent / "frontend", html=True), name="frontend")


# ---- next-observation / blind spots / weather scenarios (owner: nextobs agent; logic in backend/nextobs.py)
from .nextobs import router as _nextobs_router  # noqa: E402

app.include_router(_nextobs_router)   # GET /nextobs, /blindspots, /scenarios

# Background pre-warmer of landscape / weather / simulation caches for the strongest live fires
# (backend/prewarm.py; GET /prewarm/status; off with EQUIFIRE_NO_PREWARM=1, LITE or NO_LOAD).
from . import prewarm as _prewarm  # noqa: E402

_prewarm.install(app)
# the static "/" mount above matches every path: keep it last so routes registered after it stay reachable
app.router.routes.sort(key=lambda r: getattr(r, "name", None) == "frontend")
