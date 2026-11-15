#!/usr/bin/env python3
"""MODIS -> VIIRS-equivalent reconstruction (see equifire/harmonize.py).

    .venv/bin/python harmonize.py train [--frac 0.3] [--countries Brazil India]
    .venv/bin/python harmonize.py calibrate                              # FRP / count / area total correction
    .venv/bin/python harmonize.py eval  [--years 2021 2022 2023 2024 2025]
    .venv/bin/python harmonize.py apply [--years 2000 ... 2011] [--all]   # --all also writes 2012-2025
    .venv/bin/python harmonize.py spread [--cases 60]                    # spread accuracy: recon vs real input
"""
import argparse
import os

import numpy as np


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["train", "calibrate", "eval", "apply", "spread", "all"])
    ap.add_argument("--frac", type=float, default=0.06, help="fraction of training candidate cell-days used")
    ap.add_argument("--val-frac", type=float, default=0.1)
    ap.add_argument("--eval-frac", type=float, default=0.25)
    ap.add_argument("--countries", nargs="*", default=None, help="default: all available")
    ap.add_argument("--years", nargs="*", type=int, default=None)
    ap.add_argument("--all", action="store_true", help="apply: also reconstruct 2012-2025 (consistency checks)")
    ap.add_argument("--threads", type=int, default=8)
    ap.add_argument("--workers", type=int, default=1, help="apply: years in parallel (~2-3 GB RAM each)")
    ap.add_argument("--cases", type=int, default=60)
    ap.add_argument("--out", default=None, help="apply: output parquet (default cache/viirs_equiv_daily.parquet)")
    a = ap.parse_args()
    os.environ.setdefault("OMP_NUM_THREADS", str(a.threads))
    from equifire import harmonize as h
    if a.step in ("train", "all"):
        h.train(frac=a.frac, val_frac=a.val_frac, countries=a.countries, nthread=a.threads)
    if a.step in ("calibrate", "all"):
        h.calibrate(countries=a.countries)
    if a.step in ("eval", "all"):
        h.evaluate(a.years, a.countries, a.eval_frac)
    if a.step in ("apply", "all"):
        years = a.years or (list(range(2000, 2026)) if a.all else list(range(2000, 2012)))
        h.apply(years, a.countries, out=h.OUT if a.out is None else a.out, workers=a.workers)
    if a.step == "spread":
        h.spread_check(n_cases=a.cases, years=a.years or (2023, 2024, 2025), countries=a.countries)


if __name__ == "__main__":
    main()
