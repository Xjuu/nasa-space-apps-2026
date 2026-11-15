"""Per-fire daily weather from the Open-Meteo archive API (ERA5-based, no key), cached on disk.

Why: the old weather source (equifire/weather.py, 2-degree tiles for the busiest tiles only) covers
~5% of fire cases, so weather features were mostly NaN. This module fetches weather for exactly the
fire cases the direct model trains / is tested on.

Key: the case window centre rounded to 0.25 degrees (ERA5 resolution). Per key we fetch a short
date range ending on the start day t0 (default t0-13 .. t0 = 14 days). Measured Open-Meteo cost
(X-RateLimit-Cost header, 9 daily variables): 14 days = 1.0 call, a whole year = 23.5 calls, so
whole-year requests would cost ~23x more for the same fires (fires at one location rarely repeat
within a year). Ranges of nearby cases at the same key are merged when they are close in time.

Daily variables (timezone GMT, same UTC day convention as the fire index):
  temperature_2m_max/mean, relative_humidity_2m_min/mean, precipitation_sum, wind_speed_10m_max,
  wind_direction_10m_dominant, wind_gusts_10m_max, vapour_pressure_deficit_max.
NOTE: a "day t0" value aggregates the whole UTC day t0, i.e. also hours after the last VIIRS overpass
of t0. In operation that part would come from a same-day forecast; nothing from days > t0 is used.

Cache: cache/firewx/raw/<lat>_<lon>.json  {date: [values...]}  (one file per key, merged on update)
       cache/firewx/all.pkl                consolidated {(lat_r, lon_r): {day_int: np.array}} for fast loads

    python -m equifire.firewx prefetch --year 2024 --cases 2000      # weather for one walk-forward fold
    python -m equifire.firewx coverage --year 2024 --cases 2000
"""
import argparse
import json
import pickle
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import numpy as np

from .common import CACHE, EPOCH, cell_center

URL = "https://archive-api.open-meteo.com/v1/archive"
VARS = ["temperature_2m_max", "temperature_2m_mean", "relative_humidity_2m_min", "relative_humidity_2m_mean",
        "precipitation_sum", "wind_speed_10m_max", "wind_direction_10m_dominant", "wind_gusts_10m_max",
        "vapour_pressure_deficit_max"]
IDX = {v: i for i, v in enumerate(VARS)}
DIR = CACHE / "firewx"
RAW = DIR / "raw"
ALL_F = DIR / "all.pkl"
RES = 0.25
LOOKBACK = 13           # days before t0 fetched (t0-13 .. t0 = 14 days = 1 API call)
MERGE_GAP = 20          # merge two ranges at a key if the gap between them is <= this many days


def key_of(lat, lon):
    return round(round(float(lat) / RES) * RES, 2), round(round(float(lon) / RES) * RES, 2)


def _date(d):
    return str(EPOCH + np.timedelta64(int(d), "D"))


def _dayint(s):
    return int((np.datetime64(s) - EPOCH).astype(int))


# ------------------------------------------------------------------ store (read side)
class FireWx:
    """Lookup of cached per-key daily weather. get(day, lat, lon) -> array len(VARS) or None."""

    def __init__(self, data=None):
        if data is None:
            data = pickle.loads(ALL_F.read_bytes()) if ALL_F.exists() else {}
        self.d = data

    def series(self, t0, lat, lon, back=LOOKBACK):
        """[back+1, len(VARS)] for days t0-back .. t0 (NaN where missing), or None if t0 missing."""
        k = self.d.get(key_of(lat, lon))
        if k is None or int(t0) not in k:
            return None
        out = np.full((back + 1, len(VARS)), np.nan)
        for j, d in enumerate(range(int(t0) - back, int(t0) + 1)):
            v = k.get(d)
            if v is not None:
                out[j] = v
        return out


_STORE = None


def store():
    global _STORE
    if _STORE is None:
        _STORE = FireWx()
    return _STORE


