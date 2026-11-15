"""Physics-based fire-spread simulator (Rothermel surface spread + elliptical growth on a raster).

Used by GET /simulate and GET /simulate/weather (block at the end of backend/app.py). Everything
here is a *screening estimate*. Optional mapped fuels/terrain/firebreaks/dryness and hourly spatial
forecast weather are used when available, with explicit fallback metadata. Suppression is not modelled.

Model chain (per hour of the simulation, per raster cell)
---------------------------------------------------------
1. Weather (hourly, one point at the ignition centroid): Open-Meteo forecast API (recent past /
   future, https://api.open-meteo.com/v1/forecast) or Open-Meteo archive API (ERA5-based, older
   than ~5 days, https://archive-api.open-meteo.com/v1/archive). Variables temperature_2m,
   relative_humidity_2m, wind_speed_10m, wind_direction_10m, wind_gusts_10m, precipitation.
   48 h before `start` are fetched as moisture spin-up.
   Gridded mode (default in /simulate, weather=grid): equifire.fireweather.get_weather_grid fetches the same
   variables on a WG_N x WG_N = 5 x 5 point grid over +-WG_HALF_KM = 40 km around the ignition centroid (one
   multi-point Open-Meteo call per API). Fuel moisture, Rothermel no-slope ROS and the wind phi vector are computed
   at every weather point and bilinearly interpolated to the raster cells each hour (wind direction through the
   phi vector, not angles); outside the 40 km box values are clamped to the edge. Falls back to one point.
   The Canadian FWI System for the start date (equifire.fireweather.fwi_for) is added to the response as context
   and as a cross-check of fine-fuel moisture (FFMC -> m = 147.2 (101 - FFMC) / (59.5 + FFMC)); not used in spread.
2. Dead fuel moisture: equilibrium moisture content EMC from T and RH (Simard 1968, as used in
   NFDRS, Cohen & Deeming 1985):
       RH < 10 %  : EMC = 0.03229 + 0.281073 H - 0.000578 H T
       10-50 %    : EMC = 2.22749 + 0.160107 H - 0.01478 T
       RH >= 50 % : EMC = 21.0606 + 0.005565 H^2 - 0.00035 H T - 0.483199 H     (T in F, H in %)
   1 h / 10 h / 100 h fuels relax toward EMC with time lags 1 / 10 / 100 h (Byram 1963 time-lag
   concept): M += (EMC - M)(1 - exp(-dt / tau)). Rain wets fuels toward saturation (35 %):
   M += (35 - M)(1 - exp(-P / P_i)), P_i = 0.5 / 2 / 10 mm (heuristic, documented as such).
   Live herbaceous 60 %, live woody 100 % (assumed constants; no satellite live-fuel product used).
3. Rate of spread: Rothermel (1972) with Albini (1976) corrections, Anderson (1982) fuel models
       R = I_R xi (1 + phi_w + phi_s) / (rho_b eps Q_ig)
       phi_w = C U^B (beta/beta_op)^-E,  phi_s = 5.275 beta^-0.3 tan^2(slope)
   U = mid-flame wind = WAF x U_20ft, U_20ft = U_10m / 1.15 (Lawson & Armitage 2008). WAF for
   unsheltered fuels from Albini & Baughman (1979): WAF = 1.83 / ln((20 + 0.36 d) / (0.13 d)),
   d = fuel bed depth (ft); 0.15 for surface fuels under a forest canopy (Andrews 2012, sheltered).
   Wind and slope are combined as vectors (Rothermel 1972 / Finney 1998 FARSITE): the resultant
   phi_e gives the head ROS R0 (1 + phi_e) and the spread direction.
   Fuel classes (no fuel map -> ASSUMPTION, see choose_fuel):
       grass   = Anderson FM1 (short grass)       savanna = FM2 (timber/grass understory)
       shrub   = FM6 (dormant brush)              forest  = FM10 (timber litter + understory)
   Forest crown fire: Van Wagner (1977) initiation I0 = (0.010 CBH (460 + 25.9 FMC))^1.5 kW/m vs
   Byram surface intensity I_B = H_A R; active crown ROS = 3.34 x R_FM10(WAF 0.4) (Rothermel 1991)
   when it exceeds 3 / CBD (Van Wagner 1977). Assumed CBH 4 m, CBD 0.15 kg/m3, FMC 100 %.
4. Fire shape: ellipse with length-to-breadth LB = 0.936 e^(0.2566 U) + 0.461 e^(-0.1548 U) - 0.397
   (Anderson 1983, U = effective mid-flame wind in mph, capped at 8). Rate in direction theta from
   the head, fire at the rear focus: R(theta) = R_head (1 - e) / (1 - e cos theta), e = sqrt(LB^2-1)/LB.
5. Growth: minimum-travel-time raster (cf. Finney 2002) solved exactly with a time-dependent
   Dijkstra on 16 neighbour directions; travel along an edge integrates the hourly rate (stalls
   when fuels are too moist or it rains). Terrain from the Open-Meteo elevation API (Copernicus
   DEM 90 m) sampled on a 15 x 15 grid and bilinearly interpolated -> slope / aspect (smoothed).
6. Envelope: two extra runs, "low" = wind x0.8 & RH +10, "high" = wind x1.2 & RH -10 (weather-forecast
   uncertainty proxy, not a calibrated probability). They run on the SAME grid as the central run when the
   central fire reaches <= ENV_SAME_GRID_CELLS cells (cheap, exact comparison); otherwise on a 2x coarser grid,
   with the coarse ignition area replaced by the central one (area = central initial + coarse growth). Values
   are then clipped so low <= central <= high (lower wind can widen the flanks/back, so tiny inversions
   happen); summary.envelope_clipped_values counts the clips. (v1 bug: small fires showed low = high > central
   because the coarse grid quadrupled the ignition cell area.)
7. Several fires: ignition cells are split into fires A, B, C ... (largest first) = connected components with a
   gap tolerance gap_km (1 km: two cells join when the empty gap between them is <= 1 km). The Dijkstra carries
   the label of the first-arriving front (mtt_labelled), so each frame reports per-fire burned area (attributed
   to the first-arriving fire) and the groups still separate; a merge = first time two labelled burned areas
   touch (8-neighbour contact time = max of the two arrival times), union-find over groups -> merges[].
8. Calibration (data assimilation, ignition=live/archive, calibration=on): the fire's detections of the last
   D = 1..3 days (largest D with >= 3 detections in the 24 h before t_now - D d) are associated to the current
   ignition (within CAL_ASSOC_KM), the physics is run over that past window with that window's weather, and one
   rate-of-spread multiplier m (secant search in log m, <= 4 runs) is fitted so simulated growth = observed
   growth (new 375 m VIIRS pixels for live; new 0.05 deg cells for archive). Optional (goes=on, Americas): GOES
   ABI 2 km fire pixels of the same window (equifire.goes, sampled hourly) as a second residual on the GOES pixel
   grid (simulated area -> GOES pixels via the fixed-grid projection), weighted by 1/(s_model^2 + 1/(N+1)).
   Shrinkage toward 1 (v3, Bayesian, chosen a priori): log m = w log m_fit, w = p^2 s_p^2 / (p^2 s_p^2 + s_e^2),
   prior sd s_p = ln 3, s_e^2 = s_model^2 + 1/(N+1) with s_model = ln 3 (day-to-day growth persistence error),
   p = fitted d log area / d log m; no calibration with < 3 new polar pixels and no GOES evidence; m clipped
   to 0.1..5 (v2: w = N/(N+15), 0.05..5, kept as shrink='v2'). m multiplies every rate of the forward run.
   Only detections with t <= start are used. Honest tests: reports/physics_validation_v2.json and _v3.json.

Grid geometry (for external fuel / barrier / DEM / weather layers to align): square n x n raster, n odd,
centred on the ignition centroid (clat, clon) = mean of the ignition points. Row i = south->north, column j =
west->east; cell centre (i, j) is ((j - n//2) * cell_m east, (i - n//2) * cell_m north) of the centroid in a local
equirectangular frame: 1 deg lat = 111 320 m, 1 deg lon = 111 320 cos(clat) m (see class Grid, Grid.ij,
Grid.centre_lonlat). cell_m = max(250 m, 2 * half / (max_cells - 1)), max_cells = 161; half = domain half width
(3..80 km, from the maximum possible run). The response reports grid.cell_m, grid.size, grid.half_width_km and
grid.center.

All external calls are cached in backend/.cache/meteo_*.json and degrade gracefully (fallback
weather / flat terrain) with the failure reported in the response.
"""
from __future__ import annotations

import datetime as dt
import functools
import hashlib
import heapq
import json
import math
import time
from pathlib import Path

import numpy as np
import pandas as pd

CACHE_DIR = Path(__file__).resolve().parent / ".cache"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
ELEVATION_URL = "https://api.open-meteo.com/v1/elevation"
HOURLY = ["temperature_2m", "relative_humidity_2m", "wind_speed_10m", "wind_direction_10m",
          "wind_gusts_10m", "precipitation"]
WCOLS = ["temp_c", "rh", "wind_kmh", "wind_dir", "gust_kmh", "rain_mm"]
SPINUP_H = 48
FORECAST_TTL_S = 3600
M_TO_DEG = 1 / 111_320.0

# ------------------------------------------------------------------ fuel models (Anderson 1982)
T_AC = 0.0459137  # tons/acre -> lb/ft2
FUEL_MODELS = {
    # name: loads (t/ac) 1h, 10h, 100h, live herb, live woody; SAV 1h, herb, woody (1/ft); depth ft; Mx
    "grass":   dict(fm="FM1 short grass", w=(0.74, 0, 0, 0, 0), sav=(3500, 1500, 1500), depth=1.0, mx=0.12),
    "savanna": dict(fm="FM2 timber (grass and understory)", w=(2.0, 1.0, 0.5, 0.5, 0), sav=(3000, 1500, 1500),
                    depth=1.0, mx=0.15),
    "shrub":   dict(fm="FM6 dormant brush", w=(1.5, 2.5, 2.0, 0, 0), sav=(1750, 1500, 1500), depth=2.5, mx=0.25),
    "forest":  dict(fm="FM10 timber (litter and understory) + crown fire check", w=(3.01, 2.0, 5.01, 0, 2.0),
                    sav=(2000, 1500, 1500), depth=1.0, mx=0.25),
}
# All 13 Anderson (1982) models (same numbers as equifire/landscape.ANDERSON13), keyed "fm1".."fm13"; used per
# cell when the landscape fuel map is available (fuel_model codes 1..13).
_A13 = {
    1: ("FM1 short grass", (0.74, 0, 0, 0, 0), (3500, 1500, 1500), 1.0, 0.12),
    2: ("FM2 timber (grass and understory)", (2.0, 1.0, 0.5, 0.5, 0), (3000, 1500, 1500), 1.0, 0.15),
    3: ("FM3 tall grass", (3.01, 0, 0, 0, 0), (1500, 1500, 1500), 2.5, 0.25),
    4: ("FM4 chaparral", (5.01, 4.01, 2.0, 0, 5.01), (2000, 1500, 1500), 6.0, 0.20),
    5: ("FM5 brush", (1.0, 0.5, 0, 0, 2.0), (2000, 1500, 1500), 2.0, 0.20),
    6: ("FM6 dormant brush", (1.5, 2.5, 2.0, 0, 0), (1750, 1500, 1500), 2.5, 0.25),
    7: ("FM7 southern rough", (1.13, 1.87, 1.5, 0, 0.37), (1750, 1500, 1500), 2.5, 0.40),
    8: ("FM8 closed timber litter", (1.5, 1.0, 2.5, 0, 0), (2000, 1500, 1500), 0.2, 0.30),
    9: ("FM9 hardwood litter", (2.92, 0.41, 0.15, 0, 0), (2500, 1500, 1500), 0.2, 0.25),
    10: ("FM10 timber (litter and understory)", (3.01, 2.0, 5.01, 0, 2.0), (2000, 1500, 1500), 1.0, 0.25),
    11: ("FM11 light logging slash", (1.5, 4.51, 5.51, 0, 0), (1500, 1500, 1500), 1.0, 0.15),
    12: ("FM12 medium logging slash", (4.01, 14.03, 16.53, 0, 0), (1500, 1500, 1500), 2.3, 0.20),
    13: ("FM13 heavy logging slash", (7.01, 23.04, 28.05, 0, 0), (1500, 1500, 1500), 3.0, 0.25),
}
for _k, (_fm, _w, _sav, _d, _mx) in _A13.items():
    FUEL_MODELS[f"fm{_k}"] = dict(fm=_fm, w=_w, sav=_sav, depth=_d, mx=_mx)
TIMBER = {"forest", "fm8", "fm9", "fm10"}          # surface fuels under a canopy: sheltered WAF 0.15
CROWN_CAPABLE = {"forest", "fm10"}                  # Van Wagner crown check (conifer canopy assumed)
HEAT = 8000.0       # BTU/lb
ST, SE = 0.0555, 0.010
RHO_P = 32.0        # lb/ft3
LIVE_HERB_M, LIVE_WOODY_M = 0.60, 1.00
CBH_M, CBD, FMC = 4.0, 0.15, 100.0
FT_MIN_TO_M_MIN = 0.3048
NEIGH = [(1, 0), (0, 1), (-1, 0), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1),
         (1, 2), (2, 1), (-1, 2), (-2, 1), (1, -2), (2, -1), (-1, -2), (-2, -1)]  # (dx, dy)


def choose_fuel(lat: float, fuel: str = "auto") -> tuple[str, str]:
    """Fuel class + why. Without a fuel map the latitude rule is a crude ASSUMPTION."""
    if fuel in FUEL_MODELS:
        return fuel, "chosen by the user (fuel=%s)" % fuel
    a = abs(lat)
    if a >= 45:
        return "forest", "ASSUMPTION: |lat| >= 45 deg -> boreal/temperate forest (no fuel map used)"
    if a >= 23.5:
        return "shrub", "ASSUMPTION: 23.5 <= |lat| < 45 deg -> shrubland/Mediterranean (no fuel map used)"
    return "savanna", "ASSUMPTION: |lat| < 23.5 deg -> tropical savanna/grass (no fuel map used)"


def waf(fuel: str) -> float:
    if fuel in TIMBER:
        return 0.15
    d = FUEL_MODELS[fuel]["depth"]
    return 1.83 / math.log((20 + 0.36 * d) / (0.13 * d))


# ------------------------------------------------------------------ Rothermel 1972
def rothermel(fuel: str, m1: float, m10: float, m100: float, mlh: float = LIVE_HERB_M,
              mlw: float = LIVE_WOODY_M) -> dict:
    """No-wind no-slope ROS and the wind/slope coefficients. Moistures as fractions."""
    p = FUEL_MODELS[fuel]
    w = np.array(p["w"], float) * T_AC
    sav = np.array([p["sav"][0], 109.0, 30.0, p["sav"][1], p["sav"][2]])
    mois = np.array([m1, m10, m100, mlh, mlw])
    depth, mx_d = p["depth"], p["mx"]
    dead, live = slice(0, 3), slice(3, 5)
    a = sav * w / RHO_P
    out = {"R0": 0.0, "IR": 0.0, "sigma": 0.0, "beta": 0.0, "beta_ratio": 1.0, "C": 0.0, "B": 1.0, "E": 0.0,
           "tr_min": 0.0}
    A_d, A_l = a[dead].sum(), a[live].sum()
    A_t = A_d + A_l
    if A_t <= 0:
        return out
    f_ij = np.zeros(5)
    if A_d > 0:
        f_ij[dead] = a[dead] / A_d
    if A_l > 0:
        f_ij[live] = a[live] / A_l
    f_i = np.array([A_d / A_t, A_l / A_t])
    sig_i = np.array([(f_ij[dead] * sav[dead]).sum(), (f_ij[live] * sav[live]).sum()])
    sigma = float((f_i * sig_i).sum())
    beta = float(w.sum() / depth / RHO_P)
    rho_b = w.sum() / depth
    beta_op = 3.348 * sigma ** -0.8189
    br = beta / beta_op
    gmax = sigma ** 1.5 / (495 + 0.0594 * sigma ** 1.5)
    A = 133 * sigma ** -0.7913
    gamma = gmax * br ** A * math.exp(A * (1 - br))
    # live moisture of extinction (Albini 1976)
    eps = np.where(sav > 0, np.exp(-138 / sav), 0)
    wd_e = (w[dead] * eps[dead]).sum()
    wl_e = (w[live] * np.exp(-500 / sav[live])).sum()
    if wl_e > 0:
        mf_dead = (w[dead] * eps[dead] * mois[dead]).sum() / max(wd_e, 1e-12)
        mx_l = max(2.9 * wd_e / wl_e * (1 - mf_dead / mx_d) - 0.226, mx_d)
    else:
        mx_l = mx_d
    m_i = np.array([(f_ij[dead] * mois[dead]).sum(), (f_ij[live] * mois[live]).sum()])
    mx = np.array([mx_d, mx_l])
    rm = np.clip(m_i / mx, 0, 1)
    eta_m = np.clip(1 - 2.59 * rm + 5.11 * rm ** 2 - 3.52 * rm ** 3, 0, 1)
    eta_m[rm >= 1] = 0.0
    eta_s = min(0.174 * SE ** -0.19, 1.0)
    wn = np.array([(f_ij[dead] * w[dead]).sum(), (f_ij[live] * w[live]).sum()]) * (1 - ST)
    if A_l == 0:
        eta_m[1] = 0
    IR = gamma * HEAT * float((wn * eta_m * eta_s).sum())
    xi = math.exp((0.792 + 0.681 * sigma ** 0.5) * (beta + 0.1)) / (192 + 0.2595 * sigma)
    qig = 250 + 1116 * mois
    sink_i = np.array([(f_ij[dead] * eps[dead] * qig[dead]).sum(), (f_ij[live] * eps[live] * qig[live]).sum()])
    sink = rho_b * float((f_i * sink_i).sum())
    R0 = IR * xi / sink if sink > 0 else 0.0
    if R0 < 1e-6:
        R0 = 0.0
    out.update(R0=R0, IR=IR, sigma=sigma, beta=beta, beta_ratio=br,
               C=7.47 * math.exp(-0.133 * sigma ** 0.55), B=0.02526 * sigma ** 0.54,
               E=0.715 * math.exp(-3.59e-4 * sigma), tr_min=384 / sigma)
    return out


