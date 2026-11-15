"""ESTIMATED EXPOSURE: which mapped assets does a simulated fire footprint reach, and when?

    GET  /exposure?lat&lon&start&hours&ignition=live|point|archive&...   (runs /simulate machinery)
    POST /exposure   body = a /simulate (or /predict/simulate) response, or {"frames": [...], ...}

Every number is an ESTIMATED EXPOSURE (an asset lies inside the simulated burned footprint at some frame). It
is NOT damage, NOT loss and NOT a casualty estimate. The simulation is experimental and uncalibrated, and
OpenStreetMap completeness varies by region (rural buildings, minor roads and power lines are often missing).

Assets come from OpenStreetMap (Overpass API, keyless, ODbL, mirrors + disk cache reused from
equifire.landscape; only the bbox of the final low/central/high footprint is queried, capped at 40 km half-width):
  settlement   place=city|town|village|hamlet nodes (buffered by an assumed built-up radius, see PLACE_RADIUS_M)
  residential_area  landuse=residential polygons
  building     building=* (centres only; counted, never listed one by one; aggregated to ~500 m cells)
  road         highway=motorway..tertiary (+links) and unclassified: evacuation-relevant network, length exposed
  rail         railway=rail|light_rail|narrow_gauge, length exposed
  power_line   power=line (length), power_site power=substation|plant
  hospital / school / fire_station   amenity=*  (buffered FACILITY_RADIUS_M)
Water is a barrier (handled by the landscape layer used by the simulator), never counted as exposed.

Population exposure is SEPARATE and OPTIONAL:
  * `hrsl`: Meta/CIESIN High Resolution Settlement Layer (public COG VRT on AWS open data, keyless, 30 m,
    aggregated here to ~150 m cells). Static estimate, not a census; not available everywhere (e.g. none over
    much of Canada).
  * `buildings_proxy`: OSM buildings x an assumed persons-per-dwelling (PERSONS_PER_BUILDING), generic
    building=yes weighted 0..1 (range), labelled as a rough proxy.
NEVER outputs monetary or insured-loss values (INSURED_LOSS_NOTE says what that would need).

Time is the first FRAME of the cumulative burned footprint that touches the asset, so arrival times have the
frame step (`resolution_h`) as resolution and frame 0 (t = 0) means "already inside the initial footprint".
"""
from __future__ import annotations

import concurrent.futures as cf
import gzip
import hashlib
import json
import math
import os
import time
from pathlib import Path

import numpy as np
import shapely
from shapely.geometry import MultiPolygon, Point, Polygon, shape

from equifire import landscape as _ls

VERSION = 1
CACHE = Path(__file__).resolve().parent.parent / "cache" / "exposure"
HALF_KM_CAP = 40.0
MARGIN_KM = 1.0
SNAP_DEG = 0.05
FAIL_TTL_S = 600
BUILDING_CAP = 400_000          # above this the building query is shrunk, then skipped (size budget)
PERSONS_PER_BUILDING = 2.5      # assumed residents per dwelling-type building (rough proxy)
M_PER_DEG = 111_320.0

LABEL = "ESTIMATED EXPOSURE: an asset inside the simulated burned footprint. Not damage, not loss, not a forecast of casualties."
DISCLAIMER = ("Experimental and uncalibrated wildfire spread simulation; scenario ranges (low/high weather) are "
              "sensitivity scenarios, not confidence intervals. OpenStreetMap completeness varies by region: "
              "counts and lengths are lower bounds where the map is incomplete.")
INSURED_LOSS_NOTE = ("This tool does not estimate insured or monetary loss and never outputs such values. "
                     "Insured-loss modelling needs per-asset exposure values (replacement cost, contents, "
                     "business interruption), vulnerability curves (damage ratio versus fire intensity, "
                     "ember exposure, construction type, defensible space), and policy terms (deductibles, "
                     "limits, exclusions), none of which are available here. Counts below are exposure "
                     "(what could be in the way), not damage.")

PLACE_RADIUS_M = {"city": 1500.0, "town": 700.0, "village": 350.0, "hamlet": 150.0}
FACILITY_RADIUS_M = 75.0
PLACE_RANK = {"city": 0, "town": 1, "village": 2, "hamlet": 3}
ROAD_GROUPS = {"motorway": "major", "motorway_link": "major", "trunk": "major", "trunk_link": "major",
               "primary": "major", "primary_link": "major", "secondary": "secondary",
               "secondary_link": "secondary", "tertiary": "local", "tertiary_link": "local",
               "unclassified": "local"}
RES_BUILDINGS = ("house|residential|detached|semidetached_house|terrace|apartments|bungalow|dormitory|farm|"
                 "cabin|hut|static_caravan")
# order in the response = display priority (most critical first) for the arrival-time list
CLASS_PRIORITY = ["hospital", "school", "fire_station", "power_site", "settlement", "residential_area",
                  "road", "rail", "power_line"]
CLASS_LABELS = {"hospital": "Hospitals", "school": "Schools", "fire_station": "Fire stations",
                "power_site": "Substations / power plants", "settlement": "Settlements (place nodes)",
                "residential_area": "Residential areas (landuse)", "road": "Roads (evacuation-relevant)",
                "rail": "Railways", "power_line": "Power lines", "building": "Buildings (OSM, centres)"}


# ------------------------------------------------------------------ Overpass (reuses landscape mirrors)
def _cache_file(q: str) -> Path:
    return CACHE / ("ov_%s.json.gz" % hashlib.sha1(q.encode()).hexdigest()[:20])


