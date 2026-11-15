"""Drone recon route generator (heuristic planning aid, NOT a certified flight plan).

Takes a finished simulation (frames with burned footprints, ignition, weather) and builds an
inspection route that flies around the forecast fire footprint at a standoff distance:

  * the footprint is the simulated burned area at the chosen horizon (default 2 h after the start);
  * the route is the standoff ring around it, with a larger standoff on the downwind (head-fire)
    side, where heat, smoke and ember fall-out are strongest;
  * the launch point and the return leg are on the upwind side;
  * the waypoints closest to the ignition points hover a few seconds and point the camera at them,
    so the thermal camera can verify ignition points;
  * the camera looks at the nearest burned area from every waypoint.

It does NOT model thermal updrafts, turbulence, smoke, terrain, airspace or battery limits. It only
uses the simulated footprint and the hourly wind. The response lists these limits as warnings.

Endpoints:  POST /drone/mission?format=json|geojson|gpx|kml|csv  (body: finished simulation JSON)
            GET  /drone/mission?lat&lon&start&hours&ignition&...  (runs a light simulation itself)
"""
from __future__ import annotations

import io
import math
import xml.sax.saxutils as sx
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Body, HTTPException, Query, Response
from shapely.geometry import MultiPolygon, Point, Polygon, shape
from shapely.ops import unary_union

M_LAT = 110574.0
M_LON = 111320.0


def _proj(lat0: float):
    kx = M_LON * math.cos(math.radians(lat0))
    return (lambda lon, lat: ((lon - _proj.lon0) * kx, (lat - lat0) * M_LAT),
            lambda x, y: (_proj.lon0 + x / kx, lat0 + y / M_LAT))


def _bearing(dx: float, dy: float) -> float:
    return (math.degrees(math.atan2(dx, dy)) + 360.0) % 360.0


def _frame_at(frames: list[dict], horizon_h: float) -> dict:
    best = min(frames, key=lambda f: abs(float(f.get("t_hours", 0)) - horizon_h))
    return best


def _wind_at(sim: dict, frame: dict) -> tuple[float | None, float | None]:
    w = frame.get("weather") or {}
    if w.get("wind_dir_deg") is not None:
        return float(w["wind_dir_deg"]), float(w.get("wind_kmh") or 0)
    tl = sim.get("weather_timeline") or []
    if tl:
        i = min(len(tl) - 1, max(0, int(round(float(frame.get("t_hours", 0))))))
        return float(tl[i]["wind_dir_deg"]), float(tl[i].get("wind_kmh") or 0)
    return None, None


