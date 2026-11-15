"""Goal 4: routine (agricultural / managed, seasonal) burning vs fire anomaly.

Idea: every region has a fire calendar. Stubble and field burning, and managed savanna burning,
come back in the same weeks every year, in the same fields, as many small short fires. Dangerous
anomalies are out of season, far above the usual level for that week, or large/intense/persistent.

Deterministic engine (as in the project concept), per 0.25 deg region (~28 km) and date:
  1. Calendar baseline from all OTHER years (leave-one-year-out, so a year never explains itself):
     weekly burning cell-days per region, smoothed over +-1 week.
  2. Evidence:
     season_freq   share of other years with activity in this week +-1        (routine if high)
     z             (this week's activity - usual) / spread                      (anomaly if high)
     recurrence    share of today's burning cells that burned within +-14 days
                   of this date in >= 2 other years (same fields every year)   (routine if high)
     frp_ratio     FRP per detection vs the region's typical value            (anomaly if high)
     cluster       largest connected group of burning 0.05 deg cells today     (anomaly if large)
     persistence   longest run of consecutive burning days of a cell (<= date) (anomaly if long)
  3. Scores in [0, 1] and a label:
     "routine seasonal burning"  - in season, normal level, recurring fields, small fires
     "anomaly"                   - out of season with real activity, or far above normal, or big+intense
     "elevated seasonal activity"- in between
Data: VIIRS daily burning cells (2012+). The baseline uses only data available in the archive; when
a MODIS-era reconstruction (cache/viirs_equiv_daily.parquet) exists it can extend the calendar.
"""
from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy import ndimage

from .common import EPOCH, GRID, NY, cell_center

REGION = 5            # region = 5 x 5 cells of 0.05 deg = 0.25 deg
NWEEK = 53
MIN_ACTIVE = 3        # cell-days in a week to call a region-week "active"


def _year_doy(day):
    d = (EPOCH + np.asarray(day).astype("timedelta64[D]")).astype("datetime64[D]")
    y = d.astype("datetime64[Y]")
    return y.astype(int) + 1970, (d - y.astype("datetime64[D]")).astype(int)