def overpass(q: str, timeout: float = 60.0, fetch=None, budget_s: float = 150.0) -> tuple[dict | None, dict]:
    """POST one Overpass query to landscape.OVERPASS mirrors (in order); disk-cached; failures cached
    FAIL_TTL_S so a busy Overpass is retried later, not on every request. `fetch(url, q, timeout)` is
    injectable for tests and must return a dict or raise."""
    f = _cache_file(q)
    if f.exists():
        try:
            return json.loads(gzip.decompress(f.read_bytes())), {"cached": True}
        except Exception:  # noqa: BLE001
            f.unlink(missing_ok=True)
    fail = f.with_suffix(".fail")
    if fail.exists() and time.time() - fail.stat().st_mtime < FAIL_TTL_S:
        return None, {"cached_failure": True, "errors": [fail.read_text()[:300]]}
    errs, t = [], time.time()
    for url in _ls.OVERPASS:
        if time.time() - t > budget_s:
            errs.append("time budget")
            break
        try:
            if fetch is not None:
                js = fetch(url, q, timeout)
            else:
                import requests
                r = requests.post(url, data={"data": q}, headers=_ls.UA, timeout=timeout)
                if r.status_code != 200:
                    errs.append("%s HTTP %d" % (url, r.status_code))
                    continue
                js = r.json()
            if js.get("remark") and "runtime error" in js["remark"]:
                errs.append("%s %s" % (url, js["remark"][:120]))
                continue
            CACHE.mkdir(parents=True, exist_ok=True)
            f.write_bytes(gzip.compress(json.dumps(js).encode()))
            return js, {"cached": False, "url": url, "seconds": round(time.time() - t, 2)}
        except Exception as ex:  # noqa: BLE001
            errs.append("%s %r" % (url, ex))
    CACHE.mkdir(parents=True, exist_ok=True)
    fail.write_text("; ".join(errs)[:500])
    return None, {"errors": errs}


def assets_query(w, s, e, n) -> str:
    b = "(%.5f,%.5f,%.5f,%.5f)" % (s, w, n, e)
    hw = "|".join(ROAD_GROUPS)
    return ("[out:json][timeout:90][maxsize:268435456];"
            '(node["place"~"^(city|town|village|hamlet)$"]%s;);out body;'
            '(way["landuse"="residential"]%s;);out tags geom;'
            '(way["highway"~"^(%s)$"]%s;);out tags geom;'
            '(way["railway"~"^(rail|light_rail|narrow_gauge)$"]%s;);out tags geom;'
            '(way["power"="line"]%s;);out tags geom;'
            '(nwr["power"~"^(substation|plant)$"]%s;);out body center;'
            '(nwr["amenity"~"^(hospital|school|fire_station)$"]%s;);out body center;'
            % (b, b, hw, b, b, b, b, b))


def poly_string(geom) -> str:
    """Overpass poly filter string ("lat lon lat lon ...", <= ~150 vertices) around a footprint."""
    g = geom.buffer(0.006)
    if g.geom_type == "MultiPolygon":
        g = g.convex_hull
    tol = 0.002
    p = g.simplify(tol)
    while len(p.exterior.coords) > 150:
        tol *= 1.6
        p = g.simplify(tol)
    return " ".join("%.5f %.5f" % (y, x) for x, y in p.exterior.coords[:-1])


def buildings_count_query(poly: str) -> str:
    return '[out:json][timeout:60];way["building"](poly:"%s");out count;' % poly


def buildings_query(poly: str) -> str:
    p = 'poly:"%s"' % poly
    return ("[out:json][timeout:180][maxsize:536870912];"
            'way["building"~"^(%s)$"](%s)->.r;.r out ids center qt;out count;'
            'way["building"="yes"](%s)->.y;.y out ids center qt;out count;'
            'way["building"]["building"!~"^(yes|%s)$"](%s)->.o;.o out ids center qt;'
            % (RES_BUILDINGS, p, p, RES_BUILDINGS, p))


# ------------------------------------------------------------------ parsing
def _local(lat0: float):
    kx = M_PER_DEG * math.cos(math.radians(lat0))
    return kx, M_PER_DEG


def _ellipse(lon: float, lat: float, r_m: float):
    kx, ky = _local(lat)
    from shapely.affinity import scale
    return scale(Point(lon, lat).buffer(1.0, 8), r_m / kx, r_m / ky)


def _name(tags: dict, fallback: str) -> str:
    return tags.get("name") or tags.get("name:en") or tags.get("ref") or fallback


def _coords(el) -> list[tuple[float, float]] | None:
    g = el.get("geometry")
    if g:
        return [(p["lon"], p["lat"]) for p in g]
    return None


def _center(el):
    if "lat" in el:
        return el["lon"], el["lat"]
    c = el.get("center")
    return (c["lon"], c["lat"]) if c else None


def parse_assets(js: dict | None) -> list[dict]:
    """Overpass JSON -> list of asset dicts {cls, subclass, name, osm, geom, point, group}."""
    out = []
    for el in (js or {}).get("elements", []):
        tags = el.get("tags") or {}
        osm = "%s/%s" % (el.get("type"), el.get("id"))
        try:
            if "place" in tags and el.get("type") == "node":
                sub = tags["place"]
                if sub not in PLACE_RADIUS_M:
                    continue
                c = _center(el)
                out.append(dict(cls="settlement", subclass=sub, name=_name(tags, "(unnamed %s)" % sub), osm=osm,
                                geom=_ellipse(c[0], c[1], PLACE_RADIUS_M[sub]), point=c, group=osm,
                                population=tags.get("population")))
            elif tags.get("landuse") == "residential" and el.get("type") == "way":
                cs = _coords(el)
                if not cs or len(cs) < 4:
                    continue
                g = Polygon(cs).buffer(0)
                if g.is_empty:
                    continue
                out.append(dict(cls="residential_area", subclass="residential", osm=osm, geom=g,
                                name=_name(tags, "(unnamed residential area)"), point=(g.centroid.x, g.centroid.y),
                                group=osm))
            elif "highway" in tags and tags["highway"] in ROAD_GROUPS:
                cs = _coords(el)
                if not cs or len(cs) < 2:
                    continue
                grp = ROAD_GROUPS[tags["highway"]]
                nm = tags.get("name") or tags.get("ref")
                out.append(dict(cls="road", subclass=tags["highway"], road_group=grp, osm=osm,
                                geom=shapely.LineString(cs), name=nm or "(unnamed %s road)" % tags["highway"],
                                group="road|%s|%s" % (tags["highway"], nm or "")))
            elif tags.get("railway") in ("rail", "light_rail", "narrow_gauge"):
                cs = _coords(el)
                if not cs or len(cs) < 2:
                    continue
                nm = tags.get("name") or tags.get("ref")
                out.append(dict(cls="rail", subclass=tags["railway"], osm=osm, geom=shapely.LineString(cs),
                                name=nm or "(unnamed railway)", group="rail|%s" % (nm or "")))
            elif tags.get("power") == "line":
                cs = _coords(el)
                if not cs or len(cs) < 2:
                    continue
                nm = tags.get("name") or tags.get("ref")
                out.append(dict(cls="power_line", subclass="line", osm=osm, geom=shapely.LineString(cs),
                                name=nm or "(unnamed power line)", group="pl|%s|%s" % (nm or "", tags.get("operator", ""))))
            elif tags.get("power") in ("substation", "plant"):
                c = _center(el)
                if c:
                    out.append(dict(cls="power_site", subclass=tags["power"], osm=osm,
                                    geom=_ellipse(c[0], c[1], FACILITY_RADIUS_M), point=c, group=osm,
                                    name=_name(tags, "(unnamed %s)" % tags["power"])))
            elif tags.get("amenity") in ("hospital", "school", "fire_station"):
                c = _center(el)
                if c:
                    a = tags["amenity"]
                    out.append(dict(cls=a, subclass=a, osm=osm, geom=_ellipse(c[0], c[1], FACILITY_RADIUS_M),
                                    point=c, group=osm, name=_name(tags, "(unnamed %s)" % a)))
        except Exception:  # noqa: BLE001  one malformed element must not drop the rest
            continue
    return out


