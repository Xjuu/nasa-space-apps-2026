#!/usr/bin/env python3
"""Goal 4: routine seasonal (agricultural) burning vs fire anomaly.

    .venv/bin/python classify.py --check                          # reference events table
    .venv/bin/python classify.py --lat 30.6 --lon 75.6 --date 2023-11-05
"""
import argparse
import json
import time

import pandas as pd

from equifire.seasonal import HOLDOUT_EVENTS, check_known, load_calendar


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--holdout", action="store_true", help="independent events not used to design the rules")
    ap.add_argument("--lat", type=float)
    ap.add_argument("--lon", type=float)
    ap.add_argument("--date")
    a = ap.parse_args()
    t = time.time()
    cal = load_calendar()
    print(f"[classify] {len(cal.act):,} VIIRS cell-days, years {cal.years[0]}-{cal.years[-1]} "
          f"| {time.time() - t:.0f}s", flush=True)
    if a.lat is not None:
        r = cal.classify(a.lat, a.lon, a.date)
        r.pop("calendar")
        print(json.dumps(r, indent=1))
    if a.check or a.lat is None:
        df = check_known(cal, HOLDOUT_EVENTS if a.holdout else None)
        pd.set_option("display.width", 220)
        print(df.drop(columns=["date"]).to_string(index=False))
        print(f"\n[classify] reference events correct: {df['ok'].sum()}/{len(df)} "
              f"(anomalies {df[df.expected == 'anomaly'].ok.mean():.0%}, routine {df[df.expected == 'routine'].ok.mean():.0%})")
        df.to_csv(f"reports/classify_{'holdout' if a.holdout else 'known'}_events.csv", index=False)


if __name__ == "__main__":
    main()
