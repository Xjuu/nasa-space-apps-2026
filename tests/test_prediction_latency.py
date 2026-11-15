"""Latency changes preserve complete inputs and model outputs."""
import threading
import numpy as np
import pandas as pd
from backend import physics_spread as ps


def test_weather_and_landscape_overlap_and_join_before_simulation(monkeypatch):
    landscape_started, weather_started = threading.Event(), threading.Event()
    data = {'fuel_model': np.ones((2, 2), np.int16)}
    class Provider:
        seconds = 0.01
        def __init__(self, *a, **k):self.data = None
        def get(self):
            landscape_started.set()
            assert weather_started.wait(2), 'Weather must start before landscape finishes'
            self.data = data
            return data
    weather_grid = object()
    def weather(*a, **k):
        weather_started.set()
        assert landscape_started.wait(2), 'Landscape must start while weather loads'
        return weather_grid, {'errors': []}
    def simulate(*a, **k):
        assert k['landscape'].data is data
        assert k['wx_grid'] is weather_grid
        assert k['envelope'] and k['frames']
        return {'frames': [], 'weather': {}, 'summary': {'area_km2': 12.3}}
    monkeypatch.setattr(ps, 'LandscapeProvider', Provider)
    monkeypatch.setattr(ps, 'fetch_weather_grid', weather)
    monkeypatch.setattr(ps, 'resolve_ignition', lambda *a, **k: {'mode':'point', 'lats':np.array([10]), 'lons':np.array([20])})
    monkeypatch.setattr(ps, 'simulate', simulate)
    out = ps.forecast(10, 20, pd.Timestamp('2025-01-01'), hours=72, landscape='on', calibration='off', fwi=False)
    assert out['summary']['area_km2'] == 12.3
    assert out['timings']['landscape_fetch_s'] == 0.01
    assert all(out['timings'][key] >= 0 for key in ('hourly_total_s','weather_and_landscape_s','calibration_s','physical_simulation_s'))


def test_daily_cache_reuses_step_changes_without_crossing_initializations(monkeypatch):
    from collections import OrderedDict
    from backend import ensemble
    monkeypatch.setattr(ensemble, '_GUIDANCE_CACHE', OrderedDict())
    calls = []
    def predict(lat, lon, date, days, **kw):
        calls.append((lat, lon, date, days, kw['observation_cutoff']))
        return {'features': [], 'centre': {'lat':lat, 'lon':lon}, 'history': {'reduced':False}, 'warnings':[]}
    def hourly(lat, lon, start, hours, step_min, **kw):
        return {'frames':[{'time_utc':start.isoformat()+'Z'}], 'ignition':{'mode':'archive'}}
    monkeypatch.setattr(ensemble, 'predict', predict)
    t0 = pd.Timestamp('2025-01-01T12:00')
    first = ensemble.simulate_combined(10,20,t0,24,60,'archive',simulation_fn=hourly)
    first['guidance']['warnings'].append('caller mutation')
    second = ensemble.simulate_combined(10,20,t0,24,180,'archive',simulation_fn=hourly)
    assert len(calls)==1 and not second['guidance']['warnings']
    assert second['timings']['daily_guidance_cache_hit']
    ensemble.simulate_combined(10,20,t0+pd.Timedelta(hours=1),24,60,'archive',simulation_fn=hourly)
    ensemble.simulate_combined(10,20,t0,72,60,'archive',simulation_fn=hourly)
    assert len(calls)==3
    monkeypatch.setattr(ensemble, '_GUIDANCE_TTL_S', -1)
    ensemble.simulate_combined(10,20,t0,24,60,'archive',simulation_fn=hourly)
    assert len(calls)==4


def test_independent_satellite_feeds_download_concurrently(monkeypatch, tmp_path):
    from backend import live
    monkeypatch.setattr(live, 'CACHE_DIR', tmp_path)
    barrier = threading.Barrier(4)
    def download(url):
        barrier.wait(timeout=3)  # Serial loading or one global download lock fails.
        return b'latitude,longitude,acq_date,acq_time,frp\n'
    df, info = live.load_feeds('7d', fetch=download)
    assert df.empty
    assert [f['feed'] for f in info] == list(live.FEEDS)
    assert all(not f['stale'] and not f['error'] for f in info)


def test_same_feed_has_one_writer(monkeypatch, tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from backend import live
    monkeypatch.setattr(live, 'CACHE_DIR', tmp_path)
    entered, release = threading.Event(), threading.Event()
    calls = []
    def download(url):
        calls.append(url)
        entered.set()
        assert release.wait(3)
        return b'valid source bytes'
    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(live.fetch_feed, 'N20', '7d', download)
        assert entered.wait(2)
        b = pool.submit(live.fetch_feed, 'N20', '7d', download)
        release.set()
        first, second = a.result(), b.result()
    assert len(calls) == 1 and first['bytes'] == second['bytes'] == b'valid source bytes'


def test_parsed_feed_cache_invalidates_on_source_replacement(monkeypatch, tmp_path):
    from backend import live
    monkeypatch.setattr(live, 'CACHE_DIR', tmp_path)
    monkeypatch.setattr(live, '_parsed_feeds', {})
    original = live.parse_feed
    calls = []
    def parse(raw, name):
        calls.append(name)
        return original(raw, name)
    monkeypatch.setattr(live, 'parse_feed', parse)
    fetch = lambda url: b'latitude,longitude,acq_date,acq_time,frp\n'
    first, _ = live.load_feeds('7d', fetch=fetch)
    live.load_feeds('7d', fetch=fetch)
    assert len(calls)==4
    path = tmp_path / 'nrt_N20_7d.csv'
    path.write_bytes(path.read_bytes()+b'\n')
    live.load_feeds('7d', fetch=fetch)
    assert len(calls)==5 and calls[-1]=='N20'