@dataclass
class Calendar:
    weekly: pd.DataFrame          # index (rx, ry, year, week) -> cd (cell-days), frp, det
    years: np.ndarray
    act: pd.DataFrame             # day, gx, gy, n, frp sorted by cell key, then day
    cell_key: np.ndarray          # gx * NY + gy per row of act (sorted)
    frp_per_det: pd.Series        # region (rx, ry) -> typical FRP per detection

    # ------------------------------------------------------------------ build
    @classmethod
    def build(cls, act):
        a = act[["day", "gx", "gy", "n", "frp"]].copy()
        a["year"], a["doy"] = _year_doy(a["day"].values)
        a["week"] = np.minimum(a["doy"] // 7, NWEEK - 1)
        a["rx"], a["ry"] = a["gx"] // REGION, a["gy"] // REGION
        weekly = a.groupby(["rx", "ry", "year", "week"]).agg(cd=("n", "size"), frp=("frp", "sum"),
                                                             det=("n", "sum"))
        reg = a.groupby(["rx", "ry"]).agg(frp=("frp", "sum"), det=("n", "sum"))
        fpd = (reg["frp"] / reg["det"].clip(lower=1)).astype(np.float32)
        key = (a["gx"].values.astype(np.int64) * NY + a["gy"].values.astype(np.int64))
        order = np.lexsort((a["day"].values, key))
        a = a.iloc[order].reset_index(drop=True)
        return cls(weekly, np.unique(a["year"].values), a, key[order], fpd)

    # ------------------------------------------------------------------ evidence
    def _region_weeks(self, rx, ry):
        try:
            w = self.weekly.loc[(rx, ry)]
        except KeyError:
            return np.zeros((len(self.years), NWEEK)), np.zeros((len(self.years), NWEEK))
        M = np.zeros((len(self.years), NWEEK))
        F = np.zeros_like(M)
        yi = np.searchsorted(self.years, w.index.get_level_values(0))
        wi = w.index.get_level_values(1)
        M[yi, wi] = w["cd"].values
        F[yi, wi] = w["frp"].values
        return M, F

    def _cell_history(self, gx, gy):
        k = np.int64(gx) * NY + np.int64(gy)
        lo, hi = np.searchsorted(self.cell_key, [k, k + 1])
        return self.act.iloc[lo:hi]

    def evidence(self, lat, lon, date):
        day = int((np.datetime64(date) - EPOCH).astype(int))
        gx = int(np.floor((lon + 180) / GRID))
        gy = int(np.floor((lat + 90) / GRID))
        rx, ry = gx // REGION, gy // REGION
        year, doy = (int(v[0]) for v in _year_doy(np.array([day])))
        week = min(doy // 7, NWEEK - 1)
        M, F = self._region_weeks(rx, ry)
        yi = int(np.searchsorted(self.years, year))
        in_archive = yi < len(self.years) and self.years[yi] == year
        others = np.array([i for i in range(len(self.years)) if not (in_archive and i == yi)])
        wk = [(week + d) % NWEEK for d in (-1, 0, 1)]
        smooth = M[:, wk].mean(1)                                  # per year, +-1 week
        base = smooth[others]
        season_freq = float(np.mean((M[others][:, wk] >= MIN_ACTIVE).any(1))) if len(others) else 0.0
        obs = float(M[yi, week]) if in_archive else 0.0
        mean, std = float(base.mean()) if len(base) else 0.0, float(base.std()) if len(base) else 0.0
        # year-to-year variability is far above Poisson noise: floor the spread at 30% of the mean
        spread = max(std, 0.3 * mean, np.sqrt(mean) + 1.0)
        z = (obs - mean) / spread

        # today's burning cells in the region window (+ 1 region margin for clusters)
        x0, y0 = rx * REGION - REGION, ry * REGION - REGION
        size = 3 * REGION
        a = self.act
        today = a[(a["day"] == day) & (a["gx"] >= x0) & (a["gx"] < x0 + size) &
                  (a["gy"] >= y0) & (a["gy"] < y0 + size)]
        grid = np.zeros((size, size), bool)
        grid[today["gx"] - x0, today["gy"] - y0] = True
        lab, n = ndimage.label(grid, np.ones((3, 3)))
        cluster = int(np.bincount(lab.ravel())[1:].max()) if n else 0
        inside = today[(today["gx"] // REGION == rx) & (today["gy"] // REGION == ry)]
        cells = inside if len(inside) else today

        # recurrence: same cells burning around this date in other years; persistence up to today
        rec, pers = [], []
        for cx, cy in zip(cells["gx"].values, cells["gy"].values):
            h = self._cell_history(cx, cy)
            hy, hd = h["year"].values, h["doy"].values
            near = np.minimum(np.abs(hd - doy), 365 - np.abs(hd - doy)) <= 14
            other_years = np.unique(hy[near & (hy != year)])
            rec.append(len(other_years) >= 2)
            days = np.sort(h["day"].values[(h["day"].values <= day) & (h["day"].values > day - 60)])
            run, best = 0, 0
            for i, d in enumerate(days):
                run = run + 1 if i and d == days[i - 1] + 1 else 1
                best = max(best, run)
            pers.append(best)
        recurrence = float(np.mean(rec)) if rec else 0.0
        persistence = int(np.max(pers)) if pers else 0

        # one-day spike: today's burning cells vs a usual day in this window in other years
        usual_day = mean / 7.0
        day_ratio = float(len(inside) / max(usual_day, 0.5))
        det = float(cells["n"].sum())
        typical = float(self.frp_per_det.get((rx, ry), np.nan))
        frp_ratio = float(cells["frp"].sum() / det / typical) if det and typical > 0 else np.nan
        return {"date": str(np.datetime64(date)), "lat": lat, "lon": lon,
                "region": {"rx": rx, "ry": ry, "deg": REGION * GRID,
                           "center": [float(v) for v in cell_center(rx * REGION + 2, ry * REGION + 2)]},
                "burning_cells_today": int(len(cells)), "week_cell_days": obs,
                "baseline_mean": mean, "baseline_std": std, "baseline_years": int(len(others)),
                "z": float(z), "season_freq": season_freq, "usual_cells_per_day": usual_day,
                "day_ratio": day_ratio, "cells_in_region_today": int(len(inside)), "recurrence": recurrence,
                "frp_ratio": frp_ratio, "cluster_cells": cluster, "persistence_days": persistence,
                "calendar": smooth.tolist(), "calendar_years": self.years.tolist(),
                "typical_weeks": [int(w) for w in np.argsort(-M[others].mean(0))[:6]] if len(others) else []}

    # ------------------------------------------------------------------ decision
    @staticmethod
    def decide(e):
        clip = lambda v: float(np.clip(v, 0, 1))
        fr = e["frp_ratio"] if np.isfinite(e["frp_ratio"]) else 1.0
        routine = np.mean([
            clip(e["season_freq"]),                              # expected window
            clip(1 - (e["z"] - 1) / 3),                          # normal level
            clip(e["recurrence"] / 0.5),                         # same fields every year
            clip(1 - (fr - 1.2) / 1.8),                          # small fires
            clip(1 - (e["cluster_cells"] - 6) / 20),             # scattered, not one big front
            clip(1 - (e["persistence_days"] - 2) / 5),           # short-lived
        ])
        out_of_season = e["season_freq"] <= 0.25 and e["burning_cells_today"] >= 3
        # how strongly this place/time looks like a recurring burning calendar (same fields, same weeks)
        habitual = clip(min(e["season_freq"], e["recurrence"]))
        spike = clip((e["day_ratio"] - 4) / 6) * (1.0 if e["cells_in_region_today"] >= 10 else 0.0)
        anomaly = max(
            clip((e["z"] - 2) / 4) * (1 - 0.6 * habitual),         # far above this week's usual level
            1.0 if out_of_season else 0.0,                          # fire where/when it normally doesn't burn
            spike * (1 - 0.5 * habitual),                           # explosive one-day jump
            clip((e["cluster_cells"] - 15) / 30) * clip((fr - 1.0) / 1.5 + 0.5) * (1 - habitual),
            clip((e["persistence_days"] - 4) / 6) * clip(e["cluster_cells"] / 10) * (1 - habitual),
        )
        if e["burning_cells_today"] == 0 and e["week_cell_days"] == 0:
            label = "no fire detected"
        elif anomaly >= 0.6:
            label = "anomaly"
        elif routine >= 0.6 and anomaly < 0.35:
            label = "routine seasonal burning"
        else:
            label = "elevated seasonal activity"
        reasons = []
        if out_of_season:
            reasons.append("out of season (active in <=25% of other years this week)")
        if spike > 0:
            reasons.append(f"{e['day_ratio']:.0f}x more burning cells today than a usual day in this window")
        if e["z"] >= 2:
            reasons.append(f"{e['z']:.1f} std above the usual level for this week")
        if e["season_freq"] >= 0.6:
            reasons.append(f"usual burning window (active in {e['season_freq']:.0%} of other years)")
        if e["recurrence"] >= 0.3:
            reasons.append(f"{e['recurrence']:.0%} of cells burn around this date in other years too")
        if e["cluster_cells"] >= 15:
            if min(e["season_freq"], e["recurrence"]) >= 0.6:
                reasons.append(f"many recurring fields burning at once ({e['cluster_cells']} connected cells)")
            else:
                reasons.append(f"large connected fire ({e['cluster_cells']} cells ~{e['cluster_cells'] * 30} km2)")
        if e["persistence_days"] >= 5:
            reasons.append(f"cells burning {e['persistence_days']} days in a row")
        if np.isfinite(e["frp_ratio"]) and e["frp_ratio"] >= 2:
            reasons.append(f"FRP per detection {e['frp_ratio']:.1f}x the regional norm")
        return {"label": label, "routine_score": round(float(routine), 3),
                "anomaly_score": round(float(anomaly), 3), "reasons": reasons}

    def classify(self, lat, lon, date):
        e = self.evidence(lat, lon, date)
        return {**e, **self.decide(e)}


# Reference events for a sanity check (approximate locations; labels from public reporting).
KNOWN_EVENTS = [
    # anomalies: extreme / out-of-season wildfires
    ("Australia Black Summer, NSW south coast", -35.6, 150.1, "2019-12-31", "anomaly"),
    ("Australia Black Summer, Kangaroo Island", -35.85, 137.0, "2020-01-04", "anomaly"),
    ("Australia Black Summer, Gospers Mountain", -33.2, 150.6, "2019-12-15", "anomaly"),
    ("Canada 2023, Quebec", 48.9, -76.2, "2023-06-05", "anomaly"),
    ("Canada 2023, Donnie Creek BC", 57.6, -121.6, "2023-06-10", "anomaly"),
    ("Canada 2016, Fort McMurray", 56.6, -111.1, "2016-05-05", "anomaly"),
    ("USA 2021, Dixie fire", 40.1, -121.1, "2021-08-05", "anomaly"),
    ("USA 2020, Oregon Labor Day fires", 44.8, -122.2, "2020-09-09", "anomaly"),
    ("USA 2018, Camp fire", 39.8, -121.5, "2018-11-09", "anomaly"),
    ("Brazil 2020, Pantanal", -17.6, -57.0, "2020-09-12", "anomaly"),
    ("Brazil 2024, Pantanal (June, early)", -18.5, -57.3, "2024-06-20", "anomaly"),
    ("Brazil 2024, Sao Paulo state (August)", -21.2, -48.3, "2024-08-24", "anomaly"),
    # routine seasonal / agricultural burning
    ("India Punjab stubble burning", 30.6, 75.6, "2023-11-05", "routine"),
    ("India Punjab stubble burning", 30.4, 75.2, "2021-11-02", "routine"),
    ("India Haryana stubble burning", 29.6, 76.3, "2022-11-01", "routine"),
    ("India Punjab wheat residue (spring)", 30.7, 75.5, "2022-04-28", "routine"),
    ("India Madhya Pradesh wheat residue", 23.2, 77.3, "2023-04-20", "routine"),
    ("USA Kansas Flint Hills prescribed burning", 38.6, -96.5, "2022-04-10", "routine"),
    ("USA Louisiana sugarcane harvest burning", 30.0, -91.6, "2021-11-05", "routine"),
    ("USA Arkansas rice-stubble burning", 34.8, -91.3, "2022-10-15", "routine"),
    ("Australia Kimberley early-dry-season burning", -16.4, 126.2, "2022-06-10", "routine"),
    ("Australia Arnhem Land early-dry-season burning", -12.6, 134.0, "2021-06-15", "routine"),
]


def check_known(cal, events=None):
    rows = []
    for name, lat, lon, date, truth in (events or KNOWN_EVENTS):
        r = cal.classify(lat, lon, date)
        if r["cells_in_region_today"] == 0:  # reference points are approximate: snap to the strongest
            day = int((np.datetime64(date) - EPOCH).astype(int))  # burning cell within 1 deg, +-5 days
            a = cal.act
            gx, gy = np.floor((lon + 180) / GRID), np.floor((lat + 90) / GRID)
            near = a[(abs(a["day"] - day) <= 5) & (abs(a["gx"] - gx) <= 20) & (abs(a["gy"] - gy) <= 20)]
            if len(near):
                b = near.sort_values("frp").iloc[-1]
                la, lo = cell_center(b["gx"], b["gy"])
                date = str(EPOCH + np.timedelta64(int(b["day"]), "D"))
                r = cal.classify(float(la), float(lo), date)
        pred = {"anomaly": "anomaly", "routine seasonal burning": "routine"}.get(r["label"], r["label"])
        rows.append({"event": name, "date": date, "expected": truth, "label": r["label"],
                     "ok": pred == truth, "z": round(r["z"], 1), "season_freq": round(r["season_freq"], 2),
                     "recurrence": round(r["recurrence"], 2), "cluster": r["cluster_cells"],
                     "persist": r["persistence_days"], "routine": r["routine_score"],
                     "anomaly": r["anomaly_score"], "cells": r["burning_cells_today"],
                     "day_ratio": round(r["day_ratio"], 1)})
    return pd.DataFrame(rows)


def load_calendar(verbose=True):
    """Calendar from the fast VIIRS daily cache, itself cached in cache/seasonal_calendar.pkl
    (rebuilt when the daily table changes)."""
    import pickle
    import time
    from .common import CACHE
    from .datasets import load_viirs_daily
    t = time.time()
    act = load_viirs_daily()
    tag = (len(act), int(act["day"].max()), float(act["frp"].sum()))
    f = CACHE / "seasonal_calendar.pkl"
    if f.exists():
        try:
            with open(f, "rb") as fh:
                saved_tag, cal = pickle.load(fh)
            if saved_tag == tag:
                if verbose:
                    print(f"[seasonal] calendar loaded from cache ({time.time() - t:.0f}s)", flush=True)
                return cal
        except Exception:
            pass
    cal = Calendar.build(act)
    tmp = f.with_suffix(".tmp")
    with open(tmp, "wb") as fh:
        pickle.dump((tag, cal), fh, protocol=pickle.HIGHEST_PROTOCOL)
    tmp.replace(f)
    if verbose:
        print(f"[seasonal] calendar built from {len(act):,} VIIRS cell-days ({time.time() - t:.0f}s)", flush=True)
    return cal


# Independent events, added AFTER the rules were fixed (not used for tuning).
HOLDOUT_EVENTS = [
    ("Canada 2023, Yellowknife NWT", 62.6, -114.4, "2023-08-15", "anomaly"),
    ("Canada 2021, Lytton BC", 50.2, -121.6, "2021-07-01", "anomaly"),
    ("USA 2020, August Complex CA", 39.8, -122.9, "2020-09-10", "anomaly"),
    ("Australia 2019, NSW mid-north coast", -31.4, 152.6, "2019-11-09", "anomaly"),
    ("Canada 2024, Jasper AB", 52.9, -118.1, "2024-07-24", "anomaly"),
    ("USA 2017, Thomas fire CA", 34.45, -119.3, "2017-12-10", "anomaly"),
    ("India Punjab stubble burning 2019", 30.5, 75.7, "2019-11-05", "routine"),
    ("India Punjab stubble burning 2020", 30.8, 75.4, "2020-11-03", "routine"),
    ("USA Florida sugarcane burning", 26.6, -80.8, "2022-01-15", "routine"),
    ("USA Kansas Flint Hills 2019", 38.4, -96.6, "2019-04-12", "routine"),
    ("Australia NT early-dry-season burning", -12.8, 131.5, "2023-05-25", "routine"),
    ("India Haryana stubble burning 2021", 29.4, 76.5, "2021-11-08", "routine"),
]
