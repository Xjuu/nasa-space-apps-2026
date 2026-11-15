import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

import numpy as np
from netCDF4 import Dataset
from equifire import ncep_weather as W
from equifire.common import EPOCH


class NCEPWeatherTests(unittest.TestCase):
    def test_previous_day_units_and_missing_files(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(W,'DIR',Path(folder)):
            W.load.cache_clear()
            for variable,value in {'air':300.,'uwnd':3.,'vwnd':4.,'rhum':40.,'prate':.0001}.items():
                _,stem=W.SPECS[variable]
                with Dataset(Path(folder)/f'{stem}.2023.nc','w') as ds:
                    for name,n in [('time',2),('lat',2),('lon',2)]:ds.createDimension(name,n)
                    t=ds.createVariable('time','f8',('time',));t.units='hours since 2023-07-01 00:00:00';t[:]=[0,24]
                    ds.createVariable('lat','f4',('lat',))[:]=[1,-1]
                    ds.createVariable('lon','f4',('lon',))[:]=[0,180]
                    v=ds.createVariable(variable,'f4',('time','lat','lon'))
                    v[0]=value;v[1]=999.
            day=int((np.datetime64('2023-07-02')-EPOCH).astype(int))
            with patch('urllib.request.urlopen',side_effect=AssertionError('inference must stay offline')):
                speed,direction,temp,rh,rain=W.NCEPWeather().get(day,1,0)
                self.assertAlmostEqual(speed,18.)
                self.assertAlmostEqual(direction,216.8699,places=3)
                self.assertAlmostEqual(temp,26.85)
                self.assertEqual(rh,40.)
                self.assertAlmostEqual(rain,8.64,places=5)
                self.assertTrue(np.isnan(W.NCEPWeather().get(day-365,1,0)).all())
            W.load.cache_clear()

    def test_model_uses_its_saved_weather_source(self):
        from equifire.short_spread import forecast
        provider=object()
        meta={'settings':{'ncep_weather':True},'features':['k'],'policy_rules':[['empty']]*3}
        P=np.full((3,3,3),np.nan)
        with patch.object(W,'NCEPWeather',return_value=provider), \
             patch('equifire.direct.predict_case',return_value=(P,None,None,None)) as predict:
            masks=forecast(None,meta,None,case=(8000,1,1))
        self.assertIs(predict.call_args.args[5],provider)
        self.assertFalse(masks.any())

    def test_longitude_is_cyclic(self):
        self.assertEqual(W.nearest([0,90,180,270],-90),3)
        self.assertEqual(W.nearest([0,179,181],-179),2)


if __name__=='__main__':unittest.main()