def parse_buildings(js: dict | None) -> dict | None:
    """-> {"x","y","kind"(0 residential-type, 1 generic yes, 2 other)} arrays, or None."""
    if not js:
        return None
    xs, ys, kd, k = [], [], [], 0
    for el in js.get("elements", []):
        if el.get("type") == "count":
            k += 1
            continue
        c = _center(el)
        if c:
            xs.append(c[0]); ys.append(c[1]); kd.append(min(k, 2))
    return {"x": np.array(xs, float), "y": np.array(ys, float), "kind": np.array(kd, np.int8)}


# ------------------------------------------------------------------ simulation footprints
def _frames_of(sim: dict) -> list[dict]:
    frames = []
    for i, f in enumerate(sim.get("frames") or []):
        g = f.get("burned")
        geom = shape(g).buffer(0) if g else Polygon()
        frames.append({"index": i, "t_hours": float(f.get("t_hours", i)), "time_utc": f.get("time_utc"),
                       "burned_km2": f.get("burned_km2"), "geom": geom})
    return frames


def _env_geoms(sim: dict) -> dict:
    ep = ((sim.get("summary") or {}).get("envelope_perimeters")) or sim.get("envelope_perimeters") or {}
    out = {}
    for k in ("low", "high"):
        if ep.get(k):
            out[k] = shape(ep[k]).buffer(0)
    return out


def query_bbox(union, centre: tuple[float, float] | None = None, half_km: float = HALF_KM_CAP):
    """bbox (w, s, e, n) of the footprint + margin, snapped outward to SNAP_DEG (cache reuse), capped at
    +-half_km around `centre`. Returns (bbox, capped)."""
    w, s, e, n = union.bounds
    lat0 = (s + n) / 2
    c = centre or ((w + e) / 2, lat0)
    dlat = half_km / 111.32
    dlon = half_km / (111.32 * max(math.cos(math.radians(lat0)), 0.05))
    capped = False
    mlat = MARGIN_KM / 111.32
    mlon = MARGIN_KM / (111.32 * max(math.cos(math.radians(lat0)), 0.05))
    box = [w - mlon, s - mlat, e + mlon, n + mlat]
    lim = [c[0] - dlon, c[1] - dlat, c[0] + dlon, c[1] + dlat]
    for i, (v, l) in enumerate(zip(box, lim)):
        if (i < 2 and v < l) or (i >= 2 and v > l):
            box[i] = l
            capped = True
    sn = SNAP_DEG
    box = [math.floor(box[0] / sn) * sn, math.floor(box[1] / sn) * sn,
           math.ceil(box[2] / sn) * sn, math.ceil(box[3] / sn) * sn]
    return tuple(round(v, 5) for v in box), capped


# ------------------------------------------------------------------ first-hit machinery
def first_frame(n: int, test, n_frames: int, last_ok: np.ndarray | None = None) -> np.ndarray:
    """Smallest frame index at which each of n assets is hit (-1 = never). `test(frame, idx) -> bool array`.
    The burned footprint is cumulative (monotone), so bisection needs ~log2(F) tests per asset."""
    res = np.full(n, -1, int)
    if n == 0 or n_frames == 0:
        return res
    last = np.arange(n)
    ok = test(n_frames - 1, last) if last_ok is None else last_ok
    cand = last[ok]
    lo = np.zeros(len(cand), int)
    hi = np.full(len(cand), n_frames - 1)
    while True:
        act = np.flatnonzero(lo < hi)
        if not len(act):
            break
        mid = (lo[act] + hi[act]) // 2
        for m in np.unique(mid):
            sel = act[mid == m]
            hit = test(int(m), cand[sel])
            hi[sel[hit]] = m
            lo[sel[~hit]] = m + 1
    res[cand] = hi
    return res


def _length_m(geom, lat0: float) -> float:
    kx, ky = _local(lat0)
    g = shapely.transform(geom, lambda c: c * np.array([kx, ky]))
    return float(shapely.length(g))


def _sample_line(geom, lat0: float, step_m: float = 50.0):
    """Points every ~step_m along a (multi)line + the metres each represents."""
    if geom.is_empty:
        return np.zeros((0, 2)), 0.0
    L = _length_m(geom, lat0)
    if L <= 0:
        return np.zeros((0, 2)), 0.0
    seg = step_m / M_PER_DEG
    pts = shapely.get_coordinates(shapely.segmentize(geom, seg))
    return pts, L / max(len(pts), 1)