def build_mission(sim: dict, horizon_h: float = 2.0, standoff_m: float = 250.0, alt_agl_m: float = 100.0,
                  speed_ms: float = 10.0, spacing_m: float = 400.0, launch: tuple[float, float] | None = None,
                  hover_s: int = 6, max_flight_min: float = 25.0) -> dict:
    frames = sim.get("frames") or []
    if not frames:
        raise ValueError("simulation has no frames")
    fr = _frame_at(frames, horizon_h)
    geom = (fr.get("burned") or {})
    if not geom.get("coordinates"):
        raise ValueError("no burned footprint at this horizon")
    q = sim.get("query") or {}
    c = (sim.get("grid") or {}).get("center") or [q.get("lon"), q.get("lat")]
    # centre of the footprint in lon/lat
    raw = shape(geom)
    lon0, lat0 = raw.centroid.x, raw.centroid.y
    kx = M_LON * math.cos(math.radians(lat0))

    def to_m(lon, lat):
        return ((lon - lon0) * kx, (lat - lat0) * M_LAT)

    def to_ll(x, y):
        return (lon0 + x / kx, lat0 + y / M_LAT)

    def poly_m(p: Polygon) -> Polygon:
        return Polygon([to_m(*pt[:2]) for pt in p.exterior.coords],
                       [[to_m(*pt[:2]) for pt in r.coords] for r in p.interiors])

    parts = [poly_m(p) for p in (raw.geoms if isinstance(raw, MultiPolygon) else [raw])]
    burned = unary_union(parts).buffer(0)
    wdir, wkmh = _wind_at(sim, fr)
    downwind = ((wdir + 180.0) % 360.0) if wdir is not None else None   # direction the fire is pushed toward

    # standoff ring: union of buffers, keep the main components
    ring_poly = burned.buffer(standoff_m, join_style=1)
    comps = list(ring_poly.geoms) if hasattr(ring_poly, "geoms") else [ring_poly]
    comps.sort(key=lambda p: -p.area)
    comps = [p for p in comps if p.area >= 0.05 * comps[0].area][:4]
    cen = burned.centroid

    def extra(p: Point, comp_c: Point) -> float:
        if downwind is None:
            return 0.0
        d = _bearing(p.x - comp_c.x, p.y - comp_c.y)
        delta = abs((d - downwind + 180.0) % 360.0 - 180.0)
        return standoff_m * max(0.0, math.cos(math.radians(delta))) if delta < 90 else 0.0

    # launch point on the upwind side, 1.5 km beyond the footprint
    main = comps[0]            # launch next to the largest fire group, not next to the whole scatter
    cen = main.centroid
    rad = max(main.bounds[2] - main.bounds[0], main.bounds[3] - main.bounds[1]) / 2.0 + 1500.0
    if launch:
        lx, ly = to_m(launch[1], launch[0])
    elif wdir is not None:
        budget0 = max_flight_min * 60.0 * speed_ms * 0.85 - 60.0 * speed_ms
        # whole ring fits: launch upwind. Ring too long (arc around the head fire only): launch on the flank, never downwind.
        ang = wdir if main.exterior.length + 2 * rad <= budget0 else (downwind + 90.0) % 360.0
        lx, ly = cen.x + rad * math.sin(math.radians(ang)), cen.y + rad * math.cos(math.radians(ang))
    else:
        lx, ly = cen.x, cen.y - rad
    launch_pt = Point(lx, ly)

    ign_ll = [tuple(p) for p in ((sim.get("ignition") or {}).get("points") or [])][:40]
    ign_m = [Point(*to_m(lo, la)) for lo, la in ign_ll]

    comp_routes: list[list[dict]] = []
    for comp in comps:
        comp_c = comp.centroid
        ext = comp.exterior
        n = max(8, int(ext.length / spacing_m))
        pts = [ext.interpolate(i * ext.length / n) for i in range(n)]
        # order: start at the ring point closest to the launch point, then walk around the ring
        k = min(range(n), key=lambda i: pts[i].distance(launch_pt))
        pts = pts[k:] + pts[:k]
        r_ = []
        for p in pts:
            e = extra(p, comp_c)
            if e > 1:
                v = (p.x - comp_c.x, p.y - comp_c.y)
                nrm = math.hypot(*v) or 1.0
                p = Point(p.x + v[0] / nrm * e, p.y + v[1] / nrm * e)
            r_.append({"x": p.x, "y": p.y, "kind": "perimeter", "head_side": e > 0.5 * standoff_m, "_comp_c": (comp_c.x, comp_c.y)})
        comp_routes.append(r_)

    # flight budget: largest components first, nearest transit; an oversize ring becomes an arc centred on the head fire
    budget_m = max_flight_min * 60.0 * speed_ms * 0.85 - 60.0 * speed_ms     # keep a reserve, minus climb/descent
    def _len(seq, start=None):
        tot, prev = 0.0, start
        for w in seq:
            if prev is not None:
                tot += math.hypot(w["x"] - prev[0], w["y"] - prev[1])
            prev = (w["x"], w["y"])
        return tot
    wps, used, skipped, truncated = [], 0.0, 0, False
    cur = (lx, ly)
    for r_ in comp_routes:
        need = _len(r_, cur) + math.hypot(r_[-1]["x"] - lx, r_[-1]["y"] - ly)
        if used + need <= budget_m:
            wps += r_; used += need - math.hypot(r_[-1]["x"] - lx, r_[-1]["y"] - ly); cur = (r_[-1]["x"], r_[-1]["y"])
            continue
        if not wps and r_:
            # single oversize ring: keep an arc around the head-fire side that fits the budget
            cx, cy = r_[0]["_comp_c"]
            tgt = downwind if downwind is not None else _bearing(lx - cx, ly - cy)
            c0 = min(range(len(r_)), key=lambda i: abs((_bearing(r_[i]["x"] - cx, r_[i]["y"] - cy) - tgt + 180) % 360 - 180))
            arc, lo, hi = [r_[c0]], c0, c0
            while True:
                grew = False
                for side in (1, -1):
                    j = (hi + 1) % len(r_) if side == 1 else (lo - 1) % len(r_)
                    if r_[j] in arc:
                        continue
                    cand = arc + [r_[j]] if side == 1 else [r_[j]] + arc
                    if _len(cand, cur) + math.hypot(cand[-1]["x"] - lx, cand[-1]["y"] - ly) <= budget_m:
                        arc = cand
                        if side == 1:
                            hi = j
                        else:
                            lo = j
                        grew = True
                if not grew:
                    break
            wps += arc; used += _len(arc, cur); cur = (arc[-1]["x"], arc[-1]["y"])
            truncated = len(arc) < len(r_)
            continue
        skipped += 1
    for w in wps:
        w.pop("_comp_c", None)

    # camera heading: toward the nearest burned area
    for w in wps:
        p = Point(w["x"], w["y"])
        nearest = burned.boundary.interpolate(burned.boundary.project(p))
        w["heading"] = round(_bearing(nearest.x - p.x, nearest.y - p.y), 1)
        w["dist_to_fire_m"] = round(p.distance(burned))
        w["hover_s"] = 0
    # ignition verification: hover at the waypoint nearest to each ignition point, camera on that point
    marked = set()
    for ip in ign_m[:12]:
        if not wps:
            break
        j = min(range(len(wps)), key=lambda i: Point(wps[i]["x"], wps[i]["y"]).distance(ip))
        if j in marked:
            continue
        marked.add(j)
        w = wps[j]
        w["heading"] = round(_bearing(ip.x - w["x"], ip.y - w["y"]), 1)
        w["hover_s"] = hover_s
        w["kind"] = "ignition_check"
        w["poi"] = list(to_ll(ip.x, ip.y))

    # limit to a sane number of waypoints
    if len(wps) > 99:
        step = len(wps) / 99.0
        keep = {int(i * step) for i in range(99)} | marked
        wps = [w for i, w in enumerate(wps) if i in keep]

    # route: takeoff (launch, upwind) -> waypoints -> return to launch
    route = [{"x": lx, "y": ly, "kind": "launch", "heading": 0.0, "hover_s": 0, "dist_to_fire_m": round(launch_pt.distance(burned))}] \
        + wps + [{"x": lx, "y": ly, "kind": "return", "heading": 0.0, "hover_s": 0, "dist_to_fire_m": round(launch_pt.distance(burned))}]
    length = 0.0
    for a, b in zip(route, route[1:]):
        length += math.hypot(b["x"] - a["x"], b["y"] - a["y"])
    flight_s = length / max(0.5, speed_ms) + sum(w["hover_s"] for w in route) + 60.0   # +60 s climb/descent
    out_pts = []
    for i, w in enumerate(route):
        lon, lat = to_ll(w["x"], w["y"])
        o = {"n": i + 1, "lat": round(lat, 6), "lon": round(lon, 6), "alt_agl_m": alt_agl_m, "heading_deg": w["heading"],
             "kind": w["kind"], "hover_s": w["hover_s"], "dist_to_fire_m": w["dist_to_fire_m"], "gimbal_pitch_deg": -35}
        if w.get("head_side"):
            o["head_side"] = True
        if w.get("poi"):
            o["poi"] = [round(w["poi"][0], 6), round(w["poi"][1], 6)]
        out_pts.append(o)
    if out_pts:   # launch/return: face the fire
        h = _bearing(cen.x - lx, cen.y - ly)
        out_pts[0]["heading_deg"] = out_pts[-1]["heading_deg"] = round(h, 1)

    warnings = [
        "Planning aid only. Not a certified flight plan. The pilot is responsible for airspace permission, VLOS and local rules, and for a go/no-go decision on site.",
        "Route is built from the simulated footprint at +%.0f h. The simulation is an uncalibrated estimate; the real fire can be elsewhere." % float(fr.get("t_hours", horizon_h)),
        "Thermal updrafts, turbulence, smoke and embers are NOT modelled. The route only keeps a larger standoff on the downwind (head-fire) side and launches from the upwind side.",
        "Altitudes are metres above ground at the launch point. Turn on terrain following in the planner, check terrain, wires and other aircraft (firefighting helicopters) before flying.",
        "Battery: the route is cut to fit %.0f min of flight at %.0f m/s (with a reserve); check this against your drone's real endurance." % (max_flight_min, speed_ms),
    ]
    if truncated:
        warnings.append("The ring around the main fire group is longer than the flight-time budget, so the route covers only an arc centred on the head-fire side.")
    if skipped:
        warnings.append("%d smaller fire group(s) did not fit into the flight-time budget and are not on the route." % skipped)
    if sim.get("ignition", {}).get("estimate"):
        warnings.append("Ignition points are estimates (e.g. centres of 0.05 degree satellite cells), so ignition checks point at the cell centre.")
    return {
        "type": "drone_recon_mission",
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "estimate": True,
        "basis": {"frame_t_hours": fr.get("t_hours"), "frame_time_utc": fr.get("time_utc"), "burned_km2": fr.get("burned_km2"),
                  "wind_from_deg": wdir, "wind_kmh": wkmh, "downwind_deg": downwind,
                  "start_utc": q.get("start")},
        "params": {"horizon_h": horizon_h, "standoff_m": standoff_m, "head_extra_standoff_m": standoff_m, "alt_agl_m": alt_agl_m,
                   "speed_ms": speed_ms, "spacing_m": spacing_m, "hover_s": hover_s, "max_flight_min": max_flight_min},
        "stats": {"waypoints": len(out_pts), "length_km": round(length / 1000.0, 2), "est_flight_min": round(flight_s / 60.0, 1),
                  "ignition_checks": sum(1 for p in out_pts if p["kind"] == "ignition_check"),
                  "launch_dist_to_fire_m": out_pts[0]["dist_to_fire_m"],
                  "fire_groups_total": len(comps), "fire_groups_skipped_for_battery": skipped, "main_ring_truncated": truncated},
        "waypoints": out_pts,
        "footprint": raw.__geo_interface__,
        "warnings": warnings,
    }