# ------------------------------------------------------------------ fetching
class _Throttle:
    """Respects X-RateLimit-* headers (600 cost / 60 s window) plus a min interval between calls."""

    def __init__(self, min_interval=0.15):
        self.lock = threading.Lock()
        self.next_t = 0.0
        self.min_interval = min_interval
        self.cost = 0.0

    def wait(self):
        with self.lock:
            now = time.time()
            if now < self.next_t:
                time.sleep(self.next_t - now)
            self.next_t = max(time.time(), self.next_t) + self.min_interval

    def update(self, headers):
        try:
            c = float(headers.get("X-RateLimit-Cost", 1))
            rem = float(headers.get("X-RateLimit-Remaining", 600))
        except (TypeError, ValueError):
            return
        with self.lock:
            self.cost += c
            if rem < 30:  # close to the per-minute budget: pause for the window
                self.next_t = max(self.next_t, time.time() + 20)


def _get(params, th, tries=6):
    q = "&".join(f"{k}={v}" for k, v in params.items())
    for a in range(tries):
        th.wait()
        try:
            with urllib.request.urlopen(f"{URL}?{q}", timeout=60) as r:
                th.update(r.headers)
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            body = e.read()[:300].decode(errors="replace")
            if e.code == 429:
                wait = 65 if "inute" in body else 600 if "our" in body else 60
                if "aily" in body:
                    raise RuntimeError(f"Open-Meteo daily limit reached: {body}")
                print(f"[firewx] 429 ({body.strip()[:120]}); sleeping {wait}s", flush=True)
                with th.lock:
                    th.next_t = max(th.next_t, time.time() + wait)
                continue
            if e.code == 400:
                raise ValueError(body)
            time.sleep(5 * (a + 1))
        except Exception as e:  # network hiccup
            print(f"[firewx] {type(e).__name__}: {e}; retry", flush=True)
            time.sleep(5 * (a + 1))
    raise RuntimeError("Open-Meteo: too many failures")


def _raw_f(key):
    return RAW / f"{key[0]:.2f}_{key[1]:.2f}.json"


def _load_raw(key):
    f = _raw_f(key)
    return json.loads(f.read_text()) if f.exists() else {}


def _fetch_one(key, lo, hi, th):
    js = _get({"latitude": key[0], "longitude": key[1], "start_date": _date(lo), "end_date": _date(hi),
               "daily": ",".join(VARS), "timezone": "GMT"}, th)
    d = js["daily"]
    rec = _load_raw(key)
    for i, t in enumerate(d["time"]):
        rec[t] = [d[v][i] for v in VARS]
    rec["_elev"] = js.get("elevation")
    _raw_f(key).write_text(json.dumps(rec))
    return len(d["time"])


def _needed_ranges(cases, back=LOOKBACK):
    """{key: [(lo, hi), ...]} of day ranges not yet in the raw cache, merged per key."""
    want = {}
    for t0, cx, cy in cases:
        lat, lon = cell_center(cx, cy)
        want.setdefault(key_of(lat, lon), []).append((int(t0) - back, int(t0)))
    out = {}
    for key, rs in want.items():
        have = {_dayint(t) for t in _load_raw(key) if not t.startswith("_")}
        rs = [(lo, hi) for lo, hi in sorted(rs) if not all(d in have for d in range(lo, hi + 1))]
        merged = []
        for lo, hi in rs:
            if merged and lo - merged[-1][1] <= MERGE_GAP:
                merged[-1] = (merged[-1][0], max(merged[-1][1], hi))
            else:
                merged.append((lo, hi))
        if merged:
            out[key] = merged
    return out


