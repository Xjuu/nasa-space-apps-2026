"""NOAA forecast parsing and hourly interpolation; no network needed."""
from pathlib import Path
import numpy as np
import pandas as pd
import pytest
from equifire import gfs_weather as gfs


def test_real_regional_grib_units_levels_and_valid_time():
    raw=(Path(__file__).parent/'data_gfs/regional_20261114_12_f009.grib2').read_bytes()
    d=gfs.decode(raw,[-18.25],[124.5],pd.Timestamp('2026-11-14T21:00'))
    assert 20 < d['temperature_2m'][0,0] < 45
    assert 10 < d['relative_humidity_2m'][0,0] < 90
    assert 0 <= d['precipitation'][0,0] < 10
    assert d['wind_gusts_10m'][0,0] > 5
    with pytest.raises(ValueError,match='lacks required'):
        gfs.decode(raw,[-18.25],[124.5],pd.Timestamp('2026-11-14T22:00'))


def test_hourly_interpolation_uses_wind_vectors_and_keeps_source_times():
    now=pd.Timestamp('2026-11-14T20:00')
    def slices(lats,lons,valid,asof):
        shape=(len(lats),len(lons))
        hour=(valid-now.floor('3h')).total_seconds()/3600
        # north wind turning across 360 degrees, never via southerly 180 degrees
        angle=np.radians(350+hour*20/3)
        d={k:np.full(shape,value) for k,value in {
            'temperature_2m':20+hour,'relative_humidity_2m':50,'u':-5*np.sin(angle),'v':-5*np.cos(angle),
            'wind_gusts_10m':25,'precipitation':.1*hour}.items()}
        return d,gfs.cycle_for(valid,asof).isoformat()+'Z'
    wg=gfs.get_weather_grid(-18.3,124.4,10,now,6,n=3,as_of=now,slice_fn=slices)
    assert wg.arrays['temperature_2m'].shape==(6,3,3)
    assert np.all(np.diff(wg.arrays['temperature_2m'][:,0,0])==pytest.approx(1))
    assert wg.arrays['wind_direction_10m'][:,0,0].max()<60
    assert wg.source_meta['source_interval_hours']==3 and wg.source_meta['output_interval_hours']==1
    assert max(pd.Timestamp(t).tz_localize(None) for t in wg.source_meta['cycles_utc']) <= now


def test_old_replay_is_not_filled_with_current_forecasts():
    with pytest.raises(ValueError,match='recent history'):
        gfs.get_weather_grid(0,0,1,'2024-01-01',6,as_of='2026-11-14')


def test_429_interactive_call_does_not_wait_for_another_retry(monkeypatch,tmp_path):
    from equifire import fireweather
    from types import SimpleNamespace
    monkeypatch.setattr(fireweather,'HTTP_DIR',tmp_path)
    monkeypatch.setattr(fireweather,'_LAST_429',0)
    monkeypatch.setattr(fireweather,'_throttle',lambda:None)
    monkeypatch.setattr(fireweather.requests,'get',lambda *a,**k:SimpleNamespace(status_code=429,headers={}))
    monkeypatch.setattr(fireweather.time,'sleep',lambda *a:pytest.fail('No retry sleep expected'))
    with pytest.raises(RuntimeError,match='429'):
        fireweather._get_json('https://test.invalid',{},1,retries=1)


def test_physics_automatically_uses_noaa_when_openmeteo_fails(monkeypatch):
    from backend import physics_spread as ps
    from equifire import fireweather
    from types import SimpleNamespace
    def unavailable(*a,**k):
        raise RuntimeError('429 test')
    sentinel=SimpleNamespace(source_meta={'provider':'noaa_gfs','api':'NOAA GFS via NOMADS'})
    monkeypatch.setattr(fireweather,'get_weather_grid',unavailable)
    monkeypatch.setattr(gfs,'get_weather_grid',lambda *a,**k:sentinel)
    wg,meta=ps.fetch_weather_grid(-18.3,124.4,pd.Timestamp('2026-11-14'),6)
    assert wg is sentinel and meta['provider']=='noaa_gfs'
    assert '429 test' in meta['errors'][0]