# ------------------------------------------------------------------ core
def compute_exposure(sim: dict, assets: list[dict], buildings: dict | None = None, pop: dict | None = None,
                     top_n: int = 25, coverage: dict | None = None, now=None, geojson_cap: int = 2500) -> dict:
    """Intersect the simulated footprints with parsed assets. Pure function (no network)."""
    frames = _frames_of(sim)
    if not frames:
        raise ValueError("no frames with burned footprints in the simulation")
    F = len(frames)
    env = _env_geoms(sim)
    central_last = frames[-1]["geom"]
    union = shapely.union_all([central_last, *env.values(), *[fr["geom"] for fr in frames[:1]]])
    if union.is_empty:
        raise ValueError("simulated footprint is empty")
    lat0 = union.centroid.y
    steps = np.diff([fr["t_hours"] for fr in frames])
    res_h = float(np.median(steps)) if len(steps) else 0.0
    T = np.array([fr["t_hours"] for fr in frames])
    for fr in frames:
        shapely.prepare(fr["geom"])
    for g in env.values():
        shapely.prepare(g)

    def geom_test(G):
        def t(fi, idx):
            return shapely.intersects(frames[fi]["geom"], G[idx])
        return t

    def xy_test(x, y):
        def t(fi, idx):
            return shapely.contains_xy(frames[fi]["geom"], x[idx], y[idx])
        return t

    classes: dict[str, dict] = {}
    asset_rows: list[dict] = []        # per hit asset (grouped for lines)
    features: list[dict] = []
    env_counts: dict[str, dict] = {"low": {}, "central": {}, "high": {}}

    def cum(first, w=None):
        """cumulative per-frame totals of `w` (default 1) over assets with first-hit frame f >= 0."""
        m = first >= 0
        v = np.bincount(first[m], weights=None if w is None else w[m], minlength=F).astype(float)
        return np.cumsum(v)

    by_cls: dict[str, list[dict]] = {}
    for a in assets:
        by_cls.setdefault(a["cls"], []).append(a)

    def env_hit(name, G=None, x=None, y=None):
        out = {}
        for k, g in (("central", central_last), *env.items()):
            if x is not None:
                out[k] = int(shapely.contains_xy(g, x, y).sum())
            else:
                out[k] = int(shapely.intersects(g, G).sum())
        return out

    for cls in CLASS_PRIORITY:
        lst = by_cls.get(cls, [])
        info = {"label": CLASS_LABELS[cls], "mapped_in_bbox": len(lst)}
        if not lst:
            info.update(exposed=0, first_exposure=None, cumulative=[0] * F)
            classes[cls] = info
            continue
        if cls in ("road", "rail", "power_line"):
            G = np.array([a["geom"] for a in lst], dtype=object)
            cand = np.flatnonzero(shapely.intersects(union, G))
            clipped = shapely.intersection(G[cand], union)
            pts, wts, owner = [], [], []
            clip_of = dict(zip(cand.tolist(), clipped))
            for j, g in zip(cand, clipped):
                p, w = _sample_line(g, lat0)
                if len(p):
                    pts.append(p); wts.append(np.full(len(p), w)); owner.append(np.full(len(p), j))
            if not pts:
                info.update(exposed=0, first_exposure=None, cumulative=[0.0] * F, length_km_cumulative=[0] * F)
                classes[cls] = info
                continue
            P, W, O = np.vstack(pts), np.concatenate(wts), np.concatenate(owner)
            ff = first_frame(len(P), xy_test(P[:, 0], P[:, 1]), F)
            km = cum(ff, W) / 1000.0
            info["length_km_cumulative"] = [round(float(v), 3) for v in km]
            if cls == "road":
                sub = {}
                for gname in ("major", "secondary", "local"):
                    sel = np.array([lst[j].get("road_group") == gname for j in O], bool)
                    sub[gname] = [round(float(v), 3) for v in cum(np.where(sel, ff, -1), W) / 1000.0]
                info["length_km_cumulative_by_group"] = sub
                info["groups"] = {"major": "motorway/trunk/primary", "secondary": "secondary",
                                  "local": "tertiary/unclassified"}
            hit_total = km[-1]
            info["exposed"] = round(float(hit_total), 2)
            info["unit"] = "km"
            info["cumulative"] = info["length_km_cumulative"]
            # per grouped asset (named road / line): earliest frame + exposed length
            gk: dict[str, dict] = {}
            for j in np.unique(O):
                sel = O == j
                f_ = ff[sel]
                f_ok = f_[f_ >= 0]
                if not len(f_ok):
                    continue
                a = lst[j]
                d = gk.setdefault(a["group"], {"a": a, "first": int(f_ok.min()), "m": 0.0, "n": 0, "geoms": [],
                                               "sub": a.get("road_group")})
                d["first"] = min(d["first"], int(f_ok.min()))
                d["m"] += float(W[sel][f_ >= 0].sum())
                d["n"] += 1
                d["geoms"].append(clip_of[int(j)])
            for d in gk.values():
                a = d["a"]
                fr = d["first"]
                geom = shapely.union_all(d["geoms"]).simplify(0.0003)
                row = {"class": cls, "type": a["subclass"], "name": a["name"], "osm": a["osm"],
                       "first_frame": fr, "arrival_h": round(float(T[fr]), 2), "time_utc": frames[fr]["time_utc"],
                       "exposed_length_km": round(d["m"] / 1000, 2), "segments": d["n"]}
                c = geom.representative_point()
                row["lon"], row["lat"] = round(c.x, 5), round(c.y, 5)
                asset_rows.append(row)
                features.append({"type": "Feature", "geometry": shapely.geometry.mapping(geom),
                                 "properties": {k: row[k] for k in ("class", "type", "name", "first_frame",
                                                                    "arrival_h", "time_utc", "exposed_length_km")}})
            first = ff[ff >= 0]
            info["first_exposure"] = _fe(int(first.min()), frames, T) if len(first) else None
            info["scenario_range_exposed_segments"] = None
        else:
            G = np.array([a["geom"] for a in lst], dtype=object)
            ff = first_frame(len(lst), geom_test(G), F)
            cc = cum(ff)
            info["cumulative"] = [int(v) for v in cc]
            info["exposed"] = int(cc[-1])
            info["unit"] = "count"
            hit = np.flatnonzero(ff >= 0)
            info["first_exposure"] = _fe(int(ff[hit].min()), frames, T) if len(hit) else None
            eh = env_hit(cls, G=G)
            env_counts["central"][cls], env_counts["low"][cls], env_counts["high"][cls] = (
                eh["central"], eh.get("low"), eh.get("high"))
            info["scenario_range"] = {"low": eh.get("low"), "central": eh["central"], "high": eh.get("high")}
            unnamed_res = []
            for j in hit:
                a = lst[j]
                fr = int(ff[j])
                if cls == "residential_area" and a["name"].startswith("(unnamed"):
                    unnamed_res.append(j)           # aggregated below, never listed one by one
                    continue
                row = {"class": cls, "type": a["subclass"], "name": a["name"], "osm": a["osm"], "first_frame": fr,
                       "arrival_h": round(float(T[fr]), 2), "time_utc": frames[fr]["time_utc"],
                       "lon": round(a["point"][0], 5), "lat": round(a["point"][1], 5)}
                if a.get("population"):
                    row["osm_population_tag"] = a["population"]
                asset_rows.append(row)
                if cls == "residential_area":
                    gg = shapely.geometry.mapping(a["geom"].simplify(0.0003))
                else:
                    gg = {"type": "Point", "coordinates": [row["lon"], row["lat"]]}
                features.append({"type": "Feature", "geometry": gg,
                                 "properties": {k: row[k] for k in ("class", "type", "name", "first_frame",
                                                                    "arrival_h", "time_utc")}})
            if unnamed_res:
                j0 = min(unnamed_res, key=lambda j: ff[j])
                a = lst[j0]
                fr = int(ff[j0])
                asset_rows.append({"class": cls, "type": "residential", "name": "%d unnamed residential areas (aggregated)"
                                   % len(unnamed_res), "osm": a["osm"], "first_frame": fr, "arrival_h": round(float(T[fr]), 2),
                                   "time_utc": frames[fr]["time_utc"], "lon": round(a["point"][0], 5),
                                   "lat": round(a["point"][1], 5), "count": len(unnamed_res)})
                for j in sorted(unnamed_res, key=lambda j: ff[j])[:600]:
                    a = lst[j]
                    features.append({"type": "Feature", "geometry": shapely.geometry.mapping(a["geom"].simplify(0.0003)),
                                     "properties": {"class": cls, "type": "residential", "name": a["name"],
                                                    "first_frame": int(ff[j]), "arrival_h": round(float(T[int(ff[j])]), 2),
                                                    "time_utc": frames[int(ff[j])]["time_utc"]}})
            # assets only reached in the high-weather scenario (not by the central run)
            if "high" in env:
                hh = shapely.intersects(env["high"], G) & (ff < 0)
                info["only_in_high_scenario"] = int(hh.sum())
                for j in [j for j in np.flatnonzero(hh) if not lst[j]["name"].startswith("(unnamed")][:15]:
                    a = lst[j]
                    asset_rows.append({"class": cls, "type": a["subclass"], "name": a["name"], "osm": a["osm"],
                                       "first_frame": None, "arrival_h": None, "time_utc": None,
                                       "scenario": "high weather scenario only",
                                       "lon": round(a["point"][0], 5), "lat": round(a["point"][1], 5)})
        classes[cls] = info

    # buildings ------------------------------------------------------------------------------------
    b_out = None
    if buildings is not None and len(buildings["x"]):
        x, y, kind = buildings["x"], buildings["y"], buildings["kind"]
        m = (x >= union.bounds[0]) & (x <= union.bounds[2]) & (y >= union.bounds[1]) & (y <= union.bounds[3])
        m &= shapely.contains_xy(union, x, y)
        sub = np.flatnonzero(m)
        ffs = first_frame(len(sub), xy_test(x[sub], y[sub]), F)
        ff = np.full(len(x), -1, int)
        ff[sub] = ffs
        ks = [kind == 0, kind == 1, kind == 2]
        cum_k = [cum(np.where(k_, ff, -1)) for k_ in ks]
        tot = sum(cum_k)
        b_out = {"label": CLASS_LABELS["building"], "mapped_in_bbox": int(len(x)),
                 "exposed": int(tot[-1]), "unit": "count", "cumulative": [int(v) for v in tot],
                 "by_type": {"residential_type": [int(v) for v in cum_k[0]],
                             "generic_building_yes": [int(v) for v in cum_k[1]],
                             "other": [int(v) for v in cum_k[2]]},
                 "first_exposure": _fe(int(ff[ff >= 0].min()), frames, T) if (ff >= 0).any() else None,
                 "note": "centres of OSM building ways inside the footprint; buildings are aggregated, not listed"}
        eh = {k: int(shapely.contains_xy(g, x, y).sum()) for k, g in (("central", central_last), *env.items())}
        b_out["scenario_range"] = {"low": eh.get("low"), "central": eh["central"], "high": eh.get("high")}
        classes["building"] = b_out
        # ~500 m cells for the map
        hitm = ff >= 0
        cx = np.round(x[hitm] / 0.005).astype(np.int64)
        cy = np.round(y[hitm] / 0.005).astype(np.int64)
        key = cx * 1_000_000 + cy
        order = np.argsort(key)
        uk, start_ix = np.unique(key[order], return_index=True)
        groups = np.split(np.arange(len(order)), start_ix[1:])
        cells = []
        fhit = ff[hitm][order]
        for g in groups:
            ii = order[g]
            cells.append((len(g), int(fhit[g].min()), float(cx[ii[0]] * 0.005), float(cy[ii[0]] * 0.005)))
        cells.sort(reverse=True)
        for n_, fr, lo, la in cells[:1500]:
            features.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [round(lo, 4), round(la, 4)]},
                             "properties": {"class": "building_cell", "buildings": n_, "first_frame": fr,
                                            "arrival_h": round(float(T[fr]), 2), "time_utc": frames[fr]["time_utc"],
                                            "cell_deg": 0.005}})
        b_out["map_cells"] = min(len(cells), 1500)

    # population (separate from everything else) ----------------------------------------------------
    population = _population(pop, b_out, buildings, union, frames, F, T, xy_test, first_frame, cum, central_last, env)

    # ordered arrival list ---------------------------------------------------------------------------
    pr = {c: i for i, c in enumerate(CLASS_PRIORITY)}

    def sk(r):
        return (1 if r["arrival_h"] is None else 0, r["arrival_h"] if r["arrival_h"] is not None else 0,
                pr[r["class"]], PLACE_RANK.get(r["type"], 9), r["name"])
    asset_rows.sort(key=sk)
    arrivals = [r for r in asset_rows if r["arrival_h"] is not None][:top_n]
    scenario_only = [r for r in asset_rows if r["arrival_h"] is None][:top_n]
    if len(features) > geojson_cap:
        features = sorted(features, key=lambda f: (pr.get(f["properties"]["class"], 99), f["properties"]["first_frame"]))[:geojson_cap]

    per_frame = []
    for i, fr in enumerate(frames):
        d = {"index": i, "t_hours": fr["t_hours"], "time_utc": fr["time_utc"], "burned_km2": fr["burned_km2"]}
        for c, info in classes.items():
            if c == "building" and info.get("cumulative"):
                d["buildings"] = info["cumulative"][i]
            elif c in ("road", "rail", "power_line"):
                d[c + "_km"] = info["cumulative"][i]
            else:
                d[c] = info["cumulative"][i]
        if population.get("buildings_proxy"):
            d["people_proxy"] = population["buildings_proxy"]["central_cumulative"][i]
        if (population.get("hrsl") or {}).get("cumulative"):
            d["people_hrsl"] = population["hrsl"]["cumulative"][i]
        per_frame.append(d)

    first_exposure = {c: info.get("first_exposure") for c, info in classes.items()}
    summary = {c: ({"exposed": info["exposed"], "unit": info.get("unit", "count"),
                    "first_exposure": info.get("first_exposure"), "mapped_in_bbox": info["mapped_in_bbox"],
                    "scenario_range": info.get("scenario_range")}) for c, info in classes.items()}
    for c in classes.values():  # lists duplicated in per_frame; drop from class blocks to keep the payload small
        c.pop("cumulative", None)
    bbox = tuple(round(v, 4) for v in union.bounds)
    return {
        "label": LABEL, "disclaimer": DISCLAIMER, "version": VERSION,
        "kind": "estimated_exposure", "monetary_values": None, "insured_loss_note": INSURED_LOSS_NOTE,
        "query": sim.get("query"), "ignition": {k: (sim.get("ignition") or {}).get(k) for k in
                                                 ("mode", "estimate", "fallback_reason", "n_detections", "latest_utc",
                                                  "satellites", "points")},
        "resolution_h": res_h, "n_frames": F, "footprint_bbox": bbox,
        "footprint_km2": {"central_final": frames[-1]["burned_km2"],
                          "envelope": (sim.get("summary") or {}).get("envelope_km2")},
        "coverage": coverage or {"complete": None},
        "summary": summary, "first_exposure": first_exposure, "classes": classes,
        "arrivals": arrivals, "scenario_only_assets": scenario_only, "per_frame": per_frame,
        "population": population, "assets_geojson": {"type": "FeatureCollection", "features": features},
        "estimate": True,
    }


