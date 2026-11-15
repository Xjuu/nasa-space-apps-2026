"""Serve the same out-of-sample ML + physics blender measured by ensemble_eval.py.

Never substitute the landscape-enabled physics into a blender fitted on the old physics.
The linked combined timeline keeps daily guidance alongside the richer hourly physics pipeline.
"""
from __future__ import annotations

import functools
import copy
from collections import OrderedDict
from concurrent.futures import Future
import json
import threading
import time
from pathlib import Path
from urllib.parse import urlencode

import numpy as np
import pandas as pd

from equifire import direct
from equifire.common import EPOCH, GRID, MODELS, cell_center
from equifire.ensemble_core import FEATS, case_rows, blend_predict
from equifire.physcells import physics_cells
from .risk_diagnostics import model_fingerprints
from . import geo


class PredictorUnavailable(RuntimeError):
    pass


class NoIgnition(LookupError):
    pass


_LOCK = threading.RLock()


@functools.lru_cache(maxsize=4)
def load_bundle(model_dir: str, year: int):
    """Only use an artifact fitted before the requested year, with its matching ML model."""
    import xgboost as xgb
    base = Path(model_dir)
    candidates = []
    for path in base.glob('ensemble_blend_*_meta.json'):
        meta = json.loads(path.read_text())
        if meta.get('fit_year', year) < year and meta.get('features') == FEATS:
            candidates.append((int(meta['fit_year']), path, meta))
    for _, path, meta in sorted(candidates, key=lambda row: row[0], reverse=True):
        ml_name = meta.get('apply_ml_model', '')
        # All filenames originate in local metadata, not caller-supplied paths.
        if not ml_name or Path(ml_name).name != ml_name:
            continue
        ml_path = base / (ml_name + '.json')
        mp = base / (ml_name + '_meta.json')
        blend_path = path.with_name(path.name.replace('_meta.json', '.json'))
        if not (ml_path.exists() and mp.exists() and blend_path.exists()):
            continue
        ml_meta = json.loads(mp.read_text())
        if int(ml_meta.get('test_from', year + 1)) > year:
            continue
        if meta.get('physics_version', 2) != 2:
            continue
        ml = xgb.XGBClassifier(n_jobs=2)
        ml.load_model(ml_path)
        blend = xgb.XGBClassifier(n_jobs=2)
        blend.load_model(blend_path)
        return ml, ml_meta, blend, meta, blend_path.stem
    raise PredictorUnavailable(f'No compatible ML + physics blender trained before {year}; run ensemble_eval.py first')


@functools.lru_cache(maxsize=1)
def archive_index():
    from equifire.fireindex_mm import get_fire_index
    return get_fire_index(verbose=False)


def live_index(df, day, gx, gy, as_of, modis_equiv=False):
    """Local seven-day feed; zero history beyond it is explicitly disclosed, never called full history."""
    d = df[(df['t'] <= as_of) & (df['day'] <= day)
           & (df['gx'].sub(gx).abs() <= direct.W + 5) & (df['gy'].sub(gy).abs() <= direct.W + 5)].copy()
    keys = ['day', 'gx', 'gy']
    v = d[d['sat'] != 'MODIS'].groupby(keys).agg(frp=('frp', 'sum'), n=('frp', 'size')).reset_index()
    m = d[d['sat'] == 'MODIS'].groupby(keys)['frp'].sum().reset_index()
    if modis_equiv and len(m):
        # No VIIRS fire here: treat MODIS burning cells as VIIRS-equivalent burning cells (1 km pixels, so the
        # footprint is under-counted, MODIS finds ~0.26x of VIIRS cells). Flagged as an estimate by the caller.
        have = set(zip(v.day.tolist(), v.gx.tolist(), v.gy.tolist()))
        extra = m[[k not in have for k in zip(m.day.tolist(), m.gx.tolist(), m.gy.tolist())]].copy()
        extra['n'] = 1
        v = pd.concat([v, extra[['day', 'gx', 'gy', 'frp', 'n']]], ignore_index=True)
    # Day/night is not retained by the legacy public-feed parser; don't invent solar classes.
    fi = direct.FireIndex(_v=(v.day.to_numpy(), v.gx.to_numpy(), v.gy.to_numpy(), v.frp.to_numpy(),
                             v.n.to_numpy(), np.zeros(len(v), np.int8)),
                          _m=(m.day.to_numpy(), m.gx.to_numpy(), m.gy.to_numpy(), m.frp.to_numpy()))
    fi.sub = None
    return fi, d


