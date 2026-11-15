"""Chronologically separated short-horizon walk-forward experiment.

Year Y: fit before Y-2, early-stop during Y-2, calibrate policies during Y-1,
then test Y. No source model/report from the original walk-forward run is overwritten.
"""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import xgboost as xgb
from sklearn.metrics import average_precision_score
from equifire import direct, short_spread, spread_policy as policy
from equifire.common import EPOCH
from equifire.fireindex_mm import get_fire_index, DIR as INDEX_DIR
from tune_spread_policy import dataset, aggregate, day


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--years',nargs='+',type=int,default=[2023,2024,2025])
    ap.add_argument('--cases',type=int,default=2000)
    ap.add_argument('--early-cases',type=int,default=300)
    ap.add_argument('--calibration-cases',type=int,default=300)
    ap.add_argument('--runs',type=int,default=80)
    ap.add_argument('--workers',type=int,default=4)
    ap.add_argument('--threads',type=int,default=4)
    ap.add_argument('--ncep-weather',action='store_true',help='use cached global daily NOAA weather through t0-1')
    ap.add_argument('--neighbourhood-day1',action='store_true')
    ap.add_argument('--subcell',action='store_true')
    ap.add_argument('--gpu',action='store_true')
    ap.add_argument('--output-dir',type=Path,default=Path('models/short_spread'))
    ap.add_argument('--report',type=Path,default=Path('reports/short_spread.json'))
    a=ap.parse_args()
    fi=get_fire_index()
    if a.ncep_weather:
        from equifire.ncep_weather import NCEPWeather, DIR as WEATHER_DIR, SPECS
        missing=[f'{stem}.{year}.nc' for year in range(2012,max(a.years)+1)
                 for folder,stem in SPECS.values() if not (WEATHER_DIR/f'{stem}.{year}.nc').exists()]
        if missing:ap.error(f'NOAA cache incomplete ({len(missing)} files); run python -m equifire.ncep_weather first')
        weather=NCEPWeather()
    else:
        weather=direct.Weather()
    cols=list(direct.V3_FEATURES if a.subcell else direct.V2_FEATURES)
    if a.subcell and getattr(fi,'sub',None) is None:
        ap.error('subcell cache is not ready')
    a.output_dir.mkdir(parents=True,exist_ok=True)
    results=[]
    for year in a.years:
        path=a.output_dir/f'{year}.json'
        metaf=path.with_name(path.stem+'_meta.json')
        settings={'cases':a.cases,'early_cases':a.early_cases,'calibration_cases':a.calibration_cases,
                  'features':cols,'index':json.loads((INDEX_DIR/'meta.json').read_text()),
                  'objective':'equal-fire-horizon-logloss-v1','neighbourhood_day1':a.neighbourhood_day1,'ncep_weather':a.ncep_weather,
                  'subcell_manifest': ({p.name: [p.stat().st_size,p.stat().st_mtime_ns] for p in Path('cache/subcell').glob('*.npy')} if a.subcell else None)}
        previous=json.loads(metaf.read_text()).get('settings',{}) if metaf.exists() else {}
        previous.setdefault('neighbourhood_day1',False)
        previous.setdefault('subcell_manifest',None)
        previous.setdefault('ncep_weather',False)
        if path.exists() and metaf.exists() and previous==settings:
            model,meta=direct.load(path)
        else:
            ok=(fi.day+3<day(year-2)) & (fi.day-direct.LONG>=fi.day.min())
            train=direct.sample_cases(fi,ok,a.cases,np.random.default_rng(year))
            early=direct.sample_cases(fi,(fi.day>=day(year-2))&(fi.day+3<day(year-1)),a.early_cases,np.random.default_rng(year+10))
            if a.ncep_weather:
                train=sorted(train)
                early=sorted(early)
            print(f'{year}: {len(train)} training, {len(early)} early stopping fires; calibration reserved {year-1}',flush=True)
            Xt,yt,wt=short_spread.rows(fi,train,weather,cols,year,a.neighbourhood_day1)
            Xv,yv,wv=short_spread.rows(fi,early,weather,cols,year+1,a.neighbourhood_day1)
            model=xgb.XGBClassifier(objective='binary:logistic',tree_method='hist',device='cuda' if a.gpu else 'cpu',
                n_jobs=a.threads,n_estimators=1200,max_depth=6,learning_rate=.05,min_child_weight=10,
                subsample=.8,colsample_bytree=.8,reg_lambda=2.,eval_metric='logloss',early_stopping_rounds=60,random_state=0)
            model.fit(Xt,yt,sample_weight=wt,eval_set=[(Xv,yv)],sample_weight_eval_set=[wv],verbose=200)
            best=model.best_iteration+1
            model=direct._reload(model.get_booster()[:best])
            print(f'{year}: {best} trees, early AP={average_precision_score(yv,direct._proba(model,Xv)):.4f}',flush=True)
            meta={'features':cols+['k'],'test_from':year-2,'settings':settings,'training_before_year':year-2,
                  'weather_source':'NCEP sigma995 daily means t0-1 + daily precipitation' if a.ncep_weather else 'legacy cached ERA5',
                  'early_stopping_year':year-2,'calibration_year':year-1,'horizons':[1,2,3],
                  'rules':[['thr',.2]]*3,'params':{'max_depth':6},'trees':best}
            model.save_model(path)
            metaf.write_text(json.dumps(meta,indent=2))
            del Xt,yt,wt,Xv,yv,wv
        model.set_params(n_jobs=1,device='cpu')
        digest=hashlib.sha256(path.read_bytes()+(INDEX_DIR/'meta.json').read_bytes()).hexdigest()[:16]
        calibration=sorted(direct.sample_cases(fi,(fi.day>=day(year-1))&(fi.day+3<day(year)),a.calibration_cases,np.random.default_rng(500+year)))
        test=sorted(direct.sample_cases(fi,(fi.day>=day(year))&(fi.day<=min(day(year+1)-1,int(fi.day.max())-3)),a.runs,np.random.default_rng(1000+year)))
        ca=dataset(model,meta,fi,weather,calibration,a.workers,Path(f'cache/spread_policy/{digest}_{year}_val{a.calibration_cases}.npz'))
        te=dataset(model,meta,fi,weather,test,a.workers,Path(f'cache/spread_policy/{digest}_{year}_test{a.runs}.npz'))
        chosen=[]
        values=np.zeros((len(te['X']),3,2))
        for k,obj in enumerate([1,0,0]):
            j=int(ca['scores'][:,k,:,obj].mean(0).argmax())
            chosen.append(policy.RULES[j])
            values[:,k]=te['scores'][:,k,j]
        meta['policy_rules']=chosen
        metaf.write_text(json.dumps(meta,indent=2))
        record={'year':year,'model':str(path),'calibration_disjoint_from_base_fit':True,
                'policy_rules':chosen,'scores':aggregate(values),'empty_truth_rate':(te['truth_count']==0).mean(0).tolist(),
                'ranking_oracle_diagnostic_only':te['oracle'].mean(0).tolist()}
        results.append(record)
        a.report.parent.mkdir(parents=True,exist_ok=True)
        a.report.write_text(json.dumps(results,indent=2))
        print(f"RESULT {year}: strict d3={record['scores']['iou'][2]:.1%}; tolerant d1={record['scores']['tol'][0]:.1%}",flush=True)


if __name__=='__main__':main()