def _fe(fr: int, frames, T) -> dict:
    return {"frame": fr, "hours_from_start": round(float(T[fr]), 2), "time_utc": frames[fr]["time_utc"]}


def _population(pop, b_out, buildings, union, frames, F, T, xy_test, first_frame, cum, central_last, env):
    out = {"label": "ESTIMATED POPULATION EXPOSURE (separate from any loss figure)",
           "note": ("People inside the simulated footprint, not casualties or evacuees; static gridded or proxy "
                    "estimates, no time-of-day or occupancy model. Not insured loss."),
           "hrsl": None, "buildings_proxy": None}
    if pop and pop.get("x") is not None and len(pop["x"]):
        x, y, v = pop["x"], pop["y"], pop["pop"]
        m = shapely.contains_xy(union, x, y)
        sub = np.flatnonzero(m)
        ffs = first_frame(len(sub), xy_test(x[sub], y[sub]), F)
        ff = np.full(len(x), -1, int)
        ff[sub] = ffs
        c = cum(ff, v)
        sc = {k: float(v[shapely.contains_xy(g, x, y)].sum()) for k, g in (("central", central_last), *env.items())}
        out["hrsl"] = {"source": pop.get("source"), "cell_m": pop.get("cell_m"), "vintage": pop.get("vintage"),
                       "exposed": int(round(c[-1], -1)) if c[-1] > 100 else int(round(c[-1])),
                       "cumulative": [int(round(float(t_))) for t_ in c],
                       "scenario_range": {k: int(round(sc[k])) for k in sc},
                       "window_total": int(round(float(v.sum()))), "note": pop.get("note")}
    elif pop is not None:
        out["hrsl_unavailable"] = pop.get("reason") or "no HRSL population in the window (not covered or empty)"
    if b_out is not None:
        by = b_out["by_type"]
        res = np.array(by["residential_type"], float)
        gen = np.array(by["generic_building_yes"], float)
        pp = PERSONS_PER_BUILDING
        low, mid, high = pp * res, pp * (res + 0.5 * gen), pp * (res + gen)
        out["buildings_proxy"] = {
            "persons_per_building_assumed": pp,
            "method": ("residents ~ (residential-type buildings + w x generic building=yes) x %.1f persons; w = 0 "
                       "(low), 0.5 (central), 1 (high); other building types (shops, sheds, industry) count 0. "
                       "ROUGH PROXY: ignores multi-storey density, empty/seasonal dwellings and unmapped buildings." % pp),
            "central": int(round(float(mid[-1]), -1)), "low": int(round(float(low[-1]), -1)),
            "high": int(round(float(high[-1]), -1)),
            "central_cumulative": [int(round(float(t_), -1)) for t_ in mid]}
    return out