# ------------------------------------------------------------------ formatters
def _name(m: dict) -> str:
    b = m["basis"]
    return "FireSpot recon %s +%sh" % ((b.get("start_utc") or "")[:10], b.get("frame_t_hours"))


def to_geojson(m: dict) -> dict:
    line = [[p["lon"], p["lat"], p["alt_agl_m"]] for p in m["waypoints"]]
    feats = [{"type": "Feature", "properties": {"role": "route", "length_km": m["stats"]["length_km"]},
              "geometry": {"type": "LineString", "coordinates": line}},
             {"type": "Feature", "properties": {"role": "forecast_footprint", "estimate": True}, "geometry": m["footprint"]}]
    for p in m["waypoints"]:
        feats.append({"type": "Feature", "properties": {k: v for k, v in p.items() if k not in ("lat", "lon", "poi")},
                      "geometry": {"type": "Point", "coordinates": [p["lon"], p["lat"]]}})
    return {"type": "FeatureCollection", "name": _name(m), "features": feats, "warnings": m["warnings"]}


def to_gpx(m: dict) -> str:
    e = sx.escape
    o = io.StringIO()
    o.write('<?xml version="1.0" encoding="UTF-8"?>\n<gpx version="1.1" creator="FireSpot" xmlns="http://www.topografix.com/GPX/1/1">\n')
    o.write("<metadata><name>%s</name><desc>%s</desc></metadata>\n" % (e(_name(m)), e(" ".join(m["warnings"][:2]))))
    for p in m["waypoints"]:
        o.write('<wpt lat="%s" lon="%s"><ele>%s</ele><name>WP%02d %s</name><desc>heading %s deg, gimbal %s deg, hover %s s, %s m from fire</desc></wpt>\n' % (
            p["lat"], p["lon"], p["alt_agl_m"], p["n"], e(p["kind"]), p["heading_deg"], p["gimbal_pitch_deg"], p["hover_s"], p["dist_to_fire_m"]))
    o.write("<rte><name>%s</name>\n" % e(_name(m)))
    for p in m["waypoints"]:
        o.write('<rtept lat="%s" lon="%s"><ele>%s</ele><name>WP%02d</name></rtept>\n' % (p["lat"], p["lon"], p["alt_agl_m"], p["n"]))
    o.write("</rte>\n</gpx>\n")
    return o.getvalue()


