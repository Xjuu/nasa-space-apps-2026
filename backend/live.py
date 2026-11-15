"""Live anomaly alerts: NASA FIRMS near-real-time hotspots vs the 2008-2025 harmonized background.

Goal 3 of the challenge: raw hotspot counts carry no operational context, so instead of listing points we
compare the last 7 days of NRT activity in each grid region with what that region usually does in the same
calendar week, and warn only where activity is far above the historical norm.

Pipeline
--------
1. Feeds (no key needed): FIRMS public global CSVs for VIIRS NOAA-20 (J1), NOAA-21 (J2), S-NPP (if the file is
   not empty) and MODIS C6.1, 24h and 7d. Cached on disk in backend/.cache/ for 15 minutes; on a network error
   a stale copy is used and flagged.
2. Gridding: detections -> 0.05 deg UTC cell-days exactly like the archive (gx=floor((lon+180)/0.05),
   gy=floor((lat+90)/0.05)); a region (0.25 deg or 1 deg) aggregates burning cell-days, detections and FRP.
3. Baseline (built once, backend/.cache/live_baseline.parquet): burning cell-days per region, calendar week
   (week = day_of_year // 7, 0..51) and year 2008-2025.
   * 2012-01-20 .. 2025: real S-NPP VIIRS only (cache/cells/viirs-snpp_*), ONE satellite in every year, so the
     NOAA-20 addition in 2018 does not inflate later years.
   * 2008 .. 2012-01-19: MODIS-era rows reconstructed to VIIRS-equivalent (cache/viirs_equiv_daily.parquet,
     source='reconstructed'). The reconstruction undercounts burning cells (~0.74x of real VIIRS on the
     2021-2025 test years), so its counts are multiplied by k = real VIIRS cell-days / reconstructed cell-days
     measured on the 2022-2025 overlap in the same regions (stored in the metadata).
   * Only countries fully present in the archive are used (all VIIRS + MODIS years in cache/cells).
   * Footprint: the archive is clipped to country borders, so live detections are only compared where the
     archive could have seen them: a 0.05-degree coverage mask = cells with any archive detection (any sensor,
     2008-2025, archived countries), dilated by one cell (~5 km). Regions whose live detections all fall outside
     the mask (neighbouring countries, never-burned land) and regions without any S-NPP record are reported as
     "no baseline", never as zero.
4. Normalisation of the live feed: the observation uses ONE VIIRS satellite, NOAA-20 (same VIIRS instrument
   and orbit plane as S-NPP, ~50 min apart), so obs and baseline are like-for-like single-satellite
   cell-day counts. Fallback order if NOAA-20 is empty: S-NPP, NOAA-21. All-satellite counts are reported
   alongside but not used for z.
5. Z: obs = burning NOAA-20 cell-days over the 7d feed; baseline per year = mean of weeks w-1, w, w+1
   (+-1 week smoothing) where w is the week of the middle of the 7-day window;
   z = (obs - mean) / max(std, 0.3*mean, sqrt(mean)+1)  (same floor as equifire/seasonal.py).
   Levels: watch z>=2, warning z>=3, critical z>=5. out_of_season when the region burned in <=25% of the
   baseline years in this (smoothed) week.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import io
import json
import math
import os
import re
import threading
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import pandas as pd

from equifire.common import CACHE, EPOCH, GRID

CACHE_DIR = Path(__file__).resolve().parent / ".cache"
BASELINE_F = CACHE_DIR / "live_baseline.parquet"
BASELINE_META = CACHE_DIR / "live_baseline.json"
TTL = 900
BASE_URL = "https://firms.modaps.eosdis.nasa.gov/data/active_fire"
FEEDS = {  # name -> (path, satellite label)
    "N20": ("noaa-20-viirs-c2/csv/J1_VIIRS_C2_Global_{w}.csv", "VIIRS NOAA-20"),
    "N21": ("noaa-21-viirs-c2/csv/J2_VIIRS_C2_Global_{w}.csv", "VIIRS NOAA-21"),
    "N": ("suomi-npp-viirs-c2/csv/SUOMI_VIIRS_C2_Global_{w}.csv", "VIIRS S-NPP"),
    "MODIS": ("modis-c6.1/csv/MODIS_C6_1_Global_{w}.csv", "MODIS Terra/Aqua"),
}
OBS_ORDER = ["N20", "N", "N21"]       # single satellite used for z (first non-empty)
YEARS = list(range(2008, 2026))
VIIRS_START = int((np.datetime64("2012-01-20") - EPOCH).astype(int))
NWEEK = 52
RES = {0.25: 5, 1.0: 20}               # region size -> 0.05 deg cells per side
LEVELS = [("critical", 5.0), ("warning", 3.0), ("watch", 2.0)]
LEVEL_RANK = {"none": 0, "watch": 1, "warning": 2, "critical": 3}
OUT_OF_SEASON_FRAC = 0.25
COUNTRIES_FILE_RE = re.compile(r"^(viirs-snpp|viirs-jpss1|modis)_(\d{4})_(.+)\.parquet$")


# ------------------------------------------------------------------ regions
def region_id(gx, gy, res: float):
    s = RES[res]
    ny = int(round(180 / res))
    return (np.asarray(gx, np.int64) // s) * ny + np.asarray(gy, np.int64) // s


def region_bounds(rid: int, res: float) -> tuple[float, float, float, float]:
    ny = int(round(180 / res))
    rx, ry = divmod(int(rid), ny)
    lon0, lat0 = rx * res - 180, ry * res - 90
    return round(lon0, 4), round(lat0, 4), round(lon0 + res, 4), round(lat0 + res, 4)


def week_of(day_index: int) -> int:
    d = (EPOCH + np.timedelta64(int(day_index), "D")).astype("datetime64[D]")
    doy = int((d - d.astype("datetime64[Y]").astype("datetime64[D]")).astype(int))
    return min(doy // 7, NWEEK - 1)


def day_week_year(day):
    d = EPOCH + np.asarray(day).astype("timedelta64[D]")
    y0 = d.astype("datetime64[Y]")
    doy = (d - y0.astype("datetime64[D]")).astype(int)
    return np.minimum(doy // 7, NWEEK - 1), y0.astype(int) + 1970


def level_for(z: float | None) -> str:
    if z is None:
        return "none"
    for name, thr in LEVELS:
        if z >= thr:
            return name
    return "none"


# ------------------------------------------------------------------ feeds
_feed_lock = threading.Lock()
_feed_locks = {}
_parsed_feeds = {}


def _lock_for_feed(name, window):
    # Unrelated satellites/windows must not block each other's network downloads.
    # The same file still has one writer and an atomic replace.
    with _feed_lock:
        return _feed_locks.setdefault((name, window), threading.Lock())


def _download(url: str, timeout: float = 60) -> bytes:
    import httpx
    r = httpx.get(url, timeout=timeout, follow_redirects=True, headers={"User-Agent": "equifire-demo"})
    r.raise_for_status()
    return r.content


def fetch_feed(name: str, window: str = "24h", fetch=None) -> dict:
    """Raw CSV bytes of one public FIRMS feed, cached on disk for TTL seconds.

    Returns {"name", "window", "bytes", "fetched" (UTC iso), "stale" (bool), "error" (str|None)}."""
    fetch = fetch or _download
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    f = CACHE_DIR / f"nrt_{name}_{window}.csv"
    with _lock_for_feed(name, window):
        fresh = f.exists() and time.time() - f.stat().st_mtime < TTL
        err = None
        if not fresh:
            try:
                data = fetch(f"{BASE_URL}/{FEEDS[name][0].format(w=window)}")
                tmp = f.with_suffix(".tmp")
                tmp.write_bytes(data)
                tmp.replace(f)
            except Exception as e:  # network down: keep a stale copy if any
                err = f"{type(e).__name__}: {e}"[:200]
        if not f.exists():
            return {"name": name, "window": window, "bytes": b"", "fetched": None, "stale": True, "error": err}
        mt = dt.datetime.fromtimestamp(f.stat().st_mtime, dt.timezone.utc).replace(microsecond=0)
        return {"name": name, "window": window, "bytes": f.read_bytes(), "fetched": mt.isoformat(),
                "stale": err is not None, "error": err,
                "cache_version": (f.stat().st_mtime_ns, f.stat().st_size)}


def parse_feed(raw: bytes, name: str) -> pd.DataFrame:
    """FIRMS CSV -> detections with lat, lon, frp, t (UTC datetime64), day (archive day index), gx, gy, sat."""
    cols = ["lat", "lon", "frp", "t", "day", "gx", "gy", "sat", "conf"]
    if len(raw) < 200:  # header only / empty file (S-NPP sometimes)
        return pd.DataFrame({c: [] for c in cols})
    df = pd.read_csv(io.BytesIO(raw), usecols=lambda c: c in {"latitude", "longitude", "frp", "acq_date",
                                                               "acq_time", "confidence", "satellite"},
                     dtype={"acq_time": str, "confidence": str, "satellite": str})
    if df.empty:
        return pd.DataFrame({c: [] for c in cols})
    hhmm = df["acq_time"].str.zfill(4)
    t = pd.to_datetime(df["acq_date"] + " " + hhmm.str[:2] + ":" + hhmm.str[2:], format="%Y-%m-%d %H:%M",
                       errors="coerce")
    lat, lon = df["latitude"].to_numpy(float), df["longitude"].to_numpy(float)
    day = ((t.dt.floor("D").to_numpy().astype("datetime64[D]") - EPOCH).astype(int))
    out = pd.DataFrame({
        "lat": lat, "lon": lon, "frp": pd.to_numeric(df["frp"], errors="coerce").fillna(0).to_numpy(float),
        "t": t.to_numpy(), "day": day,
        "gx": np.floor((lon + 180) / GRID).astype(np.int64), "gy": np.floor((lat + 90) / GRID).astype(np.int64),
        "sat": name, "conf": df.get("confidence", pd.Series([""] * len(df))).astype(str).to_numpy()})
    return out[out["t"].notna()].reset_index(drop=True)


def _parsed_feed(result, name, window):
    # Live forecasting reads the same history for guidance, ignition and calibration.
    # Reuse the CSV parse only while that exact on-disk source version is unchanged.
    key = (str(CACHE_DIR), name, window)
    version = result.get("cache_version")
    with _lock_for_feed(name, window):
        with _feed_lock:
            hit = _parsed_feeds.get(key)
        if version is not None and hit is not None and hit[0] == version:
            return hit[1].copy(), hit[2]
        error = None
        try:
            data = parse_feed(result["bytes"], name)
        except Exception as exc:
            data = parse_feed(b"", name)
            error = f"parse: {type(exc).__name__}"
        if version is not None:
            with _feed_lock:
                _parsed_feeds[key] = (version, data.copy(), error)
                while len(_parsed_feeds) > 16:
                    _parsed_feeds.pop(next(iter(_parsed_feeds)))
        return data, error


def load_feeds(window: str = "7d", names=None, fetch=None) -> tuple[pd.DataFrame, list[dict]]:
    from concurrent.futures import ThreadPoolExecutor
    frames, info = [], []
    selected = list(names or FEEDS)
    # Four independent public CSVs; preserve source order and all error/stale metadata.
    with ThreadPoolExecutor(max_workers=max(1, min(4, len(selected))), thread_name_prefix="firms-feed") as pool:
        results = list(pool.map(lambda name: fetch_feed(name, window, fetch=fetch), selected))
    for name, r in zip(selected, results):
        d, parse_error = _parsed_feed(r, name, window)
        r["error"] = r["error"] or parse_error
        frames.append(d)
        info.append({"feed": name, "satellite": FEEDS[name][1], "window": window, "fetched": r["fetched"],
                     "stale": r["stale"], "error": r["error"], "detections": int(len(d)),
                     "latest": None if d.empty else pd.Timestamp(d["t"].max()).isoformat() + "Z",
                     "empty": d.empty})
    df = pd.concat(frames, ignore_index=True) if frames else parse_feed(b"", "x")
    df = df.astype({"day": np.int64, "gx": np.int64, "gy": np.int64, "lat": float, "lon": float, "frp": float})
    df["t"] = pd.to_datetime(df["t"])
    return df, info


# ------------------------------------------------------------------ baseline
def complete_countries(cells_dir: Path = CACHE / "cells") -> list[str]:
    have: dict[str, dict[str, set]] = {}
    for f in cells_dir.glob("*.parquet"):
        m = COUNTRIES_FILE_RE.match(f.name)
        if m:
            have.setdefault(m.group(3), {}).setdefault(m.group(1), set()).add(int(m.group(2)))
    need_v, need_m = set(range(2012, 2026)), set(range(2008, 2012))
    return sorted(c for c, d in have.items() if need_v <= d.get("viirs-snpp", set()) and need_m <= d.get("modis", set()))


def _weekly(day, gx, gy) -> pd.DataFrame:
    """Unique cell-days -> rows (res, rid, year, week, cnt)."""
    k = np.unique(np.asarray(day, np.int64) * 10**9 + np.asarray(gx, np.int64) * 10**4 + np.asarray(gy, np.int64))
    day, rem = np.divmod(k, 10**9)
    gx, gy = np.divmod(rem, 10**4)
    wk, yr = day_week_year(day)
    out = []
    for res in RES:
        rid = region_id(gx, gy, res)
        g = pd.DataFrame({"rid": rid, "year": yr, "week": wk}).value_counts().rename("cnt").reset_index()
        g["res"] = res
        out.append(g)
    return pd.concat(out, ignore_index=True)


def build_baseline(cells_dir: Path = CACHE / "cells", equiv_f: Path = CACHE / "viirs_equiv_daily.parquet",
                   out: Path = BASELINE_F, verbose: bool = True) -> pd.DataFrame:
    """Precompute weekly burning cell-days per region and year (see module docstring). ~1-2 GB peak RAM."""
    import pyarrow.parquet as pq
    t0 = time.time()
    countries = complete_countries(cells_dir)
    parts = []
    overlap_real = []
    for c in countries:
        for y in range(2012, 2026):
            f = cells_dir / f"viirs-snpp_{y}_{c}.parquet"
            t = pq.read_table(f, columns=["day", "gx", "gy"]).to_pandas()
            t = t[t["day"] >= VIIRS_START]
            parts.append(_weekly(t["day"].to_numpy(), t["gx"].to_numpy(), t["gy"].to_numpy()).assign(src="viirs"))
            if y >= 2022:  # real VIIRS (S-NPP + NOAA-20, as the reconstruction targets) for the calibration
                fj = cells_dir / f"viirs-jpss1_{y}_{c}.parquet"
                if fj.exists():
                    t = pd.concat([t, pq.read_table(fj, columns=["day", "gx", "gy"]).to_pandas()])
                u = t.drop_duplicates(["day", "gx", "gy"])
                overlap_real.append(pd.Series(region_id(u["gx"], u["gy"], 1.0)).value_counts())
            if verbose:
                print(f"[live] baseline {c} {y} ({time.time() - t0:.0f}s)", flush=True)
    viirs = pd.concat(parts, ignore_index=True)
    viirs = viirs.groupby(["res", "rid", "year", "week", "src"], observed=True)["cnt"].sum().reset_index()
    foot1 = set(viirs.loc[viirs["res"] == 1.0, "rid"].unique())

    # reconstructed MODIS era + its calibration on the 2022-2025 overlap
    eq = pd.read_parquet(equiv_f, columns=["day", "gx", "gy"])
    real_over = pd.concat(overlap_real).groupby(level=0).sum()
    lo22 = int((np.datetime64("2022-01-01") - EPOCH).astype(int))
    ov = eq[eq["day"] >= lo22]
    rec_over = pd.Series(region_id(ov["gx"], ov["gy"], 1.0)).value_counts()
    common = sorted(set(real_over.index) & set(rec_over.index) & foot1)
    k = float(real_over.reindex(common).sum() / max(rec_over.reindex(common).sum(), 1))
    old = eq[eq["day"] < VIIRS_START]
    del eq, ov
    rec = _weekly(old["day"].to_numpy(), old["gx"].to_numpy(), old["gy"].to_numpy()).assign(src="reconstructed")
    rec["cnt"] = rec["cnt"] * k
    df = pd.concat([viirs, rec], ignore_index=True)
    df = df.groupby(["res", "rid", "year", "week", "src"], observed=True)["cnt"].sum().reset_index()
    df = df.astype({"res": np.float32, "rid": np.int32, "year": np.int16, "week": np.int8, "cnt": np.float32})
    df["src"] = df["src"].astype("category")
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out, index=False)
    meta = {"built": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
            "countries": countries, "years": YEARS, "reconstructed_years": [2008, 2009, 2010, 2011],
            "reconstructed_until": "2012-01-19", "recon_scale_k": round(k, 4),
            "recon_scale_note": "reconstructed counts x k, k = real VIIRS / reconstructed burning cell-days on "
                                "2022-2025 in the archive footprint (1 deg)",
            "viirs_baseline_satellite": "S-NPP only (one satellite every year 2012-2025)",
            "observation_satellite": "NOAA-20 only (fallback S-NPP, NOAA-21)",
            "week": "day_of_year // 7 (0..51), smoothed over weeks w-1..w+1",
            "rows": int(len(df)), "seconds": round(time.time() - t0, 1)}
    BASELINE_META.write_text(json.dumps(meta, indent=1)) if out == BASELINE_F else \
        out.with_suffix(".json").write_text(json.dumps(meta, indent=1))
    if verbose:
        print(f"[live] baseline {len(df):,} rows, k={k:.3f}, {time.time() - t0:.0f}s", flush=True)
    return df


NX, NY = int(round(360 / GRID)), int(round(180 / GRID))
MASK_F = CACHE_DIR / "live_mask.npy"
MASK_DILATE = 1  # cells (~5.5 km)


def build_mask(cells_dir: Path = CACHE / "cells", out: Path = MASK_F, countries=None) -> np.ndarray:
    """0.05-degree coverage mask: cells with any archive detection (S-NPP, NOAA-20 or MODIS, 2008-2025) in a
    fully archived country, dilated by MASK_DILATE cells. The archive is clipped to country borders, so NRT
    detections outside this mask (neighbouring countries, never-burned land) have no comparable baseline."""
    import pyarrow.parquet as pq
    from scipy import ndimage
    m = np.zeros((NX, NY), bool)
    for c in countries or complete_countries(cells_dir):
        for f in cells_dir.glob(f"*_{c}.parquet"):
            if COUNTRIES_FILE_RE.match(f.name) and COUNTRIES_FILE_RE.match(f.name).group(3) == c:
                t = pq.read_table(f, columns=["gx", "gy"])
                m[t["gx"].to_numpy(), t["gy"].to_numpy()] = True
    if MASK_DILATE:
        m = ndimage.binary_dilation(m, iterations=MASK_DILATE, structure=np.ones((3, 3), bool))
    out.parent.mkdir(parents=True, exist_ok=True)
    np.save(out, np.packbits(m, axis=None))
    return m


def load_mask(path: Path = MASK_F) -> np.ndarray:
    return np.unpackbits(np.load(path))[: NX * NY].reshape(NX, NY).astype(bool)


class Baseline:
    """Weekly counts table -> per-region stats for one calendar week."""

    def __init__(self, df: pd.DataFrame, meta: dict, mask: np.ndarray | None = None):
        self.df, self.meta, self.mask = df, meta, mask
        self.years = list(meta.get("years", YEARS))
        self.recon_years = set(meta.get("reconstructed_years", []))
        self._foot = {res: set(df.loc[(df["res"] == res) & (df["src"] == "viirs"), "rid"].unique()) for res in RES}
        self._cache: dict = {}

    @classmethod
    def load(cls, path: Path = BASELINE_F) -> "Baseline":
        meta_f = BASELINE_META if path == BASELINE_F else path.with_suffix(".json")
        mask_f = MASK_F if path == BASELINE_F else path.with_suffix(".mask.npy")
        if not mask_f.exists():
            build_mask(out=mask_f)
        return cls(pd.read_parquet(path), json.loads(meta_f.read_text()) if meta_f.exists() else {},
                   load_mask(mask_f))

    def in_mask(self, gx, gy) -> np.ndarray:
        gx, gy = np.asarray(gx, np.int64), np.asarray(gy, np.int64)
        if self.mask is None:
            return np.ones(len(gx), bool)
        ok = (gx >= 0) & (gx < NX) & (gy >= 0) & (gy < NY)
        out = np.zeros(len(gx), bool)
        out[ok] = self.mask[gx[ok], gy[ok]]
        return out

    def covered(self, rid: int, res: float) -> bool:
        return int(rid) in self._foot[res]

    def week_stats(self, week: int, res: float) -> pd.DataFrame:
        """index rid -> mean, std, years_burned, values (per-year smoothed counts)."""
        key = (week, res)
        if key in self._cache:
            return self._cache[key]
        wks = {(week - 1) % NWEEK, week, (week + 1) % NWEEK}
        d = self.df[(self.df["res"] == res) & self.df["week"].isin(list(wks)) & self.df["year"].isin(self.years)]
        piv = d.groupby(["rid", "year"])["cnt"].sum().div(3.0).unstack("year")
        piv = piv.reindex(columns=self.years).fillna(0.0)
        vals = piv.to_numpy(float)
        st = pd.DataFrame({"mean": vals.mean(1), "std": vals.std(1, ddof=1) if len(self.years) > 1 else 0.0,
                           "years_burned": (vals > 0).sum(1)}, index=piv.index)
        st["values"] = list(np.round(vals, 2))
        self._cache[key] = st
        return st


_baseline: Baseline | None = None
_baseline_lock = threading.Lock()
_baseline_status = {"status": "missing", "error": None}


def get_baseline(build: bool = True) -> Baseline | None:
    """Cached baseline; builds it (blocking, once) when missing and build=True."""
    global _baseline
    if _baseline is not None:
        return _baseline
    with _baseline_lock:
        if _baseline is None:
            if not BASELINE_F.exists():
                if not build:
                    return None
                _baseline_status["status"] = "building"
                try:
                    build_baseline()
                except Exception as e:
                    _baseline_status.update(status="error", error=f"{type(e).__name__}: {e}"[:300])
                    raise
            _baseline = Baseline.load()
            _baseline_status["status"] = "ready"
    return _baseline


def set_baseline(b: Baseline | None) -> None:
    global _baseline
    _baseline = b
    _baseline_status["status"] = "ready" if b is not None else "missing"


# ------------------------------------------------------------------ live z
def z_score(obs: float, mean: float, std: float) -> float:
    spread = max(std, 0.3 * mean, math.sqrt(mean) + 1.0)
    return (obs - mean) / spread


def compute_regions(det: pd.DataFrame, base: Baseline, res: float = 1.0, now: dt.datetime | None = None) -> dict:
    """All regions with NRT activity in the last 7 days, with z and alert level."""
    obs_sat = next((s for s in OBS_ORDER if (det["sat"] == s).any()), None)
    if det.empty or obs_sat is None:
        return {"regions": [], "obs_satellite": obs_sat, "window": None, "week": None}
    t_end = pd.Timestamp(det["t"].max())
    t_start = t_end - pd.Timedelta(days=7)
    det = det[det["t"] > t_start].copy()
    mid = t_end - pd.Timedelta(hours=84)
    mid_day = int((np.datetime64(mid.floor("D").date()) - EPOCH).astype(int))
    week = week_of(mid_day)
    det["rid"] = region_id(det["gx"], det["gy"], res)
    det["cov"] = base.in_mask(det["gx"], det["gy"])
    outside = det[~det["cov"]].groupby("rid").size()
    det_all = det
    det = det[det["cov"]]
    t24 = t_end - pd.Timedelta(hours=24)
    one = det[det["sat"] == obs_sat]
    obs = one.drop_duplicates(["day", "gx", "gy"]).groupby("rid").size()
    viirs = det[det["sat"] != "MODIS"]
    all_cd = viirs.drop_duplicates(["day", "gx", "gy"]).groupby("rid").size()
    st = base.week_stats(week, res)
    nyears = len(base.years)
    nrec = sum(1 for y in base.years if y in base.recon_years)
    out = []
    for rid, g in det_all.groupby("rid"):
        inside = g[g["cov"]]
        if len(inside):
            g = inside
        b = region_bounds(rid, res)
        top = g.loc[g["frp"].idxmax()]
        o = int(obs.get(rid, 0))
        r = {"rid": int(rid), "res": res, "bounds": b,
             "lat": round((b[1] + b[3]) / 2, 4), "lon": round((b[0] + b[2]) / 2, 4),
             "obs": o, "obs_all_viirs": int(all_cd.get(rid, 0)), "detections": int(len(g)),
             "detections_24h": int((g["t"] > t24).sum()),
             "frp_sum": round(float(g["frp"].sum()), 1), "top_frp": round(float(top["frp"]), 1),
             "top_frp_lat": round(float(top["lat"]), 4), "top_frp_lon": round(float(top["lon"]), 4),
             "latest": pd.Timestamp(g["t"].max()).isoformat() + "Z",
             "satellites": sorted({FEEDS[s][1] for s in g["sat"].unique()}),
             "detections_outside_coverage": int(outside.get(rid, 0)),
             "years_used": nyears, "years_reconstructed": nrec}
        if not len(inside) or not base.covered(rid, res):
            r.update(baseline="none", z=None, level="none", mean=None, std=None, ratio=None,
                     years_burned=None, out_of_season=None,
                     note="no baseline: outside the archived countries, or no archive fire within ~5 km in 2008-2025")
        else:
            if rid in st.index:
                s = st.loc[rid]
                mean, std, yb, vals = float(s["mean"]), float(s["std"]), int(s["years_burned"]), s["values"]
            else:
                mean, std, yb, vals = 0.0, 0.0, 0, np.zeros(nyears)
            z = z_score(o, mean, std)
            r.update(baseline="ok", z=round(z, 2), level=level_for(z), mean=round(mean, 2), std=round(std, 2),
                     ratio=None if mean <= 0 else round(o / mean, 1), years_burned=yb,
                     out_of_season=bool(yb <= OUT_OF_SEASON_FRAC * nyears),
                     history={str(y): float(v) for y, v in zip(base.years, vals)})
        out.append(r)
    out.sort(key=lambda r: (r["z"] is None, -(r["z"] or 0), -r["obs"]))
    return {"regions": out, "obs_satellite": obs_sat, "week": week,
            "window": {"start": t_start.isoformat() + "Z", "end": t_end.isoformat() + "Z"}}


# ------------------------------------------------------------------ cached orchestration
_live_cache: dict = {}
_refresh = {"thread": None, "error": None}


def live_state(res: float = 1.0, fetch=None, base: Baseline | None = None) -> dict:
    """Feeds (7d) + regions for one resolution; recomputed only when the feed cache refreshes. Blocking."""
    base = base or get_baseline()
    det, info = load_feeds("7d", fetch=fetch)
    tag = (tuple((i["feed"], i["fetched"], i["detections"]) for i in info), id(base))
    hit = _live_cache.get(res)
    if hit and hit[0] == tag:
        return hit[1]
    val = compute_regions(det, base, res)
    val["feeds"] = info
    val["computed"] = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    val["baseline"] = {k: base.meta.get(k) for k in ("built", "countries", "years", "reconstructed_years",
                                                      "recon_scale_k", "viirs_baseline_satellite",
                                                      "observation_satellite", "week")}
    _live_cache[res] = (tag, val, time.time())
    return val


def _refresh_bg(res_list):
    try:
        for res in res_list:
            live_state(res)
        _refresh["error"] = None
    except Exception as e:
        _refresh["error"] = f"{type(e).__name__}: {e}"[:300]


def get_live(res: float = 1.0) -> dict | None:
    """Non-blocking: the latest computed state (refreshed in a background thread when older than TTL), or
    None while the first download / baseline build is still running (see refresh_status())."""
    hit = _live_cache.get(res)
    t = _refresh["thread"]
    if (hit is None or time.time() - hit[2] > TTL) and (t is None or not t.is_alive()):
        others = [r for r in RES if r != res]
        th = threading.Thread(target=_refresh_bg, args=([res] + others,), daemon=True, name="live-refresh")
        _refresh["thread"] = th
        th.start()
    return None if hit is None else hit[1]


def refresh_status() -> dict:
    t = _refresh["thread"]
    return {"running": bool(t and t.is_alive()), "error": _refresh["error"], "baseline": _baseline_status["status"]}


def in_bbox(r: dict, box) -> bool:
    if box is None:
        return True
    x0, y0, x1, y1 = box
    b = r["bounds"]
    return b[0] < x1 and b[2] > x0 and b[1] < y1 and b[3] > y0


def headline(r: dict) -> str:
    times = f"{r['ratio']}x the usual" if r.get("ratio") else "far above the usual (normally ~0)"
    s = (f"{r['level'].upper()}: fire activity near {r['lat']:.2f}, {r['lon']:.2f} is {times} for this week "
         f"(z={r['z']}): {r['obs']} burning cells in 7 days vs {r['mean']} +- {r['std']} in {r['years_used']} years")
    if r.get("out_of_season"):
        s += "; out of season"
    return s


def to_geojson(regions: list[dict]) -> dict:
    feats = []
    for r in regions:
        x0, y0, x1, y1 = r["bounds"]
        props = {k: v for k, v in r.items() if k != "bounds"}
        if r.get("z") is not None:
            props["headline"] = headline(r)
        feats.append({"type": "Feature", "properties": props, "geometry": {
            "type": "Polygon", "coordinates": [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]]}})
    return {"type": "FeatureCollection", "features": feats}


# ------------------------------------------------------------------ CAP
CAP_NS = "urn:oasis:names:tc:emergency:cap:1.2"
CAP_SEVERITY = {"critical": "Extreme", "warning": "Severe", "watch": "Moderate"}


def to_cap(regions: list[dict], now: dt.datetime | None = None, window: dict | None = None) -> bytes:
    """CAP 1.2 alert with one <info>/<area> per warning-or-higher region."""
    now = now or dt.datetime.now(dt.timezone.utc)
    iso = lambda t: t.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat()  # noqa: E731
    ET.register_namespace("", CAP_NS)
    q = lambda t: f"{{{CAP_NS}}}{t}"  # noqa: E731

    def sub(parent, tag, text=None):
        e = ET.SubElement(parent, q(tag))
        if text is not None:
            e.text = str(text)
        return e

    alerts = [r for r in regions if LEVEL_RANK.get(r.get("level"), 0) >= LEVEL_RANK["warning"]]
    ident = hashlib.sha1(json.dumps([(r["rid"], r["res"], r["z"]) for r in alerts]).encode()).hexdigest()[:12]
    root = ET.Element(q("alert"))
    sub(root, "identifier", f"equifire-live-{now:%Y%m%dT%H%M}-{ident}")
    sub(root, "sender", "equifire-spaceapps-demo")
    sub(root, "sent", iso(now))
    sub(root, "status", "Actual" if alerts else "Test")
    sub(root, "msgType", "Alert")
    sub(root, "scope", "Public")
    sub(root, "note", "Satellite thermal anomaly far above the 2008-2025 norm for this grid region and week "
                      "(NASA FIRMS NRT vs FireSpot harmonized archive). Hackathon demo, verify locally."
        + ("" if alerts else " No warning-level alerts at this time."))
    for r in alerts:
        info = sub(root, "info")
        sub(info, "language", "en-US")
        sub(info, "category", "Fire")
        sub(info, "event", "Anomalous wildfire activity")
        sub(info, "responseType", "Monitor")
        sub(info, "urgency", "Immediate" if r["level"] == "critical" else "Expected")
        sub(info, "severity", CAP_SEVERITY[r["level"]])
        sub(info, "certainty", "Observed")
        sub(info, "effective", iso(now))
        sub(info, "expires", iso(now + dt.timedelta(hours=24)))
        sub(info, "senderName", "FireSpot (NASA Space Apps 2026)")
        sub(info, "headline", headline(r)[:160])
        sub(info, "description",
            f"{r['obs']} burning 0.05-degree cell-days (VIIRS, one satellite) in the last 7 days"
            + (f" ({window['start']} to {window['end']})" if window else "")
            + f" in the {r['res']}-degree region {r['bounds']}. Normal for this calendar week: {r['mean']} "
              f"+- {r['std']} ({r['years_used']} years 2008-2025, {r['years_reconstructed']} reconstructed from "
              f"MODIS); it burned in {r['years_burned']} of {r['years_used']} years. Strongest detection FRP "
              f"{r['top_frp']} MW at {r['top_frp_lat']}, {r['top_frp_lon']}; latest detection {r['latest']}.")
        sub(info, "instruction", "Check the area; activity is well above the seasonal norm. Satellite-derived, "
                                 "verify with local sources.")
        for k, v in (("z_score", r["z"]), ("level", r["level"]), ("burning_cell_days_7d", r["obs"]),
                     ("baseline_mean", r["mean"]), ("baseline_std", r["std"]),
                     ("out_of_season", str(bool(r["out_of_season"])).lower())):
            p = sub(info, "parameter")
            sub(p, "valueName", k)
            sub(p, "value", v)
        area = sub(info, "area")
        sub(area, "areaDesc", f"{r['res']}-degree grid region centred {r['lat']:.2f}, {r['lon']:.2f}")
        x0, y0, x1, y1 = r["bounds"]
        ring = [(y0, x0), (y0, x1), (y1, x1), (y1, x0), (y0, x0)]
        sub(area, "polygon", " ".join(f"{a:.4f},{b:.4f}" for a, b in ring))
    ET.indent(root)
    return b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="utf-8")


def summary(state: dict) -> dict:
    regs = state["regions"]
    counts = {lv: sum(1 for r in regs if r["level"] == lv) for lv in ("watch", "warning", "critical")}
    return {"feeds": state["feeds"], "window": state["window"], "week": state["week"],
            "obs_satellite": state["obs_satellite"], "regions_active": len(regs),
            "regions_no_baseline": sum(1 for r in regs if r["baseline"] == "none"),
            "alerts": counts, "alerts_total": sum(counts.values()),
            "out_of_season": sum(1 for r in regs if r.get("out_of_season") and r["level"] != "none"),
            "baseline": state["baseline"]}


if __name__ == "__main__":  # build the baseline:  .venv/bin/python -m backend.live
    import sys
    if "--build" in sys.argv or not BASELINE_F.exists():
        build_baseline()
    st = live_state(float(os.environ.get("RES", "1")))
    print(json.dumps(summary(st), indent=1)[:3000])
    for r in [r for r in st["regions"] if r["level"] != "none"][:10]:
        print(headline(r))
