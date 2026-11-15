"""One forecast must contain both model guidance and physical playback, without silent fallback."""
import os
os.environ.setdefault('EQUIFIRE_NO_LOAD','1')
import pandas as pd
import pytest
from backend import ensemble

START=pd.Timestamp('2025-01-01T12:00')


def guidance(*args,**kwargs):
    return {'type':'FeatureCollection','features':[], 'centre':{'lat':12.5,'lon':23.5},
            'query':{'date':'2025-01-01'},'history':{'reduced':False},
            'per_day':[],'warnings':['Daily guidance, not hourly ML probabilities.']}


def simulation(lat,lon,start,hours,step_min,**kwargs):
    offsets=list(range(0,hours+1,max(1,step_min//60)))
    if offsets[-1]!=hours:offsets.append(hours)
    return {'frames':[{'time_utc':(start+pd.Timedelta(hours=h)).isoformat()+'Z','t_hours':h}
                      for h in offsets], 'ignition':{'mode':kwargs['ignition']},'summary':{}}


def test_both_components_share_initialization_and_horizon():
    calls={}
    def daily(lat,lon,date,days,**kwargs):
        calls['daily']=(lat,lon,date,days,kwargs)
        return guidance()
    def hourly(lat,lon,start,hours,step_min,**kwargs):
        calls['hourly']=(lat,lon,start,hours,step_min,kwargs)
        return simulation(lat,lon,start,hours,step_min,**kwargs)
    r=ensemble.simulate_combined(12.,23.,START,72,180,'archive',predict_fn=daily,simulation_fn=hourly)
    assert calls['daily'][3]==3 and calls['daily'][4]['observation_cutoff']==START
    assert calls['hourly'][:3]==(12.5,23.5,START)
    assert calls['hourly'][5]['weather']=='grid' and calls['hourly'][5]['landscape']=='on'
    assert r['prediction_mode']=='ml_physics' and r['guidance']['centre']['lat']==12.5
    assert {f['guidance_day'] for f in r['frames']}=={1,2,3}
    assert r['frames'][-1]['guidance_valid_until_utc']=='2025-01-05T00:00:00Z'


def test_daily_utc_horizons_are_preserved_on_short_timeline():
    r=ensemble.simulate_combined(12,23,START,3,720,'archive',predict_fn=guidance,simulation_fn=simulation)
    assert [f['t_hours'] for f in r['frames']]==[0,3]
    assert all(f['guidance_day']==1 for f in r['frames'])
    assert all(f['guidance_valid_until_utc']=='2025-01-03T00:00:00Z' for f in r['frames'])


def test_missing_ml_cannot_fall_back_to_physics():
    def missing(*a,**k):raise ensemble.PredictorUnavailable('ML missing')
    def should_not_run(*a,**k):pytest.fail('Physics-only fallback must not run')
    with pytest.raises(ensemble.PredictorUnavailable,match='ML missing'):
        ensemble.simulate_combined(12,23,START,3,60,'archive',predict_fn=missing,simulation_fn=should_not_run)


def test_point_fallback_cannot_be_labelled_combined():
    def point(*a,**k):return {'ignition':{'mode':'point'},'frames':[]}
    with pytest.raises(ensemble.NoIgnition):
        ensemble.simulate_combined(12,23,START,3,60,'archive',predict_fn=guidance,simulation_fn=point)
    with pytest.raises(ValueError,match='satellite ignition'):
        ensemble.simulate_combined(12,23,START,3,60,'point')


def test_failed_physics_cannot_return_guidance_only():
    def failed(*a,**k):raise RuntimeError('Physics unavailable')
    with pytest.raises(RuntimeError,match='Physics unavailable'):
        ensemble.simulate_combined(12,23,START,3,60,'archive',predict_fn=guidance,simulation_fn=failed)


def test_api_validates_horizon_and_component_failure(monkeypatch):
    from fastapi.testclient import TestClient
    from backend import app as api
    def unavailable(*a,**k):raise ensemble.PredictorUnavailable('Combined model unavailable')
    monkeypatch.setattr(ensemble,'simulate_combined',unavailable)
    api._COMBINED_SIM_CACHE.clear()
    client=TestClient(api.app)
    assert client.get('/predict/simulate?lat=0&lon=0&hours=73').status_code==422
    r=client.get('/predict/simulate?lat=0&lon=0&start=2025-01-01T12:00&ignition=archive')
    assert r.status_code==503 and r.json()['detail']=='Combined model unavailable'


def test_live_physics_cannot_use_detections_after_initialization(monkeypatch):
    from backend import live
    df=pd.DataFrame({'t':[START-pd.Timedelta(hours=1),START+pd.Timedelta(hours=1),START-pd.Timedelta(hours=25)]})
    monkeypatch.setattr(live,'load_feeds',lambda **k:(df,[]))
    def current(*a,**k):
        d=guidance();d['history']['reduced']=True;return d
    def hourly(*a,**k):
        selected,_=k['live_loader']()
        assert selected['t'].tolist()==[START-pd.Timedelta(hours=1)]
        return simulation(*a,**k)
    ensemble.simulate_combined(12,23,START,3,60,'live',predict_fn=current,simulation_fn=hourly)
