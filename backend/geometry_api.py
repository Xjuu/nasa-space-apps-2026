"""Goal 2 endpoints: detection view geometry and footprint-adaptive (bow-tie aware) clusters.

    GET /geometry?lat=-10&lon=-55&time=2026-11-13T07:47:00Z&sat=Aqua[&scan=2.2&track=1.4&daynight=N]
    GET /clusters?date=2023-09-05&bbox=-56,-12,-54,-10[&sensor=all|modis|viirs][&method=adaptive][&country=Brazil]

/geometry uses SGP4 with aux/tle/*.tle when `time` is within 3 days of the TLE epoch, else inverts the
FIRMS pixel size (`scan`/`track`), else nadir; the answer says which (`geom_source`).

/clusters reads the raw FIRMS archive CSVs in data_country/<product>/<year>/ for the countries whose
detection bbox (built once per year from the MODIS files, cached in cache/bowtie_country_bbox_<year>.json)
intersects the query bbox. Parsed files are cached in memory (small LRU of compact frames).
Clusters are per overpass (same satellite, date, acq_time within 10 min); see equifire/clusters.py.
"""
from __future__ import annotations

import datetime as dt
import json
import math
from collections import OrderedDict
from pathlib import Path
from threading import Lock

import numpy as np
import pandas as pd
from fastapi import APIRouter, HTTPException, Query

from equifire import clusters as C
from equifire import geometry as G
from equifire.common import CACHE, DATA_DIRS

router = APIRouter(tags=["geometry"])
PRODUCTS = {"modis": "modis", "viirs": ("viirs-snpp", "viirs-jpss1")}
MAX_FILES = 12
FILE_CACHE_SIZE = 6
_files: OrderedDict = OrderedDict()
_lock = Lock()
_bbox_mem: dict[int, dict] = {}


def _data_root() -> Path:
    return DATA_DIRS[0]


def _country_bboxes(year: int) -> dict[str, list[float]]:
    """country -> [minlon, minlat, maxlon, maxlat] of its MODIS detections that year (+0.5 deg pad)."""
    if year in _bbox_mem:
        return _bbox_mem[year]
    f = CACHE / f"bowtie_country_bbox_{year}.json"
    if f.exists():
        _bbox_mem[year] = json.loads(f.read_text())
        return _bbox_mem[year]
    out = {}
    for p in sorted((_data_root() / "modis" / str(year)).glob(f"modis_{year}_*.csv")):
        country = p.stem[len(f"modis_{year}_"):]
        try:
            d = pd.read_csv(p, usecols=["latitude", "longitude"], dtype=np.float32)
        except (ValueError, pd.errors.EmptyDataError):
            continue
        if len(d):
            out[country] = [round(float(d.longitude.min()) - .5, 3), round(float(d.latitude.min()) - .5, 3),
                            round(float(d.longitude.max()) + .5, 3), round(float(d.latitude.max()) + .5, 3)]
    if out:
        CACHE.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(out))
    _bbox_mem[year] = out
    return out


def _load_file(p: Path) -> pd.DataFrame:
    key = (str(p), p.stat().st_mtime)
    with _lock:
        if key in _files:
            _files.move_to_end(key)
            return _files[key]
    df = C.load_firms(p)
    with _lock:
        _files[key] = df
        while len(_files) > FILE_CACHE_SIZE:
            _files.popitem(last=False)
    return df


def _files_for(year: int, box, sensor: str, country: str | None) -> list[Path]:
    root = _data_root()
    if country:
        names = [country.replace(" ", "_")]
    else:
        bb = _country_bboxes(year)
        names = [c for c, b in bb.items() if b[0] <= box[2] and b[2] >= box[0] and b[1] <= box[3] and b[3] >= box[1]]
    prods = []
    if sensor in ("all", "modis"):
        prods.append("modis")
    if sensor in ("all", "viirs"):
        prods += ["viirs-snpp", "viirs-jpss1"]
    paths = [root / pr / str(year) / f"{pr}_{year}_{n}.csv" for n in names for pr in prods]
    return [p for p in paths if p.exists()]


def _circle(lon, lat, r_km, n=24):
    out = []
    for k in range(n + 1):
        a = 2 * math.pi * k / n
        out.append([round(lon + r_km * math.sin(a) / (C.KM_PER_DEG * max(math.cos(math.radians(lat)), 1e-3)), 5),
                    round(lat + r_km * math.cos(a) / C.KM_PER_DEG, 5)])
    return [out]


