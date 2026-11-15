#!/usr/bin/env python3
"""Calibrate the CURRENT frozen2024-fit blender: Jan–May2025 -> July–Dec2025.

This is a retrospective reuse of an existing benchmark, not fresh prospective
validation. Fixed chronological split with a June embargo; no test-driven tuning.
"""
import sys,json,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'scripts'))
import numpy as np
from calibrate_daily_risk import collect,sha
from equifire import direct,risk_calibration as rc
from equifire.common import MODELS,REPORTS,EPOCH
from equifire.ensemble_core import FEATS


def main():
    from threadpoolctl import threadpool_limits
    threadpool_limits(2)
    import xgboost as xgb
    start=time.perf_counter()
    source=MODELS/'ensemble_blend_2025.json';meta=json.loads((MODELS/'ensemble_blend_2025_meta.json').read_text())
    assert meta['fit_year']==2024 and meta['features']==FEATS
    model=xgb.XGBClassifier(n_jobs=2,device='cpu');model.load_model(source);model.set_params(n_jobs=2,device='cpu')
    all_cases=json.loads((REPORTS/'ensemble_ab_2025.json').read_text())['test_cases']
    date=lambda c:EPOCH+np.timedelta64(c[0],'D')
    cal_cases=[c for c in all_cases if date(c)+np.timedelta64(3,'D')<=np.datetime64('2025-05-31')]
    test_cases=[c for c in all_cases if date(c)>=np.datetime64('2025-07-01')]
    assert max(date(c)+np.timedelta64(3,'D') for c in cal_cases)+np.timedelta64(30,'D')<min(map(date,test_cases))
    output=MODELS/'daily_risk_calibration_current';output.mkdir(exist_ok=True)
    protocol=dict(version=1,source_file=source.name,source_sha256=sha(source),source_fit_year=2024,
      source_meta_sha256=sha(MODELS/'ensemble_blend_2025_meta.json'),base_ml_sha256=sha(MODELS/'wf_2025.json'),
      calibration_outcomes_through_year=2025,calibration_outcomes_through_date='2025-05-31',test_year=2025,
      days=3,features=FEATS,calibration_cases=cal_cases,test_cases=test_cases,
      target='observed newly burning eligible coarse cell by horizon day; cumulative, not disjoint daily hazard',
      weighting='equal total weight per 50km/28day spatial-temporal proxy block',
      method='fixed per-horizon monotone logistic calibration and cumulative-maximum temporal coherence',
      selection='current-production source chosen for compatibility, not by comparing test winners; no parameter search',
      scope='archive only; retrospective reuse of previously examined2025 benchmark; not independent prospective validation',
      deployment_status='research_candidate_not_promoted')
    (output/'protocol.json').write_text(json.dumps(protocol,indent=2))
    cal=collect(2025,cal_cases,model,model,source_path=source)
    artifact={**protocol,'horizons':{}}
    for h in (1,2,3):
        m=cal['horizon']==h;artifact['horizons'][str(h)]=rc.fit_horizon(cal['raw'][m],cal['y'][m],cal['group'][m])
    path=output/'calibrator.json';path.write_text(json.dumps(artifact,indent=2));frozen_hash=sha(path)
    print('[current] frozen on',len(cal_cases),'cases before scoring',len(test_cases),'later cases',flush=True)
    test=collect(2025,test_cases,model,model,source_path=source)
    mapped=np.empty(len(test['raw']))
    first_date=str(min(map(date,test_cases)))
    for h in (1,2,3):
        m=test['horizon']==h
        mapped[m]=rc.apply(test['raw'][m],h,artifact,source_sha256=sha(source),prediction_year=2025,
                          observation_mode='archive',prediction_date=first_date)
    coherent=rc.cumulative(mapped,test['horizon'],test['case_index']*direct.S**2+test['cell_index'])
    report=dict(protocol=protocol,artifact_sha256=frozen_hash,n_calibration_cases=len(cal_cases),n_test_cases=len(test_cases),
                coherence_adjusted_cells=int(np.sum(coherent!=mapped)),horizons={})
    for h in (1,2,3):
        m=test['horizon']==h;p=test['raw'][m];y=test['y'][m];g=test['group'][m];c=coherent[m]
        row=dict(parameters=artifact['horizons'][str(h)],raw_frozen=rc.metrics(p,y,g),calibrated=rc.metrics(c,y,g),
                 production_raw=rc.metrics(p,y,g),brier_difference=rc.paired_bootstrap(p,c,y,g),
                 weather_fallback_cell_fraction=float(test['weather_fallback'][m].mean()))
        report['horizons'][str(h)]=row
        print(f"[current d{h}] Brier {row['raw_frozen']['brier']:.5f}->{row['calibrated']['brier']:.5f}; "
              f"ECE {row['raw_frozen']['ece']:.5f}->{row['calibrated']['ece']:.5f}",flush=True)
    assert sha(path)==frozen_hash
    report['elapsed_s']=round(time.perf_counter()-start,2)
    report['limits']=['Retrospective reused benchmark, not a fresh independent test.',
      'Chronological calibration and test fire seasons differ; no test-year parameter tuning.',
      'Not validated for live reduced-history input, South Sudan, or physical perimeter confidence.',
      'No production weights, selection rules, API or website confidence labels replaced.']
    (REPORTS/'daily_risk_calibration_current.json').write_text(json.dumps(report,indent=2))
    print('[current done]',report['elapsed_s'],'seconds',flush=True)

if __name__=='__main__':main()
