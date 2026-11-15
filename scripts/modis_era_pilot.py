import sys,json,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
from threadpoolctl import threadpool_limits
from equifire import direct
from equifire.fireindex_mm import get_fire_index
import walk_forward as wf
outdir=Path('models/modis_era_pilot');outdir.mkdir(exist_ok=True)
fi=get_fire_index(False);wx=direct.Weather()
year=2024;days=3;seed=71
lo,hi=wf._day(year),wf._day(year+1)-1-days
cases=sorted(direct.sample_cases(fi,(fi.day>=lo)&(fi.day<=hi),15,np.random.default_rng(1000+year)))
result={'test_year':year,'test_fires':len(cases),'training_requested':40,'validation_requested':20,
        'reconstructed_cases_requested':20,'synthetic_weight':0.5,'seed':seed,'days':days,
        'caveat':'Small paired smoke experiment, not an accuracy claim or deployment selection.',
        'case_selection':'Same 15 test cases for both variants; no tuning on their outcomes.','variants':{}}
with threadpool_limits(limits=2):
 for name,n_old in [('modern',0),('with_modis_era',20)]:
  started=time.monotonic()
  model,meta=direct.train(None,year,n_cases=40,n_val=20,seed=seed,features=direct.V2_FEATURES,
                         model_f=outdir/(name+'.json'),modis_era_cases=n_old,modis_era_weight=.5)
  scores=[wf.score(model,meta,fi,wx,c,days) for c in cases]
  valid=[r for r in scores if r is not None]
  strict=np.array([r[0] for r in valid]);tol=np.array([r[1] for r in valid])
  result['variants'][name]={'strict_by_day':strict.mean(0).tolist(),'tolerant_by_day':tol.mean(0).tolist(),
                            'fires':len(valid),'seconds':round(time.monotonic()-started,1),
                            'modis_era':meta.get('modis_era')}
  print(name,result['variants'][name],flush=True)
Path('reports/modis_era_pilot.json').write_text(json.dumps(result,indent=2))
