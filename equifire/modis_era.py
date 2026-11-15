"""MODIS-era (pre-VIIRS) fire cases for direct-model training.

Before 2012-01-20 there is no VIIRS. equifire.harmonize reconstructed VIIRS-equivalent daily 0.05 deg
cells from MODIS alone (cache/viirs_equiv_daily.parquet, years 2008-2011 built). This module turns that
reconstruction into a direct.FireIndex so old fires can be used as extra training cases:

  * VIIRS layer  = reconstructed cells (source='reconstructed'), used as BOTH inputs and truth.
                   No day/night split exists -> dn = 3 (both passes) for every row; no sub-cell positions
                   (v3 sub-cell features are NaN, as for any fire without sub-cell data).
  * MODIS layer  = the real Terra/Aqua cells of the memory-mapped fire index (days < 2012-01-01).
  * cases        = t0 with a full 365-day history inside the reconstruction and t0 + 28 < 2012-01-01,
                   i.e. 2009-01-01 .. 2011-12-03 when the reconstruction starts in 2008. Nothing after
                   2011 is touched, so these rows cannot leak into any test year >= 2012.
Rows are marked by the caller with a sample weight (default 0.5) because the reconstruction is
imperfect (harmonize spread check: start-day IoU of reconstructed vs real cells only ~0.24).
"""
import json
import numpy as np

from . import direct
from .common import EPOCH, MODELS

END = int((np.datetime64("2012-01-01") - EPOCH).astype(int))


def reconstruction_provenance(test_from):
    """The reconstruction teacher must not have learned from the spread test period."""
    path = MODELS / 'harmonize_meta.json'
    meta = json.loads(path.read_text())
    used = list(meta['train_years']) + list(meta['val_years'])
    if not used or max(used) >= test_from:
        raise ValueError('MODIS reconstruction teacher was fitted/tuned in or after the spread test year')
    return {'years': [2008, 2009, 2010, 2011], 'source': 'reconstructed MODIS to VIIRS-equivalent',
            'teacher_fit_and_validation_years': sorted(used), 'day_night': 'unknown; legacy both-pass encoding',
            'note': 'Synthetic training labels; not additional observed VIIRS fires.'}


def old_fire_index(start_year=2008, fi_mm=None):
    from .fireindex_mm import get_fire_index
    from .harmonize import load_harmonized_daily
    act = load_harmonized_daily(start_year, 2011)
    act = act[act["source"] == "reconstructed"]
    if act.empty:
        raise ValueError('No reconstructed MODIS-era data available; run harmonize.py apply first')
    fi_mm = fi_mm or get_fire_index(False)
    md = np.asarray(fi_mm.m.day)
    sel = np.flatnonzero(md < END)
    m = (md[sel], np.asarray(fi_mm.m.gx)[sel], np.asarray(fi_mm.m.gy)[sel],
         np.asarray(fi_mm.m.vals["frp"])[sel].astype(np.float32))
    v = (act["day"].values, act["gx"].values, act["gy"].values, act["frp"].values.astype(np.float32),
         act["n"].values.astype(np.float32), np.full(len(act), 3, np.int8))
    fi = direct.FireIndex(_v=v, _m=m)
    fi.sub = None
    fi.first_day = int(act["day"].min())
    return fi


def modis_era_rows(n_cases, wx, cols, rng, start_year=2008, verbose=True):
    """Training rows (X, y, w, n_cases_used) from reconstructed pre-VIIRS fires."""
    fi = old_fire_index(start_year)
    back = direct.LONG if direct.needs_extras(cols) else direct.HIST
    ok = (fi.day.astype(np.int64) + direct.MAX_K < END) & (fi.day.astype(np.int64) - back >= fi.first_day)
    cases = direct.sample_cases(fi, ok, n_cases, rng)
    if not cases:
        raise ValueError('No MODIS-era cases have the required history and complete forecast window')
    if verbose:
        print(f"[modis-era] {len(cases):,} reconstructed fire cases "
              f"({str(EPOCH + np.timedelta64(min(c[0] for c in cases), 'D'))} .. "
              f"{str(EPOCH + np.timedelta64(max(c[0] for c in cases), 'D'))})", flush=True)
    X, y, w, _ = direct.build_rows(fi, cases, wx, rng=rng, verbose=False, cols=cols)
    return X, y, w, len(cases)
