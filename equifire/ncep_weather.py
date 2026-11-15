"""Offline NOAA PSL NCEP/NCAR daily reanalysis for short-spread experiments.

Public bulk files, not a metered API. Surface sigma=.995 daily mean air, wind,
humidity plus Gaussian-grid precipitation. These are coarse (~2 degrees), NOT
local observations or archived forecasts. Uses t0-1 only. Legacy direct feature
names temp_max/rh_min receive daily MEANS here; model metadata identifies this
source and models must be retrained. Existing Weather/model defaults are untouched.
https://psl.noaa.gov/data/gridded/data.ncep.reanalysis.html
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from functools import lru_cache
import os
from pathlib import Path
import urllib.request

import numpy as np
from .common import CACHE, EPOCH

_NETCDF_LOCK=Lock()
DIR=CACHE/'ncep_daily'
BASE='https://downloads.psl.noaa.gov/Datasets/ncep.reanalysis.dailyavgs'
SPECS={'air':('surface','air.sig995'), 'uwnd':('surface','uwnd.sig995'),
       'vwnd':('surface','vwnd.sig995'), 'rhum':('surface','rhum.sig995'),
       'prate':('surface_gauss','prate.sfc.gauss')}


def fetch(variable,year):
    folder,stem=SPECS[variable]
    DIR.mkdir(parents=True,exist_ok=True)
    path=DIR/f'{stem}.{year}.nc'
    if path.exists():return path
    url=f'{BASE}/{folder}/{path.name}'
    temp=path.with_suffix(f'.{os.getpid()}.part')
    try:
        with urllib.request.urlopen(url,timeout=120) as response,temp.open('wb') as out:
            while chunk:=response.read(1024*1024):out.write(chunk)
        from netCDF4 import Dataset
        with _NETCDF_LOCK:
            with Dataset(temp) as ds:
                if variable not in ds.variables:raise ValueError(f'{url}: missing {variable}')
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)
    return path


@lru_cache(maxsize=10)
def load(variable,year):
    from netCDF4 import Dataset,num2date
    folder,stem=SPECS[variable]
    path=DIR/f'{stem}.{year}.nc'
    if not path.exists():return None
    with _NETCDF_LOCK:
        with Dataset(path) as ds:
            times=ds.variables['time']
            dates=num2date(times[:],times.units,only_use_cftime_datetimes=False)
            days=(np.array([d.isoformat()[:10] for d in dates],dtype='datetime64[D]')-EPOCH).astype(int)
            v=ds.variables[variable]
            values=np.ma.filled(v[:],np.nan).astype(np.float32)
            units=str(getattr(v,'units',''))
            lat=np.asarray(ds.variables['lat'][:]);lon=np.asarray(ds.variables['lon'][:])
    return days,values,lat,lon,units


def nearest(longitudes,lon):
    return int(np.abs((np.asarray(longitudes)-lon+180)%360-180).argmin())


class NCEPWeather:
    """Interface matching direct.Weather; reads cached files only, never downloads."""
    def value(self,variable,day,lat,lon):
        year=int((EPOCH+np.timedelta64(int(day),'D')).astype('datetime64[Y]').astype(int))+1970
        data=load(variable,year)
        if data is None:return np.nan
        days,values,lats,lons,units=data
        i=int(np.searchsorted(days,day))
        if i==len(days) or days[i]!=day:return np.nan
        y=int(np.abs(lats-lat).argmin());x=nearest(lons,lon)
        return float(values[i,y,x])

    def get(self,day,lat,lon):
        previous=int(day)-1
        temp,u,v,rh,rain=[self.value(name,previous,lat,lon) for name in SPECS]
        speed=float(np.hypot(u,v)*3.6)
        direction=float((np.rad2deg(np.arctan2(u,v))+180)%360)
        return speed,direction,temp-273.15,rh,max(0.,rain*86400) if np.isfinite(rain) else np.nan


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--years',type=int,nargs='+',default=list(range(2012,2026)))
    args=ap.parse_args()
    jobs=[(v,y) for y in args.years for v in SPECS]
    # Two modest concurrent transfers; HDF5 validation stays serialized.
    with ThreadPoolExecutor(2) as pool:
        paths=pool.map(lambda job:fetch(*job),jobs)
        for i,path in enumerate(paths,1):
            print(f'{i}/{len(jobs)} {path.name}: {path.stat().st_size/1e6:.1f} MB',flush=True)


if __name__=='__main__':main()
