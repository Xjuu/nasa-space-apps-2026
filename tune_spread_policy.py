"""Diagnose and tune coarse spread policies without changing truth or score definitions."""
import argparse
from concurrent.futures import ProcessPoolExecutor
import hashlib
import json
import multiprocessing as mp
from pathlib import Path

import joblib
import numpy as np
from sklearn.ensemble import ExtraTreesRegressor
from threadpoolctl import threadpool_limits

from equifire import direct, spread_policy as policy
from equifire.common import EPOCH
from equifire.fireindex_mm import get_fire_index, DIR as INDEX_DIR

_STATE = {}


def day(year):
    return int((np.datetime64(f'{year}-01-01') - EPOCH).astype(int))


def _init():
    threadpool_limits(1)
    _STATE['model'].get_booster().set_param({'nthread': 1, 'device': 'cpu'})


def _job(case):
    result = direct.predict_case(_STATE['model'], _STATE['fi'], *case, _STATE['weather'], 3,
                                 _STATE['meta']['features'])
    if result is None:
        return None
    P, A, distance, A0 = result
    truth = direct.case_truth(A, distance, A0, 3)
    scores = policy.score_rules(P, truth)
    base = []
    for k in range(3):
        mask = direct.select(P[k], direct.rule_for(_STATE['meta'], k+1))
        base.append([direct.iou_masks(mask, truth[k]), policy.tolerant_score(mask, truth[k])])
    # Label-dependent ranking oracle is DIAGNOSTIC ONLY, never a forecast.
    oracle = []
    for k in range(3):
        eligible = np.isfinite(P[k])
        p, y = P[k][eligible], truth[k][eligible]
        order = np.argsort(-p, kind='stable')
        hits = np.cumsum(y[order])
        n = np.arange(1, len(p)+1)
        iou = hits / np.maximum(n+y.sum()-hits, 1)
        oracle.append(max(float(iou.max()) if len(iou) else 0., float(not truth[k].any())))
    return policy.summary_features(P, A, case), scores, np.asarray(base), truth.sum((1, 2)), oracle


def dataset(model, meta, fi, weather, cases, workers, cache):
    signature=json.dumps(policy.RULES)
    if cache.exists():
        with np.load(cache) as z:
            if 'rule_signature' in z.files and str(z['rule_signature'])==signature:
                return {k:z[k] for k in z.files if k!='rule_signature'}
    _STATE.update(model=model, meta=meta, fi=fi, weather=weather)
    if workers == 1:
        _init()
        rows = [_job(c) for c in cases]
    else:
        with ProcessPoolExecutor(workers, mp_context=mp.get_context('fork'), initializer=_init) as ex:
            rows = list(ex.map(_job, cases, chunksize=2))
    pairs = [(c,r) for c,r in zip(cases, rows) if r is not None]
    if not pairs:
        raise ValueError('no eligible forecast cases')
    data = dict(cases=np.array([c for c,r in pairs]), X=np.array([r[0] for c,r in pairs]),
                scores=np.array([r[1] for c,r in pairs]), base=np.array([r[2] for c,r in pairs]),
                truth_count=np.array([r[3] for c,r in pairs]), oracle=np.array([r[4] for c,r in pairs]))
    cache.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(cache, **data, rule_signature=np.array(signature))
    return data