def prefetch(cases, workers=4, max_cost=None):
    """Fetch weather for all cases (only what is missing), then rebuild the consolidated store."""
    RAW.mkdir(parents=True, exist_ok=True)
    jobs = [(k, lo, hi) for k, rs in _needed_ranges(cases).items() for lo, hi in rs]
    last_day = _dayint(str(np.datetime64("today"))) - 6  # archive lags ~5 days
    jobs = [(k, lo, min(hi, last_day)) for k, lo, hi in jobs if lo <= last_day]
    est = sum(max(1.0, (hi - lo + 1) / 14) for _, lo, hi in jobs)
    print(f"[firewx] {len(cases):,} cases -> {len(jobs):,} requests to make (estimated cost {est:.0f} calls)",
          flush=True)
    if max_cost is not None and est > max_cost:
        raise SystemExit(f"[firewx] estimated cost {est:.0f} > --max-cost {max_cost}; aborting")
    th = _Throttle()
    t = time.time()
    done = [0]
    # one key per worker at a time (raw files are per key); group jobs by key
    by_key = {}
    for k, lo, hi in jobs:
        by_key.setdefault(k, []).append((lo, hi))

    def run(item):
        k, rs = item
        for lo, hi in rs:
            try:
                _fetch_one(k, lo, hi, th)
            except ValueError as e:
                print(f"[firewx] bad request {k} {lo}-{hi}: {e}", flush=True)
            done[0] += 1
            if done[0] % 200 == 0:
                print(f"[firewx] {done[0]:,}/{len(jobs):,} requests | cost so far {th.cost:.0f} | "
                      f"{time.time() - t:.0f}s", flush=True)

    with ThreadPoolExecutor(workers) as ex:
        list(ex.map(run, by_key.items()))
    print(f"[firewx] fetched {done[0]:,} requests, cost {th.cost:.1f} calls, {time.time() - t:.0f}s", flush=True)
    consolidate()


def consolidate():
    data = {}
    for f in RAW.glob("*.json"):
        a, b = f.stem.split("_")
        rec = json.loads(f.read_text())
        data[(float(a), float(b))] = {_dayint(t): np.array([np.nan if x is None else x for x in v], np.float32)
                                      for t, v in rec.items() if not t.startswith("_")}
    DIR.mkdir(parents=True, exist_ok=True)
    tmp = ALL_F.with_suffix(".tmp")
    tmp.write_bytes(pickle.dumps(data, protocol=4))
    tmp.replace(ALL_F)
    global _STORE
    _STORE = None
    print(f"[firewx] consolidated {len(data):,} locations -> {ALL_F}", flush=True)


def coverage(cases, wx=None):
    wx = wx or FireWx()
    ok = 0
    for t0, cx, cy in cases:
        lat, lon = cell_center(cx, cy)
        s = wx.series(t0, lat, lon)
        ok += s is not None and np.isfinite(s[-1, IDX["wind_speed_10m_max"]])
    return ok / max(len(cases), 1)


# ------------------------------------------------------------------ the case lists walk_forward uses
def fold_cases(year, n_cases, runs=100, seed=0, back=None, days=3):
    """Training + validation cases of direct.train(test_from=year, n_cases, n_val=max(200, n/10), seed)
    and the test cases of walk_forward (rng 1000 + year), exactly as those functions sample them."""
    from . import direct
    from .fireindex_mm import get_fire_index
    fi = get_fire_index()
    first_test_day = int((np.datetime64(f"{year}-01-01") - EPOCH).astype(int))
    back = direct.LONG if back is None else back
    ok = (fi.day.astype(np.int64) + direct.MAX_K < first_test_day) & (fi.day.astype(np.int64) - back >= fi.day.min())
    n_val = max(200, n_cases // 10)
    train = direct.sample_cases(fi, ok, n_cases + n_val, np.random.default_rng(seed))
    lo = first_test_day
    hi = min(int((np.datetime64(f"{year + 1}-01-01") - EPOCH).astype(int)) - 1, int(fi.day.max()) - days)
    test = direct.sample_cases(fi, (fi.day >= lo) & (fi.day <= hi), runs, np.random.default_rng(1000 + year))
    return train, test


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["prefetch", "coverage", "consolidate"])
    ap.add_argument("--year", type=int, nargs="+", default=[2024])
    ap.add_argument("--cases", type=int, default=2000)
    ap.add_argument("--runs", type=int, default=100)
    ap.add_argument("--days", type=int, default=3, help="walk_forward --days (test window end)")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--max-cost", type=float, default=None)
    ap.add_argument("--limit", type=int, default=None, help="only the first N training cases (small test)")
    a = ap.parse_args()
    if a.cmd == "consolidate":
        return consolidate()
    for y in a.year:
        tr, te = fold_cases(y, a.cases, a.runs, days=a.days)
        if a.limit:
            tr = tr[:a.limit]
        if a.cmd == "prefetch":
            prefetch(tr + te, a.workers, a.max_cost)
        print(f"[firewx] {y}: coverage train {coverage(tr):.1%} ({len(tr)}), test {coverage(te):.1%} ({len(te)})",
              flush=True)


if __name__ == "__main__":
    main()
