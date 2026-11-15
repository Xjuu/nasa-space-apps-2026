"""Exact solver optimization and real overlap; no reduced inputs or response deadlines."""
import threading
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

from backend import ensemble, physics_spread as ps
from equifire import landscape as land


def test_barrier_crossings_match_original_rounding_for_every_valid_edge():
    n = 11
    a = np.random.default_rng(42).random((n, n))
    a[3, :] = 0  # impermeable barrier; long edges must not jump it
    got = ps._edge_entries(a, n)
    for k, (dx, dy) in enumerate(ps.NEIGH):
        for y in range(n):
            for x in range(n):
                if not (0 <= x + dx < n and 0 <= y + dy < n):
                    continue
                crossed = {(round(y + dy * q / 8), round(x + dx * q / 8)) for q in range(1, 9)}
                assert got[k, y * n + x] == min(a[yy, xx] for yy, xx in crossed)


def test_hourly_work_overlaps_daily_guidance_and_both_complete(monkeypatch):
    ensemble._GUIDANCE_CACHE.clear()
    hourly_started, daily_done = threading.Event(), threading.Event()
    centre = {'lat': 12.5, 'lon': 23.5}
    start = pd.Timestamp('2025-01-01T12:00')
    def daily(*a, **k):
        k['on_ignition'](centre, 'archive')
        assert hourly_started.wait(2), 'Hourly work was serialized after daily guidance'
        daily_done.set()
        return {'centre': centre, 'history': {'reduced': False}, 'features': []}
    def hourly(lat, lon, t0, hours, step, **k):
        assert (lat, lon, t0) == (12.5, 23.5, start)
        assert k['weather'] == 'grid' and k['landscape'] == 'on'
        hourly_started.set()
        assert daily_done.wait(2)
        return {'ignition': {'mode': 'archive'}, 'frames': [{'time_utc': start.isoformat() + 'Z'}]}
    monkeypatch.setattr(ensemble, 'predict', daily)
    result = ensemble.simulate_combined(12, 23, start, 24, 60, 'archive', simulation_fn=hourly)
    assert result['prediction_mode'] == 'ml_physics' and daily_done.is_set()
    assert len(result['frames']) == 1 and result['guidance']['centre'] == centre
    ensemble._GUIDANCE_CACHE.clear()


def test_distinct_daily_guidance_does_not_hold_global_lock_during_work(monkeypatch):
    ensemble._GUIDANCE_CACHE.clear()
    gate = threading.Barrier(2)
    def daily(lat, *a, **k):
        gate.wait(2)
        return {'centre': {'lat': lat, 'lon': 0}, 'features': []}
    monkeypatch.setattr(ensemble, 'predict', daily)
    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(ensemble._daily_guidance, lat, 0, pd.Timestamp('2025-01-01'), 1) for lat in (1, 2)]
        assert [f.result()[0]['centre']['lat'] for f in futures] == [1, 2]
    ensemble._GUIDANCE_CACHE.clear()


def test_osm_boxes_overlap_without_losing_coverage(monkeypatch):
    gate = threading.Barrier(2)
    calls = []
    def fetch(*args):
        calls.append(args[:4])
        gate.wait(2)
        return {'elements': [], 'osm3s': {'timestamp_osm_base': '2026-11-15'}}, {'cached': False}
    monkeypatch.setattr(land, 'fetch_osm', fetch)
    layers, meta = land.osm_layer(land.LGrid(18.4, 78.6, 50_000, 250))
    assert len(calls) == 4 and meta['sub_boxes'] == 4
    assert meta['coverage_pct'] == 100 and not meta['failed_boxes']
    assert set(layers) == {'barrier_lines', 'barrier_area'}


def test_combined_missing_satellite_stops_before_weather_or_landscape(monkeypatch):
    import pytest
    monkeypatch.setattr(ps, 'resolve_ignition', lambda *a, **k: {'mode': 'point', 'reason': 'no nearby fire'})
    def forbidden(*a, **k):
        raise AssertionError('Missing satellite ignition must stop before input downloads')
    monkeypatch.setattr(ps, 'fetch_weather_grid', forbidden)
    monkeypatch.setattr(ps, 'LandscapeProvider', forbidden)
    with pytest.raises(LookupError, match='no nearby fire'):
        ps.forecast(18, 78, '2026-11-14T22:00', ignition='live', require_satellite=True)


def test_identical_daily_guidance_requests_share_one_calculation(monkeypatch):
    import time
    ensemble._GUIDANCE_CACHE.clear()
    entered, release = threading.Event(), threading.Event()
    calls = []
    def daily(*a, **k):
        calls.append(1)
        entered.set()
        assert release.wait(2)
        return {'centre': {'lat': 12, 'lon': 23}, 'features': []}
    monkeypatch.setattr(ensemble, 'predict', daily)
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(ensemble._daily_guidance, 12, 23, pd.Timestamp('2025-01-01'), 1)
        assert entered.wait(2)
        second = pool.submit(ensemble._daily_guidance, 12, 23, pd.Timestamp('2025-01-01'), 1)
        release.set()
        a, b = first.result(), second.result()
    assert len(calls) == 1
    a[0]['features'].append('mutation')
    assert not b[0]['features']
    ensemble._GUIDANCE_CACHE.clear()


