"""Version-4 "physics-informed" features for the direct spread model.

Per fire case (one weather point = the window centre, from equifire.firewx, days t0-13 .. t0 only):
  - raw weather of day t0 and the days before (wind, gust, T, RH, VPD, rain, days since rain)
  - dead fuel moisture (Simard 1968 EMC + daily time-lag drying / rain wetting for 10 h / 100 h fuels)
  - Rothermel 1972 head rate of spread, Byram intensity, crown check, L/B ratio (Anderson 1983),
    using backend/physics_spread.py (fuel class by the latitude rule there = an ASSUMPTION, no fuel map)
Per candidate cell:
  - direction from the nearest burning cell of t0 to the cell vs the physics head direction (downwind)
  - directional rate of spread on the fire ellipse, physics arrival time (hours) = distance from the
    nearest burning cell edge / directional ROS, and indicators "physics reaches it within 24/48/72 h".
No slope (no DEM in the training pipeline). Missing weather -> NaN in every weather/physics feature.
Nothing from days > t0 enters these features.
"""
import math

import numpy as np
import pandas as pd
from scipy import ndimage

from .common import GRID

V4_EXTRA = ["fw_known", "fw_wind", "fw_gust", "fw_tmax", "fw_rh_min", "fw_rh_mean", "fw_vpd", "fw_precip",
            "fw_precip3", "fw_precip14", "fw_dsr", "fw_wind_lag1", "fw_rh_min_lag1",
            "ph_m1", "ph_m10", "ph_m100", "ph_ros_head", "ph_lb", "ph_intensity", "ph_crown",
            "ph_dist_km", "ph_align", "ph_ros_dir", "ph_arrival_h", "ph_reach24", "ph_reach48", "ph_reach72",
            "ph_push"]
CAP_H = 9999.0
KM_DEG = 111.32

_PS = None


def _ps():
    global _PS
    if _PS is None:
        from backend import physics_spread
        _PS = physics_spread
    return _PS


def _moisture(s, I):
    """Dead fuel moisture (%) on t0 from the daily series s [days, vars]."""
    ps = _ps()
    emc_min = ps.emc_simard(s[:, I["temperature_2m_max"]], s[:, I["relative_humidity_2m_min"]])
    emc_mean = ps.emc_simard(s[:, I["temperature_2m_mean"]], s[:, I["relative_humidity_2m_mean"]])
    rain = np.nan_to_num(s[:, I["precipitation_sum"]])
    ok = np.isfinite(emc_mean)
    init = float(np.nanmean(emc_mean)) if ok.any() else 15.0
    m10, m100 = init + 1, init + 2
    for d in range(len(s)):
        e = emc_mean[d] if ok[d] else init
        m10 += (e - m10) * (1 - math.exp(-24 / 10))
        m100 += (e - m100) * (1 - math.exp(-24 / 100))
        if rain[d] > 0:
            m10 += (35 - m10) * (1 - math.exp(-rain[d] / 2.0))
            m100 += (35 - m100) * (1 - math.exp(-rain[d] / 10.0))
    m1 = float(emc_min[-1])
    if rain[-1] > 0:
        m1 += (35 - m1) * (1 - math.exp(-rain[-1] / 0.5))
    return m1, float(m10), float(m100)


