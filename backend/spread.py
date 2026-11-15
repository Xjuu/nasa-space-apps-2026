"""Spread forecast around a fire with the direct multi-horizon model (equifire.direct)."""
from __future__ import annotations

import math
from collections import OrderedDict
from dataclasses import dataclass, field

import numpy as np

from equifire import direct

from . import geo
from .data import State, date_of

CACHE_MAX = 64


class NoFireError(LookupError):
    pass


@dataclass
class Forecast:
    t0: int
    days: int
    cx: int
    cy: int
    start: list[dict]                       # cells burning on t0 (gx, gy, frp, n)
    predicted: list[dict]                   # cells predicted to newly burn (gx, gy, day, probability, p_max)
    observed: list[dict]                    # cells that actually newly burned (gx, gy, day), historical only
    per_day: list[dict]                     # day, date, predicted_cells, predicted_km2, observed_cells, iou
    masks: list[list[tuple[int, int]]]      # cumulative predicted cells by day k (global gx, gy)
    observed_through_day: int
    wind: dict = field(default_factory=dict)

    @property
    def lat(self) -> float:
        return geo.cell_center(self.cx, self.cy)[0]

    @property
    def lon(self) -> float:
        return geo.cell_center(self.cx, self.cy)[1]


def find_centre(state: State, t0: int, gx: int, gy: int, max_cells: int) -> tuple[int, int]:
    """Nearest VIIRS-burning cell to (gx, gy) on day t0 within max_cells (chessboard distance)."""
    v = state.viirs_slice(t0)
    if not len(v):
        raise NoFireError(f"no VIIRS fire detections at all on {date_of(t0)}")
    dx = v["gx"].values.astype(np.int64) - gx
    dy = v["gy"].values.astype(np.int64) - gy
    ok = np.maximum(np.abs(dx), np.abs(dy)) <= max_cells
    if not ok.any():
        raise NoFireError(f"no VIIRS fire within {max_cells * 0.05:.2f} deg of the point on {date_of(t0)}")
    i = np.flatnonzero(ok)[np.argmin((dx[ok] ** 2 + dy[ok] ** 2))]
    return int(v["gx"].values[i]), int(v["gy"].values[i])


def _f(v) -> float | None:
    v = float(v)
    return round(v, 3) if math.isfinite(v) else None


