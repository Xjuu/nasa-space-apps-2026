"""Footprint-adaptive clustering of hot-spot detections within one satellite overpass (goal 2).

Neighbour rule (single-linkage / DBSCAN with min_samples=1) for detections i, j of the same overpass:

    (ds / S_ij)^2 + (dt / T_ij)^2 <= f^2

ds, dt = separation along-scan / along-track (km, axes from the ground-track heading),
S_ij, T_ij = mean along-scan / along-track pixel size of the pair. Methods:

* ``adaptive``      S, T = FIRMS scan/track (ellipse grows towards the swath edge), f = 1.5
                    (connects edge- and corner-adjacent pixels and bow-tie duplicates of the next scan)
* ``adaptive_iso``  S = T = max(scan, track) (circle with footprint-based radius), f = 1.5
* ``fixed_nadir``   S = T = nadir pixel (MODIS 1 km, VIIRS 0.375 km), f = 1.5
* ``fixed_2km``     plain 2 km distance threshold

Fast path: candidate pairs from cKDTree (sparse_distance_matrix) per pixel-size class with the
class-pair maximum radius, then the exact ellipse test, then connected components.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from scipy.spatial import cKDTree

from .geometry import R_E, SATS, add_geometry

F_DEFAULT = 1.5
OVERPASS_GAP_MIN = 10
METHODS = ("fixed_nadir", "fixed_2km", "adaptive_iso", "adaptive")
KM_PER_DEG = np.pi * R_E / 180


def overpass_ids(df: pd.DataFrame) -> np.ndarray:
    """Same satellite, same acq_date, acq_time chained with gaps <= 10 min -> one overpass id."""
    t = df["acq_time"].astype(int).to_numpy()
    minutes = (t // 100) * 60 + t % 100
    sk = pd.factorize(df["sat_key"])[0].astype(np.int64)
    dk = pd.factorize(df["acq_date"])[0].astype(np.int64)
    key = sk * 1_000_000 + dk
    order = np.lexsort((minutes, key))
    k, m = key[order], minutes[order]
    new = np.r_[True, (k[1:] != k[:-1]) | (np.diff(m) > OVERPASS_GAP_MIN)]
    gid = np.empty(len(df), np.int64)
    gid[order] = np.cumsum(new) - 1
    return gid


def _sizes(df: pd.DataFrame, method: str):
    nad = np.where(df["sat_key"].map(lambda k: SATS[k].instrument if k in SATS else "MODIS").to_numpy() == "MODIS", 1.0, 0.375)
    if method == "adaptive":
        return df["along_scan_km"].to_numpy(float), df["along_track_km"].to_numpy(float), F_DEFAULT
    if method == "adaptive_iso":
        m = np.maximum(df["along_scan_km"].to_numpy(float), df["along_track_km"].to_numpy(float))
        return m, m, F_DEFAULT
    if method == "fixed_nadir":
        return nad, nad.copy(), F_DEFAULT
    if method == "fixed_2km":
        two = np.full(len(df), 2.0)
        return two, two.copy(), 1.0
    raise ValueError(f"unknown method {method!r}; expected one of {METHODS}")


def _xyz(lat, lon, gid):
    la, lo = np.radians(lat), np.radians(lon)
    return np.c_[R_E * np.cos(la) * np.cos(lo), R_E * np.cos(la) * np.sin(lo), R_E * np.sin(la),
                 gid.astype(float) * 1e5]          # 4th axis keeps overpasses apart


def cluster(df: pd.DataFrame, method: str = "adaptive", f: float | None = None, gid=None) -> np.ndarray:
    """Cluster label per row (0..n-1). Needs columns latitude, longitude, sat_key, acq_date, acq_time,
    along_scan_km, along_track_km, heading_deg (see geometry.add_geometry)."""
    n = len(df)
    if n == 0:
        return np.zeros(0, np.int64)
    gid = overpass_ids(df) if gid is None else gid
    S, T, f0 = _sizes(df, method)
    f = f0 if f is None else f
    lat, lon = df["latitude"].to_numpy(float), df["longitude"].to_numpy(float)
    head = np.radians(df["heading_deg"].to_numpy(float))
    X = _xyz(lat, lon, gid)
    size = np.maximum(S, T)
    edges = np.unique(np.r_[np.quantile(size, [0, .5, .8, .95]), size.max()])
    cls = np.clip(np.searchsorted(edges, size, side="left"), 0, len(edges) - 1)
    upper = edges[cls]
    groups = [np.flatnonzero(cls == c) for c in np.unique(cls)]
    trees = [cKDTree(X[g]) for g in groups]
    rows, cols = [], []
    for a in range(len(groups)):
        for b in range(a, len(groups)):
            ga, gb = groups[a], groups[b]
            r = f * (upper[ga[0]] + upper[gb[0]]) / 2 * 1.001
            m = trees[a].sparse_distance_matrix(trees[b], r, output_type="ndarray")
            if len(m) == 0:
                continue
            i, j = ga[m["i"]], gb[m["j"]]
            keep = i < j if a == b else i != j
            rows.append(i[keep]); cols.append(j[keep])
    if rows:
        i, j = np.concatenate(rows), np.concatenate(cols)
        dn = (lat[j] - lat[i]) * KM_PER_DEG
        dlon = (lon[j] - lon[i] + 540) % 360 - 180
        de = dlon * KM_PER_DEG * np.cos(np.radians((lat[i] + lat[j]) / 2))
        h = (head[i] + head[j]) / 2
        d_track = de * np.sin(h) + dn * np.cos(h)
        d_scan = de * np.cos(h) - dn * np.sin(h)
        ok = (d_scan / ((S[i] + S[j]) / 2)) ** 2 + (d_track / ((T[i] + T[j]) / 2)) ** 2 <= f * f
        i, j = i[ok], j[ok]
    else:
        i = j = np.zeros(0, np.int64)
    g = coo_matrix((np.ones(len(i), np.int8), (i, j)), shape=(n, n))
    _, lab = connected_components(g, directed=False)
    return lab.astype(np.int64)


def summarize(df: pd.DataFrame, labels: np.ndarray) -> pd.DataFrame:
    """One row per cluster: centroid, n, FRP sum, satellites, mean scan angle, max pixel size."""
    d = df.assign(_c=labels)
    agg = d.groupby("_c").agg(
        latitude=("latitude", "mean"), longitude=("longitude", "mean"), n=("latitude", "size"),
        frp_sum=("frp", "sum"), scan_deg=("scan_deg", "mean"),
        along_scan_km=("along_scan_km", "max"), along_track_km=("along_track_km", "max"),
        lat_min=("latitude", "min"), lat_max=("latitude", "max"),
        lon_min=("longitude", "min"), lon_max=("longitude", "max"),
        satellites=("satellite", lambda s: sorted(set(map(str, s)))),
        acq_date=("acq_date", "first"), acq_time=("acq_time", "min"),
    )
    return agg.reset_index(drop=True)


def load_firms(path, usecols_extra=()) -> pd.DataFrame:
    """Read a raw FIRMS CSV (type==0 kept when present) and add pixel-size geometry."""
    cols = ["latitude", "longitude", "scan", "track", "acq_date", "acq_time", "satellite", "frp",
            "daynight", "type", *usecols_extra]
    head = pd.read_csv(path, nrows=0).columns
    df = pd.read_csv(path, usecols=[c for c in cols if c in head],
                     dtype={"satellite": "category", "daynight": "category", "acq_date": "category",
                            "scan": np.float32, "track": np.float32, "frp": np.float32, "acq_time": np.int16})
    if "type" in df:
        df = df[df["type"] == 0].drop(columns="type")
    df = add_geometry(df.reset_index(drop=True))
    return df.drop(columns=[c for c in ("daynight",) if c in df])
