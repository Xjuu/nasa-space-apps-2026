"""Regional NOAA GFS forecast fallback using public NOMADS GRIB filtering (no account/key).

0.25 degree weather, sampled every 3 hours and interpolated hourly, including vector wind.
Forecasts are not 250 m weather. PRATE (kg/m2/s) is treated as an approximate hourly rain rate.
Past spin-up uses older forecast cycles, not observations. Cache each regional forecast slice.
Docs: https://nomads.ncep.noaa.gov/info.php?page=opendap_grib_migration
"""
from __future__ import annotations
import concurrent.futures
import hashlib
import json
import math
import os
import threading
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from .common import CACHE
from .fireweather import WeatherGrid, grid_points

URL = 'https://nomads.ncep.noaa.gov/cgi-bin/filter_gfs_0p25.pl'
DIR = CACHE / 'gfs_forecast'
_LOCK = threading.Lock()
_LAST_REQUEST = 0.0
FIELDS = {('TMP','2-HTGL'): 'temperature_2m', ('RH','2-HTGL'): 'relative_humidity_2m',
          ('UGRD','10-HTGL'): 'u', ('VGRD','10-HTGL'): 'v', ('GUST','0-SFC'): 'wind_gusts_10m',
          ('PRATE','0-SFC'): 'precipitation'}


def cycle_for(valid, as_of):
    # Four hours for publication; choose an earlier cycle for historical spin-up and rain at f000.
    latest = pd.Timestamp(as_of) - pd.Timedelta(hours=4)
    candidate = min(latest, pd.Timestamp(valid) - pd.Timedelta(hours=3))
    return candidate.floor('6h')


def decode(raw, lats, lons, valid):
    from rasterio.io import MemoryFile
    from rasterio.env import Env
    LA, LO = np.meshgrid(lats, lons, indexing='ij')
    out = {}
    expected = int(pd.Timestamp(valid).timestamp())
    with Env(GRIB_NORMALIZE_UNITS='YES'), MemoryFile(raw) as mem, mem.open() as ds:
        # GDAL normally normalizes longitude to [-180,180]; adapt to the actual regional raster bounds.
        points = [(float(lo+360 if lo < ds.bounds.left else lo-360 if lo > ds.bounds.right else lo), float(la))
                  for la, lo in zip(LA.ravel(), LO.ravel())]
        values = np.asarray(list(ds.sample(points, masked=True)), dtype=float)
        for band in range(1, ds.count+1):
            tags = ds.tags(band)
            field = FIELDS.get((tags.get('GRIB_ELEMENT'), tags.get('GRIB_SHORT_NAME')))
            if field is None or int(tags.get('GRIB_VALID_TIME', -1)) != expected:
                continue
            # Prefer the instantaneous PRATE over the interval-average duplicate.
            if field in out and tags.get('GRIB_PDS_PDTN') != '0':
                continue
            a = values[:, band-1].reshape(LA.shape)
            unit = tags.get('GRIB_UNIT', '')
            if field == 'temperature_2m' and unit == '[K]':
                a = a - 273.15
            if field == 'wind_gusts_10m':
                a = a * 3.6
            if field == 'precipitation':
                a = np.maximum(a, 0) * 3600
            out[field] = a
    if set(out) != set(FIELDS.values()) or not all(np.isfinite(a).all() for a in out.values()):
        raise ValueError('GFS slice lacks required weather fields or point coverage')
    return out