@functools.lru_cache(maxsize=50_000)
def _rothermel_memo(fuel: str, m1: float, m10: float, m100: float, mlh: float = LIVE_HERB_M,
                    mlw: float = LIVE_WOODY_M) -> dict:
    """rothermel() memoised on moisture rounded to 1e-4 (0.01 % moisture); do not mutate the result."""
    return rothermel(fuel, m1, m10, m100, mlh, mlw)


def phi_wind(r: dict, u_ftmin: float) -> float:
    if u_ftmin <= 0 or r["C"] == 0:
        return 0.0
    return r["C"] * u_ftmin ** r["B"] * r["beta_ratio"] ** -r["E"]


def eff_wind_ftmin(r: dict, phi: np.ndarray) -> np.ndarray:
    """Invert phi_w: the mid-flame wind that alone would give phi (Rothermel 1972 effective wind)."""
    if r["C"] == 0:
        return np.zeros_like(phi)
    return (np.maximum(phi, 0) * r["beta_ratio"] ** r["E"] / r["C"]) ** (1 / r["B"])


def phi_slope_coef(r: dict) -> float:
    return 5.275 * r["beta"] ** -0.3 if r["beta"] > 0 else 0.0


def length_to_breadth(u_mph):
    u = np.asarray(u_mph, float)
    lb = 0.936 * np.exp(0.2566 * u) + 0.461 * np.exp(-0.1548 * u) - 0.397
    return np.clip(lb, 1.0, 8.0)


def emc_simard(temp_c, rh):
    """Equilibrium moisture content, % (Simard 1968)."""
    T = np.asarray(temp_c, float) * 9 / 5 + 32
    H = np.clip(np.asarray(rh, float), 0, 100)
    lo = 0.03229 + 0.281073 * H - 0.000578 * H * T
    mid = 2.22749 + 0.160107 * H - 0.01478 * T
    hi = 21.0606 + 0.005565 * H ** 2 - 0.00035 * H * T - 0.483199 * H
    return np.clip(np.where(H < 10, lo, np.where(H < 50, mid, hi)), 0.5, 35)


def fuel_moisture(wx: pd.DataFrame) -> pd.DataFrame:
    """Hourly dead fuel moisture (%) for 1 h / 10 h / 100 h fuels; time-lag + rain wetting."""
    emc = emc_simard(wx["temp_c"].to_numpy(), wx["rh"].to_numpy())
    rain = np.nan_to_num(wx["rain_mm"].to_numpy(float))
    init = float(np.mean(emc[:24])) if len(emc) else 10.0
    m = np.array([init, init + 1, init + 2])
    tau = np.array([1.0, 10.0, 100.0])
    p_i = np.array([0.5, 2.0, 10.0])
    out = np.zeros((len(emc), 3))
    for i in range(len(emc)):
        m = m + (emc[i] - m) * (1 - np.exp(-1 / tau))
        if rain[i] > 0:
            m = m + (35 - m) * (1 - np.exp(-rain[i] / p_i))
        out[i] = m
    return pd.DataFrame({"m1h": out[:, 0], "m10h": out[:, 1], "m100h": out[:, 2], "emc": emc}, index=wx.index)


# ------------------------------------------------------------------ external data (cached)
def _cache_get(key: str, ttl: float | None):
    f = CACHE_DIR / f"meteo_{hashlib.sha1(key.encode()).hexdigest()[:20]}.json"
    if f.exists() and (ttl is None or time.time() - f.stat().st_mtime < ttl):
        try:
            return json.loads(f.read_text())
        except Exception:
            return None
    return None


def _cache_put(key: str, obj) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        f = CACHE_DIR / f"meteo_{hashlib.sha1(key.encode()).hexdigest()[:20]}.json"
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps(obj))
        tmp.replace(f)
    except Exception:
        pass


_LAST_429 = 0.0


def _get_json(url: str, params: dict, ttl: float | None, timeout: float = 20):
    key = url + "?" + "&".join(f"{k}={params[k]}" for k in sorted(params))
    hit = _cache_get(key, ttl)
    if hit is not None:
        return hit, True
    import requests
    global _LAST_429
    for attempt in range(2):
        r = requests.get(url, params=params, timeout=timeout, headers={"User-Agent": "equifire-physics/1.0"})
        if r.status_code != 429 or attempt or time.time() - _LAST_429 < 60:
            break
        _LAST_429 = time.time()   # Open-Meteo free tier: per-minute limit, multi-point requests count per point
        time.sleep(2.0)
    if r.status_code == 429:
        _LAST_429 = time.time()
    r.raise_for_status()
    js = r.json()
    if isinstance(js, dict) and js.get("error"):
        raise RuntimeError(js.get("reason", "open-meteo error"))
    _cache_put(key, js)
    return js, False


def _hourly_frame(js: dict) -> pd.DataFrame:
    h = js["hourly"]
    df = pd.DataFrame({"time": pd.to_datetime(h["time"])})
    for src, dst in zip(HOURLY, WCOLS):
        df[dst] = pd.to_numeric(pd.Series(h.get(src, [None] * len(df))), errors="coerce")
    return df


def _interpolate_weather(frame):
    """Fill weather gaps without turning a 350 -> 10 degree wind shift into a southerly wind."""
    out = frame.interpolate(limit_direction="both")
    if "wind_dir" in frame:
        rad = np.radians(frame["wind_dir"])
        sx = np.sin(rad).interpolate(limit_direction="both")
        sy = np.cos(rad).interpolate(limit_direction="both")
        out["wind_dir"] = np.degrees(np.arctan2(sx, sy)) % 360
    return out


def fetch_weather(lat: float, lon: float, start: pd.Timestamp, hours: int, now: pd.Timestamp | None = None,
                  getter=None) -> tuple[pd.DataFrame, dict]:
    """Hourly weather from start - SPINUP_H to start + hours (UTC, naive timestamps).

    Returns (frame, info). Archive API for anything older than 5 days, forecast API otherwise.
    On failure: a constant fallback weather with info["fallback"] = True."""
    getter = getter or _get_json
    now = now or pd.Timestamp.now("UTC").tz_localize(None)
    t0 = start - pd.Timedelta(hours=SPINUP_H)
    t1 = start + pd.Timedelta(hours=hours)
    info = {"lat": round(lat, 4), "lon": round(lon, 4), "sources": [], "errors": [], "fallback": False,
            "note": "single-point hourly weather at the ignition centroid; 2 m T/RH, 10 m wind"}
    cut = (now - pd.Timedelta(days=5)).floor("D")
    pieces = []
    spans = []
    if t0 < cut:
        spans.append(("archive", t0, min(t1, cut - pd.Timedelta(hours=1))))
    if t1 >= cut:
        spans.append(("forecast", max(t0, cut), t1))
    for kind, a, b in spans:
        url = ARCHIVE_URL if kind == "archive" else FORECAST_URL
        params = {"latitude": round(lat, 3), "longitude": round(lon, 3), "hourly": ",".join(HOURLY),
                  "start_date": a.date().isoformat(), "end_date": b.date().isoformat(), "timezone": "GMT",
                  "wind_speed_unit": "kmh"}
        try:
            js, cached = getter(url, params, None if kind == "archive" else FORECAST_TTL_S)
            df = _hourly_frame(js)
            pieces.append(df[(df["time"] >= a) & (df["time"] <= b)])
            info["sources"].append({"api": f"open-meteo {kind}", "url": url, "from": a.isoformat(),
                                    "to": b.isoformat(), "cached": cached,
                                    "model": "ERA5 reanalysis (via Open-Meteo)" if kind == "archive"
                                    else "Open-Meteo best-match NWP forecast"})
        except Exception as e:
            info["errors"].append(f"{kind}: {type(e).__name__}: {str(e)[:160]}")
    idx = pd.date_range(t0, t1, freq="h")
    if pieces:
        wx = pd.concat(pieces).drop_duplicates("time").set_index("time").reindex(idx)
        missing = int(wx["temp_c"].isna().sum())
        wx = _interpolate_weather(wx)
        info["interpolated_hours"] = missing
        if missing:
            info["errors"].append(f"{missing} missing hours interpolated/extended")
    else:
        wx = None
    if wx is None or wx[["temp_c", "rh", "wind_kmh", "wind_dir"]].isna().any().any():
        # Preserve dependency injection in pure tests. Real requests can use public NOAA forecasts.
        if ((getter is _get_json and _get_json.__module__ == __name__)
                or getattr(getter, "__module__", None) == "equifire.physcells") and t0 >= now - pd.Timedelta(days=7):
            try:
                from equifire.gfs_weather import get_weather_grid
                wg = get_weather_grid(lat, lon, 0, t0, len(idx), n=1, as_of=now)
                wx = wg_centre_frame(wg, lat, lon)
                info["sources"].append(wg.source_meta)
                info["provider"] = "noaa_gfs"
                return wx, info
            except Exception as e:
                info["errors"].append(f"NOAA GFS: {type(e).__name__}: {str(e)[:160]}")
        info["fallback"] = True
        info["errors"].append("weather unavailable -> constant fallback scenario (25 C, RH 30 %, "
                              "wind 15 km/h from 270 deg, no rain); results are illustrative only")
        wx = pd.DataFrame({"temp_c": 25.0, "rh": 30.0, "wind_kmh": 15.0, "wind_dir": 270.0, "gust_kmh": 25.0,
                           "rain_mm": 0.0}, index=idx)
    wx["rain_mm"] = wx["rain_mm"].fillna(0).clip(lower=0)
    wx.index.name = "time"
    return wx, info


def fetch_elevation(lats: np.ndarray, lons: np.ndarray, getter=None) -> tuple[np.ndarray | None, dict]:
    getter = getter or _get_json
    lats, lons = np.asarray(lats).ravel(), np.asarray(lons).ravel()
    out = np.full(len(lats), np.nan)
    info = {"api": "open-meteo elevation (Copernicus DEM GLO-90)", "url": ELEVATION_URL, "points": int(len(lats)),
            "errors": []}
    try:
        for i in range(0, len(lats), 100):
            p = {"latitude": ",".join(f"{v:.4f}" for v in lats[i:i + 100]),
                 "longitude": ",".join(f"{v:.4f}" for v in lons[i:i + 100])}
            js, _ = getter(ELEVATION_URL, p, None)
            out[i:i + 100] = np.asarray(js["elevation"], float)
    except Exception as e:
        info["errors"].append(f"{type(e).__name__}: {str(e)[:160]} -> flat terrain assumed")
        return None, info
    return out, info


# ------------------------------------------------------------------ hourly fire behaviour (scalars)
def hourly_behaviour(fuel: str, wx: pd.DataFrame, fm: pd.DataFrame, wind_mult: float = 1.0,
                     mlh: float = LIVE_HERB_M, mlw: float = LIVE_WOODY_M) -> dict:
    """Per-hour arrays: no-slope surface/crown quantities used by the raster and the frames.

    mlh / mlw: live herbaceous / woody moisture (fractions; default 60 % / 100 %)."""
    n = len(wx)
    w_fact = waf(fuel)
    res = {k: np.zeros(n) for k in ("R0", "phiw", "spread_dir", "IR", "ros_flat", "lb", "intensity_kw_m",
                                    "crown")}
    res["rk"] = []
    res["fire_type"] = []
    u10 = wx["wind_kmh"].to_numpy(float) * wind_mult
    wdir = wx["wind_dir"].to_numpy(float)
    i0 = (0.010 * CBH_M * (460 + 25.9 * FMC)) ** 1.5
    M = np.round(fm[["m1h", "m10h", "m100h"]].to_numpy(float) / 100, 4)
    for i in range(n):
        r = _rothermel_memo(fuel, float(M[i, 0]), float(M[i, 1]), float(M[i, 2]), float(mlh), float(mlw))
        u20 = u10[i] / 1.15 / 3.6 * 196.85  # km/h -> ft/min at 20 ft
        pw = phi_wind(r, u20 * w_fact)
        R = r["R0"] * (1 + pw)
        ib = r["IR"] * r["tr_min"] * R / 60 * 3.4613   # Byram kW/m
        ftype = "surface" if R > 0 else "none"
        if fuel in CROWN_CAPABLE and R > 0 and ib > i0:
            Rc = 3.34 * r["R0"] * (1 + phi_wind(r, u20 * 0.4))
            if Rc * FT_MIN_TO_M_MIN >= 3 / CBD:
                ftype = "active crown"
                r = dict(r)
                r["R0"] = Rc / (1 + pw) if (1 + pw) else Rc   # keep phi_w/phi_s machinery, scale R0
                R = Rc
            else:
                ftype = "passive crown (torching)"
        res["rk"].append(r)
        res["fire_type"].append(ftype)
        res["R0"][i] = r["R0"] * FT_MIN_TO_M_MIN
        res["phiw"][i] = pw
        res["spread_dir"][i] = (wdir[i] + 180) % 360
        res["IR"][i] = r["IR"]
        res["ros_flat"][i] = R * FT_MIN_TO_M_MIN
        res["lb"][i] = length_to_breadth(u10[i] / 1.15 / 1.609 * w_fact) if R > 0 else 1.0
        res["intensity_kw_m"][i] = ib
        res["crown"][i] = ftype == "active crown"
    return res


