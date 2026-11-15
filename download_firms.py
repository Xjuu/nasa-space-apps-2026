#!/usr/bin/env python3
"""Download global FIRMS active-fire data (MODIS + VIIRS) in 5-day chunks.

Needs a free MAP_KEY: https://firms.modaps.eosdis.nasa.gov/api/map_key
Usage:
    export FIRMS_MAP_KEY=...
    python download_firms.py            # all sources, all periods
    python download_firms.py --sources MODIS_SP --periods 2008-01-01:2008-01-31
Resumable: existing chunk files are skipped.
"""
import argparse
import csv
import io
import os
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

BASE = "https://firms.modaps.eosdis.nasa.gov/api"
CHUNK_DAYS = 5  # API max day_range
DEFAULT_PERIODS = [("2008-01-01", "2012-12-31"), ("2020-01-01", date.today().isoformat())]
# SP = standard (reprocessed, archive). NRT = near-real-time, fills recent gap SP does not cover yet.
DEFAULT_SOURCES = [
    "MODIS_SP", "MODIS_NRT",
    "VIIRS_SNPP_SP", "VIIRS_SNPP_NRT",
    "VIIRS_NOAA20_SP", "VIIRS_NOAA20_NRT",
    "VIIRS_NOAA21_NRT",
]
OUT = Path(__file__).parent / "data"


def get(url, retries=6):
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=120) as r:
                return r.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                sys.exit(f"HTTP {e.code}: bad MAP_KEY or key blocked")
            wait = 2 ** attempt * 5
        except Exception:
            wait = 2 ** attempt * 5
        time.sleep(wait)
    raise RuntimeError(f"failed after {retries} tries: {url.replace(KEY, '***')}")


def availability(key):
    txt = get(f"{BASE}/data_availability/csv/{key}/ALL")
    out = {}
    for row in csv.DictReader(io.StringIO(txt)):
        out[row["data_id"]] = (row["min_date"], row["max_date"])
    return out


def chunks(start, end):
    d = start
    while d <= end:
        n = min(CHUNK_DAYS, (end - d).days + 1)
        yield d, n
        d += timedelta(days=n)


def fetch_chunk(args):
    key, src, d, n = args
    f = OUT / src / f"{d.isoformat()}_{n}d.csv"
    if f.exists():
        return "skip"
    txt = get(f"{BASE}/area/csv/{key}/{src}/world/{n}/{d.isoformat()}")
    if txt.lstrip().lower().startswith(("invalid", "exceeded", "error")) or "<html" in txt[:200].lower():
        raise RuntimeError(f"{src} {d}: {txt[:200]}")
    if WRITTEN[0] >= BUDGET[0]:
        return "budget"
    WRITTEN[0] += len(txt)
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".part")
    tmp.write_text(txt)
    tmp.rename(f)
    return "ok"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sources", nargs="+", default=DEFAULT_SOURCES)
    ap.add_argument("--periods", nargs="+", default=[f"{a}:{b}" for a, b in DEFAULT_PERIODS],
                    help="START:END pairs, ISO dates")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--stride", type=int, default=1,
                    help="take every k-th 5-day chunk (spreads sample over all seasons/years)")
    ap.add_argument("--budget-gb", type=float, default=1e9, help="stop after this many GB written by this process")
    a = ap.parse_args()
    BUDGET[0] = a.budget_gb * 1e9

    global KEY
    envf = Path(__file__).parent / ".env"
    if envf.exists():
        for line in envf.read_text().splitlines():
            k, sep, v = line.partition("=")
            if sep and not k.strip().startswith("#"):
                os.environ.setdefault(k.strip(), v.strip().strip("\"'"))
    KEY = os.environ.get("FIRMS_MAP_KEY")
    if not KEY:
        sys.exit("Set FIRMS_MAP_KEY env var.")

    avail = availability(KEY)
    jobs = []
    for src in a.sources:
        if src not in avail:
            print(f"{src}: not in availability list, skip")
            continue
        amin, amax = (date.fromisoformat(x) for x in avail[src])
        for p in a.periods:
            s, e = (date.fromisoformat(x) for x in p.split(":"))
            s, e = max(s, amin), min(e, amax)
            if s > e:
                print(f"{src} {p}: outside available {amin}..{amax}, skip")
                continue
            jobs += [(KEY, src, d, n) for d, n in list(chunks(s, e))[::a.stride]]
    print(f"{len(jobs)} chunks")

    done = 0
    with ThreadPoolExecutor(a.workers) as ex:
        for r in ex.map(fetch_chunk, jobs):
            done += 1
            if done % 50 == 0:
                print(f"{done}/{len(jobs)}", flush=True)
    print("done")


KEY = None
WRITTEN = [0]
BUDGET = [1e18]
if __name__ == "__main__":
    main()
