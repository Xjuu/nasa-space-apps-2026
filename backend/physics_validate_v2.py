"""Validation v2/v3 of the physics simulator: calibration (data assimilation) on vs off.

v3 (this version) adds gridded weather (5 x 5 Open-Meteo points, per-cell wind/RH), the Bayesian shrinkage
with bounds 0.1..5 and the GOES pixel measure. Variants per start: off_point (v2 physics, one weather point),
off (gridded weather), on_v2 (v2 calibration rule: w = N/(N+15), bounds 0.05..5), on (Bayesian shrinkage:
prior sd ln 3, model sd ln 3, >= 3 new pixels), on_goes (on + GOES pixels, Americas fires only; same as on
elsewhere), off_land / on_land / on_land_goes (as off / on / on_goes with the landscape layers: WorldCover
Anderson fuel groups, Copernicus slope, OSM firebreak slowdown, Sentinel-2 dryness bins; imagery up to the fire's
S0 only). All calibrated variants use gridded weather. Output reports/physics_validation_v3.json
(v2 result kept in reports/physics_validation_v2.json).

    PYTHONPATH=. .venv/bin/python -m backend.physics_validate_v2

Fires: (a) the SAME 10 fires as reports/physics_validation.json (same fixed rule, seed 2026);
       (b) 10 NEW fires, same rule, seed 7, excluding fires of set (a). Nothing is tuned on set (b).
The selection rule picks NEW fires (no VIIRS detection within ~15 km in the 3 days before day 0), so at the
original start S0 (end of day 0) a fire has no growth history and calibration cannot run. Protocols:
  S0: start = end of day 0, ignition = day-0 detections (as v1; calibration impossible -> off only)
  S1: start = S0 + 24 h, ignition = all of the fire's detections up to S1; calibration window = (S0, S1] (1 day)
  S2: start = S0 + 48 h, ignition = detections up to S2; calibration window = (S0, S2] (2 days)
Calibration only sees detections with t <= start (no leakage). Forecast is scored on the 3 days after start.
Observed new area (day k) = unique 375 m VIIRS pixels detected in (start, start + k d] within 0.4 deg that were
not detected before start (active-fire proxy, as v1). hit = % of those pixels inside the simulated area (+1 cell).
Terrain for all runs of a fire comes from one dem_sampler fetch (two 11 x 11 Open-Meteo elevation samples), and
one archive weather fetch covers all runs of a fire, so on/off runs differ ONLY by the multiplier m.
"""
from __future__ import annotations

import functools
import json
import math
import time
from pathlib import Path

import numpy as np
import pandas as pd

from backend import physics_spread as ps

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "reports" / "physics_validation_v3.json"
SEED_ORIG, SEED_NEW = 2026, 7


def chain_associate(near: pd.DataFrame, lats, lons, t_max, km: float = ps.CAL_ASSOC_KM) -> pd.DataFrame:
    """Detections (t <= t_max) linked to the seed points by chains of <= km hops (the growing fire)."""
    pool = near[near["t"] <= t_max]
    cur = ps.associate(pool, lats, lons, km)
    for _ in range(6):
        nxt = ps.associate(pool, cur["lat"].to_numpy(), cur["lon"].to_numpy(), km)
        if len(nxt) == len(cur):
            break
        cur = nxt
    return cur


def _score(res, near, c, S, prior_pix) -> list[dict]:
    out = []
    for k in (1, 2, 3):
        tk = S + pd.Timedelta(days=k)
        obs = near[(near["t"] > S) & (near["t"] <= tk)]
        obs_pix = ps._pix_set(obs["lat"], obs["lon"], c["clat"], c["clon"]) - prior_pix
        obs_km2 = len(obs_pix) * (ps.PIX_M / 1000) ** 2
        fr = res["frames"][24 * k]
        sim_new = max(fr["burned_km2"] - res["summary"]["initial_km2"], 0.0)
        out.append({"day": k, "obs_new_km2": round(obs_km2, 2), "sim_new_km2": round(sim_new, 2),
                    "ratio_sim_obs": round(sim_new / obs_km2, 3) if obs_km2 else None,
                    "hit_pct": ps._hit_rate(res, obs_pix, c, tk, S)})
    return out


VARIANTS_ON = ("on_v2", "on", "on_goes", "on_land", "on_land_goes")
OFF_MODES = ("off_point", "off", "off_land")
LAND_HALF_KM = 40.0


