"""Disk-backed (memory-mapped) fire index.

The fire index (VIIRS burning cell-days with day/night split + MODIS cell-days, sorted for fast
window queries) is built once and saved as plain .npy column files in cache/fireindex/. Programs
open them with numpy memory mapping: nothing is read into RAM up front, the operating system
loads only the pages a query touches, and all programs share one copy through the page cache.

    from equifire.fireindex_mm import get_fire_index
    fi = get_fire_index()          # same object type/behaviour as direct.FireIndex.from_cells(cells)

The cache is keyed on the cell-file fingerprint and rebuilt automatically after new data arrives.
Building reads only 7 columns of cache/cells_all.parquet (not the full load_cells() table).
"""
import json
import time

import numpy as np

from . import collect, direct, fastcache
from .common import CACHE

DIR = CACHE / "fireindex"
VERSION = 1
COLS = ["day", "gx", "gy", "sat", "dn", "n", "frp_sum"]


def _save_tiled(t, prefix):
    np.save(DIR / f"{prefix}_day.npy", t.day)
    np.save(DIR / f"{prefix}_gx.npy", t.gx)
    np.save(DIR / f"{prefix}_gy.npy", t.gy)
    for k, v in t.vals.items():
        np.save(DIR / f"{prefix}_val_{k}.npy", np.asarray(v))
    keys = np.array(list(t.seg.keys()), np.int64)
    se = np.array(list(t.seg.values()), np.int64).reshape(-1, 2)
    np.savez(DIR / f"{prefix}_seg.npz", keys=keys, se=se)


def _load_tiled(prefix, vals):
    t = direct._Tiled.__new__(direct._Tiled)
    t.day = np.load(DIR / f"{prefix}_day.npy", mmap_mode="r")
    t.gx = np.load(DIR / f"{prefix}_gx.npy", mmap_mode="r")
    t.gy = np.load(DIR / f"{prefix}_gy.npy", mmap_mode="r")
    t.vals = {k: np.load(DIR / f"{prefix}_val_{k}.npy", mmap_mode="r") for k in vals}
    z = np.load(DIR / f"{prefix}_seg.npz")
    t.seg = {int(k): (int(a), int(b)) for k, (a, b) in zip(z["keys"], z["se"])}
    return t


def _fingerprint():
    return {"cells": collect.cells_fingerprint(), "version": VERSION}


def _build(verbose):
    t = time.time()
    parts = collect.cell_parts()
    fp = collect.cells_fingerprint(parts)
    meta = fastcache.read_meta(collect.CELLS_ALL)
    if meta is not None and meta.get("fingerprint") == fp:
        cells = fastcache.read_frame(collect.CELLS_ALL, columns=COLS)  # 7 columns only
    else:
        cells = collect.load_cells()[COLS]  # rebuilds cells_all.parquet once
    if verbose:
        print(f"[fireindex] building from {len(cells):,} cell observations...", flush=True)
    fi = direct.FireIndex.from_cells(cells)
    del cells
    DIR.mkdir(parents=True, exist_ok=True)
    for f in DIR.glob("*"):
        f.unlink()
    _save_tiled(fi.v, "v")
    if fi.m is not None:
        _save_tiled(fi.m, "m")
    np.save(DIR / "s_day.npy", fi.day)
    np.save(DIR / "s_gx.npy", fi.gx)
    np.save(DIR / "s_gy.npy", fi.gy)
    (DIR / "meta.json").write_text(json.dumps({**_fingerprint(), "has_m": fi.m is not None,
                                               "v_vals": list(fi.v.vals), "rows_v": len(fi.v),
                                               "rows_m": len(fi.m) if fi.m is not None else 0}))
    if verbose:
        print(f"[fireindex] saved to {DIR} in {time.time() - t:.0f}s "
              f"({len(fi.v):,} VIIRS + {len(fi.m) if fi.m is not None else 0:,} MODIS cell-days)", flush=True)


def get_fire_index(verbose=True):
    """Memory-mapped FireIndex (VIIRS day/night + MODIS); builds/refreshes the disk cache if needed."""
    t = time.time()
    mf = DIR / "meta.json"
    meta = json.loads(mf.read_text()) if mf.exists() else None
    want = _fingerprint()
    if meta is None or {k: meta.get(k) for k in want} != want:
        _build(verbose)
        meta = json.loads(mf.read_text())
    fi = direct.FireIndex.__new__(direct.FireIndex)
    fi.v = _load_tiled("v", meta["v_vals"])
    fi.m = _load_tiled("m", ["frp"]) if meta["has_m"] else None
    fi.has_dn = True
    fi.day = np.load(DIR / "s_day.npy", mmap_mode="r")
    fi.gx = np.load(DIR / "s_gx.npy", mmap_mode="r")
    fi.gy = np.load(DIR / "s_gy.npy", mmap_mode="r")
    from .subcell import SubcellIndex
    fi.sub = SubcellIndex() if SubcellIndex.available() else None
    if verbose:
        print(f"[fireindex] memory-mapped {meta['rows_v']:,} VIIRS + {meta['rows_m']:,} MODIS cell-days "
              f"from disk ({time.time() - t:.1f}s)", flush=True)
    return fi