def test_optional_barriers_never_wait_for_network_and_queue_preparation(monkeypatch, tmp_path):
    import requests
    monkeypatch.setattr(land, 'CACHE', tmp_path)
    queued = []
    monkeypatch.setattr(land, '_schedule_osm', lambda grid, key: queued.append(key) or True)
    def forbidden(*a, **k):
        raise AssertionError('Forecast waited for uncached optional barriers')
    monkeypatch.setattr(requests, 'post', forbidden)
    out = land.get_landscape(18.4, 78.6, 5, layers=('osm',), osm_mode='cached')
    meta = out['meta']['layers']['osm']
    assert meta['optional'] and meta['cache_only'] and meta['coverage_pct'] == 0
    assert meta['refresh_queued'] and queued and 'not awaited' in meta['reason']
    assert not list(tmp_path.glob('osm_*.npz')), 'Incomplete optional data must not shadow full layer cache'


def test_optional_barriers_use_complete_cached_raster_without_refresh(monkeypatch, tmp_path):
    monkeypatch.setattr(land, 'CACHE', tmp_path)
    key = (18.4, 78.6, 5.0, 250.0)
    grid = land.LGrid(key[0], key[1], key[2] * 1000, key[3])
    barrier = np.full((grid.n, grid.n), .4, np.float32)
    layers = {'barrier_lines': barrier, 'barrier_area': np.zeros_like(barrier)}
    path = tmp_path / ('osm_%s.npz' % land._cache_key('osm', *key))
    land._save_npz(path, layers, {'coverage_pct': 100.0, 'source': 'cached OpenStreetMap'})
    def forbidden(*a, **k):
        raise AssertionError('Complete cached barriers need no download')
    monkeypatch.setattr(land, '_schedule_osm', forbidden)
    monkeypatch.setattr(land, 'fetch_osm', forbidden)
    out = land.get_landscape(18.4, 78.6, 5, layers=('osm',), osm_mode='cached')
    np.testing.assert_array_equal(out['barrier_lines'], barrier)
    assert out['meta']['layers']['osm']['coverage_pct'] == 100


def test_combined_hourly_explicitly_uses_optional_cached_barriers(monkeypatch):
    from tests.test_combined_simulation import guidance, simulation
    def hourly(*a, **k):
        assert k['osm_mode'] == 'cached' and k['require_satellite']
        return simulation(*a, **k)
    monkeypatch.setattr(ps, 'forecast', hourly)
    ensemble.simulate_combined(12, 23, '2025-01-01T12:00', 24, 60, 'archive', predict_fn=guidance)


def test_sentinel_scene_overlap_preserves_newest_clear_pixel_mosaic(monkeypatch):
    import datetime as dt
    items = [{'id': f'scene{i}', 'bbox': [-1, -1, 1, 1],
              'properties': {'datetime': f'2026-11-{14-i:02d}T12:00:00Z', 'eo:cloud_cover': 10},
              'assets': {band: {'href': f'{i}/{band}'} for band in ('scl', 'red', 'nir', 'swir16')}} for i in range(2)]
    monkeypatch.setattr(land, '_stac_search', lambda *a: items)
    flags = [threading.Event(), threading.Event()]
    parallel = [False]
    def read(href, lo, la, *a):
        scene, band = href.split('/');scene=int(scene)
        if parallel[0]:
            flags[scene].set()
            assert flags[1-scene].wait(2), 'Scene downloads did not overlap'
        if band == 'scl':
            out = np.full(len(lo), 4.0)
            if scene == 0:out[len(out)//2:] = 9  # clouds: older scene must fill only these samples
            return out
        return np.full(len(lo), ({'red':2000, 'nir':4000, 'swir16':2000} if scene==0 else
                                 {'red':1000, 'nir':5000, 'swir16':1000})[band], np.float32)
    monkeypatch.setattr(land, '_read_utm', read)
    grid = land.LGrid(0, 0, 1000, 250)
    reference, refmeta = land.sentinel2_layer(grid, dt.datetime(2026,11,15), scene_workers=1)
    parallel[0] = True
    result, meta = land.sentinel2_layer(grid, dt.datetime(2026,11,15), scene_workers=2)
    for key in reference:
        np.testing.assert_array_equal(reference[key], result[key])
    assert meta['scenes'] == refmeta['scenes'] and meta['coverage_pct'] == refmeta['coverage_pct']