def nearest_fire(fi, day, gx, gy, search_cells=4):
    ids = fi.v.query(day, day, gx, gy)
    if not len(ids):
        raise NoIgnition('No VIIRS ignition detected near this point on the selected UTC day')
    x, y = fi.v.gx[ids].astype(int), fi.v.gy[ids].astype(int)
    distance = np.hypot(x - gx, y - gy)
    i = int(np.argmin(distance))
    if max(abs(x[i] - gx), abs(y[i] - gy)) > search_cells:
        raise NoIgnition('No VIIRS ignition within four grid cells of the selected point')
    return int(x[i]), int(y[i])


def predict(lat, lon, date, days=3, objective='iou', *, model_dir=MODELS,
            fi=None, bundle=None, physics_fn=None, live_loader=None, observation_cutoff=None, on_ignition=None):
    t = pd.Timestamp(date)
    if t.tzinfo is not None:
        t = t.tz_convert('UTC').tz_localize(None)
    if not 1 <= days <= 3 or objective not in ('iou', 'tol'):
        raise ValueError('days must be 1..3; objective must be iou or tol')
    now = pd.Timestamp.now('UTC').tz_localize(None)
    if t.floor('D') > now.floor('D'):
        raise ValueError('Select an ignition date no later than today; future spread is controlled by days')
    day = int((t.to_datetime64().astype('datetime64[D]') - EPOCH).astype(int))
    gx, gy = int(np.floor((lon + 180) / GRID)), int(np.floor((lat + 90) / GRID))
    with _LOCK:
        ml, ml_meta, blender, blend_meta, name = bundle or load_bundle(str(model_dir), t.year)
        fi = archive_index() if fi is None else fi
    if int(ml_meta['test_from']) > t.year or int(blend_meta['fit_year']) >= t.year:
        raise PredictorUnavailable('Model training cutoff is later than the requested ignition date')
    if days > int(blend_meta['days']):
        raise PredictorUnavailable('Blender does not support this forecast horizon')
    # FireIndex.day is sorted (also in its memory-mapped representation).
    reduced = day > int(fi.day[-1])
    live_df, feeds = None, []
    as_of = None
    if reduced:
        from .live import load_feeds
        df, feeds = (live_loader or load_feeds)(window='7d')
        if df.empty or day < int(df.day.min()) or day > int(df.day.max()):
            raise NoIgnition('Ignition date is outside the archive and available seven-day public feed')
        as_of = min(now.floor('h'), t.floor('D') + pd.Timedelta(hours=23))
        if observation_cutoff is not None:
            cutoff = pd.Timestamp(observation_cutoff)
            if cutoff.tzinfo is not None:
                cutoff = cutoff.tz_convert('UTC').tz_localize(None)
            as_of = min(as_of, cutoff.floor('h'))
        fi, live_df = live_index(df, day, gx, gy, as_of)
    ignition_source, shifted_days = 'VIIRS', 0
    for shift in ((0, 1) if reduced else (0,)):
        try:
            cx, cy = nearest_fire(fi, day - shift, gx, gy)
            ignition_source = 'VIIRS'
        except NoIgnition as first_error:
            if not reduced or not str(first_error).startswith('No VIIRS ignition'):
                raise
            fi_m, live_df_m = live_index(df, day, gx, gy, as_of, modis_equiv=True)
            try:
                cx, cy = nearest_fire(fi_m, day - shift, gx, gy)
            except NoIgnition:
                if shift == 0:
                    continue  # nothing on the current UTC day yet: use the latest detections of the last 24 h
                raise NoIgnition('No VIIRS or MODIS ignition detected near this point in the last 48 h') from None
            fi, live_df = fi_m, live_df_m
            ignition_source = 'MODIS (used as VIIRS-equivalent)'
        shifted_days = shift
        break
    if shifted_days:
        day -= shifted_days
        t = t - pd.Timedelta(days=shifted_days)
    if on_ignition is not None:
        clat, clon = map(float, cell_center(cx, cy))
        on_ignition({'lat': clat, 'lon': clon}, 'live' if reduced else 'archive')
    cols = list(ml_meta['features'])
    result = direct.predict_case(ml, fi, day, cx, cy, direct.Weather(), days, cols)
    if result is None:
        raise NoIgnition('Insufficient detected ignition for this model')
    P, _, dist, _ = result  # Future observation cube is deliberately never read or returned.
    kwargs = dict(days=days, calibration=bool(blend_meta.get('calibration', True)))
    if reduced:
        kwargs.update(start=as_of, history=live_df[live_df['sat'] != 'MODIS'], use_cache=False)
    pc = (physics_fn or physics_cells)(fi, (day, cx, cy), **kwargs)
    if not pc['meta'].get('ok', True):
        raise PredictorUnavailable('Physics input unavailable: ' + '; '.join(pc['meta'].get('errors', [])))
    X, ids = case_rows(P, dist, pc, days)
    combined = blend_predict(blender, {'X': X, 'ids': ids}, days)
    rules = blend_meta['rules_' + objective][:days]
    out, per_day = [], []
    for k in range(days):
        chosen = direct.select(combined[k], rules[k])
        ml_chosen = direct.select(P[k], direct.rule_for(ml_meta, k + 1))
        for x, y in zip(*np.nonzero(np.isfinite(combined[k]))):
            out.append(geo.feature(geo.cell_polygon(cx + int(x) - direct.W, cy + int(y) - direct.W),
                kind='combined_forecast', day=k + 1, date=(t + pd.Timedelta(days=k + 1)).date().isoformat(),
                ml_probability=round(float(P[k, x, y]), 5),
                combined_probability=round(float(combined[k, x, y]), 5),
                physics_burned_fraction=round(float(pc['frac'][1, k, x, y]), 5),
                physics_low_fraction=round(float(pc['frac'][0, k, x, y]), 5),
                physics_high_fraction=round(float(pc['frac'][2, k, x, y]), 5),
                physics_coverage=round(float(pc['cover'][x, y]), 4), selected=bool(chosen[x, y]),
                ml_selected=bool(ml_chosen[x, y]), estimate=True))
        per_day.append({'day': k + 1, 'selected_cells': int(chosen.sum()), 'rule': list(rules[k])})
    clat, clon = map(float, cell_center(cx, cy))
    live_mode = 'live' if reduced else 'archive'
    start = as_of.isoformat() if reduced else t.date().isoformat() + 'T12:00'
    simulate_url = '/predict/simulate?' + urlencode(dict(lat=clat, lon=clon, start=start, hours=24*days,
                                                       ignition=live_mode))
    return geo.collection(out, query={'lat': lat, 'lon': lon, 'date': t.date().isoformat(),
                                      'days': days, 'objective': objective},
        centre={'lat': clat, 'lon': clon},
        per_day=per_day, model={'blender': name, 'fit_year': blend_meta['fit_year'],
            'fingerprints': model_fingerprints(model_dir, name, blend_meta.get('apply_ml_model', '')),
            'ml_model': blend_meta.get('apply_ml_model'), 'ml_training_before_year': ml_meta['test_from'],
            'inputs': 'VIIRS history plus MODIS activity features', 'physics_version': 2},
        physics=pc['meta'], hourly_simulation_url=simulate_url, estimate=True,
        warnings=['Experimental blend: measured gains over ML alone are not established.',
                  'Physics burned fraction is not an ignition probability; low/high are scenario envelopes.',
                  'The hourly landscape simulation is a separate richer model, not the physics used to fit this blender.']
                 + (['Nothing detected near this point yet on the selected UTC day: the forecast starts from the latest '
                     'detections of the previous UTC day, so it is up to 24 h behind the real fire.'] if shifted_days else [])
                 + (['No VIIRS fire near this point: the ignition comes from MODIS (1 km pixels) used as VIIRS-equivalent. '
                     'MODIS finds only about a quarter of the cells VIIRS would, so the starting footprint is probably too small. Estimate.']
                    if ignition_source.startswith('MODIS') else [])
                 + (['Reduced history: seven-day public feed; full 30/365-day history, sub-cell offsets '
                     'and day/night flags are unavailable. This live mode has not been validated.'] if reduced else
                    ['Physics uses observed archive weather: historical replay, not a forecast-weather backtest.']),
        history={'reduced': reduced, 'as_of_utc': None if as_of is None else as_of.isoformat()+'Z',
                 'feed_status': feeds, 'ignition_source': ignition_source, 'ignition_shifted_days': shifted_days},
        method={'grid_degrees': GRID, 'selection_objective': objective,
                'probabilities_calibrated': False, 'scope': 'regional window around a detected fire'})


