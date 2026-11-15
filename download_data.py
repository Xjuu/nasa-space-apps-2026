#!/usr/bin/env python3
"""Download the NASA FIRMS fire data this project needs, then build the local caches.

No API key and no account are needed: the per-country yearly CSVs are public.
(Pobiera publiczne dane pożarowe NASA FIRMS. Klucz API ani konto nie są potrzebne.)

    python download_data.py                    # default: Brazil, Australia, India, United States, Canada,
                                               # MODIS 2000-2025 + VIIRS 2012-2025 (about 10 GB), then builds caches
    python download_data.py --years 2018 2025  # smaller test download (about 1-2 GB)
    python download_data.py --countries Poland Germany --years 2020 2025
    python download_data.py --all              # all 176 FIRMS countries (about 43 GB)
    python download_data.py --no-collect       # download only, skip cache building
    python download_data.py --tle              # refresh satellite orbit files (aux/tle)

Files go to data_country/<sensor>/<year>/<sensor>_<year>_<Country>.csv (resumable: re-run to continue).
Country names are the FIRMS spellings, e.g. United_States, Russian_Federation, Lao_PDR (see countries_all.txt).
"""
import argparse
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEFAULT_COUNTRIES = ["Brazil", "Australia", "India", "United_States", "Canada"]
TLE = {"terra": 25994, "aqua": 27424, "snpp": 37849, "noaa20": 43013, "noaa21": 54234}


def run(cmd):
    print("+", " ".join(cmd), flush=True)
    r = subprocess.run(cmd, cwd=ROOT)
    if r.returncode:
        sys.exit(f"command failed with exit code {r.returncode}")


def refresh_tle():
    out = ROOT / "aux" / "tle"
    out.mkdir(parents=True, exist_ok=True)
    for name, norad in TLE.items():
        url = f"https://celestrak.org/NORAD/elements/gp.php?CATNR={norad}&FORMAT=tle"
        with urllib.request.urlopen(url, timeout=60) as r:
            (out / f"{name}.tle").write_bytes(r.read())
        print("TLE", name, "ok")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--countries", nargs="+", default=DEFAULT_COUNTRIES)
    ap.add_argument("--all", action="store_true", help="all countries listed in countries_all.txt (about 43 GB)")
    ap.add_argument("--years", nargs=2, type=int, default=[2000, 2025], metavar=("FROM", "TO"))
    ap.add_argument("--budget-gb", type=float, default=None, help="stop after this many GB (default 15, or 80 with --all)")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--no-collect", action="store_true", help="skip building caches after the download")
    ap.add_argument("--tle", action="store_true", help="only refresh satellite orbit (TLE) files")
    a = ap.parse_args()

    if a.tle:
        refresh_tle()
        return
    countries = a.countries
    if a.all:
        countries = [c.strip() for c in (ROOT / "countries_all.txt").read_text().split() if c.strip()]
    budget = a.budget_gb or (80.0 if a.all else 15.0)
    run([sys.executable, "download_country.py", "--countries", *countries,
         "--years", str(a.years[0]), str(a.years[1]), "--budget-gb", str(budget), "--workers", str(a.workers)])
    if not a.no_collect:
        # aggregates the CSVs onto the 0.05 degree grid (cache/cells); needs a few GB of RAM
        run([sys.executable, "main.py", "collect"])
    print("\nDone. Start the app:  python -m uvicorn backend.app:app --host 0.0.0.0 --port 8080")


if __name__ == "__main__":
    main()
