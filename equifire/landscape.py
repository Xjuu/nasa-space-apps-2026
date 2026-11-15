"""Keyless landscape layers on the fire-simulation grid (fuel, terrain, firebreaks, dryness).

    get_landscape(lat, lon, half_km, cell_m=250, date=None) -> dict

All sources are free and need NO API key and NO account:
  * ESA WorldCover 2021 v200, 10 m (AWS open data, public COGs)
      https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/ESA_WorldCover_10m_2021_v200_<TILE>_Map.tif
      TILE = 3x3 deg, named by its lower-left corner, e.g. N48W078, S21E123.
  * Copernicus DEM GLO-30 (AWS open data, public COGs), 1x1 deg tiles named by lower-left corner:
      https://copernicus-dem-30m.s3.amazonaws.com/Copernicus_DSM_COG_10_<N|S>yy_00_<E|W>xxx_00_DEM/
              Copernicus_DSM_COG_10_<N|S>yy_00_<E|W>xxx_00_DEM.tif
      (missing tile = ocean -> elevation 0). Fallback if the COGs fail: Open-Meteo elevation API
      https://api.open-meteo.com/v1/elevation (keyless, 90 m DEM, sampled on a 21x21 lattice).
  * OpenStreetMap via Overpass (keyless): https://overpass-api.de/api/interpreter,
      fallback mirrors https://maps.mail.ru/osm/tools/overpass/api/interpreter,
      https://overpass.kumi.systems/api/interpreter (tried in order). ONE query per bbox, cached.
  * Sentinel-2 L2A via the Earth Search STAC (keyless): https://earth-search.aws.element84.com/v1/search,
      collection sentinel-2-l2a; COG assets red (B04), nir (B08), swir16 (B11), scl (SCL) read from
      https://sentinel-cogs.s3.us-west-2.amazonaws.com/... at overview resolution.

GRID GEOMETRY (identical to backend/physics_spread.Grid):
  n = 2*ceil(half_m/cell_m) + 1 cells per side (square, odd, centre cell = (n//2, n//2) = (lat, lon)).
  Local equirectangular projection about (lat0, lon0) = (lat, lon):
      x_m = (lon - lon0) / kx,  y_m = (lat - lat0) / ky,
      kx = (1/111320) / max(cos(lat0), 0.05) deg per metre,  ky = 1/111320 deg per metre.
  Cell (i, j) centre: y = x0 + i*cell_m, x = x0 + j*cell_m with x0 = -(n//2)*cell_m.
  ROW ORDER: by default row 0 is the SOUTHERNMOST row and rows increase NORTHWARD (exactly like
  physics_spread, whose np.gradient axis 0 is "north"); columns increase EASTWARD. Pass
  row_order="north_up" to get image order (row 0 = north) instead; `north_up(a)` flips any array.
  Every cell value is an aggregate over the cell footprint (centre +- cell_m/2), sampled on an s x s
  sub-grid inside the cell.

Layers returned (all float32/int16 arrays of shape (n, n)):
  fuel_model    Anderson-13 fuel model number per cell (1..13) or nonburnable code
                (91 urban, 92 snow/ice, 98 water, 99 bare), 0 = no data.
  physics_class index into PHYSICS_CLASSES: 0 none/nonburnable, 1 grass, 2 savanna, 3 shrub, 4 forest
                (the four classes physics_spread already knows; FM8/FM9 map to "forest" for now).
  cropland      bool, cell dominated by cropland (fuel FM1 = stubble, often managed/irrigated/grazed).
  burnable_frac 0..1 weighted fraction of the cell that carries fire (see WC_CLASSES weights).
  frac          dict class-name -> 0..1 fraction of the cell in each WorldCover class.
  elevation     m (cell mean); slope_deg; aspect_deg = compass bearing the slope FACES (= downslope
                direction, 0 N, 90 E; NaN where flat < 0.5 deg); tan_slope, upslope_deg
                (= (aspect+180)%360, the convention of physics_spread.terrain()).
  barrier_lines 0..1 probability a spreading surface fire is stopped when it crosses the cell, from
                OSM linear features (roads, rails, rivers, firebreaks); see BARRIER_RULES.
  barrier_area  0..1 fraction of the cell covered by OSM water / urban polygons (closed ways).
  barrier       combined: 1 - (1-barrier_lines)*(1-max(barrier_area, 1-burnable_frac)).
  ndvi, ndmi    Sentinel-2 cell means over clear pixels (SCL 4 veg / 5 bare / 7 unclassified),
                NaN when no clear scene within 30 days before `date`.
  dryness       0..1, clip((0.40 - NDMI)/0.60, 0, 1): NDMI >= 0.40 (moist canopy) -> 0,
                NDMI <= -0.20 (cured grass / dry litter / bare) -> 1. Linear in between.
  green         bool, "green-up": NDVI >= 0.50 and NDMI >= 0.20 (live, high-moisture vegetation;
                slows spread / raises live fuel moisture).
Metadata in res["meta"]: grid geometry, per-layer source URLs, dates, timings, errors, coverage %.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import gzip
import hashlib
import json
import math
import os
import time
import threading
import queue
from pathlib import Path

import numpy as np

CACHE = Path(__file__).resolve().parent.parent / "cache" / "landscape"
M_TO_DEG = 1 / 111_320.0
UA = {"User-Agent": "EquiFire-hackathon (NASA Space Apps 2026)"}
WC_URL = ("https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/"
          "ESA_WorldCover_10m_2021_v200_{tile}_Map.tif")
DEM_URL = "https://copernicus-dem-30m.s3.amazonaws.com/{n}/{n}.tif"
OPEN_METEO_ELEV = "https://api.open-meteo.com/v1/elevation"
OVERPASS = ["https://overpass-api.de/api/interpreter", "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
            "https://overpass.kumi.systems/api/interpreter"]
STAC = "https://earth-search.aws.element84.com/v1/search"
GDAL_ENV = dict(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", AWS_NO_SIGN_REQUEST="YES",
                CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif", GDAL_HTTP_MULTIRANGE="YES",
                GDAL_HTTP_MERGE_CONSECUTIVE_RANGES="YES", GDAL_HTTP_TIMEOUT="30", GDAL_HTTP_MAX_RETRY="2",
                GDAL_HTTP_RETRY_DELAY="1", GDAL_CACHEMAX=256, VSI_CACHE="FALSE")
VERSION = 1

# ------------------------------------------------------------------ land cover -> fuel
# WorldCover value: (name, burnable weight, nonburnable code or None)
WC_CLASSES = {
    10: ("tree", 1.0, None),
    20: ("shrub", 1.0, None),
    30: ("grass", 1.0, None),
    40: ("crop", 1.0, None),        # FM1 stubble, flagged "cropland"
    50: ("built", 0.0, 91),         # NB1 urban (WUI structures not modelled)
    60: ("bare", 0.0, 99),          # NB9 bare / sparse vegetation
    70: ("snow", 0.0, 92),          # NB2 snow/ice
    80: ("water", 0.0, 98),         # NB8 open water
    90: ("wetland", 0.5, None),     # herbaceous wetland: burns when dry -> FM1, half weight
    95: ("mangrove", 0.0, 98),      # mangrove: wet, treated as nonburnable
    100: ("moss", 0.5, None),       # moss & lichen (tundra): FM1, half weight
}
WC_NAMES = [v[0] for v in WC_CLASSES.values()]
WC_CODES = list(WC_CLASSES)
NB_NAMES = {91: "NB urban", 92: "NB snow/ice", 98: "NB water", 99: "NB bare"}
FM_NAMES = {1: "FM1 short grass", 2: "FM2 timber (grass and understory)", 3: "FM3 tall grass",
            6: "FM6 dormant brush", 8: "FM8 closed timber litter", 9: "FM9 hardwood litter",
            10: "FM10 timber (litter and understory)"}
PHYSICS_CLASSES = ["none", "grass", "savanna", "shrub", "forest"]
FM_TO_PHYSICS = {1: 1, 2: 2, 3: 1, 6: 3, 8: 4, 9: 4, 10: 4}
FUEL_RULES = [
    "nonburnable: weighted burnable fraction < 0.5 -> dominant nonburnable class (91/92/98/99)",
    "tree >= 0.6 of burnable: |lat| >= 45 -> FM10 (boreal/temperate conifer, crown-fire capable); "
    "23.5 <= |lat| < 45 -> FM9 (temperate broadleaf / long-needle pine litter); |lat| < 23.5 -> FM8 "
    "(closed tropical forest, compact litter, slow)",
    "open tree (< 0.6) with grass+shrub+crop+wetland >= 0.2 -> FM2 (timber with grass understory / savanna "
    "woodland; WorldCover 'tree' starts at 10 % canopy)",
    "shrub dominant -> FM6 (dormant brush)",
    "grass / wetland / moss dominant -> FM2 if tree >= 0.1 else FM1 (short grass)",
    "crop dominant -> FM1 + cropland flag (stubble; often managed, irrigated or harvested)",
    "tropics only, after Sentinel-2: FM8 cells with dryness >= 0.6 -> FM2 (dry eucalypt/miombo woodland)",
]
# Anderson (1982) 13 fuel models in physics_spread FUEL_MODELS format, for integration:
# loads (t/ac) 1h, 10h, 100h, live herb, live woody; SAV 1h, herb, woody (1/ft); depth ft; Mx (fraction)
ANDERSON13 = {
    1: dict(w=(0.74, 0, 0, 0, 0), sav=(3500, 1500, 1500), depth=1.0, mx=0.12),
    2: dict(w=(2.0, 1.0, 0.5, 0.5, 0), sav=(3000, 1500, 1500), depth=1.0, mx=0.15),
    3: dict(w=(3.01, 0, 0, 0, 0), sav=(1500, 1500, 1500), depth=2.5, mx=0.25),
    4: dict(w=(5.01, 4.01, 2.0, 0, 5.01), sav=(2000, 1500, 1500), depth=6.0, mx=0.20),
    5: dict(w=(1.0, 0.5, 0, 0, 2.0), sav=(2000, 1500, 1500), depth=2.0, mx=0.20),
    6: dict(w=(1.5, 2.5, 2.0, 0, 0), sav=(1750, 1500, 1500), depth=2.5, mx=0.25),
    7: dict(w=(1.13, 1.87, 1.5, 0, 0.37), sav=(1750, 1500, 1500), depth=2.5, mx=0.40),
    8: dict(w=(1.5, 1.0, 2.5, 0, 0), sav=(2000, 1500, 1500), depth=0.2, mx=0.30),
    9: dict(w=(2.92, 0.41, 0.15, 0, 0), sav=(2500, 1500, 1500), depth=0.2, mx=0.25),
    10: dict(w=(3.01, 2.0, 5.01, 0, 2.0), sav=(2000, 1500, 1500), depth=1.0, mx=0.25),
    11: dict(w=(1.5, 4.51, 5.51, 0, 0), sav=(1500, 1500, 1500), depth=1.0, mx=0.15),
    12: dict(w=(4.01, 14.03, 16.53, 0, 0), sav=(1500, 1500, 1500), depth=2.3, mx=0.20),
    13: dict(w=(7.01, 23.04, 28.05, 0, 0), sav=(1500, 1500, 1500), depth=3.0, mx=0.25),
}


def fuel_from_fractions(frac: np.ndarray, lat: float) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """frac: (..., 11) fractions in WC_CODES order -> (fuel_model, physics_class, cropland, burnable_frac).

    Pure function; rules in FUEL_RULES. Cells with no valid pixels (all-zero fractions) -> fuel 0."""
    frac = np.asarray(frac, float)
    idx = {c: k for k, c in enumerate(WC_CODES)}
    f = {WC_CLASSES[c][0]: frac[..., idx[c]] for c in WC_CODES}
    w = np.array([WC_CLASSES[c][1] for c in WC_CODES])
    total = frac.sum(-1)
    burn = (frac * w).sum(-1)
    burnable_frac = np.where(total > 0, burn / np.maximum(total, 1e-9), 0.0)
    a = abs(lat)
    tree_fm = 10 if a >= 45 else (9 if a >= 23.5 else 8)
    bsum = np.maximum(f["tree"] + f["shrub"] + f["grass"] + f["crop"] + f["wetland"] + f["moss"], 1e-9)
    tree_r = f["tree"] / bsum
    herb = f["grass"] + f["wetland"] + f["moss"]
    # dominant burnable group
    groups = np.stack([f["tree"], f["shrub"], herb, f["crop"]], -1)
    dom = np.argmax(groups, -1)
    fm = np.zeros(frac.shape[:-1], np.int16)
    fm = np.where(dom == 0, np.where(tree_r >= 0.6, tree_fm,
                                     np.where((f["shrub"] + herb + f["crop"]) / bsum >= 0.2, 2, tree_fm)), fm)
    fm = np.where(dom == 1, 6, fm)
    fm = np.where(dom == 2, np.where(tree_r >= 0.1, 2, 1), fm)
    fm = np.where(dom == 3, 1, fm)
    crop = (dom == 3) & (burnable_frac >= 0.5)
    # nonburnable cells
    nb_codes = [c for c in WC_CODES if WC_CLASSES[c][2] is not None]
    nb_stack = np.stack([frac[..., idx[c]] for c in nb_codes], -1)
    nb_dom = np.array([WC_CLASSES[c][2] for c in nb_codes])[np.argmax(nb_stack, -1)]
    fm = np.where(burnable_frac < 0.5, nb_dom, fm)
    fm = np.where(total <= 0, 0, fm).astype(np.int16)
    pc = np.zeros_like(fm, dtype=np.int8)
    for k, v in FM_TO_PHYSICS.items():
        pc[fm == k] = v
    return fm, pc, crop & (fm == 1), burnable_frac.astype(np.float32)


# ------------------------------------------------------------------ grid
class LGrid:
    """Same geometry as backend/physics_spread.Grid (see module docstring)."""

    def __init__(self, clat: float, clon: float, half_m: float, cell_m: float):
        self.clat, self.clon, self.cell = float(clat), float(clon), float(cell_m)
        self.n = int(2 * math.ceil(half_m / cell_m)) + 1
        self.kx = M_TO_DEG / max(math.cos(math.radians(clat)), 0.05)
        self.ky = M_TO_DEG
        self.x0 = -(self.n // 2) * self.cell

    def sub_axes(self, s: int) -> tuple[np.ndarray, np.ndarray]:
        """lon (east-increasing) and lat (north-increasing) of an s x s sub-sample grid in every cell."""
        k = (np.arange(self.n * s) + 0.5) / s - 0.5          # cell units, cell centre at integer
        m = self.x0 + k * self.cell
        return self.clon + m * self.kx, self.clat + m * self.ky

    def bbox(self, pad_m: float = 0.0) -> tuple[float, float, float, float]:
        """(west, south, east, north) of the outer cell edges (+ pad)."""
        e = -self.x0 + self.cell / 2 + pad_m
        return self.clon - e * self.kx, self.clat - e * self.ky, self.clon + e * self.kx, self.clat + e * self.ky

    def geometry(self) -> dict:
        w, s, e, n = self.bbox()
        return {"n": self.n, "cell_m": self.cell, "centre_lat": self.clat, "centre_lon": self.clon,
                "x0_m": self.x0, "kx_deg_per_m": self.kx, "ky_deg_per_m": self.ky,
                "bbox_wsen": [w, s, e, n], "crs": "local equirectangular about centre (= physics_spread.Grid)",
                "cell_centre": "lon = centre_lon + (x0_m + j*cell_m)*kx; lat = centre_lat + (x0_m + i*cell_m)*ky"}


def block_mean(a: np.ndarray, s: int) -> np.ndarray:
    n = a.shape[0] // s
    return a.reshape(n, s, n, s).mean(axis=(1, 3))


def north_up(a: np.ndarray) -> np.ndarray:
    """Flip a south-first (physics order) array to image order (row 0 = north) or back."""
    return np.asarray(a)[::-1]


# ------------------------------------------------------------------ COG reading helpers (EPSG:4326 tiles)
def _read_4326_tile(url: str, lon_ax: np.ndarray, lat_ax: np.ndarray, resampling: str, nodata_fill: float):
    """Sample a geographic COG on separable lon/lat axes. Returns (array[len(lat), len(lon)] or None, mask)."""
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.windows import from_bounds
    with rasterio.Env(**GDAL_ENV):
        try:
            ds = rasterio.open("/vsicurl/" + url)
        except Exception as e:  # missing tile (ocean) or network
            return None, str(e)[:160]
        with ds:
            tw, ts, te, tn = ds.bounds
            mx = (lon_ax >= tw) & (lon_ax < te)
            my = (lat_ax >= ts) & (lat_ax < tn)
            if not mx.any() or not my.any():
                return np.empty(0), None
            w0, w1 = lon_ax[mx].min(), lon_ax[mx].max()
            s0, s1 = lat_ax[my].min(), lat_ax[my].max()
            step = abs(lon_ax[1] - lon_ax[0]) if len(lon_ax) > 1 else ds.res[0]
            pad = max(step, ds.res[0])
            win = from_bounds(max(w0 - pad, tw), max(s0 - pad, ts), min(w1 + pad, te), min(s1 + pad, tn), ds.transform)
            win = win.round_offsets().round_lengths()
            win = win.intersection(rasterio.windows.Window(0, 0, ds.width, ds.height))
            # read at roughly the sample spacing (GDAL picks an overview)
            fx = max(1.0, step / ds.res[0])
            fy = max(1.0, abs(lat_ax[1] - lat_ax[0]) / ds.res[1]) if len(lat_ax) > 1 else 1.0
            oh, ow = max(1, int(round(win.height / fy))), max(1, int(round(win.width / fx)))
            rs = getattr(Resampling, resampling)
            arr = ds.read(1, window=win, out_shape=(oh, ow), resampling=rs)
            wt = ds.window_transform(win)
            # pixel indices of the samples inside the decimated read
            px_w = (wt.a * win.width) / ow
            px_h = (-wt.e * win.height) / oh
            ci = np.floor((lon_ax[mx] - wt.c) / px_w).astype(int).clip(0, ow - 1)
            ri = np.floor((wt.f - lat_ax[my]) / px_h).astype(int).clip(0, oh - 1)
            return (np.nonzero(my)[0], np.nonzero(mx)[0], arr[np.ix_(ri, ci)]), None


def _mosaic_4326(urls_for, lon_ax, lat_ax, resampling, fill, dtype, workers=6):
    """urls_for: list of (url, tile_bounds). Returns mosaic (lat, lon order, lat increasing), errors, used."""
    out = np.full((len(lat_ax), len(lon_ax)), fill, dtype)
    have = np.zeros(out.shape, bool)
    errors, used, missing = [], [], []
    with cf.ThreadPoolExecutor(workers) as ex:
        futs = {ex.submit(_read_4326_tile, u, lon_ax, lat_ax, resampling, fill): u for u in urls_for}
        for fu in cf.as_completed(futs):
            u = futs[fu]
            try:
                r, err = fu.result()
            except Exception as e:  # noqa: BLE001
                r, err = None, repr(e)[:160]
            if r is None:
                (missing if ("404" in (err or "") or "not recognized" in (err or "") or "No such file" in (err or ""))
                 else errors).append({"url": u, "error": err})
                continue
            if isinstance(r, np.ndarray):
                continue
            ri, ci, vals = r
            out[np.ix_(ri, ci)] = vals
            have[np.ix_(ri, ci)] = True
            used.append(u)
    return out, have, errors, used, missing


def _tiles(w, s, e, n, step):
    lats = range(int(math.floor(s / step) * step), int(math.ceil(n / step) * step), step)
    lons = range(int(math.floor(w / step) * step), int(math.ceil(e / step) * step), step)
    return [(la, lo) for la in lats for lo in lons]


def wc_tile_name(lat0: int, lon0: int) -> str:
    return "%s%02d%s%03d" % ("N" if lat0 >= 0 else "S", abs(lat0), "E" if lon0 >= 0 else "W", abs(lon0))


def dem_tile_name(lat0: int, lon0: int) -> str:
    return "Copernicus_DSM_COG_10_%s%02d_00_%s%03d_00_DEM" % ("N" if lat0 >= 0 else "S", abs(lat0),
                                                            "E" if lon0 >= 0 else "W", abs(lon0))


# ------------------------------------------------------------------ layer 1: WorldCover
def landcover_layer(g: LGrid) -> tuple[dict, dict]:
    t = time.time()
    s = int(max(2, min(10, round(g.cell / 25.0))))          # ~25 m sub-samples (100 per 250 m cell)
    s = min(s, max(2, 2500 // g.n))                          # cap the sub-grid at ~2500^2 samples
    lon_ax, lat_ax = g.sub_axes(s)
    w, so, e, n = g.bbox()
    urls = [WC_URL.format(tile=wc_tile_name(la, lo)) for la, lo in _tiles(w, so, e, n, 3)]
    mos, have, errors, used, missing = _mosaic_4326(urls, lon_ax, lat_ax, "nearest", 0, np.uint8)
    lut = np.full(256, len(WC_CODES), np.int16)
    for k, c in enumerate(WC_CODES):
        lut[c] = k
    cls = lut[mos]                                           # 11 = nodata
    nc = g.n
    cell_idx = (np.arange(nc * s) // s)
    cid = (cell_idx[:, None] * nc + cell_idx[None, :]).ravel()
    counts = np.bincount(cid * (len(WC_CODES) + 1) + cls.ravel(), minlength=nc * nc * (len(WC_CODES) + 1))
    counts = counts.reshape(nc, nc, len(WC_CODES) + 1).astype(np.float32)
    frac = counts[..., :len(WC_CODES)] / float(s * s)
    fm, pc, crop, bf = fuel_from_fractions(frac, g.clat)
    valid = frac.sum(-1) > 0
    lay = {"fuel_model": fm, "physics_class": pc, "cropland": crop, "burnable_frac": bf,
           "frac": {WC_CLASSES[c][0]: frac[..., k].astype(np.float32) for k, c in enumerate(WC_CODES)}}
    meta = {"source": "ESA WorldCover 2021 v200 10 m (AWS open data, keyless)", "date": "2021",
            "url_pattern": WC_URL, "tiles_used": sorted(used), "tiles_missing": [m["url"] for m in missing],
            "errors": errors, "subsamples_per_cell": s * s, "aggregation": "class fractions on s x s nearest samples",
            "coverage_pct": round(100 * float(valid.mean()), 1), "rules": FUEL_RULES,
            "seconds": round(time.time() - t, 2)}
    return lay, meta


# ------------------------------------------------------------------ layer 2: terrain
def slope_aspect(z: np.ndarray, cell_m: float) -> dict:
    """z in physics order (row 0 = south). Returns slope_deg, aspect_deg (downslope bearing, NaN if
    slope < 0.5 deg), tan_slope, upslope_deg (physics_spread convention). Pure function."""
    z = np.asarray(z, float)
    dzdy, dzdx = np.gradient(z, cell_m)                      # dzdy: northward, dzdx: eastward
    tan = np.hypot(dzdx, dzdy)
    up = (np.degrees(np.arctan2(dzdx, dzdy)) + 360) % 360
    slope = np.degrees(np.arctan(tan))
    aspect = np.where(slope >= 0.5, (up + 180) % 360, np.nan)
    return {"slope_deg": slope.astype(np.float32), "aspect_deg": aspect.astype(np.float32),
            "tan_slope": tan.astype(np.float32), "upslope_deg": up.astype(np.float32)}


def _open_meteo_elev(g: LGrid, k: int = 21) -> np.ndarray:
    import requests
    from scipy.ndimage import map_coordinates
    lon_c, lat_c = g.sub_axes(1)
    glon, glat = np.linspace(lon_c[0], lon_c[-1], k), np.linspace(lat_c[0], lat_c[-1], k)
    LA, LO = np.meshgrid(glat, glon, indexing="ij")
    la, lo = LA.ravel(), LO.ravel()
    z = []
    for i in range(0, la.size, 100):
        r = requests.get(OPEN_METEO_ELEV, params={"latitude": ",".join("%.5f" % v for v in la[i:i + 100]),
                                                  "longitude": ",".join("%.5f" % v for v in lo[i:i + 100])},
                         headers=UA, timeout=20)
        r.raise_for_status()
        z += r.json()["elevation"]
    z = np.asarray(z, float).reshape(k, k)
    fi = np.linspace(0, k - 1, g.n)
    YY, XX = np.meshgrid(fi, fi, indexing="ij")
    return map_coordinates(z, [YY, XX], order=1)


def terrain_layer(g: LGrid) -> tuple[dict, dict]:
    t = time.time()
    s = 3 if g.cell <= 500 else 4                            # cell mean of s x s bilinear samples
    lon_ax, lat_ax = g.sub_axes(s)
    w, so, e, n = g.bbox()
    urls = [DEM_URL.format(n=dem_tile_name(la, lo)) for la, lo in _tiles(w, so, e, n, 1)]
    mos, have, errors, used, missing = _mosaic_4326(urls, lon_ax, lat_ax, "bilinear", np.nan, np.float32)
    meta = {"source": "Copernicus DEM GLO-30 (AWS open data, keyless)", "url_pattern": DEM_URL,
            "tiles_used": sorted(used), "tiles_missing_ocean": [m["url"] for m in missing], "errors": errors,
            "aggregation": "cell mean of %dx%d bilinear samples; slope/aspect from np.gradient of cell means" % (s, s),
            "aspect_convention": "aspect_deg = compass bearing the slope faces (downslope), 0=N 90=E; "
                                 "upslope_deg = aspect+180 (physics_spread convention); NaN aspect if slope<0.5 deg"}
    if used and not errors:
        mos = np.where(have, mos, 0.0)                       # missing tiles = ocean = 0 m
    else:
        mos = None
    if mos is None or not np.isfinite(mos).any():
        try:
            z = _open_meteo_elev(g)
            meta.update(source="FALLBACK Open-Meteo elevation API (keyless, 90 m DEM, 21x21 lattice "
                               "bilinear) because Copernicus COG reads failed", fallback=True, url=OPEN_METEO_ELEV)
        except Exception as ex:  # noqa: BLE001
            meta["errors"].append({"url": OPEN_METEO_ELEV, "error": repr(ex)[:160]})
            z = np.full((g.n, g.n), np.nan)
    else:
        z = block_mean(np.nan_to_num(mos.astype(float), nan=0.0), s)
    lay = {"elevation": z.astype(np.float32)}
    if np.isfinite(z).all():
        lay.update(slope_aspect(z, g.cell))
    else:
        nanf = np.full((g.n, g.n), np.nan, np.float32)
        lay.update(slope_deg=nanf, aspect_deg=nanf, tan_slope=nanf, upslope_deg=nanf)
    meta["coverage_pct"] = round(100 * float(np.isfinite(z).mean()), 1)
    if np.isfinite(z).any():
        meta.update(elev_min_m=round(float(np.nanmin(z))), elev_max_m=round(float(np.nanmax(z))),
                    slope_max_deg=round(float(np.nanmax(lay["slope_deg"])), 1))
    meta["seconds"] = round(time.time() - t, 2)
    return lay, meta


# ------------------------------------------------------------------ layer 3: OSM firebreaks
# (tag key, value) -> (default width m, stop probability). Width tag overrides via barrier_p(width).
BARRIER_RULES = {
    ("highway", "motorway"): (30, 0.85), ("highway", "trunk"): (25, 0.75), ("highway", "primary"): (15, 0.60),
    ("highway", "secondary"): (12, 0.50), ("highway", "tertiary"): (8, 0.40),
    ("highway", "motorway_link"): (10, 0.45), ("highway", "trunk_link"): (10, 0.45),
    ("highway", "primary_link"): (8, 0.40), ("highway", "secondary_link"): (8, 0.35),
    ("highway", "tertiary_link"): (6, 0.30), ("highway", "unclassified"): (6, 0.30),
    ("highway", "residential"): (6, 0.30), ("highway", "service"): (4, 0.15), ("highway", "track"): (4, 0.15),
    ("railway", "rail"): (10, 0.35), ("railway", "light_rail"): (8, 0.30), ("railway", "narrow_gauge"): (6, 0.25),
    ("waterway", "river"): (30, 0.85), ("waterway", "canal"): (15, 0.60), ("waterway", "stream"): (3, 0.15),
    ("waterway", "drain"): (2, 0.05), ("waterway", "ditch"): (2, 0.05),
    ("man_made", "firebreak"): (10, 0.50), ("landuse", "firebreak"): (10, 0.50), ("firebreak", "*"): (10, 0.50),
}
AREA_RULES = {("natural", "water"): 1.0, ("waterway", "riverbank"): 1.0, ("landuse", "reservoir"): 1.0,
              ("landuse", "residential"): 0.7, ("landuse", "industrial"): 0.9, ("landuse", "commercial"): 0.9,
              ("landuse", "retail"): 0.9}
BARRIER_NOTE = ("barrier_lines = 1 - prod(1 - p_k) over line classes present in the cell; p from BARRIER_RULES "
                "(or from a numeric width tag: p = clip(width/(width+6 m), 0.05, 0.9) for rivers/roads). Rationale: "
                "a surface fire with flame length L usually crosses a fuel-free strip narrower than ~L; "
                "a 30 m motorway or river stops most surface heads (p 0.85), a 4 m track rarely (p 0.15); "
                "crown fires and spotting cross any of them. barrier_area = OSM water/urban polygon fraction "
                "(closed ways only; urban weighted 0.7-0.9 as gardens can burn).")


def barrier_p(key: str, val: str, tags: dict) -> float | None:
    r = BARRIER_RULES.get((key, val)) or (BARRIER_RULES.get((key, "*")) if key in tags else None)
    if r is None:
        return None
    wtag = tags.get("width") or tags.get("est_width")
    try:
        wm = float(str(wtag).split()[0].replace(",", ".").rstrip("m")) if wtag else None
    except ValueError:
        wm = None
    if wm and key in ("waterway", "highway") and 0.5 < wm < 2000:
        return float(np.clip(wm / (wm + 6.0), 0.05, 0.9))
    return r[1]


def _overpass_query(w, s, e, n, big: bool) -> str:
    hw = "motorway|trunk|primary|secondary|tertiary|motorway_link|trunk_link|primary_link|secondary_link|" \
         "tertiary_link|unclassified|residential" + ("" if big else "|track|service")
    ww = "river|canal" + ("" if big else "|stream|drain|ditch")
    b = "(%.5f,%.5f,%.5f,%.5f)" % (s, w, n, e)
    return ("[out:json][timeout:120][maxsize:536870912];("
            'way["highway"~"^(%s)$"]%s;way["railway"~"^(rail|light_rail|narrow_gauge)$"]%s;'
            'way["waterway"~"^(%s|riverbank)$"]%s;%sway["landuse"~"^(residential|industrial|'
            'commercial|retail|reservoir|firebreak)$"]%s;way["man_made"="firebreak"]%s;way["firebreak"]%s;'
            ");out tags geom%s;") % (hw, b, b, ww, b, "" if big else 'way["natural"="water"]%s;' % b, b, b, b, b)


def fetch_osm(w, s, e, n, big: bool, timeout: float = 50, cache_only: bool = False) -> tuple[dict | None, dict]:
    import requests
    q = _overpass_query(w, s, e, n, big)
    key = hashlib.sha1(q.encode()).hexdigest()[:16]
    f = CACHE / ("osm_%s.json.gz" % key)
    if f.exists():
        return json.loads(gzip.decompress(f.read_bytes())), {"cached": True, "query_hash": key}
    if cache_only:
        return None, {"not_cached": True, "query_hash": key}
    errs = []
    for url in OVERPASS:
        try:
            with _OSM_NETWORK:
                r = requests.post(url, data={"data": q}, headers=UA, timeout=timeout)
            if r.status_code == 200:
                js = r.json()
                if js.get("remark") and "runtime error" in js["remark"]:
                    raise RuntimeError(js["remark"][:200])
                CACHE.mkdir(parents=True, exist_ok=True)
                f.write_bytes(gzip.compress(json.dumps(js).encode()))
                return js, {"cached": False, "url": url, "query_hash": key, "errors": errs}
            errs.append({"url": url, "error": "HTTP %d" % r.status_code})
        except Exception as ex:  # noqa: BLE001
            errs.append({"url": url, "error": repr(ex)[:200]})
    return None, {"errors": errs, "query_hash": key}


def rasterize_features(features: list[tuple[str, list[tuple[float, float]], float]], g: LGrid,
                       area_s: int = 4) -> tuple[np.ndarray, np.ndarray]:
    """features: (kind 'line'|'area', [(lon, lat), ...], p). Pure (no network).

    Lines -> barrier_lines (combined over discrete p levels, all_touched at cell resolution);
    areas -> area fraction x weight (max over polygons) via an area_s x area_s sub-grid.
    Returns arrays in physics order (row 0 = south)."""
    from rasterio import features as rf
    from rasterio.transform import Affine
    n = g.n
    w, so, e, nn = g.bbox()
    # pixel space: col = (lon - w)/dlon, row_from_north = (n - lat)/dlat ; rasterio rows go north->south
    dlon, dlat = g.cell * g.kx, g.cell * g.ky

    def tr(s_):
        return Affine(dlon / s_, 0, w, 0, -dlat / s_, nn)
    levels: dict[float, list] = {}
    areas: dict[float, list] = {}
    for kind, coords, p in features:
        if len(coords) < 2:
            continue
        pr = round(float(p), 2)
        if kind == "line":
            levels.setdefault(pr, []).append({"type": "LineString", "coordinates": coords})
        elif len(coords) >= 4:
            areas.setdefault(pr, []).append({"type": "Polygon", "coordinates": [coords]})
    keep = np.ones((n, n))
    for p, geoms in levels.items():
        m = rf.rasterize(geoms, out_shape=(n, n), transform=tr(1), all_touched=True, fill=0, default_value=1,
                         dtype="uint8")
        keep *= 1 - p * m
    lines = (1 - keep)[::-1]
    area = np.zeros((n, n))
    for p, geoms in areas.items():
        m = rf.rasterize(geoms, out_shape=(n * area_s, n * area_s), transform=tr(area_s), all_touched=False,
                         fill=0, default_value=1, dtype="uint8")
        area = np.maximum(area, p * block_mean(m.astype(float), area_s))
    return lines.astype(np.float32), area[::-1].astype(np.float32)


def osm_to_features(js: dict) -> tuple[list, dict]:
    feats, counts = [], {}
    for el in js.get("elements", []):
        tags = el.get("tags", {})
        geom = el.get("geometry")
        if not geom:
            continue
        coords = [(pt["lon"], pt["lat"]) for pt in geom if pt]
        closed = len(coords) >= 4 and coords[0] == coords[-1]
        done = False
        for (k, v), wgt in AREA_RULES.items():
            if tags.get(k) == v and closed:
                feats.append(("area", coords, wgt))
                counts["%s=%s" % (k, v)] = counts.get("%s=%s" % (k, v), 0) + 1
                done = True
                break
        if done:
            continue
        for k in ("highway", "railway", "waterway", "man_made", "landuse", "firebreak"):
            if k in tags:
                p = barrier_p(k, tags[k], tags)
                if p is not None:
                    feats.append(("line", coords, p))
                    lab = "%s=%s" % (k, tags[k] if k != "firebreak" else "*")
                    counts[lab] = counts.get(lab, 0) + 1
                    break
    return feats, counts


OSM_TILE_KM = 85.0
OSM_BUDGET_S = 150.0
_OSM_NETWORK = threading.BoundedSemaphore(2)


def osm_layer(g: LGrid, cache_only: bool = False) -> tuple[dict, dict]:
    """One Overpass query per bbox; bboxes wider than OSM_TILE_KM are split into k x k sub-boxes
    (queried two at a time, each cached) because Overpass returns 502/504 on 160 km boxes."""
    t = time.time()
    w, s, e, n = g.bbox()
    big = (-g.x0) > 40_000
    span_km = (-g.x0 * 2 + g.cell) / 1000
    k = max(1, int(math.ceil(span_km / OSM_TILE_KM)))
    xs, ys = np.linspace(w, e, k + 1), np.linspace(s, n, k + 1)
    meta = {"source": "OpenStreetMap via Overpass API (keyless, ODbL)", "urls": OVERPASS,
            "detail": "major roads/rail/rivers/canals + urban landuse only, no natural=water (WorldCover has it) "
                      "(half-width > 40 km)" if big else
                      "roads incl. tracks/service, rail, rivers, streams, canals, drains",
            "sub_boxes": k * k, "rules": {"%s=%s" % kk: {"default_width_m": v[0], "p_stop": v[1]}
                                          for kk, v in BARRIER_RULES.items()},
            "area_rules": {"%s=%s" % kk: v for kk, v in AREA_RULES.items()}, "note": BARRIER_NOTE,
            "limitations": "multipolygon relations (large lakes, some riverbanks) are not assembled; "
                           "WorldCover water/built-up covers those areas", "errors": [], "failed_boxes": []}
    seen, feats_all, counts, ok_boxes, stamps, cached = set(), [], {}, 0, set(), 0
    order = sorted(((a, b) for a in range(k) for b in range(k)),
                   key=lambda ab: abs(ab[0] - (k - 1) / 2) + abs(ab[1] - (k - 1) / 2))   # centre first
    def load_box(ab):
        a, b = ab
        box = (xs[b], ys[a], xs[b + 1], ys[a + 1])
        if time.time() - t > OSM_BUDGET_S:
            return box, None, {"budget_exceeded": True}
        js, info = fetch_osm(*box, big, cache_only=True) if cache_only else fetch_osm(*box, big)
        return box, js, info

    # Independent boxes, at most two outbound Overpass calls across this process.
    # Consume in the original centre-first order so feature deduplication is unchanged.
    with cf.ThreadPoolExecutor(max_workers=2, thread_name_prefix="osm-box") as pool:
        for box, js, info in pool.map(load_box, order):
            if info.get("budget_exceeded"):
                meta["failed_boxes"].append([round(float(v), 4) for v in box] + ["time budget"])
                continue
            if js is None:
                meta["errors"] += info.get("errors", [])
                meta["failed_boxes"].append([round(float(v), 4) for v in box])
                continue
            ok_boxes += 1
            cached += bool(info.get("cached"))
            stamps.add(js.get("osm3s", {}).get("timestamp_osm_base"))
            els = [el for el in js.get("elements", []) if el.get("id") not in seen]
            seen.update(el.get("id") for el in els)
            f, c = osm_to_features({"elements": els})
            feats_all += f
            for kk, v in c.items():
                counts[kk] = counts.get(kk, 0) + v
    nanf = np.full((g.n, g.n), np.nan, np.float32)
    cov = round(100.0 * ok_boxes / (k * k), 1)
    if ok_boxes == 0:
        meta.update(coverage_pct=0.0, seconds=round(time.time() - t, 2))
        return {"barrier_lines": nanf, "barrier_area": nanf}, meta
    lines, area = rasterize_features(feats_all, g)
    meta.update(feature_counts=counts, n_features=len(feats_all), coverage_pct=cov, boxes_from_cache=cached,
                osm_timestamp=sorted(x for x in stamps if x)[-1:] or None,
                cells_with_barrier_pct=round(100 * float((lines > 0).mean()), 1),
                seconds=round(time.time() - t, 2))
    if not meta["errors"]:
        del meta["errors"]
    return {"barrier_lines": lines, "barrier_area": area}, meta


# ------------------------------------------------------------------ layer 4: Sentinel-2 dryness
CLEAR_SCL = (4, 5, 7)


def dryness_from_ndmi(ndmi):
    return np.clip((0.40 - np.asarray(ndmi, float)) / 0.60, 0.0, 1.0)


def green_flag(ndvi, ndmi):
    ndvi, ndmi = np.asarray(ndvi, float), np.asarray(ndmi, float)
    return (ndvi >= 0.50) & (ndmi >= 0.20)


def _stac_search(bbox, date: dt.datetime, days: int = 30, max_cloud: int = 60) -> list[dict]:
    import requests
    t0 = (date - dt.timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    body = {"collections": ["sentinel-2-l2a"], "bbox": list(bbox),
            "datetime": "%s/%s" % (t0, date.strftime("%Y-%m-%dT%H:%M:%SZ")),
            "query": {"eo:cloud_cover": {"lt": max_cloud}},
            "sortby": [{"field": "properties.datetime", "direction": "desc"}], "limit": 50,
            "fields": {"include": ["id", "properties.datetime", "properties.eo:cloud_cover", "assets.red",
                                   "assets.nir", "assets.swir16", "assets.scl", "properties.proj:epsg",
                                   "properties.proj:code", "properties.view:sun_elevation", "bbox"]}}
    r = requests.post(STAC, json=body, headers=UA, timeout=30)
    r.raise_for_status()
    return r.json().get("features", [])


def _read_utm(href: str, lon_pts, lat_pts, res_m: float, resampling: str):
    """Sample a projected COG at the given lon/lat points (flattened). Returns values (NaN outside) or None."""
    import rasterio
    from pyproj import Transformer
    from rasterio.enums import Resampling
    from rasterio.windows import from_bounds, Window
    with rasterio.Env(**GDAL_ENV), rasterio.open("/vsicurl/" + href) as ds:
        tf = Transformer.from_crs("EPSG:4326", ds.crs, always_xy=True)
        X, Y = tf.transform(lon_pts, lat_pts)
        b = ds.bounds
        inside = (X >= b.left) & (X < b.right) & (Y > b.bottom) & (Y <= b.top)
        out = np.full(X.shape, np.nan, np.float32)
        if not inside.any():
            return out
        pad = 2 * res_m
        win = from_bounds(max(X[inside].min() - pad, b.left), max(Y[inside].min() - pad, b.bottom),
                          min(X[inside].max() + pad, b.right), min(Y[inside].max() + pad, b.top), ds.transform)
        win = win.round_offsets().round_lengths().intersection(Window(0, 0, ds.width, ds.height))
        f = max(1.0, res_m / ds.res[0])
        oh, ow = max(1, int(round(win.height / f))), max(1, int(round(win.width / f)))
        arr = ds.read(1, window=win, out_shape=(oh, ow), resampling=getattr(Resampling, resampling))
        wt = ds.window_transform(win)
        pw, ph = wt.a * win.width / ow, -wt.e * win.height / oh
        ci = np.floor((X[inside] - wt.c) / pw).astype(int)
        ri = np.floor((wt.f - Y[inside]) / ph).astype(int)
        ok = (ci >= 0) & (ci < ow) & (ri >= 0) & (ri < oh)
        v = np.full(ci.shape, np.nan, np.float32)
        v[ok] = arr[ri[ok], ci[ok]]
        out[inside] = v
        return out


def sentinel2_layer(g: LGrid, date: dt.datetime, max_items: int = 8, scene_workers: int = 2) -> tuple[dict, dict]:
    t = time.time()
    nanf = np.full((g.n, g.n), np.nan, np.float32)
    meta = {"source": "Sentinel-2 L2A, Earth Search STAC (keyless)", "url": STAC, "collection": "sentinel-2-l2a",
            "window_days": 30, "end_date": date.strftime("%Y-%m-%d"), "clear_scl": list(CLEAR_SCL),
            "mapping": "NDVI=(B08-B04)/(B08+B04); NDMI=(B08-B11)/(B08+B11); dryness=clip((0.40-NDMI)/0.60,0,1); "
                       "green = NDVI>=0.50 & NDMI>=0.20", "errors": [], "scenes": []}
    try:
        items = _stac_search(g.bbox(), date)
    except Exception as ex:  # noqa: BLE001
        meta["errors"].append({"url": STAC, "error": repr(ex)[:200]})
        meta.update(coverage_pct=0.0, reason="STAC search failed", seconds=round(time.time() - t, 2))
        return {"ndvi": nanf, "ndmi": nanf, "dryness": nanf, "green": np.zeros((g.n, g.n), bool)}, meta
    s = 2
    res_m = max(20.0, g.cell / s / 2)                       # read ~2 source px per sample
    lon_ax, lat_ax = g.sub_axes(s)
    LA, LO = np.meshgrid(lat_ax, lon_ax, indexing="ij")
    lo, la = LO.ravel(), LA.ravel()
    ndvi_s = np.full(lo.shape, np.nan, np.float32)
    ndmi_s = np.full(lo.shape, np.nan, np.float32)
    date_s = np.full(lo.shape, -1, np.int16)
    tried = 0
    def read_scene(item):
        assets = item["assets"]
        with cf.ThreadPoolExecutor(4) as bands:
            fs = {k: bands.submit(_read_utm, assets[k]["href"], lo, la, res_m,
                                 "nearest" if k == "scl" else "average")
                  for k in ("scl", "red", "nir", "swir16")}
            return {k: f.result() for k, f in fs.items()}

    with cf.ThreadPoolExecutor(max_workers=scene_workers, thread_name_prefix="sentinel-scene") as scenes:
        pending = {}
        for index, it in enumerate(items):
            if tried >= max_items or np.isfinite(ndmi_s).mean() > 0.97:
                break
            need = ~np.isfinite(ndmi_s)
            # cheap bbox check before reading
            ib = it.get("bbox")
            if ib:
                m = need & (lo >= ib[0]) & (lo <= ib[2]) & (la >= ib[1]) & (la <= ib[3])
                if not m.any():
                    unused = pending.pop(index, None)
                    if unused is not None:
                        unused.cancel()
                    continue
            tried += 1
            A = it["assets"]
            current = pending.pop(index, None)
            if current is None:
                current = scenes.submit(read_scene, it)
            # One scene of lookahead overlaps independent range downloads. Consume
            # in the original order, preserving newest-clear-pixel selection exactly.
            if scene_workers > 1 and tried < max_items and not pending:
                for following in range(index + 1, len(items)):
                    bbox = items[following].get("bbox")
                    if bbox and not (need & (lo >= bbox[0]) & (lo <= bbox[2]) & (la >= bbox[1]) & (la <= bbox[3])).any():
                        continue
                    pending[following] = scenes.submit(read_scene, items[following])
                    break
            try:
                v = current.result()
            except Exception as e:  # noqa: BLE001
                meta["errors"].append({"item": it["id"], "error": repr(e)[:200]})
                continue

            def refl(k):
                rb = (A[k].get("raster:bands") or [{}])[0]
                x = v[k].astype(np.float64)
                x[x == 0] = np.nan
                return np.clip(x * rb.get("scale", 1e-4) + rb.get("offset", 0.0), 0.0, None)
            red, nir, sw = refl("red"), refl("nir"), refl("swir16")
            clear = (np.isin(v["scl"], CLEAR_SCL) & np.isfinite(red) & np.isfinite(nir) & np.isfinite(sw)
                     & (nir + red > 0.01) & (nir + sw > 0.01))
            fill = need & clear
            with np.errstate(invalid="ignore", divide="ignore"):
                ndvi_s[fill] = ((nir - red) / (nir + red))[fill]
                ndmi_s[fill] = ((nir - sw) / (nir + sw))[fill]
            k = len(meta["scenes"])
            date_s[fill] = k
            meta["scenes"].append({"id": it["id"], "datetime": it["properties"]["datetime"],
                                   "cloud_cover": it["properties"].get("eo:cloud_cover"),
                                   "sun_elevation_deg": it["properties"].get("view:sun_elevation"),
                                   "filled_samples_pct": round(100 * float(fill.mean()), 1)})
    shp = (g.n * s, g.n * s)

    def agg(a):
        a = a.reshape(shp).reshape(g.n, s, g.n, s)
        with np.errstate(invalid="ignore"), __import__("warnings").catch_warnings():
            __import__("warnings").simplefilter("ignore", RuntimeWarning)
            return np.nanmean(a, axis=(1, 3)).astype(np.float32)
    ndvi, ndmi = agg(ndvi_s), agg(ndmi_s)
    cov = round(100 * float(np.isfinite(ndmi).mean()), 1)
    meta.update(n_items_found=len(items), items_read=tried, coverage_pct=cov, read_res_m=res_m,
                seconds=round(time.time() - t, 2))
    low_sun = [x["sun_elevation_deg"] for x in meta["scenes"] if (x.get("sun_elevation_deg") or 90) < 25]
    if low_sun:
        meta["warning"] = ("low sun (%.0f deg): dark canopies have ~0 red reflectance, NDVI saturates near 1; "
                           "NDMI is more robust but leaf-off / snow season values are not fuel moisture" % min(low_sun))
    if not items:
        meta["reason"] = "no Sentinel-2 L2A scene with < 60 % cloud in the 30 days before end_date"
    elif cov == 0:
        meta["reason"] = "scenes found but no clear (SCL 4/5/7) pixels"
    with np.errstate(invalid="ignore"):
        dry = np.where(np.isfinite(ndmi), dryness_from_ndmi(ndmi), np.nan).astype(np.float32)
        green = green_flag(ndvi, ndmi)
    return {"ndvi": ndvi, "ndmi": ndmi, "dryness": dry, "green": green}, meta


# ------------------------------------------------------------------ cache + API
def _cache_key(*parts) -> str:
    return hashlib.sha1(json.dumps([VERSION, *parts]).encode()).hexdigest()[:16]


def _load_npz(f: Path):
    with np.load(f, allow_pickle=False) as z:
        lay = {k: z[k] for k in z.files if not k.startswith("frac__") and k != "__meta__"}
        fr = {k[6:]: z[k] for k in z.files if k.startswith("frac__")}
        if fr:
            lay["frac"] = fr
        meta = json.loads(str(z["__meta__"]))
    return lay, meta


def _save_npz(f: Path, lay: dict, meta: dict):
    CACHE.mkdir(parents=True, exist_ok=True)
    arrs = {k: v for k, v in lay.items() if k != "frac"}
    arrs.update({"frac__" + k: v for k, v in lay.get("frac", {}).items()})
    tmp = f.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp.npz")
    np.savez_compressed(tmp, __meta__=np.array(json.dumps(meta)), **arrs)
    os.replace(tmp, f)


FAIL_TTL_S = 900


def _cached_layer(name, fn, key_parts):
    """Disk cache per layer. Results with errors / zero coverage are cached only for FAIL_TTL_S
    (so a busy Overpass is retried later but repeated calls stay fast)."""
    f = CACHE / ("%s_%s.npz" % (name, _cache_key(name, *key_parts)))
    if f.exists():
        lay, meta = _load_npz(f)
        if not meta.get("failed_at") or time.time() - meta["failed_at"] < FAIL_TTL_S:
            meta["cached"] = True
            return lay, meta
    lay, meta = fn()
    if meta.get("coverage_pct", 0) == 0 or meta.get("errors"):
        meta["failed_at"] = time.time()
        meta["retry_after_s"] = FAIL_TTL_S
    _save_npz(f, lay, meta)
    meta["cached"] = False
    return lay, meta


# Optional barrier preparation is bounded and separate from the forecast result.
# The normal prewarmer also fills the same full-resolution caches.
_OSM_QUEUE = queue.Queue(maxsize=4)
_OSM_QUEUED = set()
_OSM_QUEUE_LOCK = threading.Lock()
_OSM_WORKER = None


def _prepare_osm_worker():
    while True:
        key, grid = _OSM_QUEUE.get()
        try:
            _cached_layer("osm", lambda: osm_layer(grid), key)
        except Exception:
            pass  # Optional data failure must not affect a completed forecast.
        finally:
            with _OSM_QUEUE_LOCK:
                _OSM_QUEUED.discard(key)
            _OSM_QUEUE.task_done()


def _schedule_osm(grid, key):
    global _OSM_WORKER
    with _OSM_QUEUE_LOCK:
        if key in _OSM_QUEUED:
            return True
        if _OSM_QUEUE.full():
            return False
        _OSM_QUEUED.add(key)
        _OSM_QUEUE.put_nowait((key, grid))
        if _OSM_WORKER is None or not _OSM_WORKER.is_alive():
            _OSM_WORKER = threading.Thread(target=_prepare_osm_worker, name="optional-osm", daemon=True)
            _OSM_WORKER.start()
    return True


def _optional_osm(grid, key):
    """Use existing barrier rasters/raw boxes without issuing a network request."""
    path = CACHE / ("osm_%s.npz" % _cache_key("osm", *key))
    refresh = True
    if path.exists():
        try:
            layers, meta = _load_npz(path)
            meta["cached"] = True
            refresh = bool(meta.get("failed_at") and time.time() - meta["failed_at"] >= FAIL_TTL_S)
        except Exception:
            layers, meta = osm_layer(grid, cache_only=True)
    else:
        layers, meta = osm_layer(grid, cache_only=True)
    meta.update(optional=True, cache_only=True)
    if meta.get("coverage_pct", 0) < 100:
        meta["reason"] = "Uncached OpenStreetMap barriers were not awaited; spread may cross unmapped roads or waterways."
    if refresh:
        meta["refresh_queued"] = _schedule_osm(grid, key)
    return layers, meta


def get_landscape(lat: float, lon: float, half_km: float, cell_m: float = 250.0, date=None,
                  layers=("landcover", "terrain", "osm", "sentinel2"), row_order: str = "south_up", osm_mode: str = "full") -> dict:
    """Landscape layers on the physics_spread grid centred on (lat, lon). See module docstring.

    date: simulation start (datetime/date/ISO str, UTC); Sentinel-2 uses scenes in the 30 days before it.
    row_order: "south_up" (default, row 0 = south, = physics_spread.Grid) or "north_up" (image order)."""
    if osm_mode not in ("full", "cached"):
        raise ValueError("osm_mode must be full or cached")
    t0 = time.time()
    lat, lon = round(float(lat), 5), round(float(lon), 5)
    g = LGrid(lat, lon, half_km * 1000.0, float(cell_m))
    if g.n > 801:
        raise ValueError("grid too large (%d cells/side); increase cell_m" % g.n)
    if date is None:
        date = dt.datetime.now(dt.timezone.utc)
    elif isinstance(date, str):
        date = dt.datetime.fromisoformat(date.replace("Z", "+00:00"))
    elif isinstance(date, dt.date) and not isinstance(date, dt.datetime):
        date = dt.datetime(date.year, date.month, date.day)
    if date.tzinfo is not None:
        date = date.astimezone(dt.timezone.utc).replace(tzinfo=None)
    date = date.replace(minute=0, second=0, microsecond=0)
    gk = (lat, lon, float(half_km), float(cell_m))
    jobs = {"landcover": (landcover_layer, gk), "terrain": (terrain_layer, gk), "osm": (osm_layer, gk),
            "sentinel2": (lambda gg: sentinel2_layer(gg, date), gk + (date.strftime("%Y-%m-%d"),))}
    out: dict = {}
    meta = {"grid": {**g.geometry(), "row_order": row_order,
                     "rows": "row 0 = SOUTH, increasing north (physics_spread order)" if row_order == "south_up"
                     else "row 0 = NORTH (image order)", "cols": "col 0 = WEST, increasing east"},
            "layers": {}, "date": date.strftime("%Y-%m-%dT%H:%MZ")}
    with cf.ThreadPoolExecutor(4) as ex:
        futs = {name: (ex.submit(_optional_osm, g, kp) if name == "osm" and osm_mode == "cached" else
                       ex.submit(_cached_layer, name, (lambda fn=fn: fn(g)), kp))
                for name, (fn, kp) in jobs.items() if name in layers}
        for name, fu in futs.items():
            try:
                lay, m = fu.result()
            except Exception as e:  # noqa: BLE001
                lay, m = {}, {"errors": [repr(e)[:300]], "coverage_pct": 0.0}
            out.update(lay)
            meta["layers"][name] = m
    # tropical biome refinement: WorldCover dense tree in the tropics -> FM8 (moist closed forest) unless
    # Sentinel-2 says the canopy is dry (dryness >= 0.6, i.e. NDMI <= 0.04): then savanna woodland -> FM2.
    if "fuel_model" in out and "dryness" in out and abs(lat) < 23.5:
        sw = (out["fuel_model"] == 8) & (np.nan_to_num(out["dryness"], nan=0.0) >= 0.6)
        if sw.any():
            out["fuel_model"] = np.where(sw, 2, out["fuel_model"]).astype(np.int16)
            out["physics_class"] = np.where(sw, FM_TO_PHYSICS[2], out["physics_class"]).astype(np.int8)
        meta["layers"]["landcover"]["tropical_dry_tree_to_FM2_cells"] = int(sw.sum())
    # combined barrier
    if "barrier_lines" in out or "burnable_frac" in out:
        bl = np.nan_to_num(out.get("barrier_lines", np.zeros((g.n, g.n))), nan=0.0)
        ba = np.nan_to_num(out.get("barrier_area", np.zeros((g.n, g.n))), nan=0.0)
        nb = np.zeros((g.n, g.n))
        if "burnable_frac" in out:                           # only where WorldCover has data
            valid = out["fuel_model"] > 0
            nb = np.where(valid, 1 - out["burnable_frac"], 0.0)
        out["barrier"] = (1 - (1 - bl) * (1 - np.maximum(ba, nb))).astype(np.float32)
    if row_order == "north_up":
        out = {k: ({kk: vv[::-1] for kk, vv in v.items()} if isinstance(v, dict) else v[::-1]) for k, v in out.items()}
    meta["seconds"] = round(time.time() - t0, 2)
    out["meta"] = meta
    return out


def summary(res: dict) -> dict:
    m = res["meta"]
    s = {"grid": "%d x %d cells of %g m" % (m["grid"]["n"], m["grid"]["n"], m["grid"]["cell_m"]),
         "seconds": m["seconds"], "layers": {}}
    for k, lm in m["layers"].items():
        s["layers"][k] = {"coverage_pct": lm.get("coverage_pct"), "seconds": lm.get("seconds"),
                          "cached": lm.get("cached"), "errors": len(lm.get("errors") or [])}
    if "fuel_model" in res:
        fm = res["fuel_model"]
        u, c = np.unique(fm, return_counts=True)
        s["fuel_models_pct"] = {(FM_NAMES.get(int(a)) or NB_NAMES.get(int(a)) or "nodata"): round(100 * b / fm.size, 1)
                                for a, b in zip(u, c)}
        s["landcover_pct"] = {k: round(100 * float(v.mean()), 1) for k, v in res["frac"].items() if v.mean() > 0.001}
        s["cropland_pct"] = round(100 * float(res["cropland"].mean()), 1)
    if "elevation" in res and np.isfinite(res["elevation"]).any():
        s["elevation_m"] = [round(float(np.nanmin(res["elevation"]))), round(float(np.nanmax(res["elevation"])))]
        s["slope_deg_mean_max"] = [round(float(np.nanmean(res["slope_deg"])), 1),
                                   round(float(np.nanmax(res["slope_deg"])), 1)]
    if "barrier" in res:
        s["barrier_cells_ge_0.5_pct"] = round(100 * float((res["barrier"] >= 0.5).mean()), 1)
        s["osm_features"] = m["layers"].get("osm", {}).get("feature_counts")
    if "ndmi" in res:
        ok = np.isfinite(res["ndmi"])
        if ok.any():
            s["ndvi_mean"] = round(float(np.nanmean(res["ndvi"])), 3)
            s["ndmi_mean"] = round(float(np.nanmean(res["ndmi"])), 3)
            s["dryness_mean"] = round(float(np.nanmean(res["dryness"])), 3)
            s["green_pct"] = round(100 * float(res["green"].mean()), 1)
        s2 = m["layers"].get("sentinel2", {})
        s["s2_scenes"] = [x["datetime"][:10] for x in s2.get("scenes", [])]
        if s2.get("reason"):
            s["s2_reason"] = s2["reason"]
    return s


def preview_png(res: dict, path: Path) -> Path | None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.colors import ListedColormap, BoundaryNorm
    except ImportError:
        return None
    m = res["meta"]
    so = m["grid"]["row_order"] == "south_up"
    w, s_, e, n = m["grid"]["bbox_wsen"]
    ext = [w, e, s_, n]
    kw = dict(origin="lower" if so else "upper", extent=ext)
    fig, ax = plt.subplots(2, 3, figsize=(15, 9.5))
    # fuel: colour-blind-friendly-ish categorical (Okabe-Ito based)
    codes = [0, 1, 2, 6, 8, 9, 10, 91, 92, 98, 99]
    cols = ["#ffffff", "#F0E442", "#E69F00", "#D55E00", "#004D33", "#009E73", "#00593C", "#CC79A7", "#DDDDDD",
            "#0072B2", "#999999"]
    lut = {c: i for i, c in enumerate(codes)}
    fmi = np.vectorize(lambda v: lut.get(int(v), 0))(res["fuel_model"])
    im = ax[0, 0].imshow(fmi, cmap=ListedColormap(cols), norm=BoundaryNorm(np.arange(-0.5, len(codes)), len(codes)),
                         interpolation="nearest", **kw)
    cb = fig.colorbar(im, ax=ax[0, 0], ticks=range(len(codes)))
    cb.ax.set_yticklabels(["nodata", "FM1", "FM2", "FM6", "FM8", "FM9", "FM10", "urban", "snow", "water", "bare"])
    ax[0, 0].set_title("fuel model (WorldCover 2021)")
    for a, k, cm, t, vr in [(ax[0, 1], "elevation", "terrain", "elevation m", None),
                            (ax[0, 2], "slope_deg", "magma_r", "slope deg", None),
                            (ax[1, 0], "barrier", "Greys", "barrier (stop prob.)", (0, 1)),
                            (ax[1, 1], "ndmi", "BrBG", "NDMI (Sentinel-2)", (-0.4, 0.6)),
                            (ax[1, 2], "dryness", "YlOrRd", "dryness 0..1", (0, 1))]:
        if k in res:
            im = a.imshow(res[k], cmap=cm, interpolation="nearest",
                          **({"vmin": vr[0], "vmax": vr[1]} if vr else {}), **kw)
            fig.colorbar(im, ax=a)
        a.set_title(t)
    for a in ax.ravel():
        a.set_aspect(1 / max(math.cos(math.radians(m["grid"]["centre_lat"])), 0.05))
        a.plot(m["grid"]["centre_lon"], m["grid"]["centre_lat"], "c+", ms=12)
    fig.suptitle("EquiFire landscape  %.4f, %.4f  %s" % (m["grid"]["centre_lat"], m["grid"]["centre_lon"], m["date"]))
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=80)
    plt.close(fig)
    return path


def main(argv=None):
    ap = argparse.ArgumentParser(description="Keyless landscape layers for the EquiFire physics grid")
    ap.add_argument("--lat", type=float, required=True)
    ap.add_argument("--lon", type=float, required=True)
    ap.add_argument("--half-km", type=float, default=20.0)
    ap.add_argument("--cell-m", type=float, default=250.0)
    ap.add_argument("--date", default=None, help="simulation start (ISO, UTC); default now")
    ap.add_argument("--no-png", action="store_true")
    a = ap.parse_args(argv)
    res = get_landscape(a.lat, a.lon, a.half_km, a.cell_m, a.date)
    print(json.dumps(summary(res), indent=1))
    for k, lm in res["meta"]["layers"].items():
        for e in (lm.get("errors") or [])[:3]:
            print("  [%s error] %s" % (k, e))
    if not a.no_png:
        p = preview_png(res, CACHE / ("preview_%.3f_%.3f_%g.png" % (a.lat, a.lon, a.half_km)))
        print("preview:", p or "matplotlib not installed, skipped")


if __name__ == "__main__":
    main()
