"""Geostationary fire detections every 10 min: NOAA GOES-R ABI L2 Fire/Hot Spot Characterization (FDC).

Source (free, keyless, no account): NOAA Open Data Dissemination on AWS, plain HTTPS
    https://noaa-goes19.s3.amazonaws.com  GOES-East  (75.2 W) operational since 2025-04-07 (replaced GOES-16)
    https://noaa-goes16.s3.amazonaws.com  GOES-East  2017 .. 2025-04-07 (bucket ends at 2025 day 097)
    https://noaa-goes18.s3.amazonaws.com  GOES-West  (137.0 W) operational since 2023-01-04 (replaced GOES-17)
    https://noaa-goes17.s3.amazonaws.com  GOES-West  2018 .. 2023-01
  listing: ?list-type=2&prefix=ABI-L2-FDCF/YYYY/DDD/HH/   (FDCF = full disk, 10 min, ~1.8 MB/file;
           FDCC = CONUS, 5 min, also supported via product="FDCC")

COVERAGE: the Americas only (GOES-East + GOES-West full disks: roughly 165 E .. 5 W across the dateline,
~ +-65 deg latitude usable; quality degrades at large view angles, the FDC product blocks out
local zenith angle > ~80 deg). Native pixel ~2 km at nadir, 3-6 km at mid/high latitudes.

NOT USED (not keyless): Himawari-9 fire products (JAXA P-Tree needs registration; the keyless NOAA bucket
noaa-himawari9 only holds AHI-L1b radiances and L2 Clouds/Winds/ISatSS, NO fire product), Meteosat
(EUMETSAT account). So Europe/Africa/Asia/Australia have no 10-min fire product here.

Mask codes (PUG L2+ vol. 5, FDC): fire pixel categories
    10 good  11 saturated  12 cloud-contaminated  13 high-prob  14 medium-prob  15 low-prob
    30..35 = the same six categories, temporally filtered (seen in recent scans too)
Power = fire radiative power (MW), Area = sub-pixel fire area (m2), Temp = fire temperature (K);
Area/Temp/Power are only retrieved for some categories (NaN otherwise).

Fixed grid -> lat/lon: GOES-R PUG vol. 3 sec. 4.2.8 (geostationary projection, GRS80 ellipsoid).

Cache: cache/goes/fires/<sat>_<product>_<scan start>.parquet = ALL fire pixels of that disk scan (small,
so any later bbox query reuses it; the .nc is deleted after parsing), cache/goes/list/*.json = listings.

    python -m equifire.goes fires --bbox -122,38,-120,40 --hours 6
    python -m equifire.goes growth --bbox -122,38,-120,40 --hours 6
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from .common import CACHE

DIR = CACHE / "goes"
FIRES = DIR / "fires"
LIST = DIR / "list"
TMP = DIR / "tmp"
UA = {"User-Agent": "equifire-goes/1.0"}

FIRE_CODES = {10: "good", 11: "saturated", 12: "cloudy", 13: "high", 14: "medium", 15: "low",
              30: "good", 31: "saturated", 32: "cloudy", 33: "high", 34: "medium", 35: "low"}
# rough ordinal confidence for filtering/weighting (good/saturated = highest)
CONF_RANK = {"good": 5, "saturated": 5, "high": 4, "medium": 3, "cloudy": 2, "low": 1}

# nominal sub-satellite longitudes (the per-file projection attributes are used for geolocation)
SAT_LON = {16: -75.2, 19: -75.2, 17: -137.2, 18: -137.0}
G16_END = dt.datetime(2025, 4, 7, 15)       # last GOES-16 FDCF file in the bucket: 2025 day 097
G17_END = dt.datetime(2023, 1, 4, 18)

# GRS80 constants (PUG); files carry the same values in goes_imager_projection
R_EQ = 6378137.0
R_POL = 6356752.31414
H_SAT = 35786023.0 + R_EQ
E2 = 1 - R_POL ** 2 / R_EQ ** 2


# ============================================================== projection (PUG vol. 3, 4.2.8)
def xy_to_latlon(x, y, lon0=-75.0, r_eq=R_EQ, r_pol=R_POL, h=H_SAT):
    """Fixed-grid scan angles x (E/W), y (N/S) in radians -> geodetic lat, lon (deg). NaN if off-earth."""
    x = np.asarray(x, float); y = np.asarray(y, float)
    a = np.sin(x) ** 2 + np.cos(x) ** 2 * (np.cos(y) ** 2 + (r_eq ** 2 / r_pol ** 2) * np.sin(y) ** 2)
    b = -2 * h * np.cos(x) * np.cos(y)
    c = h ** 2 - r_eq ** 2
    disc = b ** 2 - 4 * a * c
    with np.errstate(invalid="ignore"):
        rs = (-b - np.sqrt(disc)) / (2 * a)
    sx = rs * np.cos(x) * np.cos(y)
    sy = -rs * np.sin(x)
    sz = rs * np.cos(x) * np.sin(y)
    lat = np.degrees(np.arctan((r_eq ** 2 / r_pol ** 2) * sz / np.sqrt((h - sx) ** 2 + sy ** 2)))
    lon = lon0 - np.degrees(np.arctan(sy / (h - sx)))
    bad = disc < 0
    return np.where(bad, np.nan, lat), np.where(bad, np.nan, lon)


def latlon_to_xy(lat, lon, lon0=-75.0, r_eq=R_EQ, r_pol=R_POL, h=H_SAT):
    """Geodetic lat, lon (deg) -> fixed-grid x, y (rad); NaN where not visible from the satellite."""
    phi = np.radians(np.asarray(lat, float)); lam = np.radians(np.asarray(lon, float))
    e = np.sqrt(1 - r_pol ** 2 / r_eq ** 2)
    phic = np.arctan((r_pol ** 2 / r_eq ** 2) * np.tan(phi))
    rc = r_pol / np.sqrt(1 - e ** 2 * np.cos(phic) ** 2)
    sx = h - rc * np.cos(phic) * np.cos(lam - np.radians(lon0))
    sy = -rc * np.cos(phic) * np.sin(lam - np.radians(lon0))
    sz = rc * np.sin(phic)
    y = np.arctan(sz / sx)
    x = np.arcsin(-sy / np.sqrt(sx ** 2 + sy ** 2 + sz ** 2))
    vis = h * (h - sx) >= sy ** 2 + (r_eq ** 2 / r_pol ** 2) * sz ** 2
    return np.where(vis, x, np.nan), np.where(vis, y, np.nan)


def view_zenith_deg(lat, lon, sat_lon):
    """Approximate satellite view zenith angle (spherical earth)."""
    la = np.radians(lat); dl = np.radians(np.asarray(lon) - sat_lon)
    cosg = np.cos(la) * np.cos(dl)                       # central angle to sub-satellite point
    g = np.arccos(np.clip(cosg, -1, 1))
    r, hh = 6371.0, 35786.0 + 6371.0
    d = np.sqrt(r ** 2 + hh ** 2 - 2 * r * hh * cosg)
    return np.degrees(np.arcsin(np.clip(hh * np.sin(g) / d, -1, 1)))


def decode_mask(mask):
    """Mask codes -> (is_fire bool array, category str array, temporally_filtered bool array)."""
    m = np.asarray(mask).astype(int)
    fire = ((m >= 10) & (m <= 15)) | ((m >= 30) & (m <= 35))
    cat = np.array([FIRE_CODES.get(int(v), "") for v in m.ravel()], dtype=object).reshape(m.shape)
    return fire, cat, (m >= 30) & (m <= 35)


# ============================================================== bucket access
def sats_for(t: dt.datetime) -> dict:
    """{'east': n, 'west': n} operational satellites at time t (UTC, naive)."""
    return {"east": 16 if t < G16_END else 19, "west": 17 if t < G17_END else 18}


def choose_sats(bbox, t: dt.datetime, sat="auto") -> list[int]:
    """'auto' -> the satellite with the smaller view zenith at the bbox centre; 'both' -> east and west;
    'east'/'west' or an int (16/17/18/19) explicitly."""
    s = sats_for(t)
    if isinstance(sat, int) or (isinstance(sat, str) and sat.isdigit()):
        return [int(sat)]
    if sat in ("east", "west"):
        return [s[sat]]
    if sat == "both":
        return [s["east"], s["west"]]
    lon = (bbox[0] + bbox[2]) / 2; lat = (bbox[1] + bbox[3]) / 2
    ze = view_zenith_deg(lat, lon, SAT_LON[s["east"]]); zw = view_zenith_deg(lat, lon, SAT_LON[s["west"]])
    return [s["east"] if ze <= zw else s["west"]]


_FN = re.compile(r"_G(\d+)_s(\d{4})(\d{3})(\d{2})(\d{2})(\d{2})(\d)_")


def parse_start(key: str) -> dt.datetime:
    m = _FN.search(key)
    y, doy, H, M, S = (int(v) for v in m.groups()[1:6])
    return dt.datetime(y, 1, 1) + dt.timedelta(days=doy - 1, hours=H, minutes=M, seconds=S)


def list_hour(sat: int, hour: dt.datetime, product="FDCF") -> list[str]:
    """Object keys for one satellite/product/hour (cached; hours older than 1 h are cached forever)."""
    prefix = f"ABI-L2-{product}/{hour:%Y}/{hour.timetuple().tm_yday:03d}/{hour:%H}/"
    f = LIST / f"g{sat}_{prefix.replace('/', '_')}.json"
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    if f.exists() and (now - hour > dt.timedelta(hours=2) or time.time() - f.stat().st_mtime < 300):
        return json.loads(f.read_text())
    r = requests.get(f"https://noaa-goes{sat}.s3.amazonaws.com/", params={"list-type": "2", "prefix": prefix},
                     headers=UA, timeout=30)
    r.raise_for_status()
    keys = re.findall(r"<Key>([^<]+\.nc)</Key>", r.text)
    LIST.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(keys))
    return keys


def _fire_file(sat, product, start: dt.datetime) -> Path:
    return FIRES / f"g{sat}_{product}_{start:%Y%m%dT%H%M%S}.parquet"


def parse_fdc(path) -> pd.DataFrame:
    """All fire pixels of one FDC netCDF file -> DataFrame (lat, lon, frp, area, temp, mask, category, ...)."""
    import xarray as xr
    with xr.open_dataset(path, engine="netcdf4", mask_and_scale=False, decode_times=False) as ds:
        mask = ds["Mask"].values
        iy, ix = np.nonzero((mask >= 10) & (mask <= 35))
        keep = (mask[iy, ix] <= 15) | (mask[iy, ix] >= 30)
        iy, ix = iy[keep], ix[keep]
        m = mask[iy, ix].astype(int)
        del mask
        proj = ds["goes_imager_projection"].attrs
        xs = ds["x"].values[ix] * ds["x"].attrs["scale_factor"] + ds["x"].attrs["add_offset"]
        ys = ds["y"].values[iy] * ds["y"].attrs["scale_factor"] + ds["y"].attrs["add_offset"]
        dx = abs(float(ds["x"].attrs["scale_factor"]))
        rows, inv = np.unique(iy, return_inverse=True)     # read only the scan lines that hold fires
        vals = {}
        for v in ("Power", "Area", "Temp", "DQF"):
            a = ds[v].isel(y=rows).values[inv, ix]
            at = ds[v].attrs
            fill = at.get("_FillValue")
            out = a.astype(float)
            if fill is not None:
                out[a == fill] = np.nan
            out = out * float(at.get("scale_factor", 1.0)) + float(at.get("add_offset", 0.0))
            vals[v] = out
    lon0 = float(proj["longitude_of_projection_origin"])
    kw = dict(lon0=lon0, r_eq=float(proj["semi_major_axis"]), r_pol=float(proj["semi_minor_axis"]),
              h=float(proj["perspective_point_height"]) + float(proj["semi_major_axis"]))
    lat, lon = xy_to_latlon(xs, ys, **kw)
    # ground pixel size from neighbouring scan angles (km)
    la1, lo1 = xy_to_latlon(xs + dx, ys, **kw)
    la2, lo2 = xy_to_latlon(xs, ys + dx, **kw)
    kx = np.hypot((lo1 - lon) * 111.32 * np.cos(np.radians(lat)), (la1 - lat) * 111.32)
    ky = np.hypot((lo2 - lon) * 111.32 * np.cos(np.radians(lat)), (la2 - lat) * 111.32)
    return pd.DataFrame({
        "lat": lat, "lon": lon, "frp": vals["Power"], "area_m2": vals["Area"], "temp_k": vals["Temp"],
        "dqf": vals["DQF"], "mask": m, "category": [FIRE_CODES[c] for c in m],
        "conf_rank": [CONF_RANK[FIRE_CODES[c]] for c in m], "filtered": m >= 30,
        "px_x": ix.astype(np.int32), "px_y": iy.astype(np.int32), "px_km2": kx * ky})


def scan_fires(sat: int, key: str, product="FDCF") -> pd.DataFrame:
    """Fire pixels of one scan, from cache or by downloading + parsing (the .nc is removed afterwards)."""
    start = parse_start(key)
    f = _fire_file(sat, product, start)
    if f.exists():
        return pd.read_parquet(f)
    TMP.mkdir(parents=True, exist_ok=True)
    nc = TMP / Path(key).name
    for a in range(3):
        try:
            r = requests.get(f"https://noaa-goes{sat}.s3.amazonaws.com/{key}", headers=UA, timeout=120)
            r.raise_for_status()
            nc.write_bytes(r.content)
            break
        except requests.RequestException:
            if a == 2:
                raise
            time.sleep(3 * (a + 1))
    try:
        df = parse_fdc(nc)
    finally:
        nc.unlink(missing_ok=True)
    df.insert(0, "time", pd.Timestamp(start))
    df.insert(1, "sat", f"G{sat}")
    FIRES.mkdir(parents=True, exist_ok=True)
    df.to_parquet(f, index=False)
    return df


def _utc(t) -> dt.datetime:
    t = pd.Timestamp(t)
    if t.tzinfo is not None:
        t = t.tz_convert("UTC").tz_localize(None)
    return t.to_pydatetime()


def select_scans(sat: int, start: dt.datetime, end: dt.datetime, step_min: int, product="FDCF"):
    """Keys of scans in [start, end], thinned to one per step_min (the scan nearest each step)."""
    keys = []
    h = start.replace(minute=0, second=0, microsecond=0)
    while h <= end:
        try:
            keys += list_hour(sat, h, product)
        except requests.RequestException as e:
            print(f"[goes] listing failed G{sat} {h}: {e}")
        h += dt.timedelta(hours=1)
    ks = sorted(((parse_start(k), k) for k in keys), key=lambda x: x[0])
    ks = [(t, k) for t, k in ks if start <= t <= end]
    if step_min <= 10 or not ks:
        return [k for _, k in ks]
    out, nxt = [], None
    for t, k in ks:
        if nxt is None or t >= nxt:
            out.append(k)
            nxt = t + dt.timedelta(minutes=step_min) - dt.timedelta(minutes=2)
    return out


def goes_fires(bbox, start_utc, end_utc, sat="auto", step_min: int = 30, product="FDCF",
               min_conf: int = 1, workers: int = 3) -> pd.DataFrame:
    """GOES ABI fire pixels inside bbox = (lon_min, lat_min, lon_max, lat_max) between start and end (UTC).

    step_min: sampling interval (10 = every full-disk scan; default 30 -> 2 files/h/satellite, ~1.8 MB each).
    min_conf: drop categories below this CONF_RANK (1 = keep all incl. low probability, 4 = high+good+sat).
    Returns columns time, sat, lat, lon, frp (MW), area_m2, temp_k, mask, category, conf_rank, filtered,
    px_x, px_y, px_km2. Americas only."""
    start, end = _utc(start_utc), _utc(end_utc)
    sats = choose_sats(bbox, start, sat)
    jobs = []
    for s in sats:
        ks = select_scans(s, start, end, step_min, product)
        if not ks and s == 19 and start < G16_END + dt.timedelta(days=1):
            s, ks = 16, select_scans(16, start, end, step_min, product)
        jobs += [(s, k) for k in ks]
    frames = []
    with ThreadPoolExecutor(max(1, workers)) as ex:
        for df in ex.map(lambda sk: scan_fires(sk[0], sk[1], product), jobs):
            frames.append(df[(df.lon >= bbox[0]) & (df.lon <= bbox[2]) & (df.lat >= bbox[1]) & (df.lat <= bbox[3])
                             & (df.conf_rank >= min_conf)])
    cols = ["time", "sat", "lat", "lon", "frp", "area_m2", "temp_k", "mask", "category", "conf_rank",
            "filtered", "px_x", "px_y", "px_km2"]
    if not frames:
        return pd.DataFrame(columns=cols)
    out = pd.concat(frames, ignore_index=True)[cols].sort_values("time").reset_index(drop=True)
    out.attrs["scans"] = len(jobs)
    out.attrs["scan_times"] = sorted({str(parse_start(k)) for _, k in jobs})
    return out


def goes_growth(bbox, start_utc, end_utc, sat="auto", step_min: int = 30, min_conf: int = 1,
                freq="1h") -> pd.DataFrame:
    """Hourly growth series: per bin mean fire-pixel count per scan, mean FRP sum per scan (MW),
    max per scan, number of scans, and cumulative unique fire pixels / footprint area (km2) since start
    (a burned-area proxy for calibrating spread speed between polar overpasses). Bins with scans but no
    fire get zeros; bins without scans are NaN."""
    df = goes_fires(bbox, start_utc, end_utc, sat=sat, step_min=step_min, min_conf=min_conf)
    scan_times = pd.to_datetime(pd.Series(df.attrs.get("scan_times", [])))
    per = (df.groupby("time").agg(n_px=("lat", "size"), frp_sum=("frp", lambda s: np.nansum(s.values)))
           if len(df) else pd.DataFrame(columns=["n_px", "frp_sum"]))
    per = per.reindex(pd.DatetimeIndex(scan_times, name="time")).fillna(0.0)
    per["bin"] = per.index.floor(freq)
    g = per.groupby("bin").agg(scans=("n_px", "size"), n_px_mean=("n_px", "mean"), n_px_max=("n_px", "max"),
                               frp_sum_mean=("frp_sum", "mean"), frp_sum_max=("frp_sum", "max"))
    idx = pd.date_range(pd.Timestamp(_utc(start_utc)).floor(freq), pd.Timestamp(_utc(end_utc)).floor(freq),
                        freq=freq, name="hour")
    g = g.reindex(idx)
    # cumulative unique pixels (same satellite grid) and their footprint
    cum_n, cum_a = [], []
    seen, area = set(), 0.0
    if len(df):
        df = df.assign(bin=df.time.dt.floor(freq))
        for h in idx:
            sub = df[df.bin == h]
            for s, x, y, a in zip(sub.sat, sub.px_x, sub.px_y, sub.px_km2):
                if (s, x, y) not in seen:
                    seen.add((s, x, y)); area += float(a)
            cum_n.append(len(seen)); cum_a.append(area)
    else:
        cum_n = [0] * len(idx); cum_a = [0.0] * len(idx)
    g["cum_pixels"] = cum_n
    g["cum_area_km2"] = np.round(cum_a, 2)
    g["scans"] = g["scans"].fillna(0).astype(int)
    return g.reset_index()


def _main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["fires", "growth"])
    ap.add_argument("--bbox", required=True, help="lon_min,lat_min,lon_max,lat_max")
    ap.add_argument("--hours", type=float, default=6)
    ap.add_argument("--end", help="UTC end (default now)")
    ap.add_argument("--sat", default="auto")
    ap.add_argument("--step", type=int, default=30)
    a = ap.parse_args()
    bbox = [float(v) for v in a.bbox.split(",")]
    end = _utc(a.end) if a.end else dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    start = end - dt.timedelta(hours=a.hours)
    t0 = time.time()
    if a.cmd == "fires":
        df = goes_fires(bbox, start, end, a.sat, a.step)
        print(df.to_string(max_rows=40))
        print(f"{len(df)} fire pixels from {df.attrs.get('scans', 0)} scans in {time.time() - t0:.1f}s")
    else:
        print(goes_growth(bbox, start, end, a.sat, a.step).to_string())
        print(f"{time.time() - t0:.1f}s")


if __name__ == "__main__":
    _main()