def case_physics(s, I, lat):
    """Scalars for one case from its weather series (or None if t0 weather is missing)."""
    t0 = s[-1]
    if not np.isfinite(t0[I["wind_speed_10m_max"]]) or not np.isfinite(t0[I["relative_humidity_2m_min"]]):
        return None
    ps = _ps()
    rain = s[:, I["precipitation_sum"]]
    wet = np.flatnonzero(np.nan_to_num(rain) >= 1.0)
    sc = {"fw_known": 1.0, "fw_wind": t0[I["wind_speed_10m_max"]], "fw_gust": t0[I["wind_gusts_10m_max"]],
          "fw_tmax": t0[I["temperature_2m_max"]], "fw_rh_min": t0[I["relative_humidity_2m_min"]],
          "fw_rh_mean": t0[I["relative_humidity_2m_mean"]], "fw_vpd": t0[I["vapour_pressure_deficit_max"]],
          "fw_precip": rain[-1], "fw_precip3": float(np.nansum(rain[-3:])),
          "fw_precip14": float(np.nansum(rain)),
          "fw_dsr": float(len(s) - 1 - wet[-1]) if len(wet) else float(len(s)),
          "fw_wind_lag1": s[-2, I["wind_speed_10m_max"]], "fw_rh_min_lag1": s[-2, I["relative_humidity_2m_min"]]}
    m1, m10, m100 = _moisture(s, I)
    fuel, _ = ps.choose_fuel(lat)
    wind_dir = t0[I["wind_direction_10m_dominant"]]
    wx = pd.DataFrame({"wind_kmh": [float(sc["fw_wind"])], "wind_dir": [float(np.nan_to_num(wind_dir))]})
    fm = pd.DataFrame({"m1h": [max(m1, 1.0)], "m10h": [max(m10, 1.0)], "m100h": [max(m100, 1.0)]})
    hb = ps.hourly_behaviour(fuel, wx, fm)
    sc.update(ph_m1=m1, ph_m10=m10, ph_m100=m100, ph_ros_head=float(hb["ros_flat"][0] * 60),  # m/h
              ph_lb=float(hb["lb"][0]), ph_intensity=float(hb["intensity_kw_m"][0]), ph_crown=float(hb["crown"][0]))
    sc["_head_dir"] = math.radians(hb["spread_dir"][0]) if np.isfinite(wind_dir) else np.nan
    return sc


def add(feats, sc, b0, t0, lat, lon, store=None):
    """Fill V4_EXTRA into feats (per-cell [S, S]) / sc (scalars). b0: burning cells on t0."""
    from . import firewx
    S = b0.shape[0]
    nan = np.full((S, S), np.nan, np.float32)
    store = store or firewx.store()
    s = store.series(t0, lat, lon)
    ph = case_physics(s, firewx.IDX, lat) if s is not None else None
    # geometry: nearest burning cell of t0 (km, x = east, y = north)
    dxk, dyk = GRID * KM_DEG * math.cos(math.radians(lat)), GRID * KM_DEG
    dist, (ix, iy) = ndimage.distance_transform_edt(~b0, sampling=(dxk, dyk), return_indices=True)
    xs, ys = np.meshgrid(np.arange(S), np.arange(S), indexing="ij")
    vx, vy = (xs - ix) * dxk, (ys - iy) * dyk
    feats["ph_dist_km"] = dist.astype(np.float32)
    if ph is None:
        for c in V4_EXTRA:
            if c.startswith("fw_") or c in ("ph_m1", "ph_m10", "ph_m100", "ph_ros_head", "ph_lb", "ph_intensity",
                                            "ph_crown"):
                sc[c] = 0.0 if c == "fw_known" else np.nan
        for c in ("ph_align", "ph_ros_dir", "ph_arrival_h", "ph_reach24", "ph_reach48", "ph_reach72", "ph_push"):
            feats[c] = nan
        return
    hd = ph.pop("_head_dir")
    sc.update(ph)
    r = np.hypot(vx, vy)
    with np.errstate(invalid="ignore", divide="ignore"):
        cosd = np.where(r > 0, (vx * math.sin(hd) + vy * math.cos(hd)) / r, np.nan) if np.isfinite(hd) else nan
        lb = ph["ph_lb"]
        e = math.sqrt(max(lb * lb - 1, 0)) / lb
        head = ph["ph_ros_head"]
        rate = head * (1 - e) / (1 - e * np.nan_to_num(cosd))  # m/h; no direction info -> mean-ish (cos 0)
        edge_km = np.maximum(dist - 0.5 * min(dxk, dyk), 0)
        arr = np.where(rate > 0, edge_km * 1000 / np.maximum(rate, 1e-9), CAP_H)
    arr = np.minimum(arr, CAP_H)
    arr[b0] = 0
    feats["ph_align"] = cosd.astype(np.float32)
    feats["ph_ros_dir"] = rate.astype(np.float32)
    feats["ph_arrival_h"] = arr.astype(np.float32)
    for h in (24, 48, 72):
        feats[f"ph_reach{h}"] = (arr <= h).astype(np.float32)
    feats["ph_push"] = (cosd * ph["fw_wind"]).astype(np.float32)