class Forecaster:
    def __init__(self, store):
        self.store = store
        self._cache: OrderedDict = OrderedDict()

    def run(self, t0: int, cx: int, cy: int, days: int) -> Forecast:
        key = (t0, cx, cy, days)
        with self.store.lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]
            fc = self._run(t0, cx, cy, days)
            self._cache[key] = fc
            while len(self._cache) > CACHE_MAX:
                self._cache.popitem(last=False)
            return fc

    def _run(self, t0: int, cx: int, cy: int, days: int) -> Forecast:
        st, model, meta, fi, wx = (self.store.state, self.store.model, self.store.meta, self.store.fire_index,
                                   self.store.weather)
        r = direct.predict_case(model, fi, t0, cx, cy, wx, days)
        if r is None:
            raise NoFireError(f"no burning cells around the point on {date_of(t0)}")
        P, A, dist, A0 = r
        W = direct.W
        off_x, off_y = cx - W, cy - W
        masks = [direct.select(P[k - 1], direct.rule_for(meta, k)) for k in range(1, days + 1)]
        truth = direct.case_truth(A, dist, A0, days)
        observed_through = int(max(0, min(days, st.viirs_day_max - t0)))

        F0 = fi.cube(t0, cx, cy, back=0, fwd=0)[1][0]
        start = [{"gx": int(x + off_x), "gy": int(y + off_y), "frp": _f(F0[x, y])}
                 for x, y in zip(*np.nonzero(A0))]

        first_pred = np.full(A0.shape, 0, np.int32)
        first_obs = np.full(A0.shape, 0, np.int32)
        for k in range(days, 0, -1):
            first_pred[masks[k - 1]] = k
            first_obs[truth[k - 1]] = k
        with np.errstate(all="ignore"):
            p_max = np.nanmax(np.where(np.isfinite(P), P, -1), axis=0)
        predicted = []
        for x, y in zip(*np.nonzero(first_pred)):
            k = int(first_pred[x, y])
            predicted.append({"gx": int(x + off_x), "gy": int(y + off_y), "day": k,
                              "probability": _f(P[k - 1, x, y]), "p_max": _f(p_max[x, y])})
        observed = []
        if observed_through > 0:
            for x, y in zip(*np.nonzero(first_obs)):
                k = int(first_obs[x, y])
                if k <= observed_through:
                    observed.append({"gx": int(x + off_x), "gy": int(y + off_y), "day": k})

        per_day, mask_cells = [], []
        for k in range(1, days + 1):
            m = masks[k - 1]
            cells = [(int(x + off_x), int(y + off_y)) for x, y in zip(*np.nonzero(m))]
            mask_cells.append(cells)
            row = {"day": k, "date": date_of(t0 + k), "predicted_cells": len(cells),
                   "predicted_km2": round(sum(geo.cell_area_km2(gy) for _, gy in cells), 1),
                   "mean_probability": _f(np.nanmean(P[k - 1][m])) if m.any() else None,
                   "observed_cells": None, "iou": None}
            if k <= observed_through:
                row["observed_cells"] = int(truth[k - 1].sum())
                row["iou"] = round(float(direct.iou_masks(m, truth[k - 1])), 3)
            per_day.append(row)

        lat, lon = geo.cell_center(cx, cy)
        ws, wd, tmax, rh, pr = wx.get(t0, lat, lon) if wx else (np.nan,) * 5
        wind = {"speed_kmh": _f(ws), "from_deg": _f(wd), "temp_max_c": _f(tmax), "rh_min_pct": _f(rh),
                "precip_mm": _f(pr), "source": "ERA5 (Open-Meteo archive, cached tiles)" if math.isfinite(ws)
                else "no cached weather for this tile/day"}
        return Forecast(t0=t0, days=days, cx=cx, cy=cy, start=start, predicted=predicted, observed=observed,
                        per_day=per_day, masks=mask_cells, observed_through_day=observed_through, wind=wind)


def to_geojson(fc: Forecast, query: dict) -> dict:
    feats = []
    for c in fc.start:
        feats.append(geo.feature(geo.cell_polygon(c["gx"], c["gy"]), kind="burning", day=0,
                                 date=date_of(fc.t0), frp_mw=c["frp"]))
    for c in fc.predicted:
        feats.append(geo.feature(geo.cell_polygon(c["gx"], c["gy"]), kind="predicted", day=c["day"],
                                 date=date_of(fc.t0 + c["day"]), probability=c["probability"],
                                 p_max=c["p_max"]))
    for c in fc.observed:
        feats.append(geo.feature(geo.cell_polygon(c["gx"], c["gy"]), kind="observed", day=c["day"],
                                 date=date_of(fc.t0 + c["day"])))
    start = [(c["gx"], c["gy"]) for c in fc.start]
    for k, cells in enumerate(fc.masks, 1):
        rings = geo.hull_rings(sorted(set(start) | set(cells)))
        if rings:
            feats.append(geo.feature({"type": "MultiPolygon", "coordinates": [[r] for r in rings]},
                                     kind="risk_area", day=k, date=date_of(fc.t0 + k),
                                     predicted_cells=len(cells)))
    return geo.collection(feats, query=query, summary=summary(fc))


def summary(fc: Forecast) -> dict:
    return {"start_date": date_of(fc.t0), "days": fc.days,
            "centre": {"lat": fc.lat, "lon": fc.lon, "gx": fc.cx, "gy": fc.cy},
            "burning_cells": len(fc.start), "wind": fc.wind, "per_day": fc.per_day,
            "observed_through_day": fc.observed_through_day,
            "historical": fc.observed_through_day > 0,
            "model": "equifire.direct (XGBoost, direct multi-horizon; cells are 0.05 deg)",
            "note": "predicted = cells newly burning by that day (first day shown); risk_area = convex hull of "
                    "burning + predicted cells by that day; observed = VIIRS truth for historical dates"}
