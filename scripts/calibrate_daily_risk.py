#!/usr/bin/env python3
"""Offline, isolated daily-risk probability calibration: fit2023 -> calibrate2024 -> test2025.

Reuses frozen ensemble_blend_2024 and cached v2 physics. No network, base-model
replacement, threshold search on test outcomes, or claim of live calibration.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import numpy as np
import pandas as pd
from equifire import direct
from equifire.common import CACHE, MODELS, REPORTS, EPOCH, cell_center
from equifire.ensemble_core import FEATS, case_rows
from equifire import risk_calibration as rc


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def clusters(cases, max_days=28, max_km=50):
    """Conservative spatial/time proxy blocks, not independently verified incident IDs."""
    parent = list(range(len(cases)))
    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]];i = parent[i]
        return i
    ll = [cell_center(c[1], c[2]) for c in cases]
    for i in range(len(cases)):
        for j in range(i):
            if abs(cases[i][0]-cases[j][0]) > max_days:continue
            la,lo=ll[i]; lb,lob=ll[j]
            km=np.hypot((la-lb)*111.32, (lo-lob)*111.32*np.cos(np.radians((la+lb)/2)))
            if km <= max_km:parent[root(i)]=root(j)
    return np.array([root(i) for i in range(len(cases))])


def collect(year, cases, frozen, production, rebuild=False, source_path=None):
    model_path = MODELS/f'wf_{year}.json'
    physical = [CACHE/'physcells'/f'{d}_{x}_{y}_d3_c1_r60_e0_v2.npz' for d,x,y in cases]
    missing = [str(p) for p in physical if not p.exists()]
    if missing:raise RuntimeError('Missing offline physics; refusing network work: '+str(missing[:3]))
    from equifire.weather import WDIR
    fingerprint = dict(version=2, year=year, cases=cases, ml=sha(model_path),
      ml_meta=sha(MODELS/f'wf_{year}_meta.json'), frozen=sha(source_path or MODELS/'ensemble_blend_2024.json'),
      production=sha(MODELS/'ensemble_blend_2025.json'), features=FEATS,
      index=sha(CACHE/'fireindex/meta.json'),
      weather=[(p.name,p.stat().st_mtime_ns,p.stat().st_size) for p in sorted(WDIR.glob('*.json'))],
      physics=[(p.name,p.stat().st_mtime_ns,p.stat().st_size) for p in physical])
    tag=hashlib.sha256(json.dumps(fingerprint,sort_keys=True).encode()).hexdigest()[:20]
    path=CACHE/'risk_calibration'/f'rows_{year}_{tag}.npz'
    if path.exists() and not rebuild:
        with np.load(path,allow_pickle=False) as z:return {k:z[k] for k in z.files}
    from equifire.fireindex_mm import get_fire_index
    fi=get_fire_index(verbose=False)
    weather=direct.Weather()
    model,meta=direct.load(model_path)
    model.set_params(n_jobs=2,device='cpu')
    if int(meta['test_from'])>year:raise ValueError('ML model sees the requested outcome year')
    block=clusters(cases)
    arrays={k:[] for k in ('raw','production','y','horizon','group','case_index','cell_index','weather_fallback')}
    for i,(case,physfile) in enumerate(zip(cases,physical)):
        result=direct.predict_case(model,fi,*case,weather,3,meta['features'])
        if result is None:raise RuntimeError(f'No ML prediction for registered case {case}')
        P,A,dist,A0=result
        truth=direct.case_truth(A,dist,A0,3)
        with np.load(physfile,allow_pickle=False) as z:
            pc={k:z[k] for k in ('frac','arr_h','cover')};pc['meta']=json.loads(str(z['meta']))
        if not pc['meta'].get('ok',True):raise RuntimeError(f'Invalid physics for {case}')
        X,ids=case_rows(P,dist,pc,3)
        if not len(ids):raise RuntimeError(f'No eligible cells for {case}')
        raw=frozen.predict_proba(X)[:,1]
        prod=production.predict_proba(X)[:,1]
        labels=truth[ids[:,0]-1].reshape(len(ids),-1)[np.arange(len(ids)),ids[:,1]]
        values=dict(raw=raw,production=prod,y=labels.astype(np.int8),horizon=ids[:,0],
          group=np.full(len(ids),block[i]),case_index=np.full(len(ids),i),cell_index=ids[:,1],
          weather_fallback=np.full(len(ids),pc['meta'].get('weather_fallback',False)))
        for key,value in values.items():arrays[key].append(value)
        if (i+1)%20==0 or i+1==len(cases):print(f'[rows {year}] {i+1}/{len(cases)} cases',flush=True)
    arrays={k:np.concatenate(v) for k,v in arrays.items()}
    path.parent.mkdir(exist_ok=True)
    np.savez_compressed(path,**arrays)
    return arrays


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output-dir',type=Path,default=MODELS/'daily_risk_calibration')
    ap.add_argument('--rebuild-cache',action='store_true')
    args=ap.parse_args()
    from threadpoolctl import threadpool_limits
    threadpool_limits(limits=2)
    import xgboost as xgb
    start=time.perf_counter()
    source=MODELS/'ensemble_blend_2024.json'
    source_meta=json.loads((MODELS/'ensemble_blend_2024_meta.json').read_text())
    assert source_meta['fit_year']==2023 and source_meta['days']==3 and source_meta['features']==FEATS
    frozen=xgb.XGBClassifier(n_jobs=2,device='cpu');frozen.load_model(source);frozen.set_params(n_jobs=2,device='cpu')
    production=xgb.XGBClassifier(n_jobs=2,device='cpu');production.load_model(MODELS/'ensemble_blend_2025.json');production.set_params(n_jobs=2,device='cpu')
    cases={y:json.loads((REPORTS/f'ensemble_ab_{y}.json').read_text())['test_cases'] for y in (2024,2025)}
    # Original blender outcome window ends in2023; calibration starts Feb2024.
    # Calibration outcomes end Nov2024, at least30 days before test-year outcomes.
    cases[2024]=[c for c in cases[2024] if np.datetime64('2024-02-01')<=EPOCH+np.timedelta64(c[0],'D')
                  and EPOCH+np.timedelta64(c[0]+3,'D')<=np.datetime64('2024-11-30')]
    protocol=dict(version=rc.VERSION, source_file=source.name,source_sha256=sha(source),source_meta_sha256=sha(MODELS/'ensemble_blend_2024_meta.json'),source_fit_year=2023,
      production_comparator_sha256=sha(MODELS/'ensemble_blend_2025.json'),
      base_ml_sha256={str(y):sha(MODELS/f'wf_{y}.json') for y in (2024,2025)},
      calibration_outcomes_through_year=2024,test_year=2025,days=3,features=FEATS,
      target='observed newly burning eligible coarse cell by horizon day; cumulative, not disjoint daily hazard',
      weighting='equal total weight per 50km/28day spatial-temporal proxy block',
      calibration_cases=cases[2024],test_cases=cases[2025],
      boundary_embargo='calibration starts2024-02-01; its 3-day outcomes end by2024-11-30',
      method='per-horizon monotone logistic calibration; slope>=0; fixed weak ridge',
      temporal_coherence='cumulative maximum across existing horizon entries for each case/cell',
      selection='one prespecified candidate; no test-based hyperparameter or threshold tuning',
      scope='historical archive only; existing retrospective benchmark; five-country archive; not live/geographic validation',
      physics_version=2,deployment_status='research_candidate_not_promoted')
    args.output_dir.mkdir(parents=True,exist_ok=True)
    (args.output_dir/'protocol.json').write_text(json.dumps(protocol,indent=2))
    # Complete/freeze the calibration artifact before accessing any2025 target rows.
    cal=collect(2024,cases[2024],frozen,production,args.rebuild_cache)
    artifact={**protocol,'horizons':{}}
    for h in (1,2,3):
        take=cal['horizon']==h
        artifact['horizons'][str(h)]=rc.fit_horizon(cal['raw'][take],cal['y'][take],cal['group'][take])
    artifact_path=args.output_dir/'calibrator.json'
    artifact_path.write_text(json.dumps(artifact,indent=2))
    frozen_hash=sha(artifact_path)
    print('[calibration] coefficients frozen before test evaluation',flush=True)
    test=collect(2025,cases[2025],frozen,production,args.rebuild_cache)
    report=dict(protocol=protocol,artifact_sha256=frozen_hash,n_calibration_cases=len(cases[2024]),n_test_cases=len(cases[2025]),horizons={})
    mapped=np.empty(len(test['raw']))
    for h in (1,2,3):
        take=test['horizon']==h
        mapped[take]=rc.apply(test['raw'][take],h,artifact,source_sha256=sha(source),prediction_year=2025,observation_mode='archive')
    coherent=rc.cumulative(mapped,test['horizon'],test['case_index']*direct.S**2+test['cell_index'])
    report['coherence_adjusted_cells']=int(np.sum(coherent!=mapped))
    for h in (1,2,3):
        take=test['horizon']==h;p=test['raw'][take];y=test['y'][take];g=test['group'][take]
        calibrated=coherent[take]
        row=dict(parameters=artifact['horizons'][str(h)],raw_frozen=rc.metrics(p,y,g),calibrated=rc.metrics(calibrated,y,g),
          production_raw=rc.metrics(test['production'][take],y,g),
          brier_difference=rc.paired_bootstrap(p,calibrated,y,g),
          weather_fallback_cell_fraction=float(test['weather_fallback'][take].mean()))
        report['horizons'][str(h)]=row
        print(f"[test d{h}] Brier {row['raw_frozen']['brier']:.5f} -> {row['calibrated']['brier']:.5f}; "
              f"logloss {row['raw_frozen']['log_loss']:.5f} -> {row['calibrated']['log_loss']:.5f}; "
              f"ECE {row['raw_frozen']['ece']:.5f} -> {row['calibrated']['ece']:.5f}",flush=True)
    assert sha(artifact_path)==frozen_hash,'Test evaluation modified the fitted calibrator'
    report['elapsed_s']=round(time.perf_counter()-start,2)
    report['all_horizons_brier_improved']=all(r['calibrated']['brier']<r['raw_frozen']['brier'] for r in report['horizons'].values())
    report['limits']=['Event blocks are proximity proxies, not independently verified incidents.',
      'Satellite non-detections under cloud/smoke are incomplete ground truth.',
      'Calibrator targets the frozen2023-fit blender; it cannot be applied to the2024-fit production blender.',
      'Not validated for live reduced-history inputs or South Sudan.',
      'Calibration does not demonstrate improved perimeter/location accuracy; no selection thresholds retuned.',
      'No source model or running website was replaced.']
    (REPORTS/'daily_risk_calibration.json').write_text(json.dumps(report,indent=2))
    print('[done]',REPORTS/'daily_risk_calibration.json',report['elapsed_s'],'seconds',flush=True)


if __name__=='__main__':main()
