"""Shared constants: grid, cell keys, paths."""
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
DATA_DIRS = [ROOT / "data_country"]
CACHE = ROOT / "cache"
MODELS = ROOT / "models"
REPORTS = ROOT / "reports"

GRID = 0.05  # degrees, ~5.5 km at equator
NX = int(round(360 / GRID))
NY = int(round(180 / GRID))
STRIDE_DAY = NX * NY
EPOCH = np.datetime64("2000-01-01")

# Nominal pixel footprint at nadir, km^2
VIIRS_PIXEL_KM2 = 0.375 * 0.375
MODIS_PIXEL_KM2 = 1.0

SAT_NORMALIZE = {"Terra": "T", "Aqua": "A", "N": "N", "N20": "N20", "1": "N20", "N21": "N21", "2": "N21"}
MODIS_SATS = {"T", "A"}
VIIRS_SATS = {"N", "N20", "N21"}


def cell_key(day, gx, gy):
    return np.asarray(day, np.int64) * STRIDE_DAY + np.asarray(gx, np.int64) * NY + np.asarray(gy, np.int64)


def decode_key(k):
    k = np.asarray(k, np.int64)
    day, rem = np.divmod(k, STRIDE_DAY)
    gx, gy = np.divmod(rem, NY)
    return day, gx, gy


def cell_center(gx, gy):
    lon = np.asarray(gx) * GRID - 180 + GRID / 2
    lat = np.asarray(gy) * GRID - 90 + GRID / 2
    return lat, lon


def day_of_year_features(day):
    doy = (EPOCH + np.asarray(day).astype("timedelta64[D]")).astype("datetime64[D]")
    year_start = doy.astype("datetime64[Y]").astype("datetime64[D]")
    d = (doy - year_start).astype(int)
    ang = 2 * np.pi * d / 365.25
    return np.sin(ang), np.cos(ang)


def day_to_year(day):
    return (EPOCH + np.asarray(day).astype("timedelta64[D]")).astype("datetime64[Y]").astype(int) + 1970


class KeySet:
    """Sorted int64 keys with vectorized membership / lookup."""

    def __init__(self, keys, **values):
        order = np.argsort(keys, kind="stable")
        self.keys = np.asarray(keys, np.int64)[order]
        self.values = {k: np.asarray(v)[order] for k, v in values.items()}

    def find(self, q):
        q = np.asarray(q, np.int64)
        if len(self.keys) == 0:
            return np.zeros(len(q), np.int64), np.zeros(len(q), bool)
        i = np.searchsorted(self.keys, q)
        i = np.clip(i, 0, len(self.keys) - 1)
        return i, self.keys[i] == q

    def has(self, q):
        return self.find(q)[1]

    def get(self, name, q, fill=0.0):
        i, ok = self.find(q)
        out = np.where(ok, self.values[name][i], fill)
        return out
