"""Direct multi-horizon spread model.

Instead of simulating day by day (errors compound), one model answers directly:
    "Given everything known on start day t0, will this cell newly burn within k days?"
for every cell around a fire and every horizon k. Each case is a 41 x 41 cell window
(~225 x 225 km) around a fire, handled as small image arrays, so features are fast.

Inputs (all from days <= t0 only):
  - VIIRS burning cells (all passes) for the last 30 days, FRP, and the t0 day / night overpasses split
    into the later and the earlier pass of that UTC day (the later one shows the newest fire front)
  - MODIS (Terra / Aqua) burning cells on t0 .. t0-7: extra looks at the fire front
  - fire shape (perimeter, elongation), recent growth (new cells per day), longer cell history
    (burned 31-90 / 31-365 days ago = fuel already gone), weather

Truth definition is identical to forecast.py: a cell counts at horizon k if it is detected
burning by VIIRS on some day j <= k, is at most j cells from the start-day fire, and was not burning on t0.

Cell selection per horizon is tuned on held-out training-period cases to maximize overlap (IoU):
a probability threshold, the expected-IoU rule, or "top-n" with n = expected count * scale.

Every model's meta file lists the feature names it uses; features are looked up by name, so old
models (subset of today's features) keep working.
"""
import json
import time

import numpy as np
import pandas as pd
from scipy import ndimage

from .common import EPOCH, GRID, MODELS, MODIS_SATS, VIIRS_SATS, cell_center, day_of_year_features
from .weather import TILE, cached_frames

W = 20                       # window half size in cells (same as forecast.py)
S = 2 * W + 1
HIST = 30                    # days of history in the dense cube
LONG = 365                   # days of history for per-cell fuel / burn-scar features
MAX_K = 28
TRAIN_KS = [1, 2, 3, 4, 5, 7, 10, 14, 21, 28]
MODEL_F = MODELS / "spread_direct.json"
META_F = MODELS / "spread_direct_meta.json"
FEATURE_VERSION = 2
_TILE = 64                   # spatial tile (cells) of the index; a window touches <= 2 x 2 tiles