# ------------------------------------------------------------------ orchestration
def _pop_cache(bbox):
    return CACHE / ("pop_%s.npz" % hashlib.sha1(json.dumps([VERSION, [round(v, 3) for v in bbox]]).encode()).hexdigest()[:16])


HRSL_VRT = "https://dataforgood-fb-data.s3.amazonaws.com/hrsl-cogs/hrsl_general/hrsl_general-latest.vrt"


def fetch_population(bbox, block: int = 5, read_fn=None) -> dict:
    """Meta/CIESIN HRSL people-per-30 m-pixel, summed into block x block cells (default 150 m); windowed reads of
    the public VRT/COGs (no key). Returns {"x","y","pop",...} or {"reason": ...}. Cached per bbox."""
    f = _pop_cache(bbox)
    if f.exists():
        z = np.load(f, allow_pickle=False)
        d = {k: z[k] for k in z.files}
        meta = json.loads(str(d.pop("meta")))
        return {**meta, **d} if len(d.get("x", [])) else meta
    t = time.time()
    meta = {"source": "Meta / CIESIN High Resolution Settlement Layer (HRSL) via AWS open data",
            "vintage": "HRSL v1.x, built from 2015-2020 imagery + census (static)", "cell_m": 30 * block,
            "note": "Not available everywhere (no HRSL over much of Canada); sums are modelled, not a census."}
    try:
        if read_fn is None:
            import rasterio
            from rasterio.windows import from_bounds
            w, s, e, n = bbox
            xs, ys, vs = [], [], []
            with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", GDAL_HTTP_MAX_RETRY="2",
                              GDAL_HTTP_TIMEOUT="30", AWS_NO_SIGN_REQUEST="YES", GDAL_NUM_THREADS="4"):
                with rasterio.open(HRSL_VRT) as d:
                    win = from_bounds(w, s, e, n, d.transform).round_offsets().round_lengths()
                    r0, c0, H, W = int(win.row_off), int(win.col_off), int(win.height), int(win.width)
                    step = 600 * block
                    res = d.res[0]
                    for r in range(0, H, step):
                        hh = min(step, H - r)
                        a = d.read(1, window=((r0 + r, r0 + r + hh), (c0, c0 + W)), masked=True)
                        a = np.ma.filled(a.astype("float32"), 0.0)
                        a = np.nan_to_num(a)
                        hb, wb = hh // block, W // block
                        if hb == 0 or wb == 0:
                            continue
                        bsum = a[:hb * block, :wb * block].reshape(hb, block, wb, block).sum((1, 3))
                        ii, jj = np.nonzero(bsum > 0)
                        lon = d.transform.c + (c0 + (jj + 0.5) * block) * res
                        lat = d.transform.f - (r0 + r + (ii + 0.5) * block) * res
                        xs.append(lon); ys.append(lat); vs.append(bsum[ii, jj])
            x = np.concatenate(xs) if xs else np.zeros(0)
            y = np.concatenate(ys) if ys else np.zeros(0)
            v = np.concatenate(vs) if vs else np.zeros(0)
        else:
            x, y, v = read_fn(bbox, block)
    except Exception as ex:  # noqa: BLE001
        return {"reason": "HRSL read failed: %r" % (ex,)}
    meta["seconds"] = round(time.time() - t, 2)
    if not len(x):
        meta["reason"] = "no HRSL population in the window (not covered, or unpopulated)"
    CACHE.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(f, meta=np.array(json.dumps(meta)), x=x.astype("float64"), y=y.astype("float64"),
                        pop=v.astype("float32"))
    return {**meta, "x": x, "y": y, "pop": v} if len(x) else meta


