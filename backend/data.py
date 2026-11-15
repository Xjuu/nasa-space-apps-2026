"""Data layer: one compact daily table of burning 0.05 deg cells (MODIS + VIIRS) and a weekly
1-degree VIIRS table for the anomaly baseline.

Built once from equifire's cached cell observations (cache/cells/*.parquet) and stored in
backend/.cache/daily.parquet, so later server starts take a few seconds.

The build reads the cell files one year at a time (all sensors and countries of that year) instead
of equifire.collect.load_cells(), which needs several GB of RAM for all 36M rows at once. Per year it
applies the same cross-country de-duplication as load_cells, so the VIIRS part of the table equals
equifire.datasets.viirs_daily(load_cells()) row for row. MODIS overpasses are harmonized to
VIIRS-equivalent FRP and footprint area with models/size_frp.joblib and models/size_area.joblib
(features as in equifire.datasets.build_size).
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from equifire.collect import CELLS
from equifire.common import (EPOCH, MODELS, MODIS_SATS, VIIRS_SATS, KeySet, cell_center, cell_key,
                             day_of_year_features)
from equifire.datasets import NEIGHBORS, RING2

CACHE_DIR = Path(__file__).resolve().parent / ".cache"
DAILY_F = CACHE_DIR / "daily.parquet"
DAILY_META_F = CACHE_DIR / "daily_meta.json"
BUILD_VERSION = 1
COARSE = 20  # 0.05 deg cells per 1 degree anomaly cell
SIZE_FEATURES = ["n", "frp_sum", "frp_max", "area_sum", "scan_mean", "track_mean", "bright_max", "conf_mean",
                 "dn", "aqua", "lat", "doy_sin", "doy_cos", "nbr_active", "ring2_active", "prev_days_active"]
READ_COLS = ["day", "gx", "gy", "sat", "dn", "n", "frp_sum", "frp_max", "area_sum", "scan_mean", "track_mean",
             "bright_max", "conf_mean", "minute"]


def day_of(date: np.datetime64 | str) -> int:
    return int((np.datetime64(date, "D") - EPOCH).astype(int))


def date_of(day: int) -> str:
    return str(EPOCH + np.timedelta64(int(day), "D"))


# ------------------------------------------------------------------ build
def _cell_files() -> list[Path]:
    return sorted(CELLS.glob("*.parquet"))


def _source_signature(files: list[Path]) -> str:
    h = hashlib.sha1(str(BUILD_VERSION).encode())
    for f in files:
        st = f.stat()
        h.update(f"{f.name}:{st.st_size}:{int(st.st_mtime)}".encode())
    return h.hexdigest()


def _year_groups(files: list[Path]) -> list[list[Path]]:
    groups: dict[str, list[Path]] = defaultdict(list)
    for f in files:
        m = re.search(r"_(\d{4})_", f.name)
        groups[m.group(1) if m else "?"].append(f)
    return [groups[k] for k in sorted(groups)]


def _load_size_models():
    import joblib
    out = {}
    for t in ("frp", "area"):
        f = MODELS / f"size_{t}.joblib"
        if f.exists():
            m = joblib.load(f)
            try:
                m.set_params(n_jobs=4)  # shared machine: stay polite
            except Exception:
                pass
            out[t] = m
    return out


def _harmonize_modis(mod: pd.DataFrame, size_models: dict) -> tuple[np.ndarray, np.ndarray]:
    """VIIRS-equivalent (frp, area) per MODIS overpass row. Falls back to raw values without models."""
    frp, area = mod["frp_sum"].values.astype(np.float32), mod["area_sum"].values.astype(np.float32)
    if not len(mod) or not size_models:
        return frp, area
    g = mod.groupby(["day", "gx", "gy"]).size().reset_index()
    ks = KeySet(cell_key(g["day"], g["gx"], g["gy"]))
    d, x, y = mod["day"].values, mod["gx"].values, mod["gy"].values

    def nbr(offsets):
        c = np.zeros(len(d), np.int16)
        for dx, dy in offsets:
            c += ks.has(cell_key(d, x + dx, y + dy))
        return c

    lat, _ = cell_center(x, y)
    s, c = day_of_year_features(d)
    X = pd.DataFrame({
        "n": mod["n"].values, "frp_sum": mod["frp_sum"].values, "frp_max": mod["frp_max"].values,
        "area_sum": mod["area_sum"].values, "scan_mean": mod["scan_mean"].values,
        "track_mean": mod["track_mean"].values, "bright_max": mod["bright_max"].values,
        "conf_mean": mod["conf_mean"].values, "dn": mod["dn"].values,
        "aqua": (mod["sat"].values == "A").astype(np.int8),
        "lat": lat, "doy_sin": s, "doy_cos": c,
        "nbr_active": nbr(NEIGHBORS), "ring2_active": nbr(RING2),
        "prev_days_active": sum(ks.has(cell_key(d - k, x, y)) for k in (1, 2, 3)).astype(np.int8),
    })[SIZE_FEATURES]
    if "frp" in size_models:
        frp = np.maximum(np.expm1(size_models["frp"].predict(X)), 0).astype(np.float32)
    if "area" in size_models:
        area = np.maximum(np.expm1(size_models["area"].predict(X)), 0).astype(np.float32)
    return frp, area


def _build_year(files: list[Path], size_models: dict) -> pd.DataFrame:
    df = pd.concat((pd.read_parquet(p, columns=READ_COLS) for p in files), ignore_index=True)
    df = df.drop_duplicates(["day", "gx", "gy", "sat", "dn", "minute"])  # same rule as load_cells
    df["sat"] = df["sat"].astype(str)
    is_v = df["sat"].isin(VIIRS_SATS).values
    is_m = df["sat"].isin(MODIS_SATS).values
    frp_mh = np.zeros(len(df), np.float32)
    area_mh = np.zeros(len(df), np.float32)
    if is_m.any():
        f, a = _harmonize_modis(df[is_m].reset_index(drop=True), size_models)
        frp_mh[is_m], area_mh[is_m] = f, a
    n = df["n"].values
    frp = df["frp_sum"].values.astype(np.float32)
    area = df["area_sum"].values.astype(np.float32)
    t = pd.DataFrame({
        "day": df["day"].values, "gx": df["gx"].values, "gy": df["gy"].values,
        "n_v": np.where(is_v, n, 0), "ov_v": is_v.astype(np.int16),
        "frp_v": np.where(is_v, frp, 0), "area_v": np.where(is_v, area, 0),
        "n_m": np.where(is_m, n, 0), "ov_m": is_m.astype(np.int16),
        "frp_m": np.where(is_m, frp, 0), "frp_mh": frp_mh, "area_mh": area_mh,
        "frp_max": df["frp_max"].values.astype(np.float32),
    })
    g = t.groupby(["day", "gx", "gy"], sort=False).agg(
        n_v=("n_v", "sum"), ov_v=("ov_v", "sum"), frp_v=("frp_v", "sum"), area_v=("area_v", "sum"),
        n_m=("n_m", "sum"), ov_m=("ov_m", "sum"), frp_m=("frp_m", "sum"), frp_mh=("frp_mh", "sum"),
        area_mh=("area_mh", "sum"), frp_max=("frp_max", "max")).reset_index()
    return _compact(g)


def _compact(g: pd.DataFrame) -> pd.DataFrame:
    types = {"day": np.int16, "gx": np.int16, "gy": np.int16, "n_v": np.int32, "n_m": np.int32,
             "ov_v": np.int16, "ov_m": np.int16}
    for c in g.columns:
        g[c] = g[c].astype(types.get(c, np.float32))
    return g


def build_daily(log=print) -> pd.DataFrame:
    """Daily cell table, from cache when the source files did not change."""
    files = _cell_files()
    if not files:
        raise RuntimeError(f"no cached cell files in {CELLS} (run: python main.py collect)")
    sig = _source_signature(files)
    if DAILY_F.exists() and DAILY_META_F.exists():
        try:
            if json.loads(DAILY_META_F.read_text()).get("signature") == sig:
                log(f"[backend] daily table from cache {DAILY_F}")
                return pd.read_parquet(DAILY_F)
        except Exception as e:  # corrupt cache: rebuild
            log(f"[backend] cache unreadable ({e}); rebuilding")
    size_models = _load_size_models()
    log(f"[backend] building daily table from {len(files)} files "
        f"(MODIS harmonization models: {sorted(size_models) or 'none'})")
    parts = []
    t0 = time.time()
    for grp in _year_groups(files):
        parts.append(_build_year(grp, size_models))
        log(f"[backend]   {grp[0].name.split('_')[1] if '_' in grp[0].name else '?'}: "
            f"{len(parts[-1]):,} cell-days | {time.time() - t0:.0f}s")
    daily = pd.concat(parts, ignore_index=True).sort_values(["day", "gx", "gy"], kind="stable")
    daily = daily.reset_index(drop=True)
    CACHE_DIR.mkdir(exist_ok=True)
    tmp = DAILY_F.with_suffix(".tmp")
    daily.to_parquet(tmp, index=False)
    tmp.replace(DAILY_F)
    DAILY_META_F.write_text(json.dumps({"signature": sig, "rows": len(daily), "built": time.time()}))
    return daily


# ------------------------------------------------------------------ in-memory state
def _iso_week_key(days: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    iso = pd.DatetimeIndex(EPOCH + days.astype("timedelta64[D]")).isocalendar()
    return iso["year"].values.astype(np.int32), iso["week"].values.astype(np.int32)


@dataclass
class State:
    """Everything the endpoints need, built from a daily table (real or synthetic)."""
    daily: pd.DataFrame
    day_min: int = 0
    day_max: int = 0
    viirs_rows: int = 0
    weekly: pd.DataFrame = field(default_factory=pd.DataFrame)      # 1 deg x iso week x year
    fire_index: object = None                                        # equifire.direct.FireIndex (VIIRS)
    viirs_day_min: int = 0
    viirs_day_max: int = 0
    _starts: np.ndarray = field(default_factory=lambda: np.zeros(0, np.int64))

    @classmethod
    def from_daily(cls, daily: pd.DataFrame) -> "State":
        from equifire import direct
        d = daily["day"].values
        if len(d) > 1 and not (np.diff(d) >= 0).all():
            daily = daily.sort_values(["day", "gx", "gy"], kind="stable").reset_index(drop=True)
        s = cls(daily=daily)
        s.day_min, s.day_max = int(daily["day"].min()), int(daily["day"].max())
        s._starts = np.searchsorted(daily["day"].values, np.arange(s.day_min, s.day_max + 2))
        m = daily["n_v"].values > 0
        s.viirs_rows = int(m.sum())
        if s.viirs_rows:
            # same columns as equifire.datasets.viirs_daily; only kept inside FireIndex (saves RAM)
            v = pd.DataFrame({"day": daily["day"].values[m].astype(np.int32),
                              "gx": daily["gx"].values[m].astype(np.int32),
                              "gy": daily["gy"].values[m].astype(np.int32), "n": daily["n_v"].values[m],
                              "frp": daily["frp_v"].values[m], "area": daily["area_v"].values[m]})
            s.viirs_day_min, s.viirs_day_max = int(v["day"].iloc[0]), int(v["day"].iloc[-1])
            s.weekly = cls._weekly(v)
            s.fire_index = direct.FireIndex(v)
        return s

    @staticmethod
    def _weekly(v: pd.DataFrame) -> pd.DataFrame:
        yr, wk = _iso_week_key(v["day"].values)
        w = pd.DataFrame({"cx": v["gx"].values // COARSE, "cy": v["gy"].values // COARSE, "year": yr,
                          "week": wk, "cell_days": 1, "detections": v["n"].values, "frp": v["frp"].values})
        w = w.groupby(["cx", "cy", "week", "year"]).agg(
            cell_days=("cell_days", "sum"), detections=("detections", "sum"), frp=("frp", "sum"))
        return w.sort_index()

    # --- queries
    def day_slice(self, day: int) -> pd.DataFrame:
        if day < self.day_min or day > self.day_max:
            return self.daily.iloc[0:0]
        i = day - self.day_min
        return self.daily.iloc[self._starts[i]:self._starts[i + 1]]

    def viirs_slice(self, day: int) -> pd.DataFrame:
        """VIIRS-burning cells of a day (columns of the daily table)."""
        sl = self.day_slice(day)
        return sl[sl["n_v"].values > 0]

    def summary(self) -> dict:
        return {"cell_days": int(len(self.daily)), "viirs_cell_days": self.viirs_rows,
                "date_min": date_of(self.day_min), "date_max": date_of(self.day_max),
                "viirs_date_min": date_of(self.viirs_day_min) if self.viirs_rows else None,
                "viirs_date_max": date_of(self.viirs_day_max) if self.viirs_rows else None}


class Store:
    """Loads State + spread model once, in a background thread."""

    def __init__(self):
        self.state: State | None = None
        self.model = None
        self.meta: dict | None = None
        self.fire_index = None
        self.weather = None
        self.status = "idle"
        self.error: str | None = None
        self.model_error: str | None = None
        self.started: float | None = None
        self.loaded_s: float | None = None
        self.log_lines: list[str] = []
        self.lock = threading.Lock()          # xgboost predict + forecast cache
        self._thread: threading.Thread | None = None

    def log(self, msg: str):
        print(msg, flush=True)
        self.log_lines = (self.log_lines + [msg])[-20:]

    def start(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self._load, name="equifire-load", daemon=True)
            self._thread.start()

    def set_state(self, state: State):
        """Install a ready State (used by tests and by the loader)."""
        self.state = state
        self.fire_index = state.fire_index
        self.status = "ready"

    def load_model(self):
        from equifire import direct
        try:
            self.model, self.meta = direct.load()
            if self.model is None:
                self.model_error = f"model file missing: {direct.MODEL_F}"
            self.weather = direct.Weather()
        except Exception as e:
            self.model_error = f"{type(e).__name__}: {e}"
            self.log(f"[backend] spread model failed to load: {self.model_error}")

    def _load(self):
        self.started = time.time()
        self.status = "loading"
        try:
            self.load_model()
            self.log(f"[backend] spread model loaded: {self.model is not None}")
            daily = build_daily(self.log)
            self.set_state(State.from_daily(daily))
            self.loaded_s = time.time() - self.started
            self.log(f"[backend] ready: {len(daily):,} cell-days in {self.loaded_s:.0f}s")
        except Exception as e:
            self.status = "error"
            self.error = f"{type(e).__name__}: {e}"
            self.log(f"[backend] data load failed: {self.error}")