class _Tiled:
    """Cell-day records sorted by (spatial tile, day): fast window x day-range queries."""

    def __init__(self, day, gx, gy, **vals):
        day = np.asarray(day, np.int32)
        gx = np.asarray(gx, np.int16)
        gy = np.asarray(gy, np.int16)
        tid = (gx.astype(np.int64) // _TILE) * 100_000 + gy.astype(np.int64) // _TILE
        order = np.lexsort((day, tid))
        self.day, self.gx, self.gy = day[order], gx[order], gy[order]
        self.vals = {k: np.asarray(v)[order] for k, v in vals.items()}
        t = tid[order]
        u, st = np.unique(t, return_index=True)
        en = np.r_[st[1:], len(t)]
        self.seg = {int(a): (int(b), int(c)) for a, b, c in zip(u, st, en)}

    def __len__(self):
        return len(self.day)

    def query(self, d_lo, d_hi, cx, cy):
        """Indices of records with d_lo <= day <= d_hi inside the window around (cx, cy)."""
        parts = []
        for tx in range((cx - W) // _TILE, (cx + W) // _TILE + 1):
            for ty in range((cy - W) // _TILE, (cy + W) // _TILE + 1):
                s = self.seg.get(tx * 100_000 + ty)
                if s is None:
                    continue
                a, b = s
                lo, hi = np.searchsorted(self.day[a:b], [d_lo, d_hi + 1])
                if hi > lo:
                    parts.append(np.arange(a + lo, a + hi))
        if not parts:
            return np.zeros(0, np.int64)
        i = np.concatenate(parts)
        m = (np.abs(self.gx[i].astype(np.int32) - cx) <= W) & (np.abs(self.gy[i].astype(np.int32) - cy) <= W)
        return i[m]


def _daily(cells, sats):
    """Per (day, cell): FRP sum, detection count and a day/night overpass bit mask (1 = night, 2 = day)."""
    c = cells[cells["sat"].isin(sats)]
    k = (c["day"].values.astype(np.int64) * 8000 + c["gx"].values) * 4000 + c["gy"].values
    u, inv = np.unique(k, return_inverse=True)
    frp = np.bincount(inv, c["frp_sum"].values.astype(np.float64), len(u)).astype(np.float32)
    n = np.bincount(inv, c["n"].values.astype(np.float64), len(u)).astype(np.float32)
    isday = c["dn"].values.astype(np.float64)
    dn = ((np.bincount(inv, 1 - isday, len(u)) > 0).astype(np.int8)
          | ((np.bincount(inv, isday, len(u)) > 0).astype(np.int8) << 1))
    day, rem = np.divmod(u, 8000 * 4000)
    gx, gy = np.divmod(rem, 4000)
    return day, gx, gy, frp, n, dn


class FireIndex:
    """VIIRS (and optionally MODIS) burning cell-days with fast window extraction.

    FireIndex(act): act = datasets.viirs_daily(cells) (VIIRS only, no day/night split).
    FireIndex.from_cells(cells): VIIRS with day/night split + MODIS, needed for version-2 features.
    """

    def __init__(self, act=None, _v=None, _m=None):
        if _v is None:
            _v = (act["day"].values, act["gx"].values, act["gy"].values, act["frp"].values.astype(np.float32),
                  act["n"].values.astype(np.float32), np.full(len(act), 3, np.int8))
        day, gx, gy, frp, n, dn = _v
        self.v = _Tiled(day, gx, gy, frp=frp, dn=dn)
        self.m = _Tiled(*_m[:3], frp=_m[3]) if _m is not None else None
        self.has_dn = act is None
        # day-sorted copies of VIIRS rows, for sampling fire cases
        o = np.argsort(self.v.day, kind="stable")
        self.day, self.gx, self.gy = self.v.day[o], self.v.gx[o], self.v.gy[o]

    @classmethod
    def from_cells(cls, cells):
        return cls(_v=_daily(cells, VIIRS_SATS), _m=_daily(cells, MODIS_SATS))

    def cube(self, t0, cx, cy, back=HIST, fwd=MAX_K):
        """VIIRS arrays [days, S, S]: active flag and FRP for days t0-back .. t0+fwd."""
        i = self.v.query(t0 - back, t0 + fwd, cx, cy)
        d = self.v.day[i] - (t0 - back)
        x = self.v.gx[i].astype(np.int32) - cx + W
        y = self.v.gy[i].astype(np.int32) - cy + W
        A = np.zeros((back + fwd + 1, S, S), np.float32)
        F = np.zeros_like(A)
        A[d, x, y] = 1
        F[d, x, y] = self.v.vals["frp"][i]
        return A, F

    def extras(self, t0, cx, cy):
        """Inputs beyond the cube, all from days <= t0: t0 overpass split, MODIS, long history."""
        e = {}
        i = self.v.query(t0, t0, cx, cy)
        x, y = self.v.gx[i].astype(np.int32) - cx + W, self.v.gy[i].astype(np.int32) - cy + W
        sub = getattr(self, "sub", None)
        if sub is not None:  # where inside each burning cell the 375 m detections sit (cell units)
            ox, oy = sub.lookup(np.full(len(i), t0), self.v.gx[i], self.v.gy[i])
            e["sox"] = np.full((S, S), np.nan, np.float32)
            e["soy"] = np.full((S, S), np.nan, np.float32)
            e["sox"][x, y], e["soy"][x, y] = ox, oy
        dn = self.v.vals["dn"][i]
        e["v_night"] = np.zeros((S, S), np.float32)
        e["v_day"] = np.zeros((S, S), np.float32)
        e["v_night"][x, y] = (dn & 1) > 0
        e["v_day"][x, y] = (dn & 2) > 0
        # long history: burned days in [t0-LONG, t0-31] and the last burn day before t0
        i = self.v.query(t0 - LONG, t0 - HIST - 1, cx, cy)
        x, y = self.v.gx[i].astype(np.int32) - cx + W, self.v.gy[i].astype(np.int32) - cy + W
        dd = self.v.day[i]
        flat = x * S + y
        e["h365"] = np.bincount(flat, minlength=S * S).reshape(S, S).astype(np.float32)
        e["h90"] = np.bincount(flat[dd >= t0 - 90], minlength=S * S).reshape(S, S).astype(np.float32)
        last = np.full(S * S, -1e9)
        np.maximum.at(last, flat, dd.astype(np.float64))
        e["last_old"] = last.reshape(S, S)
        if self.m is not None:
            i = self.m.query(t0 - 7, t0, cx, cy)
            x, y = self.m.gx[i].astype(np.int32) - cx + W, self.m.gy[i].astype(np.int32) - cy + W
            d = self.m.day[i] - (t0 - 7)
            M = np.zeros((8, S, S), np.float32)
            MF = np.zeros_like(M)
            M[d, x, y] = 1
            MF[d, x, y] = self.m.vals["frp"][i]
            e["M"], e["MF"] = M, MF
        return e


def _box(a, r):
    """Sum over (2r+1)^2 square around every cell (zero padded)."""
    return ndimage.uniform_filter(a, size=2 * r + 1, mode="constant") * (2 * r + 1) ** 2


def _dist(mask, cap=2 * S):
    if not mask.any():
        return np.full((S, S), cap, np.float32)
    return ndimage.distance_transform_cdt(~mask, metric="chessboard").astype(np.float32)


class Weather:
    def __init__(self):
        frames = cached_frames()
        self.w = {}
        if frames:
            Wd = pd.concat(frames, ignore_index=True).drop_duplicates(["day", "ty", "tx"])
            cols = ["wind_speed_10m_max", "wind_direction_10m_dominant", "temperature_2m_max",
                    "relative_humidity_2m_min", "precipitation_sum"]
            vals = Wd[cols].values.astype(float)
            self.w = dict(zip(zip(Wd["day"].astype(int), Wd["ty"].astype(int), Wd["tx"].astype(int)),
                              map(tuple, vals)))

    def get(self, day, lat, lon):
        return self.w.get((int(day), int(np.floor(lat / TILE)), int(np.floor(lon / TILE))),
                          (np.nan,) * 5)


V1_FEATURES = ["dist", "act_r1", "act_r2", "act_r3", "act_r5", "act_r8", "act_r12", "frp_r1", "frp_r2",
               "frp_r4", "frp_r8", "frp_max_r2", "act_lag1_r3", "act_lag2_r3", "act_lag3_r3", "p7_r3",
               "p7_cell", "p30_cell", "p30_r2", "centre_dist", "ux", "uy", "growth_align", "wind_align",
               "wind_push", "fire_act_lag0", "fire_act_lag1", "fire_act_lag2", "fire_act_lag3", "fire_act_p7",
               "fire_frp", "fire_frp_lag1", "lat", "lon", "doy_sin", "doy_cos", "wind_speed", "temp_max",
               "rh_min", "precip"]
FEATURES = V1_FEATURES + ["k"]  # features of the loaded / last trained model (set by load / train)
_XS, _YS = np.meshgrid(np.arange(S), np.arange(S), indexing="ij")


def case_features(A, F, t0, cx, cy, wx, ext=None, cols=None):
    """Per-cell features from days <= t0 only.
    Returns (feature matrix [S*S, len(cols)], dist [S, S], A0); cols default = FEATURES without 'k'."""
    cols = [c for c in (cols or FEATURES) if c != "k"]
    i0 = HIST
    A0, F0 = A[i0], F[i0]
    if A0.sum() == 0:
        return None
    b0 = A0 > 0
    dist = ndimage.distance_transform_cdt(A0 == 0, metric="chessboard").astype(np.float32)
    past = A[:i0]
    p7 = past[-7:].sum(0)
    p30 = past.sum(0)
    feats = {"dist": dist}
    for r in (1, 2, 3, 5, 8, 12):
        feats[f"act_r{r}"] = _box(A0, r)
    for r in (1, 2, 4, 8):
        feats[f"frp_r{r}"] = _box(F0, r)
    feats["frp_max_r2"] = ndimage.maximum_filter(F0, size=5, mode="constant")
    for lag in (1, 2, 3):
        feats[f"act_lag{lag}_r3"] = _box(A[i0 - lag], 3)
    feats["p7_r3"] = _box(p7, 3)
    feats["p7_cell"] = p7
    feats["p30_cell"] = p30
    feats["p30_r2"] = _box(p30, 2)
    # fire as a whole
    sc = {f"fire_act_lag{l}": A[i0 - l].sum() for l in (0, 1, 2, 3)}
    sc["fire_act_p7"] = p7.sum()
    sc["fire_frp"] = F0.sum()
    sc["fire_frp_lag1"] = F[i0 - 1].sum()
    # direction from FRP-weighted fire centre to cell
    xs, ys = _XS, _YS
    wgt = F0 + 1e-3 * A0
    mx, my = (wgt * xs).sum() / wgt.sum(), (wgt * ys).sum() / wgt.sum()
    vx, vy = xs - mx, ys - my
    rad = np.hypot(vx, vy) + 1e-6
    feats["centre_dist"] = rad
    feats["ux"], feats["uy"] = vx / rad, vy / rad
    # recent growth direction: centre shift from t0-2 to t0
    A2 = A[i0 - 2] + A[i0 - 1]
    if A2.sum() > 0:
        gx_, gy_ = mx - (A2 * xs).sum() / A2.sum(), my - (A2 * ys).sum() / A2.sum()
    else:
        gx_, gy_ = 0.0, 0.0
    feats["growth_align"] = (vx * gx_ + vy * gy_) / rad
    lat, lon = (float(v) for v in cell_center(cx, cy))
    ws, wd, tmax, rh, pr = wx.get(t0, lat, lon) if wx else (np.nan,) * 5
    wind_to = np.radians((wd + 180) % 360) if np.isfinite(wd) else np.nan
    # x = east, y = north in grid space
    feats["wind_align"] = (feats["ux"] * np.sin(wind_to) + feats["uy"] * np.cos(wind_to)) \
        if np.isfinite(wind_to) else np.full((S, S), np.nan, np.float32)
    feats["wind_push"] = feats["wind_align"] * ws
    s_, c_ = day_of_year_features(np.array([t0]))
    sc.update({"lat": lat, "lon": lon, "doy_sin": float(s_[0]), "doy_cos": float(c_[0]),
               "wind_speed": ws, "temp_max": tmax, "rh_min": rh, "precip": pr})

    if any(c not in feats and c not in sc for c in cols):
        _v2_features(feats, sc, A, F, b0, dist, p7, vx, vy, rad, wind_to, ws, lon, t0, ext)
    if any(c in V3_EXTRA for c in cols):
        _v3_features(feats, b0, ext)
    if any(c in V4_EXTRA for c in cols):  # v4: per-fire Open-Meteo weather + Rothermel physics (physfeat.py)
        from .physfeat import add as _v4_add
        _v4_add(feats, sc, b0, t0, lat, lon)

    X = np.empty((S * S, len(cols)), np.float32)
    for j, c in enumerate(cols):
        X[:, j] = np.asarray(feats[c], np.float32).ravel() if c in feats else sc[c]
    return X, dist, A0


def _v2_features(feats, sc, A, F, b0, dist, p7, vx, vy, rad, wind_to, ws, lon, t0, ext):
    """Version-2 features: overpass split, MODIS, fire shape, growth rate, long history. Days <= t0 only."""
    i0 = HIST
    if ext is None:
        raise ValueError("version-2 features need FireIndex.from_cells(...) extras")
    # newest fire front: of the two VIIRS overpasses of UTC day t0, which one is later here?
    day_utc = (13.5 - lon / 15) % 24
    night_utc = (1.5 - lon / 15) % 24
    night_late = night_utc > day_utc
    late, early = (ext["v_night"], ext["v_day"]) if night_late else (ext["v_day"], ext["v_night"])
    sc["night_is_later"] = float(night_late)
    sc["fire_late"], sc["fire_early"] = late.sum(), early.sum()
    feats["late_r1"], feats["late_r3"] = _box(late, 1), _box(late, 3)
    feats["early_r3"] = _box(early, 3)
    lonly = late * (1 - early)
    feats["lateonly_r2"] = _box(lonly, 2)
    feats["dist_late"] = _dist(late > 0)
    sc["fire_lateonly"] = lonly.sum()
    # new cells (burning, but not in the previous 7 days) on t0, t0-1, t0-2: front and growth rate
    new = [(A[i0 - l] > 0) & (A[i0 - l - 7:i0 - l].sum(0) == 0) for l in (0, 1, 2)]
    feats["new0_r1"] = _box(new[0].astype(np.float32), 1)
    feats["new0_r3"] = _box(new[0].astype(np.float32), 3)
    feats["new1_r3"] = _box(new[1].astype(np.float32), 3)
    feats["dist_new0"] = _dist(new[0])
    for l in (0, 1, 2):
        sc[f"fire_new{l}"] = float(new[l].sum())
    sc["fire_growth3"] = (sc["fire_new0"] + sc["fire_new1"] + sc["fire_new2"]) / 3
    sc["fire_growth_ratio"] = sc["fire_new0"] / (b0.sum() + 1)
    feats["dist_lag1"] = _dist(A[i0 - 1] > 0)
    feats["dist_p7"] = _dist(p7 > 0)
    # shape: perimeter, compactness, elongation and the cell's alignment with the long axis
    perim = b0 & ~ndimage.binary_erosion(b0, np.ones((3, 3), bool), border_value=0)
    feats["perim_r3"] = _box(perim.astype(np.float32), 3)
    n0 = b0.sum()
    sc["fire_perim_frac"] = perim.sum() / max(n0, 1)
    px, py = _XS[b0].astype(float), _YS[b0].astype(float)
    sc["fire_fill"] = n0 / ((np.ptp(px) + 1) * (np.ptp(py) + 1))
    if n0 >= 3:
        ev, evec = np.linalg.eigh(np.cov(np.vstack([px, py])) + 1e-3 * np.eye(2))
        ex, ey = evec[0, 1], evec[1, 1]
        sc["fire_elong"] = float(np.sqrt((ev[1] + 0.25) / (ev[0] + 0.25)))
        sc["fire_axis_wind"] = float(abs(ex * np.sin(wind_to) + ey * np.cos(wind_to))) \
            if np.isfinite(wind_to) else np.nan
    else:
        ex, ey = 1.0, 0.0
        sc["fire_elong"], sc["fire_axis_wind"] = 1.0, np.nan
    feats["axis_align"] = np.abs(vx * ex + vy * ey) / rad
    # long history: fuel already burned in the last year (not counting the last 30 days)
    feats["h365_cell"], feats["h90_cell"] = ext["h365"], ext["h90"]
    feats["h365_r2"] = _box((ext["h365"] > 0).astype(np.float32), 2)
    last30 = np.where(A[:i0].any(0), i0 - 1 - np.argmax(A[i0 - 1::-1] > 0, axis=0), -1)  # index of last burn
    age = np.where(last30 >= 0, i0 - last30, t0 - ext["last_old"])
    feats["burn_age"] = np.minimum(age, 999).astype(np.float32)
    sc["fire_h365_frac"] = float((ext["h365"][b0] > 0).mean())
    # MODIS (Terra / Aqua): other overpass times, sees fire the VIIRS passes may have missed
    if "M" in ext:
        M, MF = ext["M"], ext["MF"]
        m0, m1 = M[7], M[6]
        feats["m0_cell"], feats["m0_r1"], feats["m0_r3"] = m0, _box(m0, 1), _box(m0, 3)
        feats["m1_r2"] = _box(m1, 2)
        feats["mfrp0_r2"] = _box(MF[7], 2)
        monly = m0 * (1 - b0)
        feats["monly0_r2"] = _box(monly, 2)
        feats["m7_cell"] = M.sum(0)
        feats["dist_m0"] = _dist(m0 > 0)
        sc["fire_m0"], sc["fire_m1"], sc["fire_monly0"] = m0.sum(), m1.sum(), monly.sum()
        sc["fire_mfrp0"] = MF[7].sum()


def _v3_features(feats, b0, ext):
    """Version-3: sub-cell push. For each candidate cell, how far the detections inside each burning
    neighbour sit toward this candidate (dot product of the neighbour's detection offset with the
    direction neighbour -> candidate). Near +0.5 = the front is at the shared edge. Days <= t0 only."""
    sox = ext.get("sox") if ext else None
    if sox is None:
        for c in V3_EXTRA:
            feats[c] = np.full((S, S), np.nan, np.float32)
        return
    soy = ext["soy"]
    PX = np.pad(sox, 1, constant_values=np.nan)
    PY = np.pad(soy, 1, constant_values=np.nan)
    PB = np.pad(b0, 1, constant_values=False)
    pushes = []
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            if dx == 0 and dy == 0:
                continue
            sl = (slice(1 - dx, 1 - dx + S), slice(1 - dy, 1 - dy + S))  # neighbour at (x-dx, y-dy)
            p = (PX[sl] * dx + PY[sl] * dy) / np.hypot(dx, dy)
            pushes.append(np.where(PB[sl], p, np.nan))
    P = np.stack(pushes)
    known = np.isfinite(P)
    mx = np.where(known, P, -np.inf).max(0)
    feats["sub_push_max"] = np.where(np.isfinite(mx), mx, np.nan).astype(np.float32)
    cnt = known.sum(0)
    feats["sub_push_mean"] = np.where(cnt > 0, np.where(known, P, 0).sum(0) / np.maximum(cnt, 1),
                                      np.nan).astype(np.float32)
    feats["sub_push_n"] = (np.where(known, P, -1) > 0.15).sum(0).astype(np.float32)
    feats["sub_known"] = cnt.astype(np.float32)


V3_EXTRA = ["sub_push_max", "sub_push_mean", "sub_push_n", "sub_known"]


V2_EXTRA = ["night_is_later", "fire_late", "fire_early", "late_r1", "late_r3", "early_r3", "lateonly_r2",
            "dist_late", "fire_lateonly", "new0_r1", "new0_r3", "new1_r3", "dist_new0", "fire_new0", "fire_new1",
            "fire_new2", "fire_growth3", "fire_growth_ratio", "dist_lag1", "dist_p7", "perim_r3",
            "fire_perim_frac", "fire_fill", "fire_elong", "fire_axis_wind", "axis_align", "h365_cell", "h90_cell",
            "h365_r2", "burn_age", "fire_h365_frac", "m0_cell", "m0_r1", "m0_r3", "m1_r2", "mfrp0_r2",
            "monly0_r2", "m7_cell", "dist_m0", "fire_m0", "fire_m1", "fire_monly0", "fire_mfrp0"]
V2_FEATURES = V1_FEATURES + V2_EXTRA
V3_FEATURES = V2_FEATURES + V3_EXTRA
from .physfeat import V4_EXTRA  # noqa: E402  (physfeat imports only .common)
V4_FEATURES = V3_FEATURES + V4_EXTRA


def needs_extras(features):
    return any(f in V2_EXTRA or f in V3_EXTRA for f in features)


def case_truth(A, dist, A0, days):
    """Cumulative truth masks [days, S, S] with the same rules as forecast.py."""
    i0 = HIST
    out = np.zeros((days, S, S), bool)
    cum = np.zeros((S, S), bool)
    for j in range(1, days + 1):
        cum |= (A[i0 + j] > 0) & (dist <= j) & (A0 == 0)
        out[j - 1] = cum
    return out


def sample_cases(fi, days_ok, n, rng, min_cells=5):
    """Random fire cases like forecast.py: a burning cell with >= min_cells burning in its window."""
    idx = np.flatnonzero(days_ok)
    out = []
    tries = 0
    while len(out) < n and tries < n * 50:
        tries += 1
        i = idx[rng.integers(len(idx))]
        t0, cx, cy = int(fi.day[i]), int(fi.gx[i]), int(fi.gy[i])
        if len(fi.v.query(t0, t0, cx, cy)) >= min_cells:
            out.append((t0, cx, cy))
    return out


def _case_inputs(fi, t0, cx, cy, wx, cols):
    A, F = fi.cube(t0, cx, cy)
    ext = fi.extras(t0, cx, cy) if needs_extras(cols) else None
    return A, case_features(A, F, t0, cx, cy, wx, ext, cols)


def build_rows(fi, cases, wx, ks=TRAIN_KS, neg_per_pos=3, neg_min=60, pos_max=250, ks_per_case=4, rng=None,
               verbose=True, cols=None):
    """Training rows. Negatives are subsampled and given weight 1/rate so probabilities stay calibrated."""
    rng = rng or np.random.default_rng(0)
    cols = [c for c in (cols or FEATURES) if c != "k"]
    Xs, ys, ws, gs = [], [], [], []
    t = time.time()
    for ci, (t0, cx, cy) in enumerate(cases):
        A, r = _case_inputs(fi, t0, cx, cy, wx, cols)
        if r is None:
            continue
        X, dist, A0 = r
        truth = case_truth(A, dist, A0, MAX_K)
        d = dist.ravel()
        for k in (rng.choice(ks, min(ks_per_case, len(ks)), replace=False) if ks_per_case else ks):
            k = int(k)
            elig = np.flatnonzero((d >= 1) & (d <= k))
            if not len(elig):
                continue
            y = truth[k - 1].ravel()[elig]
            pos, neg = elig[y], elig[~y]
            pos_rate = 1.0
            if len(pos) > pos_max:
                pos = rng.choice(pos, pos_max, replace=False)
                pos_rate = pos_max / y.sum()
            m = min(len(neg), max(neg_min, neg_per_pos * len(pos)))
            negs = rng.choice(neg, m, replace=False) if m < len(neg) else neg
            rate = len(negs) / max(len(neg), 1)
            sel = np.concatenate([pos, negs])
            Xk = np.c_[X[sel], np.full(len(sel), k, np.float32)]
            Xs.append(Xk)
            ys.append(np.r_[np.ones(len(pos)), np.zeros(len(negs))].astype(np.float32))
            ws.append(np.r_[np.full(len(pos), 1 / pos_rate), np.full(len(negs), 1 / max(rate, 1e-6))]
                      .astype(np.float32))
            gs.append(np.full(len(sel), ci, np.int32))
        if verbose and (ci + 1) % 500 == 0:
            n = sum(map(len, ys))
            print(f"[direct] {ci + 1:,}/{len(cases):,} fire cases -> {n:,} training rows | {time.time() - t:.0f}s",
                  flush=True)
    return np.concatenate(Xs), np.concatenate(ys), np.concatenate(ws), np.concatenate(gs)


# ------------------------------------------------------------------ prediction + selection
def _proba(model, X):
    """Probabilities with the booster directly (no DMatrix copy; much faster for small batches)."""
    bst = model.get_booster() if hasattr(model, "get_booster") else model
    return bst.inplace_predict(X)


def predict_case(model, fi, t0, cx, cy, wx, days, cols=None):
    """Probabilities [days, S, S] (NaN where the cell cannot count) and the A cube for truth."""
    cols = cols or FEATURES
    A, r = _case_inputs(fi, t0, cx, cy, wx, cols)
    if r is None:
        return None
    X, dist, A0 = r
    d = dist.ravel()
    P = np.full((days, S * S), np.nan, np.float32)
    elig_all = np.flatnonzero((d >= 1) & (d <= days))
    if len(elig_all):
        rows = []
        for k in range(1, days + 1):
            e = elig_all[d[elig_all] <= k]
            rows.append((k, e))
        Xa = np.concatenate([np.c_[X[e], np.full(len(e), k, np.float32)] for k, e in rows])
        pa = _proba(model, Xa)
        o = 0
        for k, e in rows:
            P[k - 1, e] = pa[o:o + len(e)]
            o += len(e)
    return P.reshape(days, S, S), A, dist, A0


def expected_iou_select(p):
    """p: 1-D probabilities of eligible cells. Returns boolean mask maximizing expected IoU."""
    if not len(p):
        return np.zeros(0, bool)
    order = np.argsort(-p)
    ps = p[order]
    P = ps.sum()
    S_n = np.cumsum(ps)
    n = np.arange(1, len(ps) + 1)
    e = S_n / (n + P - S_n)
    p_empty = float(np.exp(np.sum(np.log1p(-np.clip(ps, 0, 1 - 1e-6)))))
    best = int(np.argmax(e))
    mask = np.zeros(len(p), bool)
    if e[best] > p_empty:
        mask[order[:best + 1]] = True
    return mask


def select(P_k, rule):
    """P_k: [S, S] probs (NaN = not eligible).
    rule: ('thr', t) | ('eiou', scale) | ('topn', scale, gate): top round(scale * expected count) cells,
    nothing if the expected count is below gate."""
    m = np.isfinite(P_k)
    out = np.zeros_like(m)
    p = P_k[m]
    if rule[0] == "thr":
        out[m] = p >= rule[1]
    elif rule[0] == "topn":
        E = float(p.sum())
        n = int(round(E * rule[1]))
        if E >= rule[2] and n > 0:
            sel = np.zeros(len(p), bool)
            sel[np.argsort(-p)[:n]] = True
            out[m] = sel
    else:
        out[m] = expected_iou_select(np.clip(p * rule[1], 0, 1))
    return out


def iou_masks(a, b):
    u = (a | b).sum()
    return 1.0 if u == 0 else (a & b).sum() / u


THR_GRID = [0.05, 0.08, 0.1, 0.12, 0.15, 0.2, 0.25, 0.3, 0.35, 0.4, 0.5]
SCALE_GRID = [0.6, 0.8, 1.0, 1.2, 1.5, 2.0]
TOPN_GRID = [(s, g) for s in (0.5, 0.7, 0.85, 1.0, 1.2, 1.5) for g in (0.0, 0.5, 1.0, 2.0)]
RULES = [("thr", t) for t in THR_GRID] + [("eiou", s) for s in SCALE_GRID] + \
        [("topn", s, g) for s, g in TOPN_GRID]


def rule_scores(model, fi, cases, wx, days=MAX_K, cols=None, rules=RULES):
    """IoU of every rule, per case and horizon: array [cases, days, rules]."""
    out = []
    for (t0, cx, cy) in cases:
        r = predict_case(model, fi, t0, cx, cy, wx, days, cols)
        if r is None:
            continue
        P, A, dist, A0 = r
        truth = case_truth(A, dist, A0, days)
        sc = np.zeros((days, len(rules)))
        for k in range(days):
            for j, rule in enumerate(rules):
                sc[k, j] = iou_masks(select(P[k], rule), truth[k])
        out.append(sc)
    return np.array(out)


def tune_from_scores(sc, rules=RULES):
    """Best rule per horizon + honest IoU: rules picked on one half of the cases, scored on the other."""
    mean = sc.mean(0)
    best = [rules[int(np.argmax(mean[k]))] for k in range(sc.shape[1])]
    half = np.arange(len(sc)) % 2 == 0
    honest = np.zeros(sc.shape[1])
    for a, b in ((half, ~half), (~half, half)):
        pick = sc[a].mean(0).argmax(1)
        honest += sc[b][:, np.arange(sc.shape[1]), pick].sum(0)
    return best, mean.max(1), honest / len(sc)


def tune_rules(model, fi, cases, wx, days=MAX_K, cols=None):
    """Pick the best selection rule per horizon on validation cases (training years only)."""
    sc = rule_scores(model, fi, cases, wx, days, cols)
    best, best_iou, honest = tune_from_scores(sc)
    return best, best_iou, honest, sc


def _paths(model_f=None, meta_f=None):
    """Model / meta paths; module globals MODEL_F / META_F are read at call time (callers may override them)."""
    if model_f is None:
        return MODEL_F, (meta_f or META_F)
    from pathlib import Path
    model_f = Path(model_f)
    return model_f, Path(meta_f) if meta_f else model_f.with_name(model_f.stem + "_meta.json")


def save(model, meta, model_f=None, meta_f=None):
    model_f, meta_f = _paths(model_f, meta_f)
    model_f.parent.mkdir(exist_ok=True)
    model.save_model(model_f)
    meta_f.write_text(json.dumps(meta))


def load(model_f=None, meta_f=None):
    import xgboost as xgb
    model_f, meta_f = _paths(model_f, meta_f)
    if not model_f.exists():
        return None, None
    m = xgb.XGBClassifier()
    m.load_model(model_f)
    meta = json.loads(meta_f.read_text())
    missing = [f for f in meta["features"] if f != "k" and f not in V4_FEATURES]
    if missing:
        raise SystemExit(f"direct model uses unknown features {missing}; retrain: main.py direct")
    global FEATURES
    FEATURES = meta["features"]
    return m, meta


def rule_for(meta, k):
    rules = meta["rules"]
    r = rules[min(k, len(rules)) - 1]
    return (r[0],) + tuple(float(v) for v in r[1:])


def fit_xgb(Xt, yt, wt, Xv, yv, wv, p, use_gpu, verbose=200):
    """Fit with early stopping; returns a plain CPU classifier trimmed to the best iteration."""
    import xgboost as xgb
    m = xgb.XGBClassifier(n_estimators=3000, tree_method="hist", device="cuda" if use_gpu else "cpu",
                          eval_metric="logloss", early_stopping_rounds=60, max_bin=256, **p)
    m.fit(Xt, yt, sample_weight=wt, eval_set=[(Xv, yv)], sample_weight_eval_set=[wv], verbose=verbose)
    n = m.best_iteration + 1
    return _reload(m.get_booster()[:n]), n


def _reload(bst):
    """Booster -> fresh CPU XGBClassifier (JSON round trip, no early-stopping state)."""
    import os
    import tempfile
    import xgboost as xgb
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "m.json")
        bst.save_model(f)
        m = xgb.XGBClassifier()
        m.load_model(f)
    m.set_params(device="cpu")
    return m


def train(cells, test_from, n_cases=6000, n_val=500, use_gpu=False, seed=0, trials=1, features=None,
          model_f=None, modis_era_cases=0, modis_era_weight=0.5):
    """Train the direct model on years < test_from; tune selection rules on held-out cases."""
    from sklearn.metrics import roc_auc_score
    global FEATURES
    t_all = time.time()
    cols = list(features or V2_FEATURES)
    from .fireindex_mm import get_fire_index
    fi = get_fire_index()  # memory-mapped from disk; `cells` is no longer needed
    wx = Weather()
    first_test_day = int((np.datetime64(f"{test_from}-01-01") - EPOCH).astype(int))
    # truth window must end before the test period starts (no leakage); history must exist
    back = LONG if needs_extras(cols) else HIST
    ok = (fi.day.astype(np.int64) + MAX_K < first_test_day) & (fi.day.astype(np.int64) - back >= fi.day.min())
    rng = np.random.default_rng(seed)
    cases = sample_cases(fi, ok, n_cases + n_val, rng)
    # split by time blocks of 30 days so validation fires are not the same fires as training ones
    block = np.array([c[0] // 30 for c in cases])
    ub = np.unique(block)
    vb = set(rng.choice(ub, max(1, int(len(ub) * n_val / (n_cases + n_val))), replace=False).tolist())
    val_cases = [c for c, b in zip(cases, block) if b in vb]
    tr_cases = [c for c, b in zip(cases, block) if b not in vb]
    print(f"[direct] {len(tr_cases):,} training fires, {len(val_cases):,} validation fires "
          f"(all from years < {test_from}); weather days cached: {len(wx.w):,}; "
          f"{len(cols)} features (version {FEATURE_VERSION})", flush=True)
    Xt, yt, wt, _ = build_rows(fi, tr_cases, wx, rng=rng, cols=cols)
    era_meta = None
    if modis_era_cases:
        if not 0 < modis_era_weight <= 1:
            raise ValueError("MODIS-era reconstruction weight must be > 0 and <= 1")
        from .modis_era import modis_era_rows, reconstruction_provenance
        era_meta = reconstruction_provenance(test_from)
        Xe, ye, we, ne = modis_era_rows(modis_era_cases, wx, cols, np.random.default_rng(seed + 91))
        Xt, yt, wt = np.concatenate((Xt, Xe)), np.concatenate((yt, ye)), np.concatenate((wt, we * modis_era_weight))
        era_meta.update(cases=ne, rows=len(ye), weight=modis_era_weight,
                        validation="real VIIRS only; reconstructed outcomes are training targets, not ground truth")
        print(f"[direct] added {ne} reconstructed MODIS-era fires, {len(ye):,} rows at weight "
              f"{modis_era_weight:g}; validation remains real VIIRS", flush=True)
        del Xe, ye, we
    Xv, yv, wv, _ = build_rows(fi, val_cases, wx, rng=np.random.default_rng(seed + 1), verbose=False,
                               ks_per_case=0, cols=cols)
    print(f"[direct] rows: train {len(yt):,} ({yt.mean():.1%} positive), val {len(yv):,} | "
          f"{Xt.shape[1]} features | RAM {Xt.nbytes / 1e9:.1f} GB", flush=True)

    rng_p = np.random.default_rng(seed + 7)
    best = None
    for trial in range(trials):
        if trial == 0:
            p = dict(learning_rate=0.05, max_depth=8, min_child_weight=10, subsample=0.8,
                     colsample_bytree=0.8, reg_lambda=1.0)
        else:
            p = dict(learning_rate=float(np.exp(rng_p.uniform(np.log(0.02), np.log(0.12)))),
                     max_depth=int(rng_p.integers(5, 11)),
                     min_child_weight=float(np.exp(rng_p.uniform(0, np.log(100)))),
                     subsample=float(rng_p.uniform(0.6, 1)), colsample_bytree=float(rng_p.uniform(0.5, 1)),
                     reg_lambda=float(np.exp(rng_p.uniform(np.log(0.3), np.log(10)))))
        t = time.time()
        m, ntree = fit_xgb(Xt, yt, wt, Xv, yv, wv, p, use_gpu)
        auc = roc_auc_score(yv, _proba(m, Xv), sample_weight=wv)
        print(f"[direct] trial {trial + 1}/{trials}: val ROC-AUC {auc:.4f}, {ntree} trees, "
              f"lr {p['learning_rate']:.3f} depth {p['max_depth']} | {time.time() - t:.0f}s", flush=True)
        if best is None or auc > best[0]:
            best = (auc, m, p)
    auc, m, p = best
    del Xt, yt, wt
    model = m
    FEATURES = cols + ["k"]

    print("[direct] tuning how many cells to call 'will burn' per horizon (validation fires)...", flush=True)
    rules, best_iou, honest, _ = tune_rules(model, fi, val_cases, wx, MAX_K, FEATURES)
    for k in (1, 3, 7, 14, 28):
        print(f"[direct]   day {k:>2}: rule {' '.join(map(str, rules[k - 1]))} -> val IoU {best_iou[k - 1]:.1%} "
              f"(honest, rule picked on other half: {honest[k - 1]:.1%})")
    meta = {"features": FEATURES, "feature_version": FEATURE_VERSION, "needs_cells": needs_extras(FEATURES),
            "rules": [list(r) for r in rules], "test_from": int(test_from),
            "val_auc": float(auc), "val_iou": [float(v) for v in best_iou],
            "val_iou_honest": [float(v) for v in honest], "params": p,
            "n_train_fires": len(tr_cases), "n_val_fires": len(val_cases)}
    if era_meta is not None:
        meta["modis_era"] = era_meta
    save(model, meta, model_f)
    print(f"[direct] saved {_paths(model_f)[0].name} | val ROC-AUC {auc:.4f}, mean val IoU days 1-{MAX_K} "
          f"{np.mean(best_iou):.1%} (honest {np.mean(honest):.1%}) | {time.time() - t_all:.0f}s", flush=True)
    return model, meta