def clusters_geojson(det: pd.DataFrame, method: str = "adaptive", polygons: bool = False) -> dict:
    if len(det) == 0:
        return {"type": "FeatureCollection", "features": [], "n_detections": 0, "n_clusters": 0}
    det = det.reset_index(drop=True)
    gid = C.overpass_ids(det)
    lab = C.cluster(det, method, gid=gid)
    base = C.cluster(det, "fixed_nadir", gid=gid)
    cl = C.summarize(det, lab)
    # cluster radius: farthest member from the centroid + half its largest footprint axis
    clat, clon = cl.latitude.to_numpy()[lab], cl.longitude.to_numpy()[lab]
    dn = (det.latitude.to_numpy() - clat) * C.KM_PER_DEG
    de = (det.longitude.to_numpy() - clon) * C.KM_PER_DEG * np.cos(np.radians(clat))
    reach = np.hypot(dn, de) + np.maximum(det.along_scan_km.to_numpy(float), det.along_track_km.to_numpy(float)) / 2
    radius = pd.Series(reach).groupby(lab).max().to_numpy()
    sats = det.sat_key.astype(str).to_numpy()
    inst = pd.Series(np.where(np.isin(sats, ["terra", "aqua"]), "MODIS", "VIIRS")).groupby(lab).first().to_numpy()
    feats = []
    for k, r in enumerate(cl.itertuples(index=False)):
        lon, lat = float(r.longitude), float(r.latitude)
        geom = ({"type": "Polygon", "coordinates": _circle(lon, lat, float(radius[k]))} if polygons
                else {"type": "Point", "coordinates": [round(lon, 5), round(lat, 5)]})
        feats.append({"type": "Feature", "geometry": geom, "properties": {
            "n_detections": int(r.n), "satellites": r.satellites, "instrument": inst[k],
            "frp_sum": round(float(r.frp_sum), 2), "scan_angle_deg": round(float(r.scan_deg), 1),
            "view_zenith_deg": round(float(G.view_zenith(r.scan_deg, 705.0 if inst[k] == "MODIS" else 833.0)), 1),
            "max_pixel_km": [round(float(r.along_scan_km), 2), round(float(r.along_track_km), 2)],
            "radius_km": round(float(radius[k]), 3), "acq_date": str(r.acq_date),
            "acq_time": f"{int(r.acq_time):04d}", "geom_source": "pixel_size",
            "centroid": [round(lon, 5), round(lat, 5)],
        }})
    return {"type": "FeatureCollection", "features": feats, "method": method,
            "n_detections": int(len(det)), "n_clusters": int(len(cl)),
            "n_clusters_fixed_nadir": int(base.max() + 1), "n_overpasses": int(gid.max() + 1)}


@router.get("/geometry")
def geometry(lat: float = Query(..., ge=-90, le=90), lon: float = Query(..., ge=-180, le=180),
             time: str = Query(..., description="ISO 8601 UTC, e.g. 2026-11-13T07:47:00Z"),
             sat: str = Query(..., description="Terra | Aqua | N (Suomi NPP) | N20 | N21 (FIRMS codes or names)"),
             scan: float | None = Query(None, gt=0, le=10, description="FIRMS along-scan pixel size, km"),
             track: float | None = Query(None, gt=0, le=10, description="FIRMS along-track pixel size, km"),
             daynight: str | None = Query(None, pattern="^[DNdn]$")) -> dict:
    """View zenith, scan angle, slant range, pixel footprint ellipse and `geom_source` (sgp4 | pixel_size | model)."""
    try:
        t = dt.datetime.fromisoformat(time.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(400, f"bad time {time!r}: expected ISO 8601") from None
    try:
        s = G.sat_of(sat)
    except KeyError as e:
        raise HTTPException(400, str(e.args[0])) from None
    return G.detection_geometry(lat, lon, t, s, scan, track, daynight.upper() if daynight else None)


@router.get("/clusters")
def clusters(date: str = Query(..., description="YYYY-MM-DD (UTC acq_date)"),
             bbox: str = Query(..., description="minlon,minlat,maxlon,maxlat (max 10 x 10 deg)"),
             sensor: str = Query("all", pattern="^(all|modis|viirs)$"),
             method: str = Query("adaptive", pattern="^(adaptive|adaptive_iso|fixed_nadir|fixed_2km)$"),
             country: str | None = Query(None, description="restrict to one data_country file name, e.g. Brazil"),
             shape: str = Query("point", pattern="^(point|polygon)$")) -> dict:
    """Footprint-adaptive hot-spot clusters (GeoJSON) from the raw FIRMS archive for one day and box."""
    try:
        d = dt.date.fromisoformat(date)
    except ValueError:
        raise HTTPException(400, f"bad date {date!r}: expected YYYY-MM-DD") from None
    try:
        box = [float(v) for v in bbox.split(",")]
        assert len(box) == 4 and box[0] < box[2] and box[1] < box[3]
        assert -180 <= box[0] and box[2] <= 180 and -90 <= box[1] and box[3] <= 90
    except (ValueError, AssertionError):
        raise HTTPException(400, "bbox must be minlon,minlat,maxlon,maxlat") from None
    if box[2] - box[0] > 10 or box[3] - box[1] > 10:
        raise HTTPException(400, "bbox larger than 10 x 10 deg")
    files = _files_for(d.year, box, sensor, country)
    if len(files) > MAX_FILES:
        raise HTTPException(400, f"bbox touches {len(files)} country files (> {MAX_FILES}); use a smaller bbox or `country`")
    parts = []
    for p in files:
        df = _load_file(p)
        sel = (df.acq_date.astype(str) == d.isoformat()) & df.longitude.between(box[0], box[2]) \
            & df.latitude.between(box[1], box[3])
        if sel.any():
            parts.append(df[sel])
    det = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
    if len(det):   # border regions appear in two country files
        det = det.drop_duplicates(["latitude", "longitude", "acq_time", "sat_key"])
        for c in ("sat_key", "satellite", "acq_date"):
            det[c] = det[c].astype(str)
    out = clusters_geojson(det, method, shape == "polygon")
    out.update(date=d.isoformat(), bbox=box, sensor=sensor,
               files=[str(p.relative_to(_data_root())) for p in files])
    if not files:
        out["note"] = f"no data_country files for {d.year} intersect this bbox"
    return out