def run_fire(c, df, log, goes: bool = True) -> dict:
    det = c["det"]
    S0 = det["t"].max().floor("h") + pd.Timedelta(hours=1)
    near = df[(np.abs(df["lat"] - c["clat"]) < 0.4) & (np.abs(df["lon"] - c["clon"]) < 0.4)
              & (df["t"] >= c["day"] - pd.Timedelta(days=3)) & (df["t"] <= S0 + pd.Timedelta(days=6))].copy()
    near["lat"], near["lon"] = near["lat"].astype(float), near["lon"].astype(float)
    wx, wxi = ps.fetch_weather(c["clat"], c["clon"], S0, 48 + 72)
    wg, wgi = ps.fetch_weather_grid(c["clat"], c["clon"], S0, 48 + 72)
    ef = ps.dem_sampler(c["clat"], c["clon"])
    flat = ef is None
    ef = ef or (lambda LA, LO: np.zeros_like(LA))
    g_df, g_err = None, "goes disabled"
    if goes and ps.goes_covered(c["clat"], c["clon"]):
        fire = chain_associate(near, det["lat"].to_numpy(float), det["lon"].to_numpy(float), S0 + pd.Timedelta(days=2))
        g_df, g_err = ps.load_goes(fire["lat"].to_numpy(), fire["lon"].to_numpy(), S0 - pd.Timedelta(hours=6),
                                   S0 + pd.Timedelta(days=2))
    row = {"day0": str(c["day"].date()), "lat": round(c["clat"], 4), "lon": round(c["clon"], 4),
           "n_ign_det_day0": int(len(det)), "weather_errors": wxi["errors"], "weather_grid_errors": wgi["errors"],
           "terrain_flat": flat, "goes": None if g_df is None else {"pixels": int(len(g_df)),
                                                                     "scans": int(g_df.attrs.get("scans", 0))},
           "goes_error": g_err if g_df is None else None, "protocols": {}}
    lp = ps.LandscapeProvider(c["clat"], c["clon"], LAND_HALF_KM, date=S0)     # imagery up to S0 only (no leakage)
    lp.get()
    row["landscape"] = lp.meta()
    log(f"  landscape: {'ok %.0fs' % lp.seconds if lp.data is not None else 'FAILED ' + str(lp.error)} "
        f"fuels={lp.fuels_present() if lp.data is not None else None}")
    if wg is None:
        log(f"  weather grid failed: {wgi['errors']} -> point weather for the grid variants")
    for k in (0, 1, 2):
        S = S0 + pd.Timedelta(days=k)
        if k == 0:
            ign_pts = det
        else:
            ign_pts = chain_associate(near, det["lat"].to_numpy(float), det["lon"].to_numpy(float), S)
        ign = {"mode": "validation", "lats": ign_pts["lat"].to_numpy(float), "lons": ign_pts["lon"].to_numpy(float),
               "estimate": True}
        prior_pix = ps._pix_set(near.loc[near["t"] <= S, "lat"], near.loc[near["t"] <= S, "lon"],
                                c["clat"], c["clon"])
        t0 = time.time()

        def sim(m=1.0, grid=True, land=False):
            return ps.simulate(c["clat"], c["clon"], S, 72, 60, ignition=ign, wx=None if (grid and wg) else wx,
                               wx_info=wxi, elev_fn=ef, envelope=False, frames=False, keep_masks=True, ros_mult=m,
                               wx_grid=wg if grid else None, landscape=lp if land else None)
        r = sim(grid=False)
        p = {"start_utc": S.isoformat() + "Z", "n_ign_points": int(len(ign_pts)), "fuel": r["fuel"]["class"],
             "cell_m": r["grid"]["cell_m"], "n_fires": r["summary"]["n_fires"],
             "off_point": _score(r, near, c, S, prior_pix)}
        del r
        r = sim()
        p["off"] = _score(r, near, c, S, prior_pix)
        del r
        r = sim(land=True)
        p["off_land"] = _score(r, near, c, S, prior_pix)
        p["landscape"] = {"fuel": r["fuel"]["class"], "info": {k: v for k, v in (r.get("landscape") or {}).items()
                                                              if k in ("applied", "coverage_pct", "nonburnable_cells",
                                                                       "blocked_cells", "error")}}
        del r
        if k:
            hist = near[near["t"] <= S]
            p["calibration"] = {}
            for v in VARIANTS_ON:
                use_land = v.startswith("on_land")
                use_goes = v.endswith("goes")
                if use_goes and g_df is None:
                    base = "on_land" if use_land else "on"
                    p[v] = p[base]
                    p["calibration"][v] = p["calibration"][base]
                    continue
                kw = dict(shrink="v2", bounds=(0.05, 5.0)) if v == "on_v2" else {}
                if use_land:
                    kw["sim_fn"] = functools.partial(ps.simulate, landscape=lp)
                cal = ps.calibrate(ign["lats"], ign["lons"], S, hist, wx=None if wg else wx, wx_grid=wg, elev_fn=ef,
                                   days_max=3, pixel="viirs375", goes_df=g_df if use_goes else None, **kw)
                p["calibration"][v] = {kk: cal.get(kk) for kk in ("applied", "m", "m_fit", "weight", "observed_km2",
                                                                   "observed_new_pixels", "simulated_km2",
                                                                   "days_used", "confidence", "reason", "goes")}
                if cal["applied"] and cal["m"] != 1.0:
                    r = sim(cal["m"], land=use_land)
                    p[v] = _score(r, near, c, S, prior_pix)
                    del r
                else:
                    p[v] = p["off_land"] if use_land else p["off"]
        p["runtime_s"] = round(time.time() - t0, 2)
        row["protocols"][f"S{k}"] = p
        ms = {v: p["calibration"][v]["m"] for v in VARIANTS_ON} if k else {}
        log(f"  S{k} {p['fuel']} m={ms} off " + " ".join(str(d['ratio_sim_obs']) for d in p["off"])
            + " | off_land " + " ".join(str(d['ratio_sim_obs']) for d in p["off_land"])
            + "".join(f" | {v} " + " ".join(str(d['ratio_sim_obs']) for d in p[v]) for v in VARIANTS_ON if k)
            + f" ({p['runtime_s']}s)")
    return row