def fetch_slice(lats, lons, valid, as_of, session=None):
    global _LAST_REQUEST
    cycle = cycle_for(valid, as_of)
    # Use normalized western/eastern bounds; regional GRIB output is decoded using its own transform.
    west, east = math.floor(min(lons)*4)/4-.25, math.ceil(max(lons)*4)/4+.25
    if east-west > 180:
        raise ValueError('GFS regional request crosses the antimeridian')
    south, north = max(-90, math.floor(min(lats)*4)/4-.25), min(90, math.ceil(max(lats)*4)/4+.25)
    last_error = None
    for older in (0, 6):
        run = cycle - pd.Timedelta(hours=older)
        lead = int((valid-run).total_seconds()/3600)
        params = dict(file=f'gfs.t{run.hour:02d}z.pgrb2.0p25.f{lead:03d}',
                      dir=f'/gfs.{run:%Y%m%d}/{run.hour:02d}/atmos', subregion='',
                      leftlon=west, rightlon=east, bottomlat=south, toplat=north,
                      lev_2_m_above_ground='on', lev_10_m_above_ground='on', lev_surface='on',
                      **{f'var_{v}': 'on' for v in ('TMP','RH','UGRD','VGRD','PRATE','GUST')})
        key = hashlib.sha256(json.dumps(params,sort_keys=True).encode()).hexdigest()[:24]
        path = DIR/(key+'.grib2')
        try:
            if path.exists():
                raw = path.read_bytes()
            else:
                with _LOCK:
                    wait = .5 - (time.monotonic()-_LAST_REQUEST)
                    if wait > 0: time.sleep(wait)
                    _LAST_REQUEST = time.monotonic()
                response = (session or requests).get(URL, params=params, timeout=12,
                                    headers={'User-Agent':'EquiFire/1.0 (regional weather research)'})
                response.raise_for_status()
                raw = response.content
                if not raw.startswith(b'GRIB'):
                    raise ValueError('NOAA forecast cycle is unavailable')
                DIR.mkdir(parents=True, exist_ok=True)
                temp = path.with_suffix(f'.{os.getpid()}.{threading.get_ident()}.tmp')
                temp.write_bytes(raw);temp.replace(path)
            data = decode(raw, lats, lons, valid)
            return data, run.isoformat()+'Z'
        except Exception as exc:
            last_error = exc
    raise RuntimeError(f'GFS slice unavailable for {valid}: {last_error}')


def get_weather_grid(lat, lon, half_km, start_utc, hours, n=5, as_of=None, slice_fn=None):
    as_of = pd.Timestamp(as_of) if as_of is not None else pd.Timestamp.now('UTC').tz_localize(None)
    if as_of.tzinfo is not None: as_of=as_of.tz_convert('UTC').tz_localize(None)
    start = pd.Timestamp(start_utc)
    if start.tzinfo is not None: start=start.tz_convert('UTC').tz_localize(None)
    end = start + pd.Timedelta(hours=hours-1)
    if start < as_of-pd.Timedelta(days=7) or end > as_of+pd.Timedelta(days=10):
        raise ValueError('Public recent GFS fallback only covers recent history and up to 10 forecast days')
    la, lo = grid_points(lat, lon, half_km, n)
    source_times = pd.date_range(start.floor('3h'),end.ceil('3h'),freq='3h')
    fn = slice_fn or fetch_slice
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        rows = list(pool.map(lambda t:fn(la,lo,t,as_of),source_times))
    hourly = pd.date_range(start,periods=hours,freq='h')
    st, ht = source_times.asi8.astype(float), hourly.asi8.astype(float)
    fields = {}
    for key in FIELDS.values():
        a = np.stack([r[0][key] for r in rows]).reshape(len(rows),-1)
        fields[key] = np.column_stack([np.interp(ht,st,a[:,i]) for i in range(a.shape[1])]).reshape(hours,n,n)
    u,v = fields.pop('u'),fields.pop('v')
    fields['wind_speed_10m'] = np.hypot(u,v)*3.6
    fields['wind_direction_10m'] = np.degrees(np.arctan2(-u,-v))%360
    fields['relative_humidity_2m'] = np.clip(fields['relative_humidity_2m'],0,100)
    fields['precipitation'] = np.maximum(fields['precipitation'],0)
    wg = WeatherGrid(np.asarray(la),np.asarray(lo),hourly.to_numpy().astype('datetime64[h]'),fields)
    wg.source_meta = dict(api='NOAA GFS via NOMADS',url=URL,provider='noaa_gfs',
        cycles_utc=sorted({r[1] for r in rows}),as_of_utc=as_of.isoformat()+'Z',
        spatial_resolution_degrees=.25,source_interval_hours=3,output_interval_hours=1,
        note='Regional GFS forecasts, interpolated hourly. Wind interpolates U/V components; PRATE approximates '
             'hourly rainfall. Past moisture spin-up uses earlier forecast cycles, not observed weather.')
    return wg
