#!/usr/bin/env python3
"""Download FIRMS per-country yearly CSVs (public, no key). Parallel, resumable, size-budgeted.

Usage: python download_country.py [--budget-gb 10] [--workers 8]
       python download_country.py --countries Brazil Australia --years 2018 2025
"""
import argparse
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

BASE = "https://firms.modaps.eosdis.nasa.gov/data/country"
OUT = Path(__file__).parent / "data_country"
SENSORS = {"modis": "modis", "viirs-snpp": "viirs-snpp", "viirs-jpss1": "viirs-jpss1"}  # jpss1 = NOAA-20
# viirs-snpp starts 2012, jpss1 2018 (404 outside)
COUNTRIES = ["Brazil", "Australia", "India", "United_States", "Canada"]


def url(sensor, year, country):
    return f"{BASE}/{sensor}/{year}/{sensor}_{year}_{country}.csv"


def head(u):
    try:
        req = urllib.request.Request(u, method="HEAD")
        with urllib.request.urlopen(req, timeout=60) as r:
            return int(r.headers.get("content-length", 0))
    except urllib.error.URLError:
        return 0


def fetch(job):
    u, f = job
    if f.exists():
        return f.stat().st_size
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".part")
    for attempt in range(4):
        try:
            urllib.request.urlretrieve(u, tmp)
            tmp.rename(f)
            print("ok", f.name, f.stat().st_size // 10**6, "MB", flush=True)
            return f.stat().st_size
        except Exception as e:
            err = e
    print("FAIL", u, err, file=sys.stderr, flush=True)
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--countries", nargs="+", default=COUNTRIES)
    ap.add_argument("--years", nargs=2, type=int, default=[2012, 2025])
    ap.add_argument("--budget-gb", type=float, default=10.0)
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args()

    cands = [(s, y, c) for y in range(a.years[0], a.years[1] + 1)
             for c in a.countries for s in SENSORS]
    with ThreadPoolExecutor(16) as ex:
        sizes = list(ex.map(lambda t: head(url(*t)), cands))
    items = [(t, sz) for t, sz in zip(cands, sizes) if sz > 0]
    print(f"{len(items)} files exist, {sum(s for _, s in items) / 1e9:.1f} GB total available")

    # Newest years first so the most recent (and overlap-rich) data fits the budget.
    items.sort(key=lambda x: -x[0][1])
    budget, used, jobs = a.budget_gb * 1e9, 0, []
    for (s, y, c), sz in items:
        if used + sz <= budget:
            used += sz
            jobs.append((url(s, y, c), OUT / s / str(y) / f"{s}_{y}_{c}.csv"))
    print(f"downloading {len(jobs)} files, {used / 1e9:.1f} GB")
    with ThreadPoolExecutor(a.workers) as ex:
        total = sum(ex.map(fetch, jobs))
    print(f"done {total / 1e9:.1f} GB")


if __name__ == "__main__":
    main()