# Playback interval changes do not change the native daily forecast. Reuse it
# briefly for the exact initialization and horizon, without sharing mutable data.
_GUIDANCE_CACHE = OrderedDict()
_GUIDANCE_TTL_S = 300
_GUIDANCE_INFLIGHT = {}


def _daily_guidance(lat, lon, t0, days, on_ignition=None):
    key = (float(lat), float(lon), t0.isoformat(), int(days))
    with _LOCK:
        now = time.monotonic()
        hit = _GUIDANCE_CACHE.get(key)
        if hit and now - hit[0] < _GUIDANCE_TTL_S:
            _GUIDANCE_CACHE.move_to_end(key)
            return copy.deepcopy(hit[1]), True
        future = _GUIDANCE_INFLIGHT.get(key)
        owner = future is None
        if owner:
            future = _GUIDANCE_INFLIGHT[key] = Future()
    # Identical concurrent requests share the calculation; unrelated fires never
    # hold the global model/cache lock while waiting for I/O or physics.
    if not owner:
        return copy.deepcopy(future.result()), True
    try:
        kwargs = {'observation_cutoff': t0}
        if on_ignition is not None:
            kwargs['on_ignition'] = on_ignition
        result = predict(lat, lon, t0.date().isoformat(), days, **kwargs)
        with _LOCK:
            _GUIDANCE_CACHE[key] = (time.monotonic(), copy.deepcopy(result))
            _GUIDANCE_CACHE.move_to_end(key)
            while len(_GUIDANCE_CACHE) > 8:
                _GUIDANCE_CACHE.popitem(last=False)
        future.set_result(copy.deepcopy(result))
        return result, False
    except BaseException as error:
        future.set_exception(error)
        raise
    finally:
        with _LOCK:
            _GUIDANCE_INFLIGHT.pop(key, None)



