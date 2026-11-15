"""Step 1: read raw FIRMS CSVs and aggregate detections into grid-cell observations.

One output row = one satellite overpass over one 0.05 degree cell:
(day, gx, gy, sat, dn) -> count, FRP, pixel footprint area, brightness, confidence, mean time.
Results are cached per CSV in cache/cells/, so re-runs only process new files.
load_cells() reads the consolidated cache/cells_all.parquet (rebuilt automatically when
cache/cells/ changes), which takes ~1.5 s instead of ~20 s for concatenating 200 files.
"""
import time
from concurrent.futures import ProcessPoolExecutor, as_completed

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from . import fastcache
from .common import CACHE, DATA_DIRS, EPOCH, GRID, SAT_NORMALIZE

CELLS = CACHE / "cells"
CELLS_ALL = CACHE / "cells_all.parquet"  # consolidated, deduplicated copy of CELLS/*.parquet
CELLS_ALL_VERSION = 1  # bump when the combine/dedup logic changes
VIIRS_CONF = {"l": 0.3, "n": 0.6, "h": 0.9}


def _out_path(csv):
    return CELLS / (csv.stem + ".parquet")


def aggregate_file(csv):
    df = pd.read_csv(csv, dtype={"acq_time": str, "satellite": str, "confidence": str, "daynight": str})
    n_raw = len(df)
    if "type" in df:
        df = df[df["type"] == 0]  # presumed vegetation fire only
    viirs = "bright_ti4" in df
    bright = df["bright_ti4"] if viirs else df["brightness"]
    if viirs:
        conf = df["confidence"].str.lower().map(VIIRS_CONF)
    else:
        conf = pd.to_numeric(df["confidence"], errors="coerce") / 100
    t = df["acq_time"].str.zfill(4)
    minute = t.str[:2].astype(int) * 60 + t.str[2:].astype(int)
    day = (pd.to_datetime(df["acq_date"]).values.astype("datetime64[D]") - EPOCH).astype(int)
    out = pd.DataFrame({
        "day": day.astype(np.int32),
        "gx": np.floor((df["longitude"].values + 180) / GRID).astype(np.int32),
        "gy": np.floor((df["latitude"].values + 90) / GRID).astype(np.int32),
        "sat": df["satellite"].map(SAT_NORMALIZE).fillna("?").values,
        "dn": (df["daynight"] == "D").astype(np.int8).values,
        "frp": df["frp"].astype(np.float32).values,
        "area": (df["scan"] * df["track"]).astype(np.float32).values,
        "scan": df["scan"].astype(np.float32).values,
        "track": df["track"].astype(np.float32).values,
        "bright": bright.astype(np.float32).values,
        "conf": conf.astype(np.float32).values,
        "minute": minute.astype(np.float32).values,
    })
    g = out.groupby(["day", "gx", "gy", "sat", "dn"], sort=False).agg(
        n=("frp", "size"), frp_sum=("frp", "sum"), frp_max=("frp", "max"),
        area_sum=("area", "sum"), scan_mean=("scan", "mean"), track_mean=("track", "mean"),
        bright_max=("bright", "max"), conf_mean=("conf", "mean"), minute=("minute", "mean"),
    ).reset_index()
    for c in g.columns:
        if g[c].dtype == np.float64:
            g[c] = g[c].astype(np.float32)
    g["n"] = g["n"].astype(np.int32)
    g.to_parquet(_out_path(csv), index=False)
    return csv, n_raw, len(g), csv.stat().st_size


def list_csvs():
    files = []
    for d in DATA_DIRS:
        files += sorted(p for p in d.rglob("*.csv"))
    return files


def collect(workers=8):
    CELLS.mkdir(parents=True, exist_ok=True)
    files = list_csvs()
    todo = [f for f in files if not _out_path(f).exists() or _out_path(f).stat().st_mtime < f.stat().st_mtime]
    print(f"[collect] {len(files)} CSV files found, {len(files) - len(todo)} already cached, {len(todo)} to process")
    tot_raw = tot_cells = tot_bytes = 0
    t0 = time.time()
    with ProcessPoolExecutor(workers) as ex:
        futs = [ex.submit(aggregate_file, f) for f in todo]
        for i, fut in enumerate(as_completed(futs), 1):
            csv, n_raw, n_cells, size = fut.result()
            tot_raw += n_raw
            tot_cells += n_cells
            tot_bytes += size
            print(f"[collect {i}/{len(todo)}] {csv.stem}: {n_raw:,} detections -> {n_cells:,} cell-obs | "
                  f"total {tot_raw:,} detections, {tot_bytes / 1e9:.2f} GB read, {time.time() - t0:.0f}s",
                  flush=True)
    return load_cells()


def cell_parts():
    return sorted(CELLS.glob("*.parquet"))


def cells_fingerprint(parts=None):
    """Fingerprint of the per-CSV cell files (names, sizes, mtimes). Changes whenever collect()
    adds or rewrites a file; caches derived from the full cell table are keyed on it."""
    parts = cell_parts() if parts is None else parts
    return fastcache.files_fingerprint(parts, tag=f"cells-v{CELLS_ALL_VERSION}")


def _combine_parts(parts):
    tables = [pq.read_table(p) for p in parts]
    t = pa.concat_tables([x.replace_schema_metadata(None) for x in tables], promote_options="default")
    del tables
    df = t.to_pandas(split_blocks=True, self_destruct=True)
    del t
    # Same detection can appear in two neighbouring countries' files near borders.
    df = df.drop_duplicates(["day", "gx", "gy", "sat", "dn", "minute"], ignore_index=True)
    df["sat"] = df["sat"].astype("category")
    return df


def load_cells():
    """All cell observations (deduplicated across country files), `sat` as category.

    Served from the consolidated cache/cells_all.parquet, which is rebuilt automatically from
    cache/cells/*.parquet whenever any of those files is added, removed or changed."""
    parts = cell_parts()
    if not parts:
        raise SystemExit("No cached cells. Run: python main.py collect")
    t0 = time.time()
    fp = cells_fingerprint(parts)
    meta = fastcache.read_meta(CELLS_ALL)
    if meta is not None and meta.get("fingerprint") == fp:
        df = fastcache.read_frame(CELLS_ALL)
        src = CELLS_ALL.name
    else:
        print(f"[collect] building {CELLS_ALL.name} from {len(parts)} files (one-time, after new data)",
              flush=True)
        df = _combine_parts(parts)
        by = df.groupby("sat", observed=True)["n"].agg(["size", "sum"])
        meta = {"fingerprint": fp, "files": len(parts), "rows": len(df), "detections": int(df["n"].sum()),
                "by_sat": {str(s): [int(r["size"]), int(r["sum"])] for s, r in by.iterrows()}}
        fastcache.write_frame(df, CELLS_ALL, meta)
        src = f"{len(parts)} files"
    fastcache.register(df, fp)
    print(f"[collect] loaded {src} -> {len(df):,} cell observations, "
          f"{meta['detections']:,} fire detections ({time.time() - t0:.1f}s)")
    for sat, (size, total) in meta["by_sat"].items():
        print(f"           sat {sat:>4}: {size:>11,} cell-obs  {total:>12,} detections")
    return df