# ------------------------------------------------------------------ raster
class Grid:
    def __init__(self, clat: float, clon: float, half_m: float, cell_m: float):
        self.clat, self.clon, self.cell = clat, clon, cell_m
        self.n = int(2 * math.ceil(half_m / cell_m)) + 1
        self.nx = self.ny = self.n
        self.kx = M_TO_DEG / max(math.cos(math.radians(clat)), 0.05)
        self.ky = M_TO_DEG
        self.x0 = -(self.n // 2) * cell_m   # metres of cell (0,0) centre

    def ij(self, lat, lon):
        x = (np.asarray(lon) - self.clon) / self.kx
        y = (np.asarray(lat) - self.clat) / self.ky
        return (np.round((y - self.x0) / self.cell).astype(int), np.round((x - self.x0) / self.cell).astype(int))

    def corner_lonlat(self, cx, cy):
        """Cell-corner grid coords (cx, cy in [0, n]) -> lon, lat."""
        x = self.x0 + (np.asarray(cx, float) - 0.5) * self.cell
        y = self.x0 + (np.asarray(cy, float) - 0.5) * self.cell
        return self.clon + x * self.kx, self.clat + y * self.ky

    def centre_lonlat(self):
        c = self.x0 + np.arange(self.n) * self.cell
        return self.clon + c * self.kx, self.clat + c * self.ky


def terrain(grid: Grid, getter=None, elev_fn=None) -> tuple[np.ndarray, np.ndarray, dict]:
    """tan(slope) and upslope bearing (deg) per cell from a 15x15 elevation sample."""
    k = 15
    lons_c, lats_c = grid.centre_lonlat()
    glon = np.linspace(lons_c[0], lons_c[-1], k)
    glat = np.linspace(lats_c[0], lats_c[-1], k)
    LA, LO = np.meshgrid(glat, glon, indexing="ij")
    if elev_fn is not None:
        z, info = elev_fn(LA, LO), {"api": "provided", "errors": []}
    else:
        z, info = fetch_elevation(LA, LO, getter=getter)
    flat = np.zeros((grid.ny, grid.nx))
    if z is None or not np.isfinite(z).all():
        info["flat"] = True
        return flat, flat, info
    z = np.asarray(z, float).reshape(k, k)
    from scipy.ndimage import map_coordinates
    fi = np.linspace(0, k - 1, grid.n)
    YY, XX = np.meshgrid(fi, fi, indexing="ij")
    zz = map_coordinates(z, [YY, XX], order=1)
    dzdy, dzdx = np.gradient(zz, grid.cell)
    tan = np.hypot(dzdx, dzdy)
    up = (np.degrees(np.arctan2(dzdx, dzdy)) + 360) % 360
    info.update(flat=False, elev_min_m=round(float(z.min())), elev_max_m=round(float(z.max())),
                sample_spacing_m=round(float(grid.n * grid.cell / (k - 1))),
                max_slope_deg=round(float(np.degrees(np.arctan(tan.max()))), 1))
    return tan, up, info


_TH = np.array([math.atan2(dx, dy) for dx, dy in NEIGH])
_COS_TH, _SIN_TH = np.cos(_TH)[:, None], np.sin(_TH)[:, None]


def _directional_rates(fuel_r: dict, hb: dict, h: int, tan: np.ndarray, up: np.ndarray, w_fact: float,
                       pre: dict | None = None):
    """(16, ncell) spread rate in m/h along each neighbour direction for hour h (and head ROS per cell).

    With flat terrain the rates are the same in every cell: returns shape (16, 1) / (1,) (callers broadcast)."""
    if not _active(hb, h):
        return None
    if pre is None:
        pre = _terrain_pre(tan, up)
    if hb.get("groups") is not None:
        return _group_rates(hb, h, pre)
    r = fuel_r
    ps = phi_slope_coef(r) * pre["tan2"]
    g = hb.get("grid")
    if g is not None:   # gridded weather: per-cell no-slope ROS and wind-phi vector (bilinear from the points)
        R0 = (g["R0"][h][g["idx"]] * g["w"]).sum(1)
        X = (g["px"][h][g["idx"]] * g["w"]).sum(1) + ps * pre["sin_up"]
        Y = (g["py"][h][g["idx"]] * g["w"]).sum(1) + ps * pre["cos_up"]
    else:
        R0 = hb["R0"][h]   # m/min, no wind no slope
        pw = hb["phiw"][h]
        sd = math.radians(hb["spread_dir"][h])
        X = pw * math.sin(sd) + ps * pre["sin_up"]
        Y = pw * math.cos(sd) + ps * pre["cos_up"]
    pe = np.hypot(X, Y)
    head = R0 * (1 + pe) * 60.0
    ue_mph = eff_wind_ftmin(r, pe) / 88.0
    lb = length_to_breadth(ue_mph)
    e = np.sqrt(np.maximum(lb ** 2 - 1, 0)) / lb
    with np.errstate(invalid="ignore", divide="ignore"):
        ch = np.where(pe > 0, Y / pe, 1.0)
        sh = np.where(pe > 0, X / pe, 0.0)
    cosd = _COS_TH * ch[None, :] + _SIN_TH * sh[None, :]      # cos(theta_k - head_dir)
    rate = head[None, :] * (1 - e[None, :]) / (1 - e[None, :] * cosd)
    return rate.astype(np.float32), head


def _active(hb: dict, h: int) -> bool:
    if hb.get("groups") is not None:
        return bool(hb["active_any"][h])
    g = hb.get("grid")
    return bool(g["active"][h]) if g is not None else bool(hb["R0"][h] > 0)


def _ellipse_rates(r: dict, R0, X, Y):
    pe = np.hypot(X, Y)
    head = R0 * (1 + pe) * 60.0
    lb = length_to_breadth(eff_wind_ftmin(r, pe) / 88.0)
    e = np.sqrt(np.maximum(lb ** 2 - 1, 0)) / lb
    with np.errstate(invalid="ignore", divide="ignore"):
        ch = np.where(pe > 0, Y / pe, 1.0)
        sh = np.where(pe > 0, X / pe, 0.0)
    cosd = _COS_TH * ch[None, :] + _SIN_TH * sh[None, :]
    return head[None, :] * (1 - e[None, :]) / (1 - e[None, :] * cosd), head


def _group_rates(hb: dict, h: int, pre: dict):
    """Landscape mode: per-cell fuel model / live-moisture groups, each with point or gridded weather."""
    N = hb["N"]
    rate = np.zeros((16, N), np.float32)
    head = np.zeros(N)
    gw = hb.get("_gw")
    for g in hb["groups"]:
        if not g["active"][h]:
            continue
        c = g["cells"]
        r = g["rk"][h]
        t2 = pre["tan2"] if pre["uniform"] else pre["tan2"][c]
        su = pre["sin_up"] if pre["uniform"] else pre["sin_up"][c]
        cu = pre["cos_up"] if pre["uniform"] else pre["cos_up"][c]
        ps = phi_slope_coef(r) * t2
        if g.get("grid") is not None:
            G = g["grid"]
            idx, w = gw["idx"][c], gw["w"][c]
            R0 = (G["R0"][h][idx] * w).sum(1)
            X = (G["px"][h][idx] * w).sum(1) + ps * su
            Y = (G["py"][h][idx] * w).sum(1) + ps * cu
        else:
            R0 = np.full(len(c), g["R0"][h])
            sd = math.radians(g["spread_dir"][h])
            X = g["phiw"][h] * math.sin(sd) + ps * su
            Y = g["phiw"][h] * math.cos(sd) + ps * cu
            X, Y = np.broadcast_to(X, R0.shape), np.broadcast_to(Y, R0.shape)
        rr, hd = _ellipse_rates(r, R0, X, Y)
        rate[:, c] = rr
        head[c] = hd
    return rate, head


def _terrain_pre(tan: np.ndarray, up: np.ndarray) -> dict:
    t = np.asarray(tan, float).ravel()
    if not t.any():
        return {"tan2": np.zeros(1), "sin_up": np.zeros(1), "cos_up": np.zeros(1), "uniform": True}
    upr = np.radians(np.asarray(up, float).ravel())
    return {"tan2": t ** 2, "sin_up": np.sin(upr), "cos_up": np.cos(upr), "uniform": False}


def mtt(grid: Grid, ign: list[int], hb: dict, tan, up, fuel: str, H: int) -> tuple[np.ndarray, list]:
    """Time-dependent minimum travel time; returns arrival hours (inf = not burned) and per-hour head ROS."""
    T, heads, _ = mtt_labelled(grid, ign, hb, tan, up, fuel, H)
    return T, heads


def _edge_entries(entry, n):
    """Exact minimum entry weight along every 16-neighbour edge, computed once.

    np.rint keeps Python round's ties-to-even rule, including the source-cell
    parity at half-cell crossings. Invalid edge destinations are never consumed.
    """
    a = np.asarray(entry, dtype=float).reshape(n, n)
    y, x = np.indices((n, n))
    out = np.ones((len(NEIGH), n * n), dtype=float)
    for k, (dx, dy) in enumerate(NEIGH):
        weight = np.full((n, n), np.inf)
        for q in range(1, 9):
            yy = np.rint(y + dy * q / 8).astype(np.intp).clip(0, n - 1)
            xx = np.rint(x + dx * q / 8).astype(np.intp).clip(0, n - 1)
            np.minimum(weight, a[yy, xx], out=weight)
        out[k] = weight.ravel()
    return out


def mtt_labelled(grid: Grid, ign: list[int], hb: dict, tan, up, fuel: str, H: int,
                 ign_labels: list[int] | None = None) -> tuple[np.ndarray, dict, np.ndarray]:
    """As mtt, plus src (n x n int): label of the ignition cluster whose front arrived first (-1 = unburned).

    ign_labels[i] is the fire label of ign[i] (default: all 0)."""
    n, N = grid.n, grid.n * grid.n
    if hb.get("grid") is not None:
        hb = dict(hb)
        hb["grid"] = {**hb["grid"], **_grid_weights(hb["grid"], grid)}
    if hb.get("groups") is not None and any(g.get("grid") is not None for g in hb["groups"]):
        hb = dict(hb)
        hb["_gw"] = _grid_weights(next(g["grid"] for g in hb["groups"] if g.get("grid") is not None), grid)
    em = hb.get("entry")
    em = None if em is None else _edge_entries(em, n)
    src = [-1] * N
    labs = list(ign_labels) if ign_labels is not None else [0] * len(ign)
    w_fact = waf(fuel)
    rates: dict[int, np.ndarray | None] = {}
    heads: dict[int, np.ndarray] = {}
    pre = _terrain_pre(tan, up)
    uni = pre["uniform"] and hb.get("grid") is None and hb.get("groups") is None

    def rate_h(h):
        if h not in rates:
            out = _directional_rates(hb["rk"][h], hb, h, tan, up, w_fact, pre)
            rates[h] = None if out is None else (out[0][:, 0].tolist() if uni else out[0])
            if out is not None:
                heads[h] = np.broadcast_to(out[1], (N,)) if uni else out[1]
        return rates[h]

    active = [_active(hb, h) for h in range(H)]
    nxt = [H] * (H + 1)
    for h in range(H - 1, -1, -1):
        nxt[h] = h if active[h] else nxt[h + 1]
    INF = math.inf
    T = [INF] * N
    heap = []
    for u, lb in zip(ign, labs):
        T[u] = 0.0
        src[u] = lb
        heap.append((0.0, u))
    heapq.heapify(heap)
    nb = [(dx, dy, grid.cell * math.hypot(dx, dy), k) for k, (dx, dy) in enumerate(NEIGH)]
    done = bytearray(N)
    while heap:
        t, u = heapq.heappop(heap)
        if done[u]:
            continue
        done[u] = 1
        y, x = divmod(u, n)
        for dx, dy, dist, k in nb:
            xx, yy = x + dx, y + dy
            if xx < 0 or yy < 0 or xx >= n or yy >= n:
                continue
            v = yy * n + xx
            if done[v] or T[v] <= t:
                continue
            tt, d = t, dist
            if em is not None:     # test the whole edge: long neighbours must not jump a river/firebreak
                entry = float(em[k, u])
                if entry <= 0:
                    continue
                d = dist / entry
            while True:
                h = int(tt)
                if h >= H:
                    tt = INF
                    break
                if not active[h]:
                    h = nxt[h]
                    if h >= H:
                        tt = INF
                        break
                    tt = float(h)
                rr = rate_h(h)[k] if uni else float(rate_h(h)[k, u])
                if hb.get("groups") is not None:
                    rv = float(rate_h(h)[k, v])
                    rr = 2 * rr * rv / (rr + rv) if rr > 0 and rv > 0 else 0.0
                rem = h + 1 - tt
                if rr * rem >= d:
                    tt += d / rr if rr > 0 else 0
                    break
                d -= rr * rem
                tt = float(h + 1)
            if tt < T[v]:
                T[v] = tt
                src[v] = src[u]
                heapq.heappush(heap, (tt, v))
    return np.array(T).reshape(n, n), heads, np.array(src, np.int32).reshape(n, n)


# ------------------------------------------------------------------ polygons
def _rings(mask: np.ndarray) -> list[list[tuple[int, int]]]:
    """Outer boundary rings (CCW, grid-corner coordinates (cx, cy)) of a boolean raster."""
    from scipy.ndimage import binary_fill_holes
    m = binary_fill_holes(mask)
    if not m.any():
        return []
    p = np.pad(m, 1)
    c = p[1:-1, 1:-1]
    edges = {}
    ys, xs = np.nonzero(c & ~p[:-2, 1:-1])      # south neighbour empty -> bottom edge (x,y)->(x+1,y)
    for y, x in zip(ys, xs):
        edges.setdefault((x, y), []).append((x + 1, y))
    ys, xs = np.nonzero(c & ~p[1:-1, 2:])       # east empty -> right edge (x+1,y)->(x+1,y+1)
    for y, x in zip(ys, xs):
        edges.setdefault((x + 1, y), []).append((x + 1, y + 1))
    ys, xs = np.nonzero(c & ~p[2:, 1:-1])       # north empty -> top edge (x+1,y+1)->(x,y+1)
    for y, x in zip(ys, xs):
        edges.setdefault((x + 1, y + 1), []).append((x, y + 1))
    ys, xs = np.nonzero(c & ~p[1:-1, :-2])      # west empty -> left edge (x,y+1)->(x,y)
    for y, x in zip(ys, xs):
        edges.setdefault((x, y + 1), []).append((x, y))
    rings = []
    while edges:
        start = next(iter(edges))
        ring = [start]
        cur, prev_dir = start, None
        while True:
            outs = edges.get(cur)
            if not outs:
                break
            if len(outs) == 1 or prev_dir is None:
                nxt = outs[0]
            else:  # pinch vertex: prefer the left turn (keeps diagonal blobs apart)
                left = (-prev_dir[1], prev_dir[0])
                nxt = next((o for o in outs if (o[0] - cur[0], o[1] - cur[1]) == left), outs[0])
            outs.remove(nxt)
            if not outs:
                del edges[cur]
            prev_dir = (nxt[0] - cur[0], nxt[1] - cur[1])
            cur = nxt
            if cur == start:
                break
            ring.append(cur)
        if len(ring) >= 4:
            rings.append(ring)
    return rings


def _dp(pts: np.ndarray, tol: float) -> np.ndarray:
    """Douglas-Peucker on an open polyline (iterative)."""
    if len(pts) < 3:
        return pts
    keep = np.zeros(len(pts), bool)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        a, b = stack.pop()
        if b <= a + 1:
            continue
        seg = pts[b] - pts[a]
        L = math.hypot(*seg)
        rel = pts[a + 1:b] - pts[a]
        d = np.abs(rel[:, 0] * seg[1] - rel[:, 1] * seg[0]) / L if L > 0 else np.hypot(rel[:, 0], rel[:, 1])
        i = int(np.argmax(d))
        if d[i] > tol:
            keep[a + 1 + i] = True
            stack += [(a, a + 1 + i), (a + 1 + i, b)]
    return pts[keep]


def mask_to_geojson(mask: np.ndarray, grid: Grid, tol_cells: float = 0.6) -> dict | None:
    polys = []
    for ring in _rings(mask):
        pts = np.asarray(ring, float)
        # split ring at its farthest point so DP works on two open halves
        j = int(np.argmax(np.hypot(*(pts - pts[0]).T)))
        a = _dp(pts[:j + 1], tol_cells)
        b = _dp(np.vstack([pts[j:], pts[:1]]), tol_cells)
        s = np.vstack([a, b[1:]])
        if len(s) < 4:
            s = np.vstack([pts, pts[:1]])
        lon, lat = grid.corner_lonlat(s[:, 0], s[:, 1])
        polys.append([[[round(float(x), 5), round(float(y), 5)] for x, y in zip(lon, lat)]])
    if not polys:
        return None
    return {"type": "MultiPolygon", "coordinates": polys}


# ------------------------------------------------------------------ ignition helpers
def _km(lat1, lon1, lat2, lon2):
    k = 111.32
    return np.hypot((np.asarray(lat2) - lat1) * k, (np.asarray(lon2) - lon1) * k * math.cos(math.radians(lat1)))


RADIUS_KM_DEFAULT, RADIUS_KM_MAX = 30.0, 60.0


def ignition_live(lat: float, lon: float, df: pd.DataFrame | None, radius_km: float = RADIUS_KM_DEFAULT) -> dict | None:
    if df is None or df.empty:
        return None
    d = df[_km(lat, lon, df["lat"].to_numpy(), df["lon"].to_numpy()) <= radius_km]
    if d.empty:
        return None
    sats = sorted(set(map(str, d["sat"])))
    pix = 1000 if all("MODIS" in s.upper() for s in sats) else 375
    return {"mode": "live", "lats": d["lat"].to_numpy(float), "lons": d["lon"].to_numpy(float),
            "times": pd.to_datetime(d["t"]).to_numpy(), "estimate": True, "n_detections": int(len(d)),
            "satellites": sats, "radius_km": radius_km,
            "latest_utc": pd.Timestamp(d["t"].max()).isoformat() + "Z",
            "note": f"{len(d)} FIRMS NRT detections within {radius_km:g} km (last 24 h) all assumed burning at "
                    f"start; positions are pixel centres (VIIRS 375 m / MODIS 1 km), not the true fire edge; "
                    f"split into separate fires by a 1 km gap rule",
            "pixel_m": pix}


def _archive_cells(state, day: int):
    from equifire.common import cell_center
    sl = state.viirs_slice(day)
    if sl is None or len(sl) == 0:
        return None
    clat, clon = cell_center(sl["gx"].to_numpy(), sl["gy"].to_numpy())
    return np.asarray(clat, float), np.asarray(clon, float)


def ignition_archive(state, lat: float, lon: float, day: int, radius_km: float = RADIUS_KM_DEFAULT) -> dict | None:
    if state is None:
        return None
    try:
        got = _archive_cells(state, day)
    except Exception:
        return None
    if got is None:
        return None
    clat, clon = got
    sel = _km(lat, lon, clat, clon) <= radius_km
    if not sel.any():
        return None
    return {"mode": "archive", "lats": clat[sel], "lons": clon[sel], "estimate": True,
            "n_cells": int(sel.sum()), "radius_km": radius_km, "day": int(day),
            "note": "burning VIIRS 0.05 deg cells of that day (FireSpot archive); ignition placed at each cell "
                    "CENTRE - the fire can be anywhere within the ~5 km cell", "pixel_m": 0}


# ------------------------------------------------------------------ main entry
METHOD = {
    "rate_of_spread": "Rothermel (1972) surface spread with Albini (1976) corrections; Anderson (1982) fuel "
                      "models; R = I_R*xi*(1+phi_w+phi_s)/(rho_b*eps*Q_ig)",
    "crown_fire": "forest only: Van Wagner (1977) initiation (CBH 4 m, FMC 100 %) and active-crown criterion "
                  "(CBD 0.15 kg/m3); crown ROS = 3.34 x FM10 ROS with WAF 0.4 (Rothermel 1991)",
    "wind": "hour-specific 10 m wind / 1.15 -> 20 ft wind (Lawson & Armitage 2008) x wind adjustment factor "
            "(Albini & Baughman 1979 unsheltered; 0.15 under forest canopy, Andrews 2012)",
    "slope": "phi_s = 5.275*beta^-0.3*tan^2(slope) (Rothermel 1972), vector-summed with wind (Finney 1998); "
             "terrain from Open-Meteo elevation API (Copernicus DEM 90 m) sampled on a 15x15 grid",
    "fuel_moisture": "Simard (1968) EMC from T and RH; 1/10/100 h time-lag fuels; rain wetting heuristic "
                     "M += (35-M)(1-exp(-P/P_i)), P_i = 0.5/2/10 mm; live herb 60 %, live woody 100 % (assumed)",
    "shape": "ellipse, length-to-breadth from effective mid-flame wind (Anderson 1983), fire at rear focus",
    "growth": "time-dependent minimum-travel-time raster (cf. Finney 2002), exact Dijkstra, 16 neighbours",
    "envelope": "low = wind x0.8 & RH +10; high = wind x1.2 & RH -10 (same grid for small fires, else 2x coarser "
                "grid with ignition area rescaled; clipped so low <= central <= high); a sensitivity range, not "
                "a calibrated probability",
    "fires": "ignition cells split into fires A, B, C ... (1 km gap rule); per-fire area = cells reached first by "
             "that fire's front; merge = first contact of two fires' burned areas",
    "calibration": "one rate-of-spread multiplier m fitted to the fire's own observed growth over the previous "
                   "1-3 days (physics re-run over that window with its weather; VIIRS pixels, + GOES 2 km pixels "
                   "if goes=on in the Americas), Bayesian shrinkage toward 1 (prior sd ln 3), clipped 0.1..5; "
                   "applied to the forward run",
    "weather_grid": "weather=grid: 5x5 Open-Meteo points over +-40 km; moisture, ROS and wind vector per point, "
                    "bilinear to cells (equifire.fireweather)",
    "fwi": "Canadian FWI System for the start date (equifire.fireweather.fwi_for, cffdrs equations), context only",
    "landscape": "landscape=on: mapped WorldCover Anderson fuel groups, Copernicus terrain, heuristic OSM "
                 "firebreak slowdown and Sentinel-2 live-moisture bins where available; fallback explicitly reported",
    "noaa_fallback": "public NOAA GFS 0.25 degree regional GRIB, 3-hourly source interpolated hourly; vector "
                     "wind interpolation; PRATE approximates hourly rainfall; older cycles for moisture spin-up",
}
UNCERTAINTY = ("Experimental screening estimate. Fuel mapping and live-moisture conversions are assumptions; "
               "landscape coverage can be partial. Firebreaks use heuristic slowdown, not calibrated stop "
               "probabilities. No suppression, spotting or explicit solar fuel heating. Weather is much coarser "
               "than the simulation raster. Low/high are sensitivity scenarios, not probability bounds. "
               "Prior validation reports apply to the older physics pipeline; the landscape update has not "
               "established improved predictive accuracy.")


def _parse_start(start) -> pd.Timestamp:
    if start is None or start == "":
        return pd.Timestamp.now("UTC").tz_localize(None).floor("h")
    t = pd.Timestamp(start)
    if t.tzinfo is not None:
        t = t.tz_convert("UTC").tz_localize(None)
    return t.floor("h")


# ------------------------------------------------------------------ gridded weather (equifire.fireweather)
WG_VARS = {"temperature_2m": "temp_c", "relative_humidity_2m": "rh", "wind_speed_10m": "wind_kmh",
           "wind_direction_10m": "wind_dir", "wind_gusts_10m": "gust_kmh", "precipitation": "rain_mm"}
WG_HALF_KM, WG_N = 40.0, 5


def wg_point_frames(wg) -> list[pd.DataFrame]:
    """WeatherGrid (arrays (T, n_lat, n_lon)) -> one hourly frame (WCOLS) per grid point, lat-major order."""
    idx = pd.DatetimeIndex(pd.to_datetime(np.asarray(wg.times).astype("datetime64[s]")), name="time")
    nla, nlo = len(wg.lats), len(wg.lons)
    out = []
    for i in range(nla):
        for j in range(nlo):
            d = pd.DataFrame({dst: np.asarray(wg.arrays[src])[:, i, j].astype(float) if src in wg.arrays else np.nan
                              for src, dst in WG_VARS.items()}, index=idx)
            d = _interpolate_weather(d)
            d["rain_mm"] = d["rain_mm"].fillna(0).clip(lower=0)
            d["gust_kmh"] = d["gust_kmh"].fillna(d["wind_kmh"])
            out.append(d)
    return out


def wg_centre_frame(wg, lat: float, lon: float) -> pd.DataFrame:
    i = int(np.argmin(np.abs(np.asarray(wg.lats) - lat)))
    j = int(np.argmin(np.abs(np.asarray(wg.lons) - lon)))
    return wg_point_frames(wg)[i * len(wg.lons) + j]


def _grid_weights(g: dict, grid: Grid) -> dict:
    """Bilinear weights from the weather points to every raster cell (row-major u = i * n + j)."""
    lons_c, lats_c = grid.centre_lonlat()
    la, lo = np.asarray(g["lats"], float), np.asarray(g["lons"], float)

    def frac(arr, v):
        if len(arr) == 1:
            return np.zeros(len(v), int), np.zeros(len(v))
        f = np.clip(np.interp(v, arr, np.arange(len(arr))), 0, len(arr) - 1)
        i0 = np.minimum(np.floor(f).astype(int), len(arr) - 2)
        return i0, f - i0
    i0, wi = frac(la, lats_c)
    j0, wj = frac(lo, lons_c)
    nlo = len(lo)
    I0, J0 = np.meshgrid(i0, j0, indexing="ij")
    WI, WJ = np.meshgrid(wi, wj, indexing="ij")
    if len(la) == 1:
        idx = np.zeros((grid.n * grid.n, 4), int)
        w = np.tile([1.0, 0, 0, 0], (grid.n * grid.n, 1))
    else:
        i1, j1 = I0 + 1, J0 + 1
        idx = np.stack([I0 * nlo + J0, I0 * nlo + j1, i1 * nlo + J0, i1 * nlo + j1], -1).reshape(-1, 4)
        w = np.stack([(1 - WI) * (1 - WJ), (1 - WI) * WJ, WI * (1 - WJ), WI * WJ], -1).reshape(-1, 4)
    return {"idx": idx, "w": w.astype(np.float32)}


def _grid_behaviour(fuel: str, frames: list[pd.DataFrame], sim_mask, hours: int, wind_mult: float, drh: float,
                    ros_mult: float, lats, lons, mlh=LIVE_HERB_M, mlw=LIVE_WOODY_M) -> dict:
    """Per weather point hourly behaviour -> arrays (H, P) of no-slope ROS and wind-phi vector components."""
    R0, px, py, run = [], [], [], []
    for d in frames:
        if drh:
            d = d.copy()
            d["rh"] = (d["rh"] + drh).clip(1, 100)
        fm = fuel_moisture(d)[sim_mask].iloc[:hours]
        b = hourly_behaviour(fuel, d[sim_mask].iloc[:hours], fm, wind_mult, mlh, mlw)
        sd = np.radians(b["spread_dir"])
        R0.append(b["R0"] * ros_mult)
        px.append(b["phiw"] * np.sin(sd))
        py.append(b["phiw"] * np.cos(sd))
        run.append(float(np.sum(b["ros_flat"]) * ros_mult * 60))
    R0 = np.array(R0, np.float32).T
    return {"R0": R0, "px": np.array(px, np.float32).T, "py": np.array(py, np.float32).T,
            "active": R0.max(1) > 0, "lats": np.asarray(lats), "lons": np.asarray(lons), "max_run_m": max(run)}


# ------------------------------------------------------------------ landscape (equifire.landscape)
LAND_CELL_M = 250.0
BARRIER_BLOCK = 0.95       # combined barrier >= this -> the cell cannot be entered
NONBURNABLE_CODES = (91, 92, 98, 99)
# dryness bins (Sentinel-2 NDMI based, equifire.landscape): live moisture (herb, woody) as fractions
DRY_BINS = {0: ("moist canopy / green-up (dryness < 0.33 or green)", 1.20, 1.50),
            1: ("medium or unknown (default constants)", LIVE_HERB_M, LIVE_WOODY_M),
            2: ("dry canopy (dryness > 0.66)", 0.30, 0.70)}


class LandscapeProvider:
    """Fetches ONE landscape raster (get_landscape, 250 m, row 0 = south) around a fire, lazily, and resamples
    it onto any simulation Grid (calibration runs with different m have different grids).

    Per simulation cell (f x f sub-samples of the 250 m raster, f = round(cell / 250)):
      fuel_model = most common burnable Anderson model among sub-samples (1..13); nonburnable code (91/92/98/99)
                   when the mean burnable fraction < 0.5; 0 = no data -> latitude-rule / user fuel fallback
      barrier    = 1 - (1 - max barrier_lines) * (1 - max(mean barrier_area, 1 - mean burnable_frac))
      tan_slope / upslope = mean slope vector; dryness = nanmean; green / cropland = majority.
    Cells outside the raster get no-data (fallback fuel, no barrier, terrain from the regular source)."""

    def __init__(self, lat: float, lon: float, half_km: float, date=None, fetch_fn=None,
                 cell_m: float = LAND_CELL_M, osm_mode: str = "full"):
        self.lat, self.lon, self.half_km, self.cell_m = float(lat), float(lon), float(half_km), float(cell_m)
        self.date = date
        self.osm_mode = osm_mode
        self.fetch_fn = fetch_fn
        self.data = None
        self.error = None
        self.seconds = None

    def get(self):
        if self.data is None and self.error is None:
            t = time.time()
            try:
                fn = self.fetch_fn
                if fn is None:
                    from equifire.landscape import get_landscape as fn
                options = {"cell_m": self.cell_m,
                           "date": None if self.date is None else pd.Timestamp(self.date).to_pydatetime()}
                if self.fetch_fn is None:
                    options["osm_mode"] = self.osm_mode
                d = fn(self.lat, self.lon, self.half_km, **options)
                if "fuel_model" not in d or not (np.asarray(d["fuel_model"]) > 0).any():
                    raise ValueError("landscape has no fuel data: " + json.dumps(d.get("meta", {}).get("layers", {}),
                                                                                default=str)[:200])
                self.data = d
            except Exception as e:
                self.error = f"{type(e).__name__}: {str(e)[:200]}"
            self.seconds = round(time.time() - t, 2)
        return self.data

    def fuels_present(self, min_share: float = 0.05) -> list[str]:
        d = self.get()
        if d is None:
            return []
        fm = np.asarray(d["fuel_model"]).ravel()
        fm = fm[(fm >= 1) & (fm <= 13)]
        if not len(fm):
            return []
        u, c = np.unique(fm, return_counts=True)
        return [f"fm{int(k)}" for k, n in zip(u, c) if n / len(fm) >= min_share]

    def sample(self, grid: Grid) -> dict | None:
        d = self.get()
        if d is None:
            return None
        n0 = np.asarray(d["fuel_model"]).shape[0]
        c0 = self.cell_m
        x00 = -(n0 // 2) * c0
        kx0 = M_TO_DEG / max(math.cos(math.radians(self.lat)), 0.05)
        f = max(1, int(round(grid.cell / c0)))
        off = (np.arange(f) + 0.5) / f - 0.5
        lons_c, lats_c = grid.centre_lonlat()
        sub_lat = (lats_c[:, None] + off[None, :] * grid.cell * grid.ky).ravel()        # (n*f,)
        sub_lon = (lons_c[:, None] + off[None, :] * grid.cell * grid.kx).ravel()
        ii = np.round(((sub_lat - self.lat) / M_TO_DEG - x00) / c0).astype(int)
        jj = np.round(((sub_lon - self.lon) / kx0 - x00) / c0).astype(int)
        vi, vj = (ii >= 0) & (ii < n0), (jj >= 0) & (jj < n0)
        ic, jc = np.clip(ii, 0, n0 - 1), np.clip(jj, 0, n0 - 1)
        n = grid.n
        valid = (vi[:, None] & vj[None, :])                                                # (n f, n f)

        def take(name, fill=np.nan):
            a = d.get(name)
            if a is None:
                return None
            v = np.asarray(a, float)[np.ix_(ic, jc)]
            return np.where(valid, v, fill)

        def blocks(a):  # (n f, n f) -> (n, n, f*f)
            return a.reshape(n, f, n, f).transpose(0, 2, 1, 3).reshape(n, n, f * f)

        def nanmean(a):
            count = np.isfinite(a).sum(-1)
            return np.divide(np.nansum(a, -1), count, out=np.full(count.shape, np.nan), where=count > 0)
        fm = blocks(take("fuel_model", 0).astype(int))
        bfrac = blocks(take("burnable_frac", np.nan))
        burn = (fm >= 1) & (fm <= 13)
        fuel = np.zeros((n, n), int)
        for k in np.unique(fm[burn]):
            cnt = (fm == k).sum(-1)
            better = cnt > np.where(fuel > 0, (fm == fuel[..., None]).sum(-1), 0)
            fuel = np.where(better, k, fuel)
        bf = nanmean(np.where(fm > 0, bfrac, np.nan))
        nb_any = np.isin(fm, NONBURNABLE_CODES)
        nb_code = np.zeros((n, n), int)
        for k in NONBURNABLE_CODES:
            cnt = (fm == k).sum(-1)
            nb_code = np.where(cnt > np.where(nb_code > 0, (fm == nb_code[..., None]).sum(-1), 0), k, nb_code)
        nonburn = (np.nan_to_num(bf, nan=1.0) < 0.5) | ((fuel == 0) & nb_any.any(-1))
        fuel = np.where(nonburn, nb_code, fuel)
        out = {"fuel_model": fuel, "nonburnable": nonburn, "coverage_pct": round(100 * float(valid.mean()), 1),
               "subsamples": f}
        bl, ba = take("barrier_lines", 0.0), take("barrier_area", 0.0)
        if bl is not None or ba is not None:
            bl = blocks(np.nan_to_num(bl if bl is not None else np.zeros_like(valid, float))).max(-1)
            ba = blocks(np.nan_to_num(ba if ba is not None else np.zeros_like(valid, float))).mean(-1)
            nbf = np.where(fuel > 0, 1 - np.nan_to_num(bf, nan=1.0), 0.0)
            out["barrier"] = 1 - (1 - bl) * (1 - np.maximum(ba, nbf))
        tsl, ups = take("tan_slope"), take("upslope_deg")
        if tsl is not None and ups is not None and np.isfinite(tsl[valid]).any():
            r = np.radians(np.nan_to_num(ups))
            t = np.nan_to_num(tsl)
            ex, ey = blocks(t * np.sin(r)).mean(-1), blocks(t * np.cos(r)).mean(-1)
            out["tan_slope"] = np.hypot(ex, ey)
            out["upslope_deg"] = (np.degrees(np.arctan2(ex, ey)) + 360) % 360
            out["terrain_valid"] = blocks((valid & np.isfinite(tsl) & np.isfinite(ups)).astype(float)).mean(-1) > 0.5
        dry = take("dryness")
        if dry is not None:
            out["dryness"] = nanmean(blocks(dry))
        for name in ("green", "cropland"):
            a = take(name, 0.0)
            if a is not None:
                out[name] = blocks(np.nan_to_num(a)).mean(-1) > 0.5
        return out

    def meta(self) -> dict:
        d = self.data or {}
        m = d.get("meta", {})
        return {"center": [round(self.lon, 5), round(self.lat, 5)], "half_km": self.half_km, "cell_m": self.cell_m,
                "fetch_s": self.seconds, "error": self.error,
                "layers": {k: {kk: v.get(kk) for kk in ("errors", "coverage_pct", "source", "sources", "date",
                                                        "scene_date", "items", "optional", "cache_only", "reason", "refresh_queued") if kk in v}
                           for k, v in m.get("layers", {}).items()} if m else {}}


def landscape_groups(L: dict | None, fallback: str, n: int, ign: list[int], user_fuel: bool) -> dict:
    """Cell groups (fuel key, dryness bin) -> cell indices; nonburnable cells get no group (never burn).

    Ignition cells always burn (VIIRS pixel centres can fall on a road / village / water cell next to the fire):
    a nonburnable ignition cell is given the fallback fuel."""
    N = n * n
    if L is None:
        return {"groups": {(fallback, 1): np.arange(N)}, "nonburnable": np.zeros(N, bool)}
    fm = L["fuel_model"].ravel()
    keys = np.where((fm >= 1) & (fm <= 13) & (not user_fuel), np.char.add("fm", fm.astype(str)), fallback)
    nb = L["nonburnable"].ravel().copy()
    nb[list(ign)] = False
    dry = L.get("dryness")
    b = np.ones(N, int)
    if dry is not None:
        dd = dry.ravel()
        b = np.where(dd < 0.33, 0, np.where(dd > 0.66, 2, 1))
        b[~np.isfinite(dd)] = 1
    if L.get("green") is not None:
        b = np.where(L["green"].ravel(), 0, b)
    groups = {}
    for k in np.unique(keys[~nb]):
        for bb in (0, 1, 2):
            sel = np.flatnonzero((keys == k) & (b == bb) & ~nb)
            if len(sel):
                groups[(str(k), bb)] = sel
    return {"groups": groups, "nonburnable": nb}


ENV_SAME_GRID_CELLS = 3000   # envelope runs on the central grid when the central fire reaches <= this many cells


def _fire_id(i: int) -> str:
    return chr(65 + i) if i < 26 else f"F{i + 1}"


def label_fires(grid: Grid, ign: list[int], gap_m: float = 1000.0) -> np.ndarray:
    """Connected components of ignition cells; two cells join when the empty gap between them is <= gap_m.

    Returns one label per ign cell; label 0 = largest cluster (fire A), 1 = next (B), ..."""
    if not ign:
        return np.zeros(0, int)
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    from scipy.spatial import cKDTree
    yx = np.array([divmod(u, grid.n) for u in ign], float)
    dmax = gap_m / grid.cell + 1.0 + 1e-6          # Chebyshev centre distance (cells) for a gap <= gap_m
    pairs = cKDTree(yx).query_pairs(dmax, p=np.inf, output_type="ndarray")
    m = len(ign)
    g = coo_matrix((np.ones(len(pairs)), (pairs[:, 0], pairs[:, 1])), shape=(m, m)) if len(pairs) else \
        coo_matrix((m, m))
    _, lab = connected_components(g, directed=False)
    sizes = np.bincount(lab)
    cx = np.array([yx[lab == k, 1].mean() for k in range(len(sizes))])
    order = sorted(range(len(sizes)), key=lambda k: (-sizes[k], cx[k]))
    remap = np.empty(len(sizes), int)
    remap[order] = np.arange(len(sizes))
    return remap[lab]


def merge_events(T: np.ndarray, src: np.ndarray, grid: Grid, t_max: float) -> list[dict]:
    """Times at which burned areas of differently-labelled fires first touch (8-neighbour contact).

    Contact time of two adjacent cells = max of their arrival times; a pair of fires merges at the earliest
    contact time over all adjacent cell pairs. Union-find: an event is reported only when two still-separate
    groups join (A+B, then (A,B)+C)."""
    cand = {}
    n = grid.n
    for dy, dx in ((0, 1), (1, 0), (1, 1), (1, -1)):
        ya, yb = (slice(0, n - dy), slice(dy, n))
        xa, xb = (slice(0, n - dx), slice(dx, n)) if dx >= 0 else (slice(-dx, n), slice(0, n + dx))
        a, b = src[ya, xa], src[yb, xb]
        tc = np.maximum(T[ya, xa], T[yb, xb])
        sel = (a >= 0) & (b >= 0) & (a != b) & (tc <= t_max + 1e-9)
        if not sel.any():
            continue
        iy, ix = np.nonzero(sel)
        for y, x, la, lb, t in zip(iy, ix, a[sel], b[sel], tc[sel]):
            key = (min(la, lb), max(la, lb))
            if key not in cand or t < cand[key][0]:
                y0 = y + (0 if dy >= 0 else 0)
                x0 = x + (-dx if dx < 0 else 0)
                cand[key] = (float(t), (y0 + dy / 2, x0 + dx / 2))
    parent = {}

    def find(i):
        while parent.get(i, i) != i:
            i = parent[i]
        return i
    out = []
    lons_c, lats_c = grid.centre_lonlat()
    for (la, lb), (t, (yy, xx)) in sorted(cand.items(), key=lambda kv: kv[1][0]):
        ra, rb = find(la), find(lb)
        if ra == rb:
            continue
        parent[max(ra, rb)] = min(ra, rb)
        lon = float(np.interp(xx, np.arange(n), lons_c))
        lat = float(np.interp(yy, np.arange(n), lats_c))
        out.append({"t_hours": round(t, 3), "fires": [_fire_id(int(la)), _fire_id(int(lb))],
                    "location": [round(lon, 5), round(lat, 5)], "_pair": (int(la), int(lb))})
    return out


def _groups_at(nf: int, merges: list[dict], t: float) -> list[list[str]]:
    parent = list(range(nf))

    def find(i):
        while parent[i] != i:
            i = parent[i]
        return i
    for m in merges:
        if m["t_hours"] <= t + 1e-9:
            a, b = (find(x) for x in m["_pair"])
            parent[max(a, b)] = min(a, b)
    g: dict[int, list[str]] = {}
    for i in range(nf):
        g.setdefault(find(i), []).append(_fire_id(i))
    return sorted(g.values(), key=lambda v: v[0])


def _env_run(name, hbs, grid, cell, half, clat, clon, tan, up, tinfo, ilats, ilons, ign, fuel, H, same):
    """One envelope variant: on the central grid (same=True) or on a 2x coarser grid."""
    if same:
        T2, _ = mtt(grid, ign, hbs[name][0], tan, up, fuel, H)
        return T2, grid
    g2 = Grid(clat, clon, half, cell * 2)
    if tinfo.get("flat"):
        tan2 = up2 = np.zeros((g2.n, g2.n))
    else:
        from scipy.ndimage import zoom
        tan2 = zoom(tan, g2.n / grid.n, order=1)[:g2.n, :g2.n]
        up2 = zoom(up, g2.n / grid.n, order=0)[:g2.n, :g2.n]
        if tan2.shape != (g2.n, g2.n):
            tan2 = np.resize(tan2, (g2.n, g2.n))
            up2 = np.resize(up2, (g2.n, g2.n))
    y2, x2 = g2.ij(ilats, ilons)
    ok2 = (y2 >= 0) & (x2 >= 0) & (y2 < g2.n) & (x2 < g2.n)
    T2, _ = mtt(g2, sorted(set((y2[ok2] * g2.n + x2[ok2]).tolist())), hbs[name][0], tan2, up2, fuel, H)
    return T2, g2


def simulate(lat: float, lon: float, start=None, hours: int = 72, step_min: int = 60, ignition: dict | None = None,
             fuel: str = "auto", wx: pd.DataFrame | None = None, wx_info: dict | None = None, getter=None,
             elev_fn=None, envelope: bool = True, max_cells: int = 161, min_cell_m: float = 250.0,
             frames: bool = True, keep_masks: bool = False, ros_mult: float = 1.0, gap_km: float = 1.0,
             calibration: dict | None = None, wx_grid=None, landscape=None) -> dict:
    """Run the simulator. `ignition` = dict(mode, lats, lons, estimate, note) or None for a point ignition.

    ros_mult multiplies every rate of spread (calibration factor m, 1 = pure physics). gap_km = gap tolerance
    used to split the ignition cells into separate fires A, B, C ... (tracked per frame, merges detected)."""
    t_start = time.time()
    start = _parse_start(start)
    hours = int(hours)
    ros_mult = float(ros_mult)
    if ignition is None:
        ignition = {"mode": "point", "lats": np.array([lat]), "lons": np.array([lon]), "estimate": True,
                    "note": "single-cell ignition at the requested point (ESTIMATE: true origin unknown)"}
    ilats, ilons = np.asarray(ignition["lats"], float), np.asarray(ignition["lons"], float)
    clat, clon = float(ilats.mean()), float(ilons.mean())
    user_fuel = fuel != "auto"
    fuel, fuel_why = choose_fuel(clat, fuel)
    wg_frames = wg_point_frames(wx_grid) if wx_grid is not None else None
    if wx is None and wx_grid is not None:
        wx = wg_centre_frame(wx_grid, clat, clon)
    if wx is None:
        wx, wx_info = fetch_weather(clat, clon, start, hours, getter=getter)
    wx_info = wx_info or {"sources": [{"api": "provided"}], "errors": [], "fallback": False}
    fm_all = fuel_moisture(wx)
    sim = wx.index >= start
    wx_s, fm_s = wx[sim].iloc[:hours], fm_all[sim].iloc[:hours]
    H = len(wx_s)
    expected = pd.date_range(start, periods=hours, freq="h")
    if H != hours or not wx_s.index.equals(expected):
        raise ValueError("hourly weather must cover every requested simulation hour in UTC")
    if not np.isfinite(wx_s[["temp_c", "rh", "wind_kmh", "wind_dir", "rain_mm"]].to_numpy()).all():
        raise ValueError("temperature, humidity, wind and precipitation are required for every simulation hour")
    variants = [("central", 1.0, 0.0)] + ([("low", 0.8, 10.0), ("high", 1.2, -10.0)] if envelope else [])
    if wx_grid is not None:
        LA, LO = np.meshgrid(np.asarray(wx_grid.lats, float), np.asarray(wx_grid.lons, float), indexing="ij")
        wx_grid_lat_lon = (np.asarray(wx_grid.lats, float), np.asarray(wx_grid.lons, float))
        wx_info = dict(wx_info or {}, gridded={"points": int(LA.size), "lat_range": [round(float(LA.min()), 4),
                                                                                    round(float(LA.max()), 4)],
                                               "lon_range": [round(float(LO.min()), 4), round(float(LO.max()), 4)],
                                               "note": "per-cell weather: rate of spread and wind vector computed "
                                                       "at each weather point, bilinear to the raster cells"})
    hbs = {}
    for name, wm, drh in variants:
        if drh:
            w2 = wx.copy()
            w2["rh"] = (w2["rh"] + drh).clip(1, 100)
            fm_v = fuel_moisture(w2)[sim].iloc[:hours]
        else:
            fm_v = fm_s
        hbv = hourly_behaviour(fuel, wx_s, fm_v, wm)
        if ros_mult != 1.0:   # calibration: scale every rate (and Byram intensity, I ~ R)
            for k in ("R0", "ros_flat", "intensity_kw_m"):
                hbv[k] = hbv[k] * ros_mult
        if wg_frames is not None:
            sim_p = wg_frames[0].index >= start
            hbv["grid"] = _grid_behaviour(fuel, wg_frames, sim_p, hours, wm, drh, ros_mult, wx_grid_lat_lon[0],
                                          wx_grid_lat_lon[1])
            if len(hbv["grid"]["R0"]) != H:
                raise ValueError("weather grid does not cover the simulation window")
        hbs[name] = (hbv, fm_v)
    # The same provider is shared by calibration and forward runs. Never refetch imagery at a later
    # date for calibration; otherwise moisture from after the calibration window leaks into its fit.
    behaviour_cache = {}

    def fuel_behaviour(name, key, drybin):
        cache_key = (name, key, drybin)
        if cache_key not in behaviour_cache:
            _, wm, drh = next(v for v in variants if v[0] == name)
            mlh, mlw = DRY_BINS[drybin][1:]
            b = hourly_behaviour(key, wx_s, hbs[name][1], wm, mlh, mlw)
            for field in ("R0", "ros_flat", "intensity_kw_m"):
                b[field] *= ros_mult
            if wg_frames is not None:
                b["grid"] = _grid_behaviour(key, wg_frames, sim_p, hours, wm, drh, ros_mult,
                                             *wx_grid_lat_lon, mlh, mlw)
            b["active"] = b["grid"]["active"] if "grid" in b else b["R0"] > 0
            behaviour_cache[cache_key] = b
        return behaviour_cache[cache_key]

    # domain: max possible run (high variant) + ignition extent, margin for slope
    hb_max = hbs["high" if envelope else "central"][0]
    run_m = float(np.sum(hb_max["ros_flat"]) * 60) * 1.3
    if wg_frames is not None:
        run_m = max(run_m, hb_max["grid"]["max_run_m"] * 1.3)
    if landscape is not None and landscape.get() is not None:
        # Include even small patches of faster fuel when sizing the domain.
        keys = [fuel] if user_fuel else landscape.fuels_present(min_share=0.0)
        for key in keys:
            b = fuel_behaviour("high" if envelope else "central", key, 2)
            run_m = max(run_m, float(b["ros_flat"].sum()) * 60 * 1.3,
                        b.get("grid", {}).get("max_run_m", 0) * 1.3)
    ext_m = float(max(np.ptp(ilats) / M_TO_DEG, np.ptp(ilons) / M_TO_DEG * math.cos(math.radians(clat)))) / 2
    half = min(max(run_m + ext_m + 1500, 3000), 80_000)
    cell = max(min_cell_m, 2 * half / (max_cells - 1))
    grid = Grid(clat, clon, half, cell)
    iy, ix = grid.ij(ilats, ilons)
    ok = (iy >= 0) & (ix >= 0) & (iy < grid.n) & (ix < grid.n)
    pcell = iy * grid.n + ix
    ign = sorted(set(pcell[ok].tolist()))
    land = landscape.sample(grid) if landscape is not None else None
    if land is not None and "tan_slope" in land and np.all(land["terrain_valid"]):
        # The mapped DEM replaces these values in every cell. Avoid a redundant
        # elevation API call whose result would be entirely discarded below.
        tan, up = land["tan_slope"].copy(), land["upslope_deg"].copy()
        tinfo = {"api": "landscape DEM", "errors": [], "landscape_dem": True,
                 "flat": not bool(np.any(tan))}
    else:
        tan, up, tinfo = terrain(grid, getter=getter, elev_fn=elev_fn)
    land_info = None
    if landscape is not None:
        land_info = {**landscape.meta(), "applied": land is not None}
    if land is not None:
        gg = landscape_groups(land, fuel, grid.n, ign, user_fuel)
        barrier = np.clip(np.nan_to_num(land.get("barrier", np.zeros((grid.n, grid.n)))), 0, 1).ravel()
        entry = np.where(gg["nonburnable"] | (barrier >= BARRIER_BLOCK), 0.0, 1 - barrier)
        entry[ign] = 1.0
        for name, _, _ in variants:
            hbv, fm_v = hbs[name]
            groups = [{**fuel_behaviour(name, key, bb), "cells": cells}
                      for (key, bb), cells in gg["groups"].items()]
            hbv = {**hbv, "groups": groups, "N": grid.n ** 2, "entry": entry,
                   "active_any": np.any([g["active"] for g in groups], axis=0) if groups else np.zeros(H, bool)}
            for field in ("R0", "ros_flat", "intensity_kw_m", "crown"):
                hbv[field] = np.max([g[field] for g in groups], axis=0) if groups else np.zeros(H)
            hbs[name] = (hbv, fm_v)
        if "tan_slope" in land:
            valid = land["terrain_valid"]
            tan = np.where(valid, land["tan_slope"], tan)
            up = np.where(valid, land["upslope_deg"], up)
            tinfo = {**tinfo, "landscape_dem": True, "flat": not bool(np.any(tan))}
        land_info.update(coverage_pct=land["coverage_pct"], nonburnable_cells=int(gg["nonburnable"].sum()),
                         blocked_cells=int((entry == 0).sum()), groups=[
                             {"fuel": key, "dryness_bin": bb, "cells": len(cells)}
                             for (key, bb), cells in gg["groups"].items()],
                         barrier_note="Heuristic spread slowdown, not a calibrated probability of stopping fire.",
                         dryness_note="Sentinel-2 moisture-index proxy mapped to assumed live-fuel moisture bins.")
    ign_lab = label_fires(grid, ign, gap_km * 1000)
    nf = int(ign_lab.max()) + 1 if len(ign_lab) else 0
    T, heads, src = mtt_labelled(grid, ign, hbs["central"][0], tan, up, fuel, H, ign_lab.tolist())
    hb, _ = hbs["central"]
    a_cell = cell * cell / 1e6
    # envelope: same grid as the central run when the fire is small (exact comparison), else 2x coarser grid
    # with the coarse ignition area replaced by the central one; finally clipped so low <= central <= high.
    same = land is not None or bool(np.isfinite(T).sum() <= ENV_SAME_GRID_CELLS)
    env = {name: _env_run(name, hbs, grid, cell, half, clat, clon, tan, up, tinfo, ilats, ilons, ign, fuel, H,
                          same) for name, _, _ in variants[1:]}
    env_init = {n: float((env[n][0] <= 0).sum() * env[n][1].cell ** 2 / 1e6) for n in env}
    init_km2 = len(ign) * a_cell
    n_clipped = 0

    def env_area(name, t, central):
        nonlocal n_clipped
        T2, g2 = env[name]
        a = float((T2 <= t + 1e-9).sum() * g2.cell ** 2 / 1e6)
        if not same:
            a = init_km2 + max(a - env_init[name], 0.0)
        c = min(a, central) if name == "low" else max(a, central)
        if abs(c - a) > 1e-9:
            n_clipped += 1
        return round(c, 3)
    edge = bool(np.isfinite(T[[0, -1], :]).any() or np.isfinite(T[:, [0, -1]]).any())
    steps = np.arange(0, H * 60 + 1, step_min) / 60.0
    if steps[-1] < H:
        steps = np.r_[steps, float(H)]
    final_t = float(steps[-1]) if len(steps) else 0.0
    merges = merge_events(T, src, grid, final_t) if nf > 1 else []
    for m in merges:
        m["time_utc"] = (start + pd.Timedelta(hours=m["t_hours"])).round("s").isoformat() + "Z"
    out_frames = []
    for k, t in enumerate(steps):
        burned = T <= t + 1e-9
        hi = min(int(t), H - 1) if H else 0
        # active front: burned cells with an unburned 8-neighbour, spreading this hour
        p = np.pad(burned, 1)
        nb_unb = ~(p[:-2, 1:-1] & p[2:, 1:-1] & p[1:-1, :-2] & p[1:-1, 2:] & p[:-2, :-2] & p[2:, 2:] & p[:-2, 2:]
                   & p[2:, :-2])
        front = burned & nb_unb
        if H and hi in heads and front.any():
            max_ros = float(heads[hi].reshape(grid.n, grid.n)[front].max()) / 60
        else:
            max_ros = float(hb["ros_flat"][hi]) if H and front.any() else 0.0
        b_km2 = round(float(burned.sum() * a_cell), 3)
        per = np.bincount(src[burned], minlength=nf) * a_cell if nf else np.zeros(0)
        fr = {"t_hours": round(float(t), 3), "time_utc": (start + pd.Timedelta(hours=float(t))).isoformat() + "Z",
              "burned_km2": b_km2,
              "new_km2": round(float(((T > steps[k - 1] + 1e-9) & burned).sum() * a_cell), 3) if k else b_km2,
              "max_ros_m_min": round(max_ros, 2),
              "fire_type": hb["fire_type"][hi] if H else "none",
              "max_intensity_kw_m": round(float(hb["intensity_kw_m"][hi]), 0) if H else 0,
              "envelope_km2": {n: env_area(n, t, b_km2) for n in env},
              "fires": [{"id": _fire_id(i), "burned_km2": round(float(per[i]), 3)} for i in range(nf)],
              "separate_fires": _groups_at(nf, merges, float(t)),
              "weather": _wx_row(wx_s, fm_s, hi) if H else None, "estimate": True}
        if keep_masks:  # internal (validation), not JSON
            fr["_mask"] = (burned, grid)
        if frames:
            fr["burned"] = mask_to_geojson(burned, grid)
            band = burned & (T > t - step_min / 60 + 1e-9) if k else burned
            fr["front"] = mask_to_geojson(band, grid) if band.any() else None
        out_frames.append(fr)
    burned_final = T <= final_t + 1e-9
    # per-fire ignition summary
    lab_of_cell = dict(zip(ign, ign_lab.tolist()))
    pt_lab = np.array([lab_of_cell.get(int(c), -1) if o else -1 for c, o in zip(pcell, ok)])
    lons_c, lats_c = grid.centre_lonlat()
    fires = []
    for i in range(nf):
        cells_i = [u for u, lb in zip(ign, ign_lab) if lb == i]
        yy, xx = np.array([divmod(u, grid.n) for u in cells_i]).T
        sel = pt_lab == i
        fires.append({"id": _fire_id(i), "ignition_cells": len(cells_i),
                      "initial_km2": round(len(cells_i) * a_cell, 3),
                      "n_points": int(sel.sum()),
                      "centroid": [round(float(ilons[sel].mean() if sel.any() else lons_c[xx].mean()), 5),
                                   round(float(ilats[sel].mean() if sel.any() else lats_c[yy].mean()), 5)],
                      "points": [[round(float(a), 5), round(float(b), 5)]
                                 for a, b in zip(ilons[sel][:50], ilats[sel][:50])],
                      "final_burned_km2": out_frames[-1]["fires"][i]["burned_km2"] if out_frames else 0.0,
                      "estimate": True})
    summary = {
        "burned_km2": round(float(burned_final.sum() * a_cell), 3),
        "initial_km2": round(float(init_km2), 3),
        "envelope_km2": {"low": out_frames[-1]["envelope_km2"].get("low"),
                         "central": out_frames[-1]["burned_km2"],
                         "high": out_frames[-1]["envelope_km2"].get("high")},
        "envelope_grid": None if not env else ("same as central" if same else "2x coarser (area rescaled)"),
        "envelope_clipped_values": n_clipped,
        "max_ros_m_min": round(max((f["max_ros_m_min"] for f in out_frames), default=0), 2),
        "spread_hours": int(sum(_active(hb, h) for h in range(H))),
        "stalled_hours": int(sum(not _active(hb, h) for h in range(H))),
        "crown_fire_hours": int(hb["crown"].sum()),
        "reached_domain_edge": edge,
        "max_run_km": round(float(_max_run(T, grid, iy[ok], ix[ok], final_t)), 2),
        "final_perimeter": mask_to_geojson(burned_final, grid),
        "envelope_perimeters": {n: mask_to_geojson(env[n][0] <= final_t + 1e-9, env[n][1]) for n in env},
        "n_fires": nf, "n_merges": len(merges),
    }
    for m in merges:
        m.pop("_pair", None)
    ros_note = ("pure physics (m = 1)" if ros_mult == 1.0 else
                f"all rates of spread multiplied by m = {ros_mult:.3g} (calibration on recent observed growth)")
    return {
        "query": {"lat": lat, "lon": lon, "start": start.isoformat() + "Z", "hours": hours, "step_min": step_min,
                  "simulated_hours": H},
        "ignition": {"mode": ignition["mode"], "estimate": bool(ignition.get("estimate", True)),
                     "note": ignition.get("note", ""), "requested_mode": ignition.get("requested_mode",
                                                                                      ignition["mode"]),
                     "fallback_reason": ignition.get("fallback_reason"),
                     "cells": len(ign), "points": [[round(float(a), 5), round(float(b), 5)]
                                                   for a, b in zip(ilons[:200], ilats[:200])],
                     **{k: ignition[k] for k in ("n_detections", "satellites", "latest_utc", "n_cells", "radius_km")
                        if k in ignition}},
        "fires": fires,
        "merges": merges,
        "fuel": {"class": fuel, "model": FUEL_MODELS[fuel]["fm"],
                 "why": ("Mapped Anderson fuel models used per cell; the listed class is the fallback for "
                         "unmapped cells. Land-cover to fuel and moisture conversions remain assumptions."
                         if land is not None else fuel_why),
                 "assumption": True, "mapped_fuels_used": land is not None,
                 "wind_adjustment_factor": round(waf(fuel), 3)},
        "grid": {"cell_m": round(cell, 1), "size": grid.n, "half_width_km": round(half / 1000, 2),
                 "center": [round(clon, 6), round(clat, 6)],
                 "geometry": "square n x n raster centred on the ignition centroid (lon, lat); cell (row i, col j) "
                             "centre = centre + ((j - n//2) * cell_m east, (i - n//2) * cell_m north), local "
                             "equirectangular metres (1 deg lat = 111.32 km, lon scaled by cos(lat))",
                 "terrain": tinfo},
        "ros_multiplier": round(ros_mult, 4),
        "ros_note": ros_note,
        "calibration": calibration,
        "weather": wx_info,
        "weather_timeline": [{"valid_from_utc": t.isoformat() + "Z",
                              "valid_to_utc": (t + pd.Timedelta(hours=1)).isoformat() + "Z",
                              **_wx_row(wx_s, fm_s, i)} for i, t in enumerate(wx_s.index)],
        "weather_time_step_minutes": 60,
        "landscape": land_info,
        "frames": out_frames,
        "summary": summary,
        "method": METHOD,
        "uncertainty": UNCERTAINTY,
        "estimate": True,
        "runtime_s": round(time.time() - t_start, 3),
    }


def _max_run(T, grid, iy, ix, t) -> float:
    b = np.argwhere(T <= t + 1e-9)
    if not len(b) or not len(iy):
        return 0.0
    d = np.hypot(b[:, None, 0] - iy[None, :], b[:, None, 1] - ix[None, :]).min(1)
    return float(d.max() * grid.cell / 1000)


def _wx_row(wx: pd.DataFrame, fm: pd.DataFrame, i: int) -> dict:
    r = wx.iloc[i]
    return {"wind_kmh": round(float(r["wind_kmh"]), 1), "wind_dir_deg": round(float(r["wind_dir"])),
            "gust_kmh": round(float(r["gust_kmh"]), 1) if pd.notna(r["gust_kmh"]) else None,
            "temp_c": round(float(r["temp_c"]), 1), "rh": round(float(r["rh"])),
            "rain_mm": round(float(r["rain_mm"]), 2), "fuel_moisture_1h_pct": round(float(fm["m1h"].iat[i]), 1)}


def weather_table(lat: float, lon: float, start=None, hours: int = 72, fuel: str = "auto", getter=None) -> dict:
    start = _parse_start(start)
    wx, info = fetch_weather(lat, lon, start, int(hours), getter=getter)
    fm = fuel_moisture(wx)
    fuel, why = choose_fuel(lat, fuel)
    sel = wx.index >= start
    hb = hourly_behaviour(fuel, wx[sel], fm[sel])
    rows = []
    for i, (t, r) in enumerate(wx[sel].iterrows()):
        f = fm[sel].iloc[i]
        rows.append({"time_utc": t.isoformat() + "Z", "temp_c": round(float(r.temp_c), 1), "rh": round(float(r.rh)),
                     "wind_kmh": round(float(r.wind_kmh), 1), "wind_dir_deg": round(float(r.wind_dir)),
                     "gust_kmh": round(float(r.gust_kmh), 1) if pd.notna(r.gust_kmh) else None,
                     "rain_mm": round(float(r.rain_mm), 2), "emc_pct": round(float(f.emc), 1),
                     "fuel_moisture_1h_pct": round(float(f.m1h), 1), "fuel_moisture_10h_pct": round(float(f.m10h), 1),
                     "fuel_moisture_100h_pct": round(float(f.m100h), 1),
                     "flat_ros_m_min": round(float(hb["ros_flat"][i]), 2), "fire_type": hb["fire_type"][i]})
    return {"query": {"lat": lat, "lon": lon, "start": start.isoformat() + "Z", "hours": int(hours)},
            "fuel": {"class": fuel, "model": FUEL_MODELS[fuel]["fm"], "why": why, "assumption": True},
            "weather": info, "hours": rows,
            "note": "flat_ros_m_min = head rate of spread on flat ground for the assumed fuel (no terrain)"}


def resolve_ignition(mode: str, lat: float, lon: float, start=None, state=None, live_loader=None,
                     radius_km: float = RADIUS_KM_DEFAULT) -> dict:
    """point | live | archive -> ignition dict; falls back to a point ignition with the reason recorded.

    radius_km (default 30, clipped to 0.5..60): detections / burning cells within this distance of (lat, lon)
    are the ignition; separate clusters become fires A, B, C ..."""
    mode = (mode or "point").lower()
    radius_km = float(min(max(radius_km, 0.5), RADIUS_KM_MAX))
    ign, reason = None, None
    if mode == "live":
        try:
            if live_loader is None:
                from backend import live as _live
                live_loader = lambda: _live.load_feeds("24h")  # noqa: E731
            df, _info = live_loader()
            ign = ignition_live(lat, lon, df, radius_km)
            if ign is None:
                reason = f"no FIRMS NRT detection within {radius_km:g} km in the last 24 h"
        except Exception as e:
            reason = f"FIRMS NRT feed unavailable ({type(e).__name__}: {str(e)[:120]})"
    elif mode == "archive":
        if state is None:
            reason = "archive not loaded on this server (EQUIFIRE_LITE / still loading)"
        else:
            st = _parse_start(start)
            try:
                from backend.data import day_of
                ign = ignition_archive(state, lat, lon, day_of(st.date().isoformat()), radius_km)
            except Exception as e:
                reason = f"archive lookup failed ({type(e).__name__})"
            if ign is None and reason is None:
                reason = f"no burning VIIRS cell within {radius_km:g} km on that day in the archive"
    elif mode != "point":
        reason = f"unknown ignition mode {mode!r}"
    if ign is None:
        ign = {"mode": "point", "lats": np.array([lat]), "lons": np.array([lon]), "estimate": True,
               "note": "single-cell ignition at the requested point (ESTIMATE: true origin unknown)"}
    ign["requested_mode"] = mode
    ign["fallback_reason"] = reason
    return ign


# ------------------------------------------------------------------ calibration on recent observed growth
CAL_BOUNDS = (0.1, 5.0)  # v2 used 0.05..5; savanna fires collapsed onto 0.05 on one day of thin evidence
CAL_N0_PIX = 15.0        # shrink='v2' only: weight = N / (N + N0), N = evidence in observation pixels
CAL_SIGMA_PRIOR = math.log(3.0)   # prior sd of log m (m within x3 of the physics at 1 sd); chosen a priori
CAL_SIGMA_MODEL = math.log(3.0)   # day-to-day growth variability not explained by weather (persistence error)
CAL_MIN_PIX = 3          # fewer new polar pixels (and no GOES evidence) -> no calibration
CAL_ASSOC_KM = 10.0      # history detections within this distance of the current ignition = "same fire"
CAL_P0 = 1.5             # first-guess exponent of area ~ m^p (between perimeter-linear 1 and point-quadratic 2)
CELL005_DEG = 0.05


def _local_xy_km(lat, lon, clat, clon):
    return (np.asarray(lon, float) - clon) * 111.32 * math.cos(math.radians(clat)), \
        (np.asarray(lat, float) - clat) * 111.32


def associate(hist: pd.DataFrame, ign_lats, ign_lons, assoc_km: float = CAL_ASSOC_KM) -> pd.DataFrame:
    """History detections within assoc_km of any current ignition point (= the same fire complex)."""
    if hist is None or hist.empty or not len(ign_lats):
        return (hist if hist is not None else pd.DataFrame(columns=["lat", "lon", "t"])).iloc[0:0]
    from scipy.spatial import cKDTree
    clat, clon = float(np.mean(ign_lats)), float(np.mean(ign_lons))
    ix, iy = _local_xy_km(ign_lats, ign_lons, clat, clon)
    hx, hy = _local_xy_km(hist["lat"], hist["lon"], clat, clon)
    d, _ = cKDTree(np.c_[ix, iy]).query(np.c_[hx, hy], k=1, distance_upper_bound=assoc_km)
    return hist[np.isfinite(d)]


def _pix_keys(lat, lon, clat, clon, pixel: str) -> set:
    if pixel == "cell005":
        return set(zip(np.floor((np.asarray(lon, float) + 180) / CELL005_DEG).astype(int).tolist(),
                       np.floor((np.asarray(lat, float) + 90) / CELL005_DEG).astype(int).tolist()))
    return _pix_set(lat, lon, clat, clon)


def _pix_km2(pixel: str, clat: float) -> float:
    if pixel == "cell005":
        return (CELL005_DEG * 111.32) ** 2 * math.cos(math.radians(clat))
    return (PIX_M / 1000) ** 2


def history_window(hist: pd.DataFrame, t_now: pd.Timestamp, days_max: int = 3, min_init: int = 3):
    """Largest D in days_max..1 with >= min_init detections in (t_now - D d - 24 h, t_now - D d]."""
    for D in range(int(days_max), 0, -1):
        t_c0 = t_now - pd.Timedelta(days=D)
        n = int(((hist["t"] > t_c0 - pd.Timedelta(hours=24)) & (hist["t"] <= t_c0)).sum())
        if n >= min_init:
            return D, t_c0
    return 0, None


GOES_LON0 = {16: -75.0, 19: -75.0, 17: -137.0, 18: -137.0}   # projection origins in the FDC files
GOES_FD_SCALE, GOES_FD_OFFSET = 5.6e-5, 0.151844              # ABI full-disk 2 km fixed grid (rad)


def goes_pixel_keys(lat, lon, sats) -> set:
    """(sat, column, row) of the ABI full-disk 2 km pixel containing each point, for every satellite in sats."""
    from equifire.goes import latlon_to_xy
    out = set()
    lat, lon = np.asarray(lat, float), np.asarray(lon, float)
    for s in sats:
        x, y = latlon_to_xy(lat, lon, lon0=GOES_LON0[int(s)])
        ok = np.isfinite(x) & np.isfinite(y)
        ix = np.round((x[ok] + GOES_FD_OFFSET) / GOES_FD_SCALE).astype(int)
        iy = np.round((GOES_FD_OFFSET - y[ok]) / GOES_FD_SCALE).astype(int)
        out |= {(f"G{int(s)}", a, b) for a, b in zip(ix.tolist(), iy.tolist())}
    return out


def goes_covered(lat: float, lon: float) -> bool:
    """GOES-East/West full disks usable: the Americas (about 165 W .. 5 W across, |lat| <= 60)."""
    return abs(lat) <= 60 and (-170 <= lon <= -10)


def calibrate(ign_lats, ign_lons, t_now, hist: pd.DataFrame | None, *, wx: pd.DataFrame | None = None,
              fuel: str = "auto", elev_fn=None, getter=None, days_max: int = 3, pixel: str = "viirs375",
              assoc_km: float = CAL_ASSOC_KM, bounds=CAL_BOUNDS, max_runs: int = 4, sim_fn=None,
              wx_grid=None, goes_df: pd.DataFrame | None = None, shrink: str = "bayes",
              sigma_prior: float = CAL_SIGMA_PRIOR, sigma_model: float = CAL_SIGMA_MODEL,
              min_pix: int = CAL_MIN_PIX, n0: float = CAL_N0_PIX) -> dict:
    """Fit one spread-rate multiplier m so that simulated growth over the last D (1..3) days matches the growth
    observed by the satellites over the same days (data assimilation of the fire's own recent history).

    hist: polar-orbiter detections (lat, lon, t); only rows with t <= t_now are used (no look-ahead).
    Observed growth = new observation pixels (375 m VIIRS pixels, or 0.05 deg cells for pixel='cell005') detected in
    (t_now - D d, t_now] that were not detected before. Simulated growth: the physics run from t_now - D d for D x 24 h,
    ignited at the detections of the 24 h before t_now - D d, with that period's weather.
    goes_df (optional, Americas): GOES ABI fire pixels (time, sat, lat, lon, px_x, px_y, px_km2) of the same window;
    a second growth measure on the 2 km GOES pixel grid (new GOES pixels vs GOES pixels touched by the simulated
    area), filling the hours between polar passes. Both residuals (log sim - log obs) are combined with weights
    1 / (sigma_model^2 + 1 / (N + 1)), N = new pixels of that sensor.
    m: secant search in log m (<= max_runs simulations), then shrinkage toward 1:
      shrink='bayes' (default): log m = w log m_fit, w = p^2 s_p^2 / (p^2 s_p^2 + s_e^2) with prior sd s_p on log m,
        s_e^2 = 1 / sum_k 1 / (sigma_model^2 + 1/(N_k+1)), p = fitted d log(area) / d log m. Not applied at all
        when fewer than min_pix new polar pixels and no GOES evidence.
      shrink='v2' (previous rule): w = N / (N + n0), N = max(observed, simulated-at-m=1) pixels.
    m is clipped to bounds."""
    sim_fn = sim_fn or simulate
    t_now = pd.Timestamp(t_now)
    base = {"applied": False, "m": 1.0, "observed_km2": None, "simulated_km2": None, "days_used": 0,
            "confidence": "none", "measure": pixel, "estimate": True}
    if hist is None or hist.empty:
        return {**base, "reason": "no detection history available"}
    h = hist[pd.to_datetime(hist["t"]) <= t_now].copy()
    h["t"] = pd.to_datetime(h["t"])
    h = associate(h, ign_lats, ign_lons, assoc_km)
    if h.empty:
        return {**base, "reason": f"no earlier detection within {assoc_km:g} km of the fire"}
    D, t_c0 = history_window(h, t_now, days_max)
    if not D:
        return {**base, "reason": "fire too young: fewer than 3 detections 1-3 days before the forecast start"}
    clat, clon = float(np.mean(ign_lats)), float(np.mean(ign_lons))
    init = h[(h["t"] > t_c0 - pd.Timedelta(hours=24)) & (h["t"] <= t_c0)]
    before = _pix_keys(h.loc[h["t"] <= t_c0, "lat"], h.loc[h["t"] <= t_c0, "lon"], clat, clon, pixel)
    after = h[h["t"] > t_c0]
    new_pix = _pix_keys(after["lat"], after["lon"], clat, clon, pixel) - before
    pk = _pix_km2(pixel, clat)
    obs_km2 = len(new_pix) * pk
    # ---- GOES measure (optional)
    g_info = None
    if goes_df is not None:
        gd = goes_df.copy()
        gd["t"] = pd.to_datetime(gd["time"])
        gd = gd[(gd["t"] >= t_c0 - pd.Timedelta(hours=6)) & (gd["t"] <= t_now)]
        gd = associate(gd, h["lat"].to_numpy(), h["lon"].to_numpy(), assoc_km + 3.0) if len(gd) else gd
        sats = sorted({int(str(s)[1:]) for s in goes_df["sat"]}) if len(goes_df) else \
            [int(s) for s in goes_df.attrs.get("sats", [])]
        if sats and goes_df.attrs.get("scans", len(goes_df) > 0):
            g_before = {(s, a, b) for s, a, b in zip(gd.loc[gd["t"] <= t_c0, "sat"], gd.loc[gd["t"] <= t_c0, "px_x"],
                                                     gd.loc[gd["t"] <= t_c0, "px_y"])}
            g_before |= goes_pixel_keys(init["lat"], init["lon"], sats)
            g_after = {(s, a, b) for s, a, b in zip(gd.loc[gd["t"] > t_c0, "sat"], gd.loc[gd["t"] > t_c0, "px_x"],
                                                    gd.loc[gd["t"] > t_c0, "px_y"])}
            g_new = g_after - g_before
            px_km2 = float(gd["px_km2"].median()) if len(gd) else 4.0
            g_info = {"sats": [f"G{s}" for s in sats], "before": g_before, "new": len(g_new),
                      "observed_km2": len(g_new) * px_km2, "px_km2": px_km2,
                      "scans": int(goes_df.attrs.get("scans", 0))}
    if shrink == "bayes" and len(new_pix) < min_pix and not (g_info and g_info["new"] > 0):
        return {**base, "observed_km2": round(obs_km2, 3), "observed_new_pixels": len(new_pix), "days_used": D,
                "reason": f"too few new pixels ({len(new_pix)} < {min_pix}) in the calibration window: m = 1"}
    ign = {"mode": "calibration-history", "lats": init["lat"].to_numpy(float), "lons": init["lon"].to_numpy(float),
           "estimate": True, "note": "detections of the 24 h before the calibration window"}
    runs = []
    need_mask = pixel == "cell005" or g_info is not None

    def growth(m):
        r = sim_fn(clat, clon, t_c0, D * 24, D * 24 * 60, ignition=ign, fuel=fuel, wx=wx, getter=getter,
                   elev_fn=elev_fn, envelope=False, frames=False, keep_masks=need_mask, ros_mult=m, wx_grid=wx_grid)
        sim_g = None
        if need_mask:
            mask, g = r["frames"][-1]["_mask"]
            lo, la = g.centre_lonlat()
            yy, xx = np.nonzero(mask)
        if pixel == "cell005":
            sim = len(_pix_keys(la[yy], lo[xx], clat, clon, pixel) - before) * pk
        else:
            sim = max(r["summary"]["burned_km2"] - r["summary"]["initial_km2"], 0.0)
        if g_info is not None:
            sim_g = len(goes_pixel_keys(la[yy], lo[xx], [int(s[1:]) for s in g_info["sats"]]) - g_info["before"])
        runs.append({"m": round(m, 4), "simulated_km2": round(sim, 3),
                     **({"simulated_goes_new_pixels": sim_g} if sim_g is not None else {})})
        return sim, sim_g, r
    eps = pk / 2
    lo_b, hi_b = bounds
    var_v = sigma_model ** 2 + 1.0 / (len(new_pix) + 1)
    var_g = sigma_model ** 2 + 1.0 / (g_info["new"] + 1) if g_info else None

    def resid(sim, sim_g):
        rv = math.log(sim + eps) - math.log(obs_km2 + eps)
        if g_info is None:
            return rv
        rg = math.log(sim_g + 0.5) - math.log(g_info["new"] + 0.5)
        return (rv / var_v + rg / var_g) / (1 / var_v + 1 / var_g)
    s1, g1, r1 = growth(1.0)
    if s1 <= 0 and not g1:
        return {**base, "observed_km2": round(obs_km2, 3), "simulated_km2": 0.0, "days_used": D,
                "reason": "no simulated spread in the calibration window (fuels too wet / rain): m not identifiable",
                "runs": runs, "fuel": r1["fuel"]["class"]}
    xs, ys = [0.0], [resid(s1, g1)]
    p = CAL_P0
    for _ in range(max_runs - 1):
        if abs(ys[-1]) < math.log(1.1):
            break
        x_new = float(np.clip(xs[-1] - ys[-1] / p, math.log(lo_b), math.log(hi_b)))
        if any(abs(x_new - x) < 1e-3 for x in xs):
            break
        s, sg, _ = growth(math.exp(x_new))
        xs.append(x_new)
        ys.append(resid(s, sg))
        if abs(xs[-1] - xs[-2]) > 1e-6:
            p = float(np.clip((ys[-1] - ys[-2]) / (xs[-1] - xs[-2]), 0.3, 3.0))
    best = int(np.argmin(np.abs(ys)))
    m_fit = math.exp(xs[best])
    if shrink == "v2":
        n_eff = max(len(new_pix), s1 / pk)
        w = n_eff / (n_eff + n0)
    else:
        var_e = 1 / (1 / var_v + (1 / var_g if var_g else 0.0))
        w = p * p * sigma_prior ** 2 / (p * p * sigma_prior ** 2 + var_e)
    m = float(np.clip(math.exp(w * math.log(m_fit)), lo_b, hi_b))
    conf = "high" if (w >= 0.8 and D >= 2) else "medium" if w >= 0.6 else "low"
    fit_run = next((r for r in runs if r["m"] == round(m_fit, 4)), {})
    out = {"applied": True, "m": round(m, 4), "m_fit": round(m_fit, 4), "weight": round(w, 3), "shrink": shrink,
           "observed_km2": round(obs_km2, 3), "observed_new_pixels": len(new_pix),
           "simulated_km2": round(s1, 3), "simulated_fit_km2": fit_run.get("simulated_km2"),
           "days_used": D, "window_utc": [t_c0.isoformat() + "Z", t_now.isoformat() + "Z"],
           "n_history_detections": int(len(h)), "confidence": conf, "measure": pixel, "runs": runs,
           "fuel": r1["fuel"]["class"], "estimate": True,
           "note": "single spread-rate multiplier fitted to this fire's own growth over the previous days "
                   "(observed = new satellite pixels, an active-fire proxy, not mapped burned area); shrunk "
                   "toward 1 when the evidence is small; shared by all fires in the domain"}
    if g_info is not None:
        out["goes"] = {"satellites": g_info["sats"], "scans": g_info["scans"], "observed_new_pixels": g_info["new"],
                       "observed_km2": round(g_info["observed_km2"], 2), "pixel_km2": round(g_info["px_km2"], 2),
                       "simulated_new_pixels_m1": g1,
                       "note": "GOES ABI 2 km fire pixels (10-min product sampled hourly) between polar passes; "
                               "pixel-count measure, much coarser than VIIRS"}
    return out


def dem_sampler(lat: float, lon: float, getter=None, fine_km: float = 8.0, coarse_km: float = 40.0, k: int = 11):
    """elev_fn(LA, LO) from two fixed elevation samples (k x k over +-fine_km and +-coarse_km): one fetch of
    2 k^2 points per fire, reused by every simulation run of that fire (calibration iterations, validation)."""
    from scipy.interpolate import RegularGridInterpolator
    kx = 1 / (111.32 * max(math.cos(math.radians(lat)), 0.05))
    boxes = []
    for half in (fine_km, coarse_km):
        la = lat + np.linspace(-half, half, k) / 111.32
        lo = lon + np.linspace(-half, half, k) * kx
        LA, LO = np.meshgrid(la, lo, indexing="ij")
        boxes.append((la, lo, LA, LO))
    z, info = fetch_elevation(np.concatenate([b[2].ravel() for b in boxes]),
                              np.concatenate([b[3].ravel() for b in boxes]), getter=getter)
    if z is None:
        return None
    zf, zc = z[:k * k].reshape(k, k), z[k * k:].reshape(k, k)
    fi = RegularGridInterpolator((boxes[0][0], boxes[0][1]), zf, bounds_error=False, fill_value=None)
    ci = RegularGridInterpolator((boxes[1][0], boxes[1][1]), zc, bounds_error=False, fill_value=None)

    def elev_fn(LA, LO):
        pts = np.c_[np.ravel(LA), np.ravel(LO)]
        inside = (np.abs(pts[:, 0] - lat) <= fine_km / 111.32) & (np.abs(pts[:, 1] - lon) <= fine_km * kx)
        pc = np.c_[np.clip(pts[:, 0], boxes[1][0][0], boxes[1][0][-1]), np.clip(pts[:, 1], boxes[1][1][0],
                                                                                 boxes[1][1][-1])]
        out = ci(pc)
        if inside.any():
            out[inside] = fi(pts[inside])
        return out.reshape(np.shape(LA))
    return elev_fn


def load_history(mode: str, ign: dict, t_now: pd.Timestamp, state=None, history_loader=None,
                 days_max: int = 3) -> tuple[pd.DataFrame | None, str, str | None]:
    """(detections lat/lon/t, pixel measure, error). live: FIRMS NRT 7-day VIIRS feeds; archive: daily 0.05 deg
    VIIRS cells of the EquiFire archive (t = noon of the day)."""
    try:
        if mode == "live":
            if history_loader is None:
                from backend import live as _live
                history_loader = lambda: _live.load_feeds("7d")  # noqa: E731
            df, _ = history_loader()
            if df is None or df.empty:
                return None, "viirs375", "empty 7-day feed"
            df = df[~df["sat"].astype(str).str.upper().str.contains("MODIS")]
            return df[["lat", "lon", "t"]].assign(t=pd.to_datetime(df["t"])), "viirs375", None
        if mode == "archive":
            if state is None:
                return None, "cell005", "archive not loaded"
            day0 = int(ign["day"])
            rows = []
            for k in range(0, days_max + 2):
                got = _archive_cells(state, day0 - k)
                if got is None:
                    continue
                la, lo = got
                rows.append(pd.DataFrame({"lat": la, "lon": lo, "day": day0 - k}))
            if not rows:
                return None, "cell005", "no archive cells in the previous days"
            df = pd.concat(rows, ignore_index=True)
            from backend.data import date_of
            df["t"] = pd.to_datetime([date_of(int(d)) for d in df["day"]]) + pd.Timedelta(hours=12)
            return df[["lat", "lon", "t"]], "cell005", None
    except Exception as e:
        return None, "viirs375", f"{type(e).__name__}: {str(e)[:120]}"
    return None, "viirs375", "point ignition: no fire history"


def fetch_weather_grid(clat: float, clon: float, start: pd.Timestamp, hours: int, half_km: float = WG_HALF_KM,
                       n: int = WG_N, grid_fn=None):
    """Gridded hourly weather (equifire.fireweather.get_weather_grid, n x n Open-Meteo points over +-half_km)
    from start - SPINUP_H to start + hours. Returns (WeatherGrid | None, info)."""
    info = {"api": "open-meteo multi-point (equifire.fireweather.get_weather_grid)", "points": n * n,
            "half_km": half_km, "errors": [], "fallback": False}
    allow_noaa = grid_fn is None
    try:
        if grid_fn is None:
            from equifire.fireweather import get_weather_grid
            grid_fn = functools.partial(get_weather_grid, retries=1)
        t0 = start - pd.Timedelta(hours=SPINUP_H)
        wg = grid_fn(clat, clon, half_km, t0.to_pydatetime(), SPINUP_H + int(hours) + 1, n=n)
        a = np.asarray(wg.arrays["temperature_2m"])
        if not np.isfinite(a).any():
            raise ValueError("weather grid is empty")
        for frame in wg_point_frames(wg):
            if not np.isfinite(frame[["temp_c", "rh", "wind_kmh", "wind_dir", "rain_mm"]].to_numpy()).all():
                raise ValueError("weather grid lacks required hourly conditions")
        return wg, info
    except Exception as e:
        info["errors"].append(f"{type(e).__name__}: {str(e)[:160]}")
        if allow_noaa:
            try:
                from equifire.gfs_weather import get_weather_grid
                wg = get_weather_grid(clat, clon, half_km, start - pd.Timedelta(hours=SPINUP_H),
                                      SPINUP_H + int(hours) + 1, n=n)
                return wg, {**info, **wg.source_meta}
            except Exception as error:
                info["errors"].append(f"NOAA GFS: {type(error).__name__}: {str(error)[:160]}")
        return None, info


def fwi_summary(lat: float, lon: float, date, m1h_noon: float | None = None, fwi_fn=None) -> dict:
    """Canadian FWI System for the start date (equifire.fireweather.fwi_for) + FFMC -> fine fuel moisture
    (Van Wagner 1987: m = 147.2 (101 - FFMC) / (59.5 + FFMC)) as a cross-check of the model's 1 h moisture."""
    try:
        if fwi_fn is None:
            from equifire.fireweather import fwi_for
            fwi_fn = functools.partial(fwi_for, retries=1)
        f = fwi_fn(lat, lon, pd.Timestamp(date).date().isoformat())
        out = {k: f.get(k) for k in ("date", "ffmc", "dmc", "dc", "isi", "bui", "fwi", "dsr", "danger")}
        ff = float(f["ffmc"])
        out["ffmc_fine_fuel_moisture_pct"] = round(147.2 * (101 - ff) / (59.5 + ff), 1)
        out["model_1h_moisture_pct_at_noon"] = None if m1h_noon is None else round(float(m1h_noon), 1)
        out["source"] = f.get("source")
        out["note"] = ("Canadian Forest Fire Weather Index System (Van Wagner 1987, cffdrs equations), daily at local "
                       "noon, 60-day spin-up; reported for context and as a moisture cross-check, NOT used by the "
                       "spread model")
        return out
    except Exception as e:
        return {"error": f"{type(e).__name__}: {str(e)[:160]}"}


def load_goes(lats, lons, t0: pd.Timestamp, t1: pd.Timestamp, goes_fn=None, step_min: int = 60):
    """GOES fire pixels around the fire between t0 and t1 (Americas only). Returns (DataFrame | None, error)."""
    lat, lon = float(np.mean(lats)), float(np.mean(lons))
    if not goes_covered(lat, lon):
        return None, "outside GOES-East/West coverage (Americas only)"
    try:
        if goes_fn is None:
            from equifire.goes import goes_fires as goes_fn
        pad = 0.15
        bbox = (float(np.min(lons)) - pad, float(np.min(lats)) - pad, float(np.max(lons)) + pad,
                float(np.max(lats)) + pad)
        # workers=1: parallel SSL downloads inside goes_fires segfaulted the interpreter (Python 3.14, seen twice)
        df = goes_fn(bbox, t0.to_pydatetime(), t1.to_pydatetime(), step_min=step_min, workers=1)
        if df is not None and not len(df):
            from equifire.goes import choose_sats
            df.attrs["sats"] = choose_sats(bbox, t0.to_pydatetime())
        return df, None
    except Exception as e:
        return None, f"{type(e).__name__}: {str(e)[:160]}"


class SatelliteIgnitionUnavailable(LookupError):
    pass


def forecast(lat: float, lon: float, start=None, hours: int = 72, step_min: int = 60, ignition: str = "point",
             fuel: str = "auto", envelope: bool = True, frames: bool = True, radius_km: float = RADIUS_KM_DEFAULT,
             calibration: str = "on", state=None, live_loader=None, history_loader=None, getter=None,
             elev_fn=None, weather: str = "grid", goes: str = "off", fwi: bool = True, grid_fn=None, fwi_fn=None,
             goes_fn=None, landscape: str = "off", landscape_fn=None, require_satellite: bool = False, osm_mode: str = "full") -> dict:
    """/simulate pipeline: ignition (point/live/archive) -> weather (gridded n x n points, or one point) ->
    optional calibration on the fire's own last 1-3 days (polar orbiters, + GOES pixels if goes='on', Americas)
    -> forward physics run with the fitted multiplier m -> FWI context."""
    from concurrent.futures import ThreadPoolExecutor
    started = time.perf_counter()
    t0 = _parse_start(start)
    ign = resolve_ignition(ignition, lat, lon, t0, state=state, live_loader=live_loader, radius_km=radius_km)
    if require_satellite and ign.get("mode") != ignition:
        raise SatelliteIgnitionUnavailable("Satellite ignition unavailable: " + ign.get("reason", ign.get("note", "no nearby detections")))
    clat, clon = float(np.mean(ign["lats"])), float(np.mean(ign["lons"]))
    cal = {"applied": False, "m": 1.0, "requested": calibration, "confidence": "none", "estimate": True}
    hist = pixel = None
    t_c0 = None
    t_now = t0 if ign["mode"] == "live" else t0.floor("D") + pd.Timedelta(hours=12)
    if calibration == "off":
        cal["reason"] = "calibration=off"
    elif ign["mode"] == "point":
        cal["reason"] = "point ignition: no fire history to calibrate on"
    else:
        hist, pixel, err = load_history(ign["mode"], ign, t_now, state=state, history_loader=history_loader)
        if hist is None:
            cal["reason"] = err
        else:
            h_assoc = associate(hist[pd.to_datetime(hist["t"]) <= t_now], ign["lats"], ign["lons"])
            D, t_c0 = history_window(h_assoc, t_now) if not h_assoc.empty else (0, None)
            if not D:
                cal["reason"] = "fire too young: fewer than 3 detections 1-3 days before the forecast start"
    w_start = t_c0 if t_c0 is not None else t0
    lp = (LandscapeProvider(clat, clon, min(80.0, radius_km + 20), date=w_start, fetch_fn=landscape_fn, osm_mode=osm_mode)
          if landscape == "on" else None)
    span = int(math.ceil((t0 + pd.Timedelta(hours=hours) - w_start).total_seconds() / 3600))
    input_started = time.perf_counter()
    # These inputs are independent. Fetch both before calibration consumes the
    # landscape, preserving every layer and the same hourly weather/model settings.
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="landscape-input") as inputs:
        landscape_future = inputs.submit(lp.get) if lp is not None else None
        wx = wx_info = wg = None
        if weather == "grid":
            wg, wg_info = fetch_weather_grid(clat, clon, w_start, span, grid_fn=grid_fn)
            if wg is not None:
                wx_info = {"sources": [wg_info], "errors": wg_info.get("errors", []), "fallback": False,
                           "note": f"gridded hourly weather, {WG_N}x{WG_N} points over +-{WG_HALF_KM:g} km"}
            else:
                wx_info = {"errors": ["weather grid failed -> single point: " + "; ".join(wg_info["errors"])]}
        if wg is None:
            wx, info = fetch_weather(clat, clon, w_start, span, getter=getter)
            wx_info = {**info, "errors": (wx_info or {}).get("errors", []) + info["errors"]}
        if landscape_future is not None:
            landscape_future.result()
    inputs_s = time.perf_counter() - input_started
    calibration_started = time.perf_counter()
    if t_c0 is not None:
        @functools.lru_cache(maxsize=1)
        def calibration_dem():
            return dem_sampler(clat, clon, getter=getter)
        def calibration_elevation(LA, LO):
            sample = calibration_dem()
            return sample(LA, LO) if sample is not None else np.zeros_like(LA)
        ef = elev_fn or calibration_elevation
        g_df, g_err = (None, "goes=off")
        if goes == "on":
            h_assoc = associate(hist[pd.to_datetime(hist["t"]) <= t_now], ign["lats"], ign["lons"])
            g_df, g_err = load_goes(h_assoc["lat"].to_numpy(), h_assoc["lon"].to_numpy(),
                                    t_c0 - pd.Timedelta(hours=6), t_now, goes_fn=goes_fn)
        c = calibrate(ign["lats"], ign["lons"], t_now, hist, wx=wx, wx_grid=wg, fuel=fuel, elev_fn=ef,
                      getter=getter, pixel=pixel, goes_df=g_df,
                      sim_fn=functools.partial(simulate, landscape=lp) if lp is not None else None)
        cal = {**c, "requested": calibration, "goes_requested": goes}
        if g_df is None and goes == "on":
            cal["goes_error"] = g_err
    calibration_s = time.perf_counter() - calibration_started
    simulation_started = time.perf_counter()
    out = simulate(lat, lon, t0, hours, step_min, ignition=ign, fuel=fuel, envelope=envelope, frames=frames,
                   wx=wx, wx_info=wx_info, getter=getter, elev_fn=elev_fn, ros_mult=cal["m"], calibration=cal,
                   wx_grid=wg, landscape=lp)
    simulation_s = time.perf_counter() - simulation_started
    if fwi:
        noon = None
        try:
            fr = out["frames"]
            hh = min(range(len(fr)), key=lambda k: abs((pd.Timestamp(fr[k]["time_utc"][:19]).hour
                                                        - (12 - clon / 15)) % 24))
            noon = fr[hh]["weather"]["fuel_moisture_1h_pct"] if fr[hh]["weather"] else None
        except Exception:
            pass
        out["fwi"] = fwi_summary(clat, clon, t0, noon, fwi_fn=fwi_fn)
    now = pd.Timestamp.now("UTC").tz_localize(None)
    historical = t0 < now.floor("D") - pd.Timedelta(days=6)
    out["weather"].update(
        mode=("fallback_scenario" if out["weather"].get("fallback") else
              "historical_reanalysis_replay" if historical else "forecast_with_recent_history"),
        simulation_from_utc=t0.isoformat() + "Z",
        simulation_to_utc=(t0 + pd.Timedelta(hours=hours)).isoformat() + "Z",
        time_step_minutes=60,
        spatial_mode="grid" if wg is not None else "point",
        historical_replay_uses_observed_future=historical,
        note_time="Each simulation hour uses that hour's wind, temperature, humidity and rain; fuel moisture "
                  "carries over between hours. Historical reanalysis replay is not an issued-weather-forecast backtest.")
    out["timings"] = {"hourly_total_s": round(time.perf_counter() - started, 3),
                      "weather_and_landscape_s": round(inputs_s, 3),
                      "calibration_s": round(calibration_s, 3),
                      "physical_simulation_s": round(simulation_s, 3),
                      "landscape_fetch_s": None if lp is None else lp.seconds}
    return out


# ------------------------------------------------------------------ validation vs VIIRS (light, honest)
VALIDATION_CASES = [  # (country file stem, year, months, how many) - fixed BEFORE looking at any result
    ("Canada", 2023, (5, 6, 7, 8), 2), ("Australia", 2023, (8, 9, 10, 11, 12), 2),
    ("United_States", 2024, (6, 7, 8, 9), 1), ("Bolivia", 2024, (8, 9), 1), ("Greece", 2023, (7, 8), 1),
    ("Portugal", 2024, (8, 9), 1), ("Canada", 2024, (6, 7, 8), 1), ("United_States", 2023, (6, 7, 8, 9), 1),
]
PIX_M = 375.0


def _load_viirs(country: str, year: int, root: Path) -> pd.DataFrame:
    frames = []
    for sat in ("viirs-snpp", "viirs-jpss1"):
        f = root / sat / str(year) / f"{sat}_{year}_{country}.csv"
        if not f.exists():
            continue
        d = pd.read_csv(f, usecols=["latitude", "longitude", "acq_date", "acq_time"], dtype={"acq_time": str})
        hh = d["acq_time"].str.zfill(4)
        t = pd.to_datetime(d["acq_date"] + " " + hh.str[:2] + ":" + hh.str[2:], format="%Y-%m-%d %H:%M")
        frames.append(pd.DataFrame({"lat": d["latitude"].astype(np.float32), "lon": d["longitude"].astype(np.float32),
                                    "t": t}))
    df = pd.concat(frames, ignore_index=True)
    df["day"] = df["t"].dt.floor("D")
    df["gx"] = np.floor((df["lon"] + 180) / 0.05).astype(np.int32)
    df["gy"] = np.floor((df["lat"] + 90) / 0.05).astype(np.int32)
    return df


def _candidates(df: pd.DataFrame, months, rng) -> list[dict]:
    """New, small fires that are still detected in the next 3 days (selection rule, not tuned)."""
    from scipy.ndimage import label
    cells = df.groupby(["day", "gx", "gy"]).size().rename("n").reset_index()
    days = sorted(d for d in cells["day"].unique() if pd.Timestamp(d).month in months)
    by_day = {d: g for d, g in cells.groupby("day")}
    out = []
    for d in rng.permutation(days)[:40]:
        d = pd.Timestamp(d)
        g = by_day[d]
        prev = pd.concat([by_day[p] for p in (d - pd.Timedelta(days=k) for k in (1, 2, 3)) if p in by_day]
                         or [g.iloc[0:0]])
        prev_set = set(zip(prev["gx"], prev["gy"]))
        x0, y0 = g["gx"].min(), g["gy"].min()
        grid = np.zeros((g["gy"].max() - y0 + 3, g["gx"].max() - x0 + 3), bool)
        grid[g["gy"] - y0 + 1, g["gx"] - x0 + 1] = True
        lab, nl = label(grid, structure=np.ones((3, 3)))
        for li in rng.permutation(np.arange(1, nl + 1))[:200]:
            ys, xs = np.nonzero(lab == li)
            gx, gy = xs + x0 - 1, ys + y0 - 1
            if len(gx) > 2:
                continue
            if any((a + i, b + j) in prev_set for a, b in zip(gx, gy) for i in (-3, -2, -1, 0, 1, 2, 3)
                   for j in (-3, -2, -1, 0, 1, 2, 3)):
                continue
            det = df[(df["day"] == d) & df["gx"].isin(gx) & df["gy"].isin(gy)]
            if len(det) < 5:
                continue
            clat, clon = float(det["lat"].mean()), float(det["lon"].mean())
            nxt = df[(df["t"] > det["t"].max()) & (df["t"] <= det["t"].max() + pd.Timedelta(days=3))
                     & (np.abs(df["lat"] - clat) < 0.3) & (np.abs(df["lon"] - clon) < 0.3)]
            if len(nxt) < 20:
                continue
            out.append({"day": d, "det": det, "clat": clat, "clon": clon})
            break
        if len(out) >= 6:
            break
    return out


def _pix_set(lat, lon, clat, clon) -> set:
    x = (np.asarray(lon, float) - clon) / (M_TO_DEG / math.cos(math.radians(clat)))
    y = (np.asarray(lat, float) - clat) / M_TO_DEG
    return set(zip(np.floor(x / PIX_M).astype(int).tolist(), np.floor(y / PIX_M).astype(int).tolist()))


def validate(root: Path | None = None, out_f: Path | None = None, seed: int = 2026, pause_s: float = 12.0,
             log=print) -> dict:
    root = root or Path(__file__).resolve().parent.parent / "data_country"
    out_f = out_f or Path(__file__).resolve().parent.parent / "reports" / "physics_validation.json"
    rng = np.random.default_rng(seed)
    rows = []
    for country, year, months, n in VALIDATION_CASES:
        try:
            df = _load_viirs(country, year, root)
        except Exception as e:
            log(f"[validate] {country} {year}: load failed {e}")
            continue
        cands = _candidates(df, months, rng)[:n]
        for c in cands:
            det = c["det"]
            start = det["t"].max().floor("h") + pd.Timedelta(hours=1)
            ign = {"mode": "archive-detections", "lats": det["lat"].to_numpy(float), "lons": det["lon"].to_numpy(float),
                   "estimate": True, "note": "VIIRS 375 m detections of day 0 (pixel centres)"}
            t0 = time.time()
            res = simulate(c["clat"], c["clon"], start, 72, 60, ignition=ign, frames=False, envelope=True,
                           keep_masks=True)
            rt = time.time() - t0
            # re-run grid arrival raster for hit-rate (cheap: reuse summary perimeters? use simulate internals)
            near = df[(np.abs(df["lat"] - c["clat"]) < 0.4) & (np.abs(df["lon"] - c["clon"]) < 0.4)]
            ign_pix = _pix_set(det["lat"], det["lon"], c["clat"], c["clon"])
            r = {"country": country, "year": year, "day0": str(c["day"].date()), "lat": round(c["clat"], 4),
                 "lon": round(c["clon"], 4), "start_utc": start.isoformat() + "Z", "fuel": res["fuel"]["class"],
                 "n_ign_det": int(len(det)), "weather_errors": res["weather"]["errors"],
                 "terrain_flat": res["grid"]["terrain"].get("flat"), "cell_m": res["grid"]["cell_m"],
                 "runtime_s": round(rt, 2), "days": []}
            for k in (1, 2, 3):
                tk = start + pd.Timedelta(hours=24 * k)
                obs = near[(near["t"] > start - pd.Timedelta(hours=1)) & (near["t"] <= tk)]
                obs_pix = _pix_set(obs["lat"], obs["lon"], c["clat"], c["clon"]) - ign_pix
                obs_km2 = len(obs_pix) * (PIX_M / 1000) ** 2
                fr = res["frames"][24 * k]
                sim_new = max(fr["burned_km2"] - res["summary"]["initial_km2"], 0.0)
                env = fr["envelope_km2"]
                hit = _hit_rate(res, obs_pix, c, tk, start)
                r["days"].append({"day": k, "obs_new_km2_viirs_pixels": round(obs_km2, 2),
                                  "sim_new_km2": round(sim_new, 2),
                                  "sim_low_high_km2": [env.get("low"), env.get("high")],
                                  "ratio_sim_obs": round(sim_new / obs_km2, 2) if obs_km2 else None,
                                  "obs_pixels_inside_sim_pct": hit})
            rows.append(r)
            log(f"[validate] {country} {year} {r['day0']} {r['fuel']}: " + "; ".join(
                f"d{d['day']} obs {d['obs_new_km2_viirs_pixels']} sim {d['sim_new_km2']} hit {d['obs_pixels_inside_sim_pct']}"
                for d in r["days"]) + f" ({rt:.1f}s)")
            time.sleep(pause_s)
        del df
    summ = {}
    for k in (1, 2, 3):
        ratios = [d["ratio_sim_obs"] for r in rows for d in r["days"] if d["day"] == k and d["ratio_sim_obs"]]
        hits = [d["obs_pixels_inside_sim_pct"] for r in rows for d in r["days"]
                if d["day"] == k and d["obs_pixels_inside_sim_pct"] is not None]
        summ[f"day{k}"] = {"n": len(ratios), "median_ratio_sim_obs": round(float(np.median(ratios)), 2) if ratios else None,
                           "within_factor_2_pct": round(100 * float(np.mean([(0.5 <= x <= 2) for x in ratios])), 0)
                           if ratios else None,
                           "median_hit_pct": round(float(np.median(hits)), 1) if hits else None}
    rep = {"protocol": "fires chosen by a fixed rule before any simulation (new fire: no VIIRS detection within "
                       "~15 km in the previous 3 days, <= 2 cells of 0.05 deg, >= 5 detections on day 0, >= 20 "
                       "detections within 0.3 deg in the next 3 days), random with seed %d; no parameter was "
                       "tuned on these or any fires. Observed new area = unique 375 m pixels with S-NPP/NOAA-20 "
                       "detections up to day k (minus day-0 pixels) within 0.4 deg: an active-fire proxy, NOT a "
                       "burned-area product (it undercounts areas that burn between overpasses and overcounts "
                       "nearby unrelated fires). hit = %% of observed new pixels inside the simulated burned area "
                       "(+1 cell)." % seed, "summary": summ, "fires": rows}
    out_f.parent.mkdir(parents=True, exist_ok=True)
    out_f.write_text(json.dumps(rep, indent=1, default=str))
    return rep


def _hit_rate(res: dict, obs_pix: set, c: dict, tk, start) -> float | None:
    """% of observed pixels inside the simulated perimeter at tk (point-in-polygon on the frame perimeter)."""
    if not obs_pix:
        return None
    k = int(round((tk - start).total_seconds() / 3600))
    geom = res["frames"][k].get("_mask")
    if geom is None:
        return None
    mask, grid = geom
    from scipy.ndimage import binary_dilation
    m = binary_dilation(mask)
    pts = np.array(list(obs_pix), float) * PIX_M + PIX_M / 2
    lon = c["clon"] + pts[:, 0] * (M_TO_DEG / math.cos(math.radians(c["clat"])))
    lat = c["clat"] + pts[:, 1] * M_TO_DEG
    iy, ix = grid.ij(lat, lon)
    ok = (iy >= 0) & (ix >= 0) & (iy < grid.n) & (ix < grid.n)
    inside = np.zeros(len(pts), bool)
    inside[ok] = m[iy[ok], ix[ok]]
    return round(100 * float(inside.mean()), 1)


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "validate":
        rep = validate()
        print(json.dumps(rep["summary"], indent=1))
