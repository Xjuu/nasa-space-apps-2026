"""Sub-cell position of VIIRS fire detections inside each 0.05 deg cell.

The spread model works on 0.05 deg (~5.5 km) cells, but each VIIRS detection is a 375 m pixel with
its own position. Where the detections sit inside a burning cell (e.g. crowded at its north edge)
tells which neighbour the front is pushing into next - the key information for day-1 localisation.

Per (day, gx, gy) we store the FRP-weighted mean offset of detections from the cell centre, in
cell units (-0.5 .. 0.5), x = east, y = north. Built from raw FIRMS VIIRS CSVs into
cache/subcell/parts/*.parquet, then combined into memory-mapped arrays in cache/subcell/.

    .venv/bin/python -m equifire.subcell build [--countries Brazil Australia ...] [--workers 6]
"""
import argparse
import json
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd

from .common import CACHE, EPOCH, GRID, NY, ROOT

DIR = CACHE / "subcell"
PARTS = DIR / "parts"
SENSORS = ("viirs-snpp", "viirs-jpss1")


def _part(csv):
    return PARTS / (csv.stem + ".parquet")


def aggregate(csv):
    df = pd.read_csv(csv, usecols=["latitude", "longitude", "acq_date", "frp", "type"])
    df = df[df["type"] == 0]
    fx = (df["longitude"].values + 180) / GRID
    fy = (df["latitude"].values + 90) / GRID
    gx, gy = np.floor(fx).astype(np.int64), np.floor(fy).astype(np.int64)
    w = np.maximum(df["frp"].values.astype(np.float64), 0.1)
    day = (pd.to_datetime(df["acq_date"]).values.astype("datetime64[D]") - EPOCH).astype(np.int64)
    out = pd.DataFrame({"key": day * (int(360 / GRID) * NY) + gx * NY + gy,
                        "w": w, "wx": w * (fx - gx - 0.5), "wy": w * (fy - gy - 0.5)})
    g = out.groupby("key", sort=False).sum().reset_index()
    g.to_parquet(_part(csv), index=False)
    return csv.name, len(df), len(g)


def build(countries=None, workers=6):
    PARTS.mkdir(parents=True, exist_ok=True)
    files = []
    for s in SENSORS:
        for f in sorted((ROOT / "data_country" / s).rglob("*.csv")):
            if countries and not any(f.stem.endswith("_" + c) for c in countries):
                continue
            files.append(f)
    todo = [f for f in files if not _part(f).exists() or _part(f).stat().st_mtime < f.stat().st_mtime]
    print(f"[subcell] {len(files)} VIIRS CSVs, {len(todo)} to process", flush=True)
    t = time.time()
    with ProcessPoolExecutor(workers) as ex:
        for i, fut in enumerate(as_completed([ex.submit(aggregate, f) for f in todo]), 1):
            name, n, m = fut.result()
            if i % 10 == 0 or i == len(todo):
                print(f"[subcell] {i}/{len(todo)} {name}: {n:,} detections -> {m:,} cell-days | "
                      f"{time.time() - t:.0f}s", flush=True)
    combine(files)
    meta = json.loads((DIR / "meta.json").read_text())
    meta["countries"] = countries
    (DIR / "meta.json").write_text(json.dumps(meta))
    print(f"[subcell] saved {meta['cell_days']:,} cell-days from {len(files)} files ({time.time() - t:.0f}s)",
          flush=True)


def combine(files):
    """Merge per-file parts with plain numpy (low RAM): sort by key, sum weights per key (reduceat).
    Peak RAM ~ 4 arrays x total rows (8 bytes each) instead of a pandas groupby."""
    import pyarrow.parquet as pq
    parts = [_part(f) for f in files if _part(f).exists()]
    n = sum(pq.ParquetFile(p).metadata.num_rows for p in parts)
    key = np.empty(n, np.int64)
    w, wx, wy = (np.empty(n, np.float32) for _ in range(3))
    o = 0
    for p in parts:
        t = pq.read_table(p)
        m = t.num_rows
        key[o:o + m] = t["key"].to_numpy()
        w[o:o + m] = t["w"].to_numpy()
        wx[o:o + m] = t["wx"].to_numpy()
        wy[o:o + m] = t["wy"].to_numpy()
        o += m
    order = np.argsort(key, kind="stable")
    key = key[order]
    start = np.flatnonzero(np.r_[True, key[1:] != key[:-1]])
    keys = key[start]
    del key
    sw = np.add.reduceat(w[order], start)
    ox = (np.add.reduceat(wx[order], start) / sw).astype(np.float32)
    oy = (np.add.reduceat(wy[order], start) / sw).astype(np.float32)
    DIR.mkdir(parents=True, exist_ok=True)
    for name, arr in (("keys", keys), ("ox", ox), ("oy", oy)):
        np.save(DIR / f"{name}.tmp.npy", arr)
        (DIR / f"{name}.tmp.npy").replace(DIR / f"{name}.npy")
    (DIR / "meta.json").write_text(json.dumps({"files": len(parts), "cell_days": int(len(keys))}))


class SubcellIndex:
    """Memory-mapped lookup: (day, gx, gy) -> (ox, oy), NaN where unknown."""

    def __init__(self):
        self.keys = np.load(DIR / "keys.npy", mmap_mode="r")
        self.ox = np.load(DIR / "ox.npy", mmap_mode="r")
        self.oy = np.load(DIR / "oy.npy", mmap_mode="r")

    @staticmethod
    def available():
        return (DIR / "keys.npy").exists()

    def lookup(self, day, gx, gy):
        k = np.asarray(day, np.int64) * (int(360 / GRID) * NY) + np.asarray(gx, np.int64) * NY \
            + np.asarray(gy, np.int64)
        i = np.searchsorted(self.keys, k)
        i = np.clip(i, 0, len(self.keys) - 1)
        ok = self.keys[i] == k
        return np.where(ok, self.ox[i], np.nan), np.where(ok, self.oy[i], np.nan)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["build", "combine"])
    ap.add_argument("--countries", nargs="*", default=["Brazil", "Australia", "India", "United_States", "Canada"])
    ap.add_argument("--workers", type=int, default=6)
    a = ap.parse_args()
    if a.cmd == "combine":  # only merge existing parts (no CSV reading)
        combine([Path(p.stem + ".csv") for p in sorted(PARTS.glob("*.parquet"))])
    else:
        build(a.countries or None, a.workers)