def simulate_combined(lat, lon, start=None, hours=24, step_min=60, ignition='live', fuel='auto',
                      radius_km=30.0, state=None, predict_fn=None, simulation_fn=None, calibration: str = 'off'):
    """One result and timeline, requiring both the fitted blend and hourly landscape physics.

    Daily scores retain their native UTC day horizons; no hourly ML probabilities are invented.
    The animated perimeter comes from hourly weather-driven physics, overlaid with the daily blend.
    """
    from . import physics_spread as ps
    started = time.perf_counter()
    t0 = ps._parse_start(start)
    now = pd.Timestamp.now('UTC').tz_localize(None)
    if ignition not in ('live', 'archive'):
        raise ValueError('Combined predictions require live or archive satellite ignition')
    if not 1 <= hours <= 72 or not 10 <= step_min <= 720:
        raise ValueError('hours must be 1..72; step_min must be 10..720')
    if t0 > now.floor('h'):
        raise ValueError('Forecast initialization cannot be in the future; use hours to forecast ahead')
    days = max(1, int(np.ceil(hours / 24)))
    from concurrent.futures import ThreadPoolExecutor
    live_options = {}
    if ignition == 'live':
        def at_initialization():
            from .live import load_feeds
            detections, info = load_feeds(window='7d')
            times = pd.to_datetime(detections['t'])
            return detections[(times <= t0) & (times > t0 - pd.Timedelta(hours=24))], info
        live_options['live_loader'] = at_initialization

    def hourly(centre):
        options = dict(live_options)
        if simulation_fn is None:
            options['require_satellite'] = True
            options['osm_mode'] = 'cached'
        try:
            return (simulation_fn or ps.forecast)(
                centre['lat'], centre['lon'], t0, hours, step_min, ignition=ignition, fuel=fuel,
                radius_km=radius_km, state=state, weather='grid', landscape='on', calibration=calibration, fwi=False,
                **options)
        except ps.SatelliteIgnitionUnavailable as error:
            raise NoIgnition(str(error)) from error

    # Ignition is resolved before the daily model's physics/calibration work. Start
    # the independent hourly pipeline then; both still finish before responding.
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix='combined-hourly') as pool:
        pending = None
        started_centre = None
        hourly_start_s = None
        def on_ignition(centre, mode):
            nonlocal pending, started_centre, hourly_start_s
            if mode != ignition:
                raise ValueError(f'The selected date requires {mode} satellite ignition')
            if pending is None:
                started_centre = centre
                hourly_start_s = time.perf_counter() - started
                pending = pool.submit(hourly, centre)
        if predict_fn is None:
            guidance, guidance_cache_hit = _daily_guidance(lat, lon, t0, days, on_ignition=on_ignition)
        else:
            guidance = predict_fn(lat, lon, t0.date().isoformat(), days, observation_cutoff=t0)
            guidance_cache_hit = False
        guidance_s = time.perf_counter() - started
        centre = guidance['centre']
        requested_mode = 'live' if guidance.get('history', {}).get('reduced') else 'archive'
        on_ignition(centre, requested_mode)
        if started_centre != centre:
            raise ValueError('Daily and hourly ignition centres differ')
        result = pending.result()
    if result.get('ignition', {}).get('mode') != ignition:
        raise NoIgnition('Hourly satellite ignition is unavailable; combined prediction was not produced')
    frames = []
    for frame in result['frames']:
        valid = ps._parse_start(frame['time_utc'])
        day = min(days, max(1, (valid.floor('D') - t0.floor('D')).days))
        frames.append({**frame, 'guidance_day': day,
                       'guidance_valid_until_utc': (t0.floor('D') + pd.Timedelta(days=day+1)).isoformat()+'Z'})
    return {**result, 'frames': frames, 'guidance': guidance,
            'timings': {**result.get('timings', {}), 'daily_guidance_s': round(guidance_s, 3),
                        'daily_guidance_cache_hit': guidance_cache_hit,
                        'hourly_start_offset_s': round(hourly_start_s, 3),
                        'combined_total_s': round(time.perf_counter() - started, 3)},
            'prediction_mode': 'ml_physics',
            'temporal_resolution': {
                'perimeter': 'hourly weather-driven physics, displayed at the chosen playback step',
                'guidance': 'daily ML + physics scores; no hourly ML probabilities are inferred'},
            'requested_point': {'lat': lat, 'lon': lon},
            'warnings': guidance.get('warnings', []),
            'estimate': True}