def select(seed: int, exclude: list[tuple] = (), log=print):
    rng = np.random.default_rng(seed)
    root = ROOT / "data_country"
    for country, year, months, n in ps.VALIDATION_CASES:
        try:
            df = ps._load_viirs(country, year, root)
        except Exception as e:
            log(f"{country} {year}: load failed {e}")
            continue
        cands = ps._candidates(df, months, rng)
        got = 0
        for c in cands:
            if any(c["day"] == d and abs(c["clat"] - la) < 0.3 and abs(c["clon"] - lo) < 0.3
                   for d, la, lo in exclude):
                continue
            yield country, year, c, df
            got += 1
            if got >= n:
                break
        del df


def summarise(fires: list[dict]) -> dict:
    out = {}
    for P in ("S0", "S1", "S2"):
        for mode in OFF_MODES + VARIANTS_ON:
            if P == "S0" and mode not in OFF_MODES:
                continue
            for k in (1, 2, 3):
                ds = [d for f in fires for d in f["protocols"][P].get(mode, []) if d["day"] == k]
                r = [d["ratio_sim_obs"] for d in ds if d["ratio_sim_obs"]]
                h = [d["hit_pct"] for d in ds if d["hit_pct"] is not None]
                out[f"{P}_{mode}_day{k}"] = {
                    "n": len(r), "median_ratio_sim_obs": round(float(np.median(r)), 2) if r else None,
                    "within_factor_2_pct": round(100 * float(np.mean([0.5 <= x <= 2 for x in r])), 0) if r else None,
                    "median_abs_log2_ratio": round(float(np.median(np.abs(np.log2(r)))), 2) if r else None,
                    "median_hit_pct": round(float(np.median(h)), 1) if h else None}
    return out


def main(log=print, pause_s: float = 3.0):
    t_all = time.time()
    rep = {"protocol": __doc__, "sets": {}}
    prev = json.loads(OUT.read_text()) if OUT.exists() and "--resume" in __import__("sys").argv else {}
    orig_keys = []
    for name, seed in (("original_10", SEED_ORIG), ("new_10", SEED_NEW)):
        fires = []
        done = {(f["country"], f["day0"]): f for f in prev.get("sets", {}).get(name, {}).get("fires", [])}
        for country, year, c, df in select(seed, orig_keys if name == "new_10" else (), log):
            log(f"[{name}] {country} {year} {c['day'].date()} ({c['clat']:.3f}, {c['clon']:.3f})")
            if (country, str(c["day"].date())) in done:
                log("  (resumed from saved result)")
                r = done[(country, str(c["day"].date()))]
                fires.append(r)
                if name == "original_10":
                    orig_keys.append((c["day"], c["clat"], c["clon"]))
                continue
            try:
                r = run_fire(c, df, log)
            except Exception as e:
                log(f"  failed: {type(e).__name__}: {e}")
                continue
            r.update(country=country, year=year)
            fires.append(r)
            if name == "original_10":
                orig_keys.append((c["day"], c["clat"], c["clon"]))
            rep["sets"][name] = {"seed": seed, "n_fires": len(fires), "partial": True, "fires": fires}
            OUT.write_text(json.dumps(rep, indent=1, default=str))
            time.sleep(pause_s)
        rep["sets"][name] = {"seed": seed, "n_fires": len(fires), "summary": summarise(fires), "fires": fires}
        OUT.write_text(json.dumps(rep, indent=1, default=str))
    rep["runtime_s"] = round(time.time() - t_all, 1)
    OUT.write_text(json.dumps(rep, indent=1, default=str))
    return rep


if __name__ == "__main__":
    import sys
    r = main(log=lambda *a: print(*a, flush=True))
    for n, s in r["sets"].items():
        print(n, json.dumps(s["summary"], indent=0))
    sys.exit(0)