def to_kml(m: dict) -> str:
    e = sx.escape
    coords = " ".join("%s,%s,%s" % (p["lon"], p["lat"], p["alt_agl_m"]) for p in m["waypoints"])
    o = io.StringIO()
    o.write('<?xml version="1.0" encoding="UTF-8"?>\n<kml xmlns="http://www.opengis.net/kml/2.2"><Document>\n<name>%s</name>\n' % e(_name(m)))
    o.write("<description>%s</description>\n" % e(" ".join(m["warnings"][:2])))
    o.write('<Style id="r"><LineStyle><color>ff1c8cf5</color><width>3</width></LineStyle></Style>\n')
    o.write('<Style id="i"><IconStyle><color>ff0000ff</color></IconStyle></Style>\n')
    o.write("<Placemark><name>Recon route</name><styleUrl>#r</styleUrl><LineString><altitudeMode>relativeToGround</altitudeMode>"
            "<coordinates>%s</coordinates></LineString></Placemark>\n" % coords)
    for p in m["waypoints"]:
        o.write("<Placemark><name>WP%02d %s</name><styleUrl>%s</styleUrl><description>heading %s deg, gimbal %s deg, hover %s s, %s m from fire</description>"
                "<Point><altitudeMode>relativeToGround</altitudeMode><coordinates>%s,%s,%s</coordinates></Point></Placemark>\n" % (
                    p["n"], e(p["kind"]), "#i" if p["kind"] == "ignition_check" else "#r", p["heading_deg"], p["gimbal_pitch_deg"],
                    p["hover_s"], p["dist_to_fire_m"], p["lon"], p["lat"], p["alt_agl_m"]))
    o.write("</Document></kml>\n")
    return o.getvalue()