def collect_assets(sim: dict, fetch=None, pop_fn=None, buildings: bool = True, population: bool = True):
    """Fetch OSM assets (+ buildings, + population) for the sim footprint. -> (assets, bld, pop, coverage)."""
    frames = _frames_of(sim)
    env = _env_geoms(sim)
    union = shapely.union_all([*(fr["geom"] for fr in frames), *env.values()])
    c = sim.get("grid", {}).get("center")
    bbox, capped = query_bbox(union, tuple(c) if c else None)
    cov = {"bbox": list(bbox), "bbox_capped_at_km": HALF_KM_CAP if capped else None, "osm": {}, "complete": True,
           "osm_note": ("OpenStreetMap completeness varies by region; unmapped assets are not counted. "
                        "Overpass can be slow or unavailable; failed queries are listed here.")}
    # clip footprint used for the building polygon filter to the capped bbox
    from shapely.geometry import box as _box
    clipped_union = union.intersection(_box(*bbox))
    clipped_union = clipped_union if not clipped_union.is_empty else union
    final_c = frames[-1]["geom"]

    def a_job():
        return overpass(assets_query(*bbox), fetch=fetch)

    def b_job():
        poly = poly_string(clipped_union)
        cnt, info = overpass(buildings_count_query(poly), timeout=60, fetch=fetch, budget_s=90)
        n = None
        if cnt is not None:
            try:
                n = int(cnt["elements"][0]["tags"]["total"])
            except Exception:  # noqa: BLE001
                n = None
        if cnt is None:
            return None, {**info, "stage": "count"}, None
        if n is not None and n > BUILDING_CAP:
            # shrink to the central final footprint + 2 km, then give up if still too large
            g2 = final_c.buffer(0.02).intersection(_box(*bbox))
            poly = poly_string(g2) if not g2.is_empty else poly
            cnt2, i2 = overpass(buildings_count_query(poly), timeout=60, fetch=fetch, budget_s=90)
            n2 = int(cnt2["elements"][0]["tags"]["total"]) if cnt2 else None
            if n2 is None or n2 > BUILDING_CAP:
                return None, {"errors": ["building count %s above size budget %d" % (n2 or n, BUILDING_CAP)],
                              "stage": "budget"}, n
            return (*overpass(buildings_query(poly), fetch=fetch), n2)
        js, info = overpass(buildings_query(poly), fetch=fetch)
        return js, info, n

    t0 = time.time()
    with cf.ThreadPoolExecutor(3) as ex:
        fa = ex.submit(a_job)
        fb = ex.submit(b_job) if buildings else None
        fp = ex.submit(lambda: (pop_fn or fetch_population)(bbox)) if population else None
        ajs, ainfo = fa.result()
        bjs = binfo = bn = None
        if fb:
            bjs, binfo, bn = fb.result()
        pop = fp.result() if fp else None
    assets = parse_assets(ajs)
    cov["osm"]["assets"] = {"ok": ajs is not None, **{k: v for k, v in ainfo.items() if k in ("cached", "errors", "seconds", "url")}}
    if ajs is None:
        cov["complete"] = False
    if buildings:
        cov["osm"]["buildings"] = {"ok": bjs is not None, "count_estimate": bn,
                                   **{k: v for k, v in (binfo or {}).items() if k in ("cached", "errors", "seconds", "stage")}}
        if bjs is None:
            cov["complete"] = False
    if ajs is not None:
        ts = (ajs.get("osm3s") or {}).get("timestamp_osm_base")
        cov["osm_timestamp"] = ts
    cov["seconds"] = round(time.time() - t0, 2)
    return assets, parse_buildings(bjs), pop, cov