def aggregate(values):
    return {'iou': values[:,:,0].mean(0).tolist(), 'tol': values[:,:,1].mean(0).tolist(), 'n':len(values)}


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--years',type=int,nargs='+',default=[2023,2024,2025])
    ap.add_argument('--validation-cases',type=int,default=400)
    ap.add_argument('--runs',type=int,default=80)
    ap.add_argument('--workers',type=int,default=4)
    ap.add_argument('--model-offset',type=int,default=0,choices=[0,1],help='1 reserves the latest pre-test year from base fitting')
    ap.add_argument('--output',type=Path,default=Path('reports/spread_policy.json'))
    a=ap.parse_args()
    fi=get_fire_index()
    weather=direct.Weather()
    results=[]
    for year in a.years:
        model_year=year-a.model_offset
        model_path=Path(f'models/wf_{model_year}.json')
        model, meta=direct.load(model_path)
        if model is None:
            raise SystemExit(f'missing {model_path}; train its pre-{model_year} base model first')
        digest=hashlib.sha256(model_path.read_bytes()+(INDEX_DIR/'meta.json').read_bytes()).hexdigest()[:16]
        validation=sorted(direct.sample_cases(fi,(fi.day>=day(year-1))&(fi.day+3<day(year)),a.validation_cases,np.random.default_rng(500+year)))
        test=sorted(direct.sample_cases(fi,(fi.day>=day(year))&(fi.day<=min(day(year+1)-1,int(fi.day.max())-3)),a.runs,np.random.default_rng(1000+year)))
        va=dataset(model,meta,fi,weather,validation,a.workers,Path(f'cache/spread_policy/{digest}_{year}_val{a.validation_cases}.npz'))
        te=dataset(model,meta,fi,weather,test,a.workers,Path(f'cache/spread_policy/{digest}_{year}_test{a.runs}.npz'))
        # Optimize the requested metric per horizon: tolerance on day 1, strict IoU on days 2/3.
        objectives=[1,0,0]
        policies=[]
        fixed=np.zeros((len(te['X']),3,2))
        adaptive=np.zeros_like(fixed)
        # Internal chronological holdout chooses meta-model complexity; test remains untouched.
        cut=max(1,int(len(va['X'])*.7))
        for k, objective in enumerate(objectives):
            y=va['scores'][:,k,:,objective]
            best=int(y.mean(0).argmax())
            fixed[:,k]=te['scores'][:,k,best]
            candidates=[]
            for leaf in (15,40,100):
                reg=ExtraTreesRegressor(n_estimators=150,min_samples_leaf=leaf,max_features=.8,n_jobs=1,random_state=0)
                reg.fit(va['X'][:cut],y[:cut])
                chosen=reg.predict(va['X'][cut:]).argmax(1)
                value=float(y[cut:][np.arange(len(chosen)),chosen].mean())
                candidates.append((value,leaf))
            fixed_value=float(y[cut:,best].mean())
            value,leaf=max(candidates)
            if value > fixed_value:
                reg=ExtraTreesRegressor(n_estimators=150,min_samples_leaf=leaf,max_features=.8,n_jobs=1,random_state=0).fit(va['X'],y)
                chosen=reg.predict(te['X']).argmax(1)
                adaptive[:,k]=te['scores'][:,k][np.arange(len(chosen)),chosen]
            else:
                reg=None
                adaptive[:,k]=fixed[:,k]
            policies.append({'fixed_rule':policy.RULES[best], 'adaptive_leaf':leaf if reg is not None else None,
                             'internal_adaptive':value,'internal_fixed':fixed_value})
            out=Path(f'models/spread_policy/{year}_{model_year}_{k+1}.joblib')
            out.parent.mkdir(parents=True,exist_ok=True)
            joblib.dump({'regressor':reg,'fixed_rule':policy.RULES[best],'rules':policy.RULES,'base_model':str(model_path)},out)
        result={'year':year,'base_model':str(model_path),'calibration_disjoint_from_base_fit':meta['test_from']<=year-1,
                'baseline':aggregate(te['base']),'fixed_policy':aggregate(fixed),'adaptive_policy':aggregate(adaptive),
                'policies':policies,'empty_truth_rate':(te['truth_count']==0).mean(0).tolist(),
                'ranking_oracle_diagnostic_only':te['oracle'].mean(0).tolist(),
                'policy_oracle_diagnostic_only':te['scores'].max(2).mean(0).tolist()}
        results.append(result)
        a.output.parent.mkdir(parents=True,exist_ok=True)
        a.output.write_text(json.dumps(results,indent=2))
        for name in ('baseline','fixed_policy','adaptive_policy'):
            s=result[name]
            print(f"{year} {name}: strict d3={s['iou'][2]:.1%}, tol d1={s['tol'][0]:.1%}",flush=True)
        print(f"{year} ranking oracle (uses truth, NOT deployable): {result['ranking_oracle_diagnostic_only']}",flush=True)
        print(f"{year} calibration disjoint: {result['calibration_disjoint_from_base_fit']}",flush=True)


if __name__=='__main__':main()