def to_csv(m: dict) -> str:
    """Litchi-style mission CSV (altitude above ground level)."""
    hdr = ("latitude,longitude,altitude(m),heading(deg),curvesize(m),rotationdir,gimbalmode,gimbalpitchangle,"
           "actiontype1,actionparam1,altitudemode,speed(m/s),poi_latitude,poi_longitude,poi_altitude(m),poi_altitudemode,"
           "photo_timeinterval,photo_distinterval")
    sp = m["params"]["speed_ms"]
    rows = [hdr]
    for p in m["waypoints"]:
        poi = p.get("poi")
        act, par = (0, p["hover_s"] * 1000) if p["hover_s"] else (-1, 0)     # 0 = stay for (ms)
        rows.append("%s,%s,%s,%s,0,0,2,%s,%s,%s,1,%s,%s,%s,0,0,-1,-1" % (
            p["lat"], p["lon"], p["alt_agl_m"], p["heading_deg"], p["gimbal_pitch_deg"], act, par, sp,
            poi[1] if poi else 0, poi[0] if poi else 0))
    return "\n".join(rows) + "\n"


FORMATS = {
    "gpx": (to_gpx, "application/gpx+xml", "gpx"),
    "kml": (to_kml, "application/vnd.google-earth.kml+xml", "kml"),
    "csv": (to_csv, "text/csv; charset=utf-8", "csv"),
}


def _respond(m: dict, fmt: str):
    if fmt == "json":
        return m
    if fmt == "geojson":
        return Response(__import__("json").dumps(to_geojson(m)), media_type="application/geo+json",
                        headers={"Content-Disposition": 'attachment; filename="firespot_recon.geojson"'})
    fn, mt, ext = FORMATS[fmt]
    return Response(fn(m), media_type=mt, headers={"Content-Disposition": 'attachment; filename="firespot_recon.%s"' % ext})


Fmt = Annotated[str, Query(pattern="^(json|geojson|gpx|kml|csv)$")]


def _router() -> APIRouter:
    r = APIRouter(tags=["drone"])

    @r.post("/drone/mission", summary="Drone recon route around the forecast fire (planning aid), from a finished simulation",
            response_model=None)
    def mission_post(body: dict = Body(...), format: Fmt = "json", horizon_h: float = Query(2.0, ge=0.5, le=24),
                     standoff_m: float = Query(250, ge=100, le=2000), alt_agl_m: float = Query(100, ge=30, le=120),
                     speed_ms: float = Query(10, ge=2, le=20), spacing_m: float = Query(400, ge=150, le=2000), max_flight_min: float = Query(25, ge=5, le=90),
                     launch_lat: float | None = Query(None, ge=-90, le=90), launch_lon: float | None = Query(None, ge=-180, le=180)):
        sim = body.get("simulation", body)
        try:
            m = build_mission(sim, horizon_h, standoff_m, alt_agl_m, speed_ms, spacing_m,
                              (launch_lat, launch_lon) if launch_lat is not None and launch_lon is not None else None,
                              max_flight_min=max_flight_min)
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
        return _respond(m, format)

    @r.get("/drone/mission", summary="Drone recon route: runs a light simulation first (can take a minute)", response_model=None)
    def mission_get(lat: Annotated[float, Query(ge=-90, le=90)], lon: Annotated[float, Query(ge=-180, le=180)],
                    start: str | None = Query(None), ignition: str = Query("live"), format: Fmt = "json",
                    horizon_h: float = Query(2.0, ge=0.5, le=24), standoff_m: float = Query(250, ge=100, le=2000),
                    alt_agl_m: float = Query(100, ge=30, le=120), speed_ms: float = Query(10, ge=2, le=20),
                    spacing_m: float = Query(400, ge=150, le=2000)):
        from backend import app as _app
        try:
            sim = _app.simulate_get(lat=lat, lon=lon, start=start, hours=max(2, math.ceil(horizon_h)), step_min=30, ignition=ignition,
                                    fuel="auto", envelope=False, frames=True, radius_km=30.0, calibration="off", weather="grid",
                                    goes="off", fwi=False, landscape="off")
            m = build_mission(sim, horizon_h, standoff_m, alt_agl_m, speed_ms, spacing_m)
        except ValueError as e:
            raise HTTPException(404, str(e)) from None
        return _respond(m, format)

    return r


router = _router()