def run_exposure(sim: dict, *, fetch=None, pop_fn=None, buildings: bool = True, population: bool = True,
                 top_n: int = 25) -> dict:
    assets, bld, pop, cov = collect_assets(sim, fetch=fetch, pop_fn=pop_fn, buildings=buildings, population=population)
    out = compute_exposure(sim, assets, bld, pop, top_n=top_n, coverage=cov)
    out["timings"] = {"assets_fetch_s": cov.get("seconds")}
    return out


def default_sim(lat, lon, start, hours, step_min, ignition, fuel, radius_km, state=None):
    from backend import physics_spread as ps
    return ps.forecast(lat, lon, start, hours, step_min, ignition=ignition, fuel=fuel, envelope=True, frames=True,
                       radius_km=radius_km, calibration="on", state=state, weather="grid", goes="off", fwi=False,
                       landscape="on")


_CACHE: dict = {}


def exposure_for(lat, lon, start=None, hours=48, step_min=60, ignition="live", fuel="auto", radius_km=30.0,
                 buildings=True, population=True, top_n=25, sim_fn=None, state=None, fetch=None, pop_fn=None,
                 return_sim=False):
    """Run (or reuse) the simulation, then the exposure. In-memory cache 30 min; Overpass has a disk cache."""
    key = (round(lat, 4), round(lon, 4), str(start), hours, step_min, ignition, fuel, round(radius_km, 2),
           buildings, population, top_n)
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < 1800 and fetch is None:
        return hit[1] if return_sim else hit[1][0]
    t = time.time()
    sim = (sim_fn or default_sim)(lat, lon, start, hours, step_min, ignition, fuel, radius_km, state)
    t_sim = time.time() - t
    out = run_exposure(sim, fetch=fetch, pop_fn=pop_fn, buildings=buildings, population=population, top_n=top_n)
    out["timings"]["simulation_s"] = round(t_sim, 2)
    out["timings"]["total_s"] = round(time.time() - t, 2)
    if fetch is None:
        if len(_CACHE) > 8:
            _CACHE.pop(next(iter(_CACHE)))
        _CACHE[key] = (time.time(), (out, sim))
    return (out, sim) if return_sim else out


# ------------------------------------------------------------------ FastAPI router
from typing import Annotated  # noqa: E402

from fastapi import Query as _Q  # noqa: E402

Lat = Annotated[float, _Q(ge=-90, le=90, description="latitude, degrees")]
Lon = Annotated[float, _Q(ge=-180, le=180, description="longitude, degrees")]

def _router():
    from fastapi import APIRouter, Body, HTTPException, Query
    r = APIRouter()

    def _state():
        try:
            from backend import app as _app
            return _app.store.state, _app
        except Exception:  # noqa: BLE001
            return None, None

    @r.get("/exposure", tags=["exposure"],
           summary="ESTIMATED exposure of mapped assets to the simulated burned footprint (not damage/loss)")
    def exposure_get(lat: Lat, lon: Lon,
                     start: str | None = Query(None, description="YYYY-MM-DDTHH:MM UTC, default now"),
                     hours: int = Query(48, ge=1, le=168), step_min: int = Query(60, ge=10, le=720),
                     ignition: str = Query("live", description="live | point | archive"),
                     fuel: str = Query("auto"), radius_km: float = Query(30.0, ge=0.5, le=60.0),
                     buildings: bool = Query(True, description="also count OSM buildings (can be slow)"),
                     population: bool = Query(True, description="optional, separate population estimate"),
                     top_n: int = Query(25, ge=1, le=200)) -> dict:
        if ignition not in ("point", "live", "archive"):
            raise HTTPException(400, "ignition must be point, live or archive")
        state, app = _state()

        def sim_fn(lat_, lon_, start_, hours_, step_, ign_, fuel_, rad_, st_):
            # reuse the /simulate cache of the main app when available
            if app is not None:
                return app.simulate_get(lat=lat_, lon=lon_, start=start_, hours=hours_, step_min=step_, ignition=ign_,
                                        fuel=fuel_, envelope=True, frames=True, radius_km=rad_, calibration="on",
                                        weather="grid", goes="off", fwi=False, landscape="on")
            return default_sim(lat_, lon_, start_, hours_, step_, ign_, fuel_, rad_, st_)
        try:
            return exposure_for(lat, lon, start, hours, step_min, ignition, fuel, radius_km, buildings, population,
                                top_n, sim_fn=sim_fn, state=state)
        except HTTPException:
            raise
        except ValueError as e:
            raise HTTPException(404, "no simulated footprint to intersect: %s" % e) from None

    @r.post("/exposure", tags=["exposure"],
            summary="Exposure for an already-run simulation (/simulate or /predict/simulate response)")
    def exposure_post(body: dict = Body(..., description='a /simulate response, or {"frames":[{"t_hours","time_utc",'
                                                          '"burned": GeoJSON}], "summary": {"envelope_perimeters": '
                                                          '{"low":..,"high":..}}}'),
                      buildings: bool = Query(True), population: bool = Query(True),
                      top_n: int = Query(25, ge=1, le=200)) -> dict:
        sim = body.get("simulation", body)
        if not sim.get("frames"):
            raise HTTPException(400, "body needs frames[] with burned GeoJSON per frame")
        try:
            return run_exposure(sim, buildings=buildings, population=population, top_n=top_n)
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
    return r


router = _router()
