"""Extra data (#9): where the MODIS -> VIIRS-equivalent reconstruction disagrees with real VIIRS, by 1-degree tile.

Test years 2022-2025 (the reconstruction model was trained on 2012-2019, threshold chosen on 2020), tiles where the reconstruction
exists (the 5 processed countries). Per tile: real VIIRS cell-days, reconstructed cell-days, cells found by both.
  ratio     = reconstructed / real cell-days   (1 = same number of cell-days; >1 the reconstruction over-predicts)
  recall    = found by both / real
  precision = found by both / reconstructed
It does NOT split by scan angle or time of day (those fields are not kept in the daily cache) and says nothing about FRP per cell.
This is auxiliary data, not a forecast result.
Endpoint: GET /extra/disagreement
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
from fastapi import APIRouter, HTTPException

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "cache" / "extradata" / "disagreement.json"
TILE = 20     # cells = 1 degree
GRID = 0.05


def _d(y):
    return int((np.datetime64(f"{y}-01-01") - np.datetime64("2000-01-01")).astype(int))


def build(y0=2022, y1=2025, min_real=200) -> dict:
    import pandas as pd
    t = time.time()
    f = [("day", ">=", _d(y0)), ("day", "<", _d(y1 + 1))]
    eq = pd.read_parquet(ROOT / "cache" / "viirs_equiv_daily.parquet", columns=["day", "gx", "gy", "frp"], filters=f)
    vr = pd.read_parquet(ROOT / "cache" / "viirs_daily.parquet", columns=["day", "gx", "gy", "frp"], filters=f)
    key = lambda d: d["day"].to_numpy(np.int64) * 10**8 + d["gx"].to_numpy(np.int64) * 10**4 + d["gy"].to_numpy(np.int64)
    ek, vk = key(eq), key(vr)
    both = np.isin(ek, vk)
    et = (eq["gx"].to_numpy(np.int64) // TILE) * 1000 + eq["gy"].to_numpy(np.int64) // TILE
    vt = (vr["gx"].to_numpy(np.int64) // TILE) * 1000 + vr["gy"].to_numpy(np.int64) // TILE
    tiles = np.unique(et)
    keep = np.isin(vt, tiles)                                    # real VIIRS only where the reconstruction exists
    rc = {int(k): int(v) for k, v in zip(*np.unique(et, return_counts=True))}
    rb = {int(k): int(v) for k, v in zip(*np.unique(et[both], return_counts=True))}
    vc = {int(k): int(v) for k, v in zip(*np.unique(vt[keep], return_counts=True))}
    rows = []
    for k in tiles.tolist():
        real, rec, bo = vc.get(k, 0), rc.get(k, 0), rb.get(k, 0)
        if real < min_real:
            continue
        tx, ty = divmod(k, 1000)
        lon0, lat0 = tx * TILE * GRID - 180, ty * TILE * GRID - 90
        rows.append({"lon0": round(lon0, 2), "lat0": round(lat0, 2), "real": real, "recon": rec, "both": bo,
                     "ratio": round(rec / real, 3), "recall": round(bo / real, 3), "precision": round(bo / rec, 3) if rec else None})
    tot_r, tot_c, tot_b = sum(r["real"] for r in rows), sum(r["recon"] for r in rows), sum(r["both"] for r in rows)
    out = {"years": f"{y0}-{y1}", "tile_deg": 1, "min_real_cell_days": min_real, "n_tiles": len(rows),
           "overall": {"real": tot_r, "recon": tot_c, "ratio": round(tot_c / tot_r, 3), "recall": round(tot_b / tot_r, 3), "precision": round(tot_b / tot_c, 3)},
           "tiles": rows, "built_s": round(time.time() - t, 1),
           "note": ("Auxiliary data. Reconstruction (from MODIS) vs real VIIRS cell-days in the test years, per 1-degree tile, only where the reconstruction exists "
                    "(5 processed countries). Not split by scan angle or time of day. Not part of any forecast score.")}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out))
    return out


router = APIRouter(tags=["extra-data"])


@router.get("/extra/disagreement", summary="MODIS-reconstruction vs real VIIRS disagreement by 1-degree tile (auxiliary data)")
def disagreement() -> dict:
    if not OUT.exists():
        try:
            return build()
        except Exception as e:  # noqa: BLE001
            raise HTTPException(500, f"could not build: {type(e).__name__}: {e}") from None
    return json.loads(OUT.read_text())


# ---------------------------------------------------------------- #10: independent burned area (EFFIS, Europe / Mediterranean)
import hashlib
import math
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

EFFIS = "https://maps.effis.emergency.copernicus.eu/effis"
_NS = {"wfs": "http://www.opengis.net/wfs/2.0", "ms": "http://mapserver.gis.umn.edu/mapserver"}
EFFIS_NOTE = ("EFFIS burnt-area polygons come from MODIS (Copernicus Emergency Management Service, open WFS, no key); they cover Europe and the "
              "Mediterranean region only. They are an independent area estimate for context, not ground truth and not part of any forecast score. "
              "VIIRS-active cells are counted per 0.05 degree cell (about 23-30 km2 each), a coarse upper bound of the burning footprint, not a burned-area measure.")


def effis_fires(lat: float, lon: float, radius_km: float, year: int, timeout: float = 45.0) -> list[dict]:
    dlat = radius_km / 110.574
    dlon = radius_km / (111.32 * max(0.05, math.cos(math.radians(lat))))
    q = {"service": "WFS", "version": "2.0.0", "request": "GetFeature", "typeNames": f"ms:modis.ba.poly.{year}",
         "bbox": f"{lat - dlat:.4f},{lon - dlon:.4f},{lat + dlat:.4f},{lon + dlon:.4f},urn:ogc:def:crs:EPSG::4326", "count": "300"}
    url = EFFIS + "?" + urllib.parse.urlencode(q)
    cache = ROOT / "cache" / "extradata" / ("effis_" + hashlib.sha1(url.encode()).hexdigest()[:16] + ".xml")
    if cache.exists() and time.time() - cache.stat().st_mtime < 7 * 86400:
        raw = cache.read_bytes()
    else:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "firespot-research/1.0"}), timeout=timeout) as r:
            raw = r.read()
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(raw)
    root = ET.fromstring(raw)
    if root.tag.endswith("ExceptionReport"):
        raise ValueError("EFFIS: " + "".join(root.itertext()).strip()[:160])
    out = []
    for m in root.findall("wfs:member", _NS):
        e = m[0]
        g = lambda n: (e.find("ms:" + n, _NS).text if e.find("ms:" + n, _NS) is not None else None)
        lc = (e.find("{http://www.opengis.net/gml/3.2}boundedBy/{http://www.opengis.net/gml/3.2}Envelope/{http://www.opengis.net/gml/3.2}lowerCorner"))
        uc = (e.find("{http://www.opengis.net/gml/3.2}boundedBy/{http://www.opengis.net/gml/3.2}Envelope/{http://www.opengis.net/gml/3.2}upperCorner"))
        c = None
        if lc is not None and uc is not None:
            a, b = [float(x) for x in lc.text.split()], [float(x) for x in uc.text.split()]
            c = [round((a[0] + b[0]) / 2, 4), round((a[1] + b[1]) / 2, 4)]
        try:
            area = float(g("AREA_HA"))
        except (TypeError, ValueError):
            area = None
        out.append({"id": g("id"), "fire_date": g("FIREDATE"), "final_date": g("FINALDATE"), "country": g("COUNTRY"), "area_ha": area, "center_latlon": c})
    return out


def effis_compare(lat: float, lon: float, radius_km: float, year: int) -> dict:
    fires = effis_fires(lat, lon, radius_km, year)
    fires = [f for f in fires if f["area_ha"]]
    fires.sort(key=lambda f: -f["area_ha"])
    res = {"year": year, "lat": lat, "lon": lon, "radius_km": radius_km, "n_fires": len(fires), "total_area_ha": round(sum(f["area_ha"] for f in fires)),
           "fires": fires[:15], "note": EFFIS_NOTE, "source": "EFFIS WFS ms:modis.ba.poly." + str(year)}
    if not fires:
        res["message"] = "No EFFIS burnt-area polygon in this area and year (EFFIS covers Europe and the Mediterranean region only)."
        return res
    try:
        from equifire.fireindex_mm import get_fire_index
        from equifire.common import GRID
        fi = get_fire_index(verbose=False)
        top = fires[0]
        d0 = int((np.datetime64(top["fire_date"][:10]) - np.datetime64("2000-01-01")).astype(int))
        d1 = int((np.datetime64(top["final_date"][:10]) - np.datetime64("2000-01-01")).astype(int))
        cy_, cx_ = top["center_latlon"]
        cx, cy = int((cx_ + 180) / GRID), int((cy_ + 90) / GRID)
        i = fi.v.query(d0, d1, cx, cy)
        cells = len(np.unique(np.asarray(fi.v.gx)[i].astype(np.int64) * 100000 + np.asarray(fi.v.gy)[i].astype(np.int64))) if len(i) else 0
        cell_km2 = (GRID * 110.574) * (GRID * 111.32 * math.cos(math.radians(cy_)))
        res["largest_fire_vs_viirs"] = {"effis_area_ha": top["area_ha"], "fire_date": top["fire_date"], "final_date": top["final_date"],
                                        "viirs_active_cells": cells, "viirs_cell_km2": round(cell_km2, 1),
                                        "viirs_cells_area_ha_upper_bound": round(cells * cell_km2 * 100),
                                        "ratio_cells_to_effis": round(cells * cell_km2 * 100 / top["area_ha"], 2) if top["area_ha"] else None,
                                        "window": "VIIRS cells within about 1 degree of the EFFIS polygon centre, between its fire date and final date"}
    except Exception as e:  # noqa: BLE001
        res["largest_fire_vs_viirs"] = {"error": f"{type(e).__name__}: {str(e)[:120]}"}
    return res


@router.get("/extra/effis", summary="Independent burnt area (EFFIS, Europe/Mediterranean) around a point, compared with VIIRS-active cells (auxiliary data)")
def effis(lat: float, lon: float, radius_km: float = 40.0, year: int = 2023) -> dict:
    if not (2016 <= year <= 2026) or not (1 <= radius_km <= 100):
        raise HTTPException(400, "year must be 2016-2026 and radius_km 1-100")
    try:
        return effis_compare(lat, lon, radius_km, year)
    except ValueError as e:
        raise HTTPException(502, str(e)) from None
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"EFFIS unavailable: {type(e).__name__}") from None
