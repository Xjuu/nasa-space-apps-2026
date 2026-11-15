#!/usr/bin/env python3
"""25-year (2000-2025) fire series for selected regions: raw MODIS, raw VIIRS and HARMONIZED.

Stage 1 (`monthly`): for every (country, year) aggregate real MODIS (Terra+Aqua), real VIIRS S-NPP,
  real NOAA-20 and the MODIS->VIIRS-equivalent reconstruction (equifire.harmonize, viirs_sats=1) to
  monthly rows per region: burning 0.05 deg cell-days, detections, FRP sum (MW). Resumable, one
  parquet per (country, year) in cache/harmonized_series/monthly/. Also saves the reconstructed
  rows of years < 2012 per country (cache/harmonized_series/recon/) so the official
  cache/viirs_equiv_daily.parquet can be extended without computing twice.
Stage 2 (`report`): tidy CSV, headline JSON (discontinuity ratios, trend tests, bridge check), charts.

    .venv/bin/python scripts/harmonized_series.py monthly [--years 2000 2001 ...] [--countries Brazil ...]
    .venv/bin/python scripts/harmonized_series.py merge        # extend cache/viirs_equiv_daily.parquet
    .venv/bin/python scripts/harmonized_series.py report

Series definitions (documented in the headline JSON):
  modis_raw        Terra + Aqua, as downloaded (1 km pixels).
  viirs_snpp_raw   S-NPP only (375 m, from 2012-01-20).
  viirs_n20_raw    NOAA-20 only (from 2018-04).
  viirs_all_raw    S-NPP + NOAA-20 (what a user who simply downloads "VIIRS" sees).
  raw_splice       the naive record: MODIS for 2000-2011, VIIRS (S-NPP + NOAA-20) from 2012.
  recon_from_modis MODIS->VIIRS(S-NPP)-equivalent model applied to every year with MODIS data.
  harmonized       reconstruction before 2012-01-20, real S-NPP from then on (one consistent record
                   that mimics ONE VIIRS satellite; NOAA-20 is not added so 2018 does not add a step).
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from equifire.common import CACHE, EPOCH, GRID, cell_center, decode_key  # noqa: E402

WORK = CACHE / "harmonized_series"
MON = WORK / "monthly"
RECON = WORK / "recon"
REPORTS = ROOT / "reports"
COUNTRIES = ["Brazil", "Australia", "India", "United_States", "Canada"]

# region -> (country file, bbox lat0, lat1, lon0, lon1) ; bbox None = whole country file
REGIONS = {
    "Brazil": ("Brazil", None),
    "Australia": ("Australia", None),
    "India": ("India", None),
    "United_States": ("United_States", None),
    "Canada": ("Canada", None),
    "amazon_arc": ("Brazil", (-14.0, -5.0, -62.0, -45.0)),
    "australia_north_savanna": ("Australia", (-19.0, -11.0, 122.0, 142.0)),
    "canada_boreal": ("Canada", (50.0, 60.0, -125.0, -90.0)),
    "punjab_haryana": ("India", (29.0, 32.5, 73.8, 77.5)),
    "five_countries": ("*", None),   # sum of the five fully archived countries
}
if os.environ.get("HS_REGIONS"):   # testing on a subset
    REGIONS = {k: v for k, v in REGIONS.items() if k in os.environ["HS_REGIONS"].split(",")}
LABELS = {
    "Brazil": "Brazil", "Australia": "Australia", "India": "India", "United_States": "United States",
    "Canada": "Canada", "amazon_arc": "Amazon arc of deforestation (Brazil, 14-5 S, 62-45 W)",
    "australia_north_savanna": "Northern Australia savanna (19-11 S, 122-142 E)",
    "canada_boreal": "Canadian boreal (50-60 N, 125-90 W)",
    "punjab_haryana": "Punjab-Haryana plains, Indian side (29-32.5 N, 73.8-77.5 E)",
    "five_countries": "Five countries (Brazil, Australia, India, United States, Canada)",
}
MEASURES = {"cell_days": ("Burning 0.05 deg cell-days", "cell-days"),
            "detections": ("Fire detections", "detections"),
            "frp_mw": ("Fire radiative power, sum", "MW")}
SERIES_LABELS = {
    "modis_raw": "MODIS raw (Terra + Aqua, 1 km)",
    "viirs_snpp_raw": "VIIRS S-NPP raw (375 m)",
    "viirs_n20_raw": "VIIRS NOAA-20 raw (375 m)",
    "viirs_all_raw": "VIIRS raw (S-NPP + NOAA-20)",
    "raw_splice": "Raw splice (MODIS to 2011, VIIRS from 2012)",
    "recon_from_modis": "MODIS reconstructed to VIIRS-equivalent (all years)",
    "harmonized": "Harmonized (reconstructed before 2012-01-20, real S-NPP after)",
    "harmonized_cal": "Harmonized + overlap calibration (reconstruction divided by its 2012-2020 ratio to real S-NPP)",
}
VIIRS_START = int((np.datetime64("2012-01-20") - EPOCH).astype(int))
TERRA_START, AQUA_START, N20_START = "2000-11-01", "2002-07-04", "2018-04-01"
YEARS = list(range(2000, 2026))
CAL = {}


# ------------------------------------------------------------------------------ stage 1
def _cell_day(df):
    """Unique (day, cell) table with summed n / frp / area (all passes and satellites merged)."""
    return df.groupby(["day", "gx", "gy"], sort=False).agg(
        n=("n", "sum"), frp=("frp_sum", "sum")).reset_index()


def _monthly(cd, regions, group, day_filter=None):
    rows = []
    if cd is None or not len(cd):
        return rows
    if day_filter is not None:
        cd = cd[day_filter(cd["day"].values)]
        if not len(cd):
            return rows
    lat, lon = cell_center(cd["gx"].values, cd["gy"].values)
    mon = (EPOCH + cd["day"].values.astype("timedelta64[D]")).astype("datetime64[M]")
    ym = mon.astype(int)  # months since 1970-01
    for reg, bbox in regions.items():
        m = np.ones(len(cd), bool) if bbox is None else \
            (lat >= bbox[0]) & (lat < bbox[1]) & (lon >= bbox[2]) & (lon < bbox[3])
        if not m.any():
            continue
        g = pd.DataFrame({"ym": ym[m], "n": cd["n"].values[m], "frp": cd["frp"].values[m]}) \
            .groupby("ym").agg(cell_days=("n", "size"), detections=("n", "sum"), frp_mw=("frp", "sum"))
        g = g.reset_index()
        g["year"] = (g["ym"] // 12 + 1970).astype(int)
        g["month"] = (g["ym"] % 12 + 1).astype(int)
        g["region"], g["group"] = reg, group
        rows.append(g.drop(columns="ym"))
    return rows


def one_task(country, year, nthread=6, recon=True):
    """All groups for one (country, year); writes monthly parquet (+ recon rows if year < 2012)."""
    out = MON / f"{country}_{year}.parquet"
    if out.exists():
        return out, 0.0
    from equifire import harmonize as h
    t0 = time.time()
    regions = {r: b for r, (c, b) in REGIONS.items() if c == country}
    rows = []
    mod = h.read_cells("modis", [year], [country])
    snpp = h.read_cells("viirs-snpp", [year], [country])
    n20 = h.read_cells("viirs-jpss1", [year], [country])
    if mod is not None:
        mod = mod[mod["sat"].isin(["T", "A"])]
        rows += _monthly(_cell_day(mod), regions, "modis_raw")
    cd_s = _cell_day(snpp) if snpp is not None else None
    cd_n = _cell_day(n20) if n20 is not None else None
    rows += _monthly(cd_s, regions, "viirs_snpp_raw")
    rows += _monthly(cd_n, regions, "viirs_n20_raw")
    parts = [p for p in (snpp, n20) if p is not None]
    if parts:
        rows += _monthly(_cell_day(pd.concat(parts, ignore_index=True)), regions, "viirs_all_raw")
    del mod, snpp, n20
    # reconstruction (needs MODIS of year-1..year+1)
    rec = None
    if recon or year < 2012:
        _, meta = h.load_models(nthread)
        rec = h._apply_year(year, [country], 1, meta["threshold"], nthread)
    if rec is not None and len(rec):
        base = rec[["day", "gx", "gy", "n", "frp"]]
        rows += _monthly(base, regions, "recon_all")
        pre = lambda d: d < VIIRS_START          # noqa: E731
        post = lambda d: d >= VIIRS_START        # noqa: E731
        rows += _monthly(base, regions, "recon_pre", pre)
        rows += _monthly(base, regions, "recon_post", post)
        if year < 2012:
            RECON.mkdir(parents=True, exist_ok=True)
            keep = rec[rec["day"] < VIIRS_START] if year == 2011 else rec
            keep.to_parquet(RECON / f"{country}_{year}.parquet", index=False)
    MON.mkdir(parents=True, exist_ok=True)
    df = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(
        columns=["cell_days", "detections", "frp_mw", "year", "month", "region", "group"])
    df.to_parquet(out, index=False)
    return out, time.time() - t0


def cmd_monthly(a):
    os.environ.setdefault("OMP_NUM_THREADS", str(a.threads))
    countries = a.countries or COUNTRIES
    years = a.years or YEARS
    for y in years:
        for c in countries:
            out, dt = one_task(c, y, a.threads, recon=y not in (a.skip_recon_years or []))
            print(f"[series] {c} {y}: {'cached' if dt == 0 else f'{dt:.0f}s'}", flush=True)


def cmd_merge(a):
    """Extend cache/viirs_equiv_daily.parquet with the per-country reconstructions of 2000-2007."""
    from equifire import harmonize as h
    yrs = a.years or list(range(2000, 2008))
    parts = []
    for y in yrs:
        for c in COUNTRIES:
            f = RECON / f"{c}_{y}.parquet"
            if f.exists():
                parts.append(pd.read_parquet(f))
        print(f"[merge] {y}: {sum(len(p) for p in parts):,} rows so far", flush=True)
    new = pd.concat(parts, ignore_index=True).sort_values("p_viirs", ascending=False)
    new = new.drop_duplicates(["day", "gx", "gy"]).assign(source="reconstructed")
    old = pd.read_parquet(h.OUT)
    oy = (EPOCH + old["day"].values.astype("timedelta64[D]")).astype("datetime64[Y]").astype(int) + 1970
    old = old[~np.isin(oy, yrs)]
    df = pd.concat([old, new], ignore_index=True).sort_values(["day", "gx", "gy"], ignore_index=True)
    df.to_parquet(h.OUT, index=False)
    yy = (EPOCH + df["day"].values.astype("timedelta64[D]")).astype("datetime64[Y]").astype(int) + 1970
    meta_f = Path(str(h.OUT).replace(".parquet", ".json"))
    info = json.loads(meta_f.read_text())
    info["rows"] = len(df)
    info["rows_per_year"] = {int(k): int(v) for k, v in pd.Series(yy).value_counts().sort_index().items()}
    info["note_2000_2007"] = ("2000-2007 rows added by scripts/harmonized_series.py (per-country apply, "
                              "viirs_sats=1, reg_scale applied before rounding like 2022-2025); Terra only before "
                              "2002-07-04 (Aqua from then; 2000 = Nov-Dec only), so recall is lower there")
    meta_f.write_text(json.dumps(info, indent=1))
    print(f"[merge] wrote {h.OUT} ({len(df):,} rows)")


# ------------------------------------------------------------------------------ stage 2
def load_monthly():
    fs = sorted(MON.glob("*.parquet"))
    if not fs:
        raise SystemExit("no monthly parquets; run: scripts/harmonized_series.py monthly")
    df = pd.concat([pd.read_parquet(f) for f in fs], ignore_index=True)
    df["period"] = df["year"] * 100 + df["month"]
    return df


def add_total(raw):
    """Region 'five_countries' = sum of the 5 country series. Reconstruction groups are only summed for
    years in which all five countries have one (some overlap years are reconstructed for a subset)."""
    c5 = raw[raw.region.isin(COUNTRIES)]
    tot = c5.groupby(["group", "year", "month"])[["cell_days", "detections", "frp_mw"]].sum().reset_index()
    ncountry = c5.groupby(["group", "year", "month"])["region"].nunique().reset_index(name="nc")
    tot = tot.merge(ncountry, on=["group", "year", "month"])
    full = tot["nc"] == len(COUNTRIES)
    rec = tot["group"].str.startswith("recon")
    tot = tot[~rec | full].drop(columns="nc")
    tot["region"] = "five_countries"
    tot["period"] = tot["year"] * 100 + tot["month"]
    return pd.concat([raw, tot], ignore_index=True)


def build_tidy(raw):
    """Tidy table: region, series, year, month, measure columns."""
    piv = {}
    for g, s in raw.groupby("group"):
        piv[g] = s.groupby(["region", "year", "month"])[["cell_days", "detections", "frp_mw"]].sum()
    keys = pd.MultiIndex.from_tuples(sorted(set().union(*[set(p.index) for p in piv.values()])),
                                     names=["region", "year", "month"])
    z = lambda g: piv.get(g, pd.DataFrame(0.0, index=keys, columns=["cell_days", "detections", "frp_mw"])) \
        .reindex(keys).fillna(0.0)  # noqa: E731
    out = {}
    out["modis_raw"] = z("modis_raw")
    out["viirs_snpp_raw"] = z("viirs_snpp_raw")
    out["viirs_n20_raw"] = z("viirs_n20_raw")
    out["viirs_all_raw"] = z("viirs_all_raw")
    yr = keys.get_level_values("year").values
    mo = keys.get_level_values("month").values
    before = pd.Series(yr * 100 + mo < 201201, index=keys)
    out["raw_splice"] = pd.DataFrame(np.where(before.values[:, None], out["modis_raw"].values,
                                              out["viirs_all_raw"].values), index=keys, columns=out["modis_raw"].columns)
    out["recon_from_modis"] = z("recon_all")
    out["harmonized"] = z("recon_pre") + z("viirs_snpp_raw")
    out["recon_post"] = z("recon_post")      # bridge only
    # overlap calibration: per region and measure, median over 2012-2020 of annual (reconstruction from
    # MODIS / real S-NPP); 2021-2025 stay untouched, so they are an honest check of the correction
    cal = {}
    ann = lambda d: d[(d.index.get_level_values("year") >= 2012) & (d.index.get_level_values("year") <= 2020)] \
        .groupby(["region", "year"]).sum()  # noqa: E731
    have = raw[raw.group == "recon_post"].groupby(["region", "year"]).size().index   # years with a reconstruction
    A_r, A_s = ann(out["recon_post"]), ann(out["viirs_snpp_raw"])
    A_r, A_s = A_r[A_r.index.isin(have)], A_s[A_s.index.isin(have)]
    ra = (A_r / A_s.replace(0, np.nan)).groupby("region").median()
    rr = ra.reindex(keys.get_level_values("region")).values
    out["harmonized_cal"] = z("recon_pre") / np.where(np.isfinite(rr), rr, 1.0) + z("viirs_snpp_raw")
    CAL.update({r: {c: float(ra.loc[r, c]) for c in ra.columns} for r in ra.index})
    frames = []
    for s, d in out.items():
        d = d.reset_index()
        d.insert(1, "series", s)
        frames.append(d)
    t = pd.concat(frames, ignore_index=True)
    # availability masks: sensor not yet flying -> NaN rather than 0
    per = t["year"] * 100 + t["month"]
    t.loc[(t.series == "modis_raw") & (per < 200011), ["cell_days", "detections", "frp_mw"]] = np.nan
    t.loc[(t.series == "recon_from_modis") & (per < 200011), ["cell_days", "detections", "frp_mw"]] = np.nan
    t.loc[(t.series == "viirs_snpp_raw") & (per < 201201), ["cell_days", "detections", "frp_mw"]] = np.nan
    t.loc[(t.series == "viirs_all_raw") & (per < 201201), ["cell_days", "detections", "frp_mw"]] = np.nan
    t.loc[(t.series == "viirs_n20_raw") & (per < 201804), ["cell_days", "detections", "frp_mw"]] = np.nan
    t.loc[(t.series == "harmonized_cal") & (per < 200011), ["cell_days", "detections", "frp_mw"]] = np.nan
    t.loc[(t.series == "harmonized") & (per < 200011), ["cell_days", "detections", "frp_mw"]] = np.nan
    t.loc[(t.series == "raw_splice") & (per < 200011), ["cell_days", "detections", "frp_mw"]] = np.nan
    return t.sort_values(["region", "series", "year", "month"], ignore_index=True)


def yearly(t):
    """Annual sums; a year is NaN unless all 12 months exist for that series (2000 = Nov-Dec only for
    MODIS, NOAA-20 2018 = Apr-Dec). Jan 2012 of real VIIRS starts on the 20th (flagged in the JSON)."""
    g = t.groupby(["region", "series", "year"])[list(MEASURES)]
    y = g.sum(min_count=1).reset_index()
    cnt = g.count().reset_index()
    for m in MEASURES:
        y.loc[cnt[m].values < 12, m] = np.nan
    return y


def _ols(y, X, lags=2):
    """OLS with classical and Newey-West (Bartlett, `lags`) standard errors."""
    n, k = X.shape
    XtXi = np.linalg.inv(X.T @ X)
    b = XtXi @ X.T @ y
    e = y - X @ b
    s2 = e @ e / (n - k)
    se = np.sqrt(np.diag(XtXi) * s2)
    S = (X * e[:, None]).T @ (X * e[:, None])
    for L in range(1, lags + 1):
        w = 1 - L / (lags + 1)
        G = (X[L:] * e[L:, None]).T @ (X[:-L] * e[:-L, None])
        S += w * (G + G.T)
    V = XtXi @ S @ XtXi * n / (n - k)
    se_hac = np.sqrt(np.diag(V))
    r2 = 1 - (e @ e) / ((y - y.mean()) @ (y - y.mean()))
    from scipy import stats
    p = 2 * stats.t.sf(np.abs(b / se), n - k)
    p_hac = 2 * stats.t.sf(np.abs(b / se_hac), n - k)
    return b, se, se_hac, p, p_hac, float(r2)


def trend_models(ys, vals, y0, y1, step_years=(2012, 2018)):
    """log(annual value) = a + b*(year-mid) [+ c12*1(year>=2012)] [+ c18*1(year>=2018)]. Returns dict."""
    sel = (ys >= y0) & (ys <= y1) & np.isfinite(vals) & (vals > 0)
    yv, v = ys[sel].astype(float), np.log(vals[sel])
    if len(yv) < 8:
        return None
    mid = yv.mean()
    base = [np.ones(len(yv)), yv - mid]
    res = {"years": [int(yv.min()), int(yv.max())], "n_years": int(len(yv))}
    specs = {"trend_only": [], "trend_step2012": [2012], "trend_step2012_2018": [2012, 2018]}
    for name, steps in specs.items():
        X = np.column_stack(base + [(yv >= s).astype(float) for s in steps])
        b, se, seh, p, ph, r2 = _ols(v, X)
        r = {"trend_pct_per_year": float(100 * (np.exp(b[1]) - 1)), "trend_se_pct": float(100 * seh[1]),
             "trend_t_hac": float(b[1] / seh[1]), "trend_p_hac": float(ph[1]), "trend_p_classical": float(p[1]),
             "r2": r2}
        for j, s in enumerate(steps):
            r[f"step{s}_ratio"] = float(np.exp(b[2 + j]))
            r[f"step{s}_t_hac"] = float(b[2 + j] / seh[2 + j])
            r[f"step{s}_p_hac"] = float(ph[2 + j])
        res[name] = r
    return res


def trend_verdict(ts):
    """Plain-language verdict from the model fits of one series (log annual values, HAC t-tests)."""
    if ts is None:
        return "insufficient data"
    t0, t1 = ts["trend_only"], ts["trend_step2012"]
    sig0, sig1 = t0["trend_p_hac"] < 0.05, t1["trend_p_hac"] < 0.05
    step = t1["step2012_p_hac"] < 0.05
    d = t0["trend_pct_per_year"] - t1["trend_pct_per_year"]
    if not sig0 and not sig1:
        return ("no significant trend, with or without a 2012 step" +
                ("; a significant 2012 step is present (artifact)" if step else ""))
    if sig0 and step and (not sig1 or abs(d) > 0.5 * abs(t0["trend_pct_per_year"])):
        return (f"apparent trend {t0['trend_pct_per_year']:+.1f}%/yr is largely an artifact of the 2012 step "
                f"(x{t1['step2012_ratio']:.2f}); with the step modelled it is {t1['trend_pct_per_year']:+.1f}%/yr"
                f"{' (not significant)' if not sig1 else ''}")
    if step:
        return (f"trend {t1['trend_pct_per_year']:+.1f}%/yr survives, but a significant 2012 step "
                f"(x{t1['step2012_ratio']:.2f}) remains")
    return (f"no significant 2012 step (x{t1['step2012_ratio']:.2f}); trend {t0['trend_pct_per_year']:+.1f}%/yr "
            f"({'significant' if sig0 else 'not significant'}), {t1['trend_pct_per_year']:+.1f}%/yr with the step "
            f"({'significant' if sig1 else 'not significant'})")


def cmd_report(a):
    raw = add_total(load_monthly())
    t = build_tidy(raw)
    REPORTS.mkdir(exist_ok=True)
    t_out = t[t.series != "recon_post"].copy()
    t_out = t_out.rename(columns={"cell_days": "burning_cell_days", "detections": "detections",
                                  "frp_mw": "frp_sum_mw"})
    t_out.round({"frp_sum_mw": 1}).to_csv(REPORTS / "harmonized_series.csv", index=False)
    print(f"[report] reports/harmonized_series.csv  {len(t_out):,} rows")
    Y = yearly(t)
    arr = lambda region, series, m: (Y[(Y.region == region) & (Y.series == series)].set_index("year")[m]  # noqa: E731
                                     .reindex(YEARS).values.astype(float))
    head = {"generated": time.strftime("%Y-%m-%d %H:%M"),
            "definitions": SERIES_LABELS,
            "eras": {"terra_only": [TERRA_START, "2002-07-03"], "terra_aqua": [AQUA_START, "2025-12-31"],
                     "snpp_start": "2012-01-20", "n20_start": N20_START,
                     "reconstructed": ["2000-11-01", "2012-01-19"]},
            "regions": {r: {"label": LABELS[r], "country_file": REGIONS[r][0], "bbox_lat0_lat1_lon0_lon1": REGIONS[r][1]}
                        for r in REGIONS},
            "measures": {m: {"label": l, "unit": u} for m, (l, u) in MEASURES.items()},
            "harmonized_choice": ("harmonized = reconstructed VIIRS(S-NPP)-equivalent for days before 2012-01-20 "
                                  "(models/harmonize_*.json, viirs_sats=1) + REAL S-NPP afterwards. NOAA-20 is not "
                                  "added, so the 2018 second-satellite jump is not part of the record."),
            "overlap_calibration_ratio_2012_2020": CAL,
            "model_metrics_heldout_2021_2025": None, "discontinuity": {}, "trend": {}, "bridge": {}}
    try:
        mm = json.loads((ROOT / "models" / "harmonize_metrics.json").read_text())["test"]["by_view"]["snpp_only"]
        head["model_metrics_heldout_2021_2025"] = {
            k: {kk: round(vv, 3) for kk, vv in mm[k].items() if isinstance(vv, float)
                and kk in ("precision", "recall", "iou", "pred_cells_over_true", "total_n_ratio", "total_frp_ratio")}
            for k in ("model", "modis_raw")}
    except Exception:
        pass
    for reg in REGIONS:
        head["discontinuity"][reg], head["trend"][reg], head["bridge"][reg] = {}, {}, {}
        for m in MEASURES:
            A = {s: arr(reg, s, m) for s in SERIES_LABELS}
            ix = {y: i for i, y in enumerate(YEARS)}
            g = lambda s, y: A[s][ix[y]]  # noqa: E731
            w = lambda s, ys: np.nanmean([A[s][ix[y]] for y in ys])  # noqa: E731
            d = {}
            for s in ("raw_splice", "harmonized", "harmonized_cal"):
                d[s] = {"first_viirs_year_2012_over_2011": float(g(s, 2012) / g(s, 2011)),
                        "mean_2012_2014_over_mean_2009_2011": float(w(s, (2012, 2013, 2014)) / w(s, (2009, 2010, 2011))),
                        "n20_year_2018_over_2017": float(g(s, 2018) / g(s, 2017)),
                        "mean_2018_2019_over_mean_2016_2017": float(w(s, (2018, 2019)) / w(s, (2016, 2017)))}
            d["modis_raw_2012_over_2011"] = float(g("modis_raw", 2012) / g("modis_raw", 2011))
            d["viirs_snpp_raw_2012_over_modis_raw_2011"] = float(g("viirs_snpp_raw", 2012) / g("modis_raw", 2011))
            head["discontinuity"][reg][m] = d
            ys = np.array(YEARS)
            head["trend"][reg][m] = {}
            for s in ("raw_splice", "harmonized", "harmonized_cal", "modis_raw"):
                for tag, (y0, y1) in (("2003_2025", (2003, 2025)), ("2001_2025", (2001, 2025))):
                    ts = trend_models(ys, A[s], y0, y1)
                    if ts is not None:
                        ts["verdict"] = trend_verdict(ts) if s != "modis_raw" else None
                    head["trend"][reg][m][f"{s}|{tag}"] = ts
            # bridge: reconstruction vs real S-NPP on the overlap (days >= 2012-01-20 only)
            sub = t[(t.region == reg) & (t.year >= 2012)]
            rp = sub[sub.series == "recon_post"].set_index(["year", "month"])[m]
            rs = sub[sub.series == "viirs_snpp_raw"].set_index(["year", "month"])[m]
            have_y = sorted(set(raw[(raw.group == "recon_post") & (raw.region == reg)].year))
            ya = rp.groupby("year").sum().reindex(have_y)
            yb = rs.groupby("year").sum().reindex(have_y)
            ratio = (ya / yb).replace([np.inf], np.nan)
            mm_ = pd.concat([rp.rename("r"), rs.rename("v")], axis=1).dropna()
            mm_ = mm_[mm_.index.get_level_values(0).isin(have_y)]
            ho = [y for y in have_y if y >= 2021]
            fit = [y for y in have_y if y <= 2020]
            hom = mm_[mm_.index.get_level_values(0) >= 2021]
            cor = lambda u, v: round(float(np.corrcoef(u, v)[0, 1]), 3) if len(u) > 2 else None  # noqa: E731
            b = {"years_with_reconstruction": [int(y) for y in have_y],
                 "ratio_by_year": {int(y): round(float(v), 3) for y, v in ratio.items()},
                 "median_ratio_2012_2020_fit_years": round(float(np.median([ratio[y] for y in fit])), 3),
                 "median_ratio_2021_2025_heldout": round(float(np.median([ratio[y] for y in ho])), 3),
                 "annual_corr_all_overlap_years": cor(ya.values, yb.values),
                 "annual_corr_2021_2025_heldout": cor(ya[ho].values, yb[ho].values),
                 "monthly_corr_all_overlap_years": cor(mm_["r"], mm_["v"]),
                 "monthly_corr_2021_2025_heldout": cor(hom["r"], hom["v"]),
                 "modis_raw_over_real_snpp_median": round(float(np.median(
                     [arr(reg, "modis_raw", m)[ix[y]] / yb[y] for y in have_y])), 3)}
            head["bridge"][reg][m] = b
    (REPORTS / "harmonization_headline.json").write_text(json.dumps(head, indent=1, default=float))
    print("[report] reports/harmonization_headline.json")
    make_charts(Y, head)
    print_summary(head)


# ------------------------------------------------------------------------------ charts
C = {"modis": "#0072B2", "snpp": "#D55E00", "n20": "#CC79A7", "harm": "#009E73", "grey": "#555555"}


def _panel(ax, Y, reg, m, kind, unit_scale):
    s = lambda name: Y[(Y.region == reg) & (Y.series == name)].set_index("year")[m].reindex(YEARS)  # noqa: E731
    ax.axvspan(2000.8, 2002.5, color="#bbbbbb", alpha=0.35, lw=0)
    ax.axvspan(2000.8, 2011.97, color="#009E73", alpha=0.07, lw=0)
    if kind == "raw":
        ax.plot(YEARS, s("modis_raw") / unit_scale, color=C["modis"], lw=2, marker="o", ms=3, label="MODIS (Terra+Aqua)")
        ax.plot(YEARS, s("viirs_snpp_raw") / unit_scale, color=C["snpp"], lw=2, marker="s", ms=3, label="VIIRS S-NPP")
        ax.plot(YEARS, s("viirs_n20_raw") / unit_scale, color=C["n20"], lw=2, marker="^", ms=3, label="VIIRS NOAA-20")
    else:
        ax.plot(YEARS, s("modis_raw") / unit_scale, color=C["modis"], lw=1.2, ls="--", alpha=0.7, label="MODIS raw (reference)")
        h = s("harmonized") / unit_scale
        ax.plot(YEARS, h.values, color=C["harm"], lw=2.2)
        ax.plot(YEARS[:12], h.values[:12], color=C["harm"], lw=0, marker="o", ms=3.5, label="Harmonized: reconstructed from MODIS")
        ax.plot(YEARS[12:], h.values[12:], color=C["harm"], lw=0, marker="s", ms=3.5, label="Harmonized: real S-NPP")
        hc = s("harmonized_cal") / unit_scale
        ax.plot(YEARS[:12], hc.values[:12], color="#000000", lw=1.3, ls="--", label="Harmonized + overlap calibration")
    ax.axvline(2012, color="#222222", lw=1, ls=":")
    ax.set_xlim(2000, 2025.5)
    ax.set_ylim(bottom=0)
    ax.grid(alpha=0.25, lw=0.6)
    ax.spines[["top", "right"]].set_visible(False)


def make_charts(Y, head):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({"font.size": 9, "axes.titlesize": 10, "font.family": "DejaVu Sans"})
    unit_div = {"cell_days": (1e3, "thousand cell-days / year"), "detections": (1e3, "thousand detections / year"),
                "frp_mw": (1e3, "GW (sum of FRP) / year")}
    sets = {"countries": ["five_countries", "Brazil", "Australia", "Canada", "India", "United_States"],
            "regions": ["amazon_arc", "australia_north_savanna", "canada_boreal", "punjab_haryana"]}
    for m in MEASURES:
        div, ylab = unit_div[m]
        for setname, regs in sets.items():
            fig, axes = plt.subplots(2, len(regs), figsize=(3.9 * len(regs), 6.6), sharex=True, squeeze=False)
            for j, reg in enumerate(regs):
                top, bot = axes[0, j], axes[1, j]
                _panel(top, Y, reg, m, "raw", div)
                _panel(bot, Y, reg, m, "harm", div)
                d = head["discontinuity"][reg][m]
                rr, hr = d["raw_splice"]["first_viirs_year_2012_over_2011"], d["harmonized"]["first_viirs_year_2012_over_2011"]
                hcr = d["harmonized_cal"]["first_viirs_year_2012_over_2011"]
                top.set_title(LABELS[reg].split(" (")[0] + f"\nraw: 2012 / 2011 = x{rr:.1f}", fontweight="bold", fontsize=9.5)
                bot.set_title(f"harmonized: x{hr:.2f}  (calibrated x{hcr:.2f})", color=C["harm"], fontweight="bold", fontsize=9)
                if j == 0:
                    top.set_ylabel("Raw record\n" + ylab)
                    bot.set_ylabel("Harmonized record\n" + ylab)
                bot.set_xlabel("Year")
                bot.set_xticks(range(2000, 2026, 5))
            h1, l1 = axes[0, 0].get_legend_handles_labels()
            h2, l2 = axes[1, 0].get_legend_handles_labels()
            fig.legend(h1 + h2, l1 + l2, loc="lower center", bbox_to_anchor=(0.5, 0.045), ncol=6, frameon=False, fontsize=8.5)
            fig.suptitle(f"{MEASURES[m][0]}, 2000-2025: raw record vs harmonized record (the 2012 MODIS-to-VIIRS step shrinks; ratios in panel titles)",
                         fontsize=12, fontweight="bold", y=0.995)
            fig.text(0.5, 0.012, "Shaded green: reconstructed from MODIS (before 2012-01-20). Grey: Terra only "
                     "(Aqua flies from mid-2002; 2000 = Nov-Dec only). Dotted line: first VIIRS year.",
                     ha="center", fontsize=8, color=C["grey"])
            fig.tight_layout(rect=(0, 0.11, 1, 0.97))
            stem = f"harmonization_chart_{m}_{setname}" if not (m == "cell_days" and setname == "countries") \
                else "harmonization_chart"
            for ext in ("png", "svg"):
                fig.savefig(REPORTS / f"{stem}.{ext}", dpi=140)
            plt.close(fig)
    # bridge figure: reconstruction / real S-NPP per year
    fig, axes = plt.subplots(1, 3, figsize=(13, 3.8), sharex=True)
    cols = plt.get_cmap("tab10")
    for ax, m in zip(axes, MEASURES):
        for k, reg in enumerate(sets["countries"][1:]):
            r = head["bridge"][reg][m]["ratio_by_year"]
            ax.plot([int(y) for y in r], list(r.values()), marker="o", ms=3, lw=1.6, label=LABELS[reg], color=cols(k))
        ax.axhline(1, color="#222222", lw=1)
        ax.axvspan(2012 - .5, 2020.5, color="#bbbbbb", alpha=0.3, lw=0)
        ax.set_title(MEASURES[m][0])
        ax.set_xlabel("Year")
        ax.set_ylabel("Reconstructed from MODIS / real S-NPP")
        ax.set_ylim(0, 2)
        ax.grid(alpha=0.25, lw=0.6)
        ax.spines[["top", "right"]].set_visible(False)
    axes[0].legend(frameon=False, fontsize=8)
    fig.suptitle("Bridge check on the overlap: MODIS-only reconstruction vs real VIIRS S-NPP (grey = years used to fit the model)",
                 fontweight="bold", fontsize=10.5)
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    for ext in ("png", "svg"):
        fig.savefig(REPORTS / f"harmonization_bridge.{ext}", dpi=140)
    plt.close(fig)
    print("[report] charts written to reports/harmonization_*.png/.svg")


def print_summary(head):
    for reg in REGIONS:
        for m in ("cell_days",):
            d = head["discontinuity"][reg][m]
            b = head["bridge"][reg][m]
            print(f"{reg:>24} {m}: raw 2012/2011 x{d['raw_splice']['first_viirs_year_2012_over_2011']:.2f} | harmonized "
                  f"x{d['harmonized']['first_viirs_year_2012_over_2011']:.2f} | bridge median ratio heldout "
                  f"{b['median_ratio_2021_2025_heldout']} annual corr {b['annual_corr_all_overlap_years']}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["monthly", "merge", "report"])
    ap.add_argument("--years", nargs="*", type=int)
    ap.add_argument("--countries", nargs="*")
    ap.add_argument("--threads", type=int, default=6)
    ap.add_argument("--skip-recon-years", nargs="*", type=int, help="overlap years (>=2012) without reconstruction")
    a = ap.parse_args()
    {"monthly": cmd_monthly, "merge": cmd_merge, "report": cmd_report}[a.step](a)


if __name__ == "__main__":
    main()
