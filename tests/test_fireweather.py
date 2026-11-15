"""FWI System vs cffdrs reference tables (tests/data_cffdrs/, copied from github.com/cffdrs/cffdrs_r tests and
cffdrs_py tests; GPL-2 test data). Network tests (Open-Meteo) run only with NET=1."""
import datetime as dt
import os
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from equifire import fireweather as fw

D = Path(__file__).parent / "data_cffdrs"
NET = os.environ.get("NET") == "1"


def _check(got, exp, tol):
    exp = np.asarray(exp, float)
    ok = ~np.isnan(exp)
    assert ok.sum() > 0
    diff = np.abs(np.asarray(got, float)[ok] - exp[ok])
    assert np.nanmax(diff) <= tol, f"max diff {np.nanmax(diff)} (n={ok.sum()})"
    assert not np.isnan(np.asarray(got, float)[ok]).any()


def _valid(df):  # cffdrs::fwi() rejects negative rh/ws/prec; the equation tables include them
    m = np.ones(len(df), bool)
    for c in ("rh", "ws", "prec"):
        if c in df:
            m &= df[c].values >= 0
    return df[m]


def test_ffmc_table():
    df = _valid(pd.read_csv(D / "FineFuelMoistureCode.csv"))
    df = df[df.rh <= 100]
    got = fw.ffmc_calc(df.ffmc_yda, df.temp, df.rh, df.ws, df.prec)
    _check(got, df.FineFuelMoistureCode, 0.006)  # table rounded to 0.01


def test_dmc_table():
    df = _valid(pd.read_csv(D / "DuffMoistureCode.csv"))
    df = df[df.rh <= 100]
    for adj in (True, False):
        d = df[df["lat.adjust"] == adj]
        got = fw.dmc_calc(d.dmc_yda, d.temp, d.rh, d.prec, d.lat, d.mon, adj)
        _check(got, d.DuffMoistureCode, 0.01)


def test_dc_table():
    df = pd.read_csv(D / "DroughtCode.csv")
    df = df[df.prec >= 0]
    for adj in (True, False):
        d = df[df["lat.adjust"] == adj]
        got = fw.dc_calc(d.dc_yda, d.temp, d.rh, d.prec, d.lat, d.mon, adj)
        _check(got, d.DroughtCode, 0.01)


def test_isi_bui_fwi_tables():
    df = _valid(pd.read_csv(D / "InitialSpreadIndex.csv"))
    for mod in (True, False):
        d = df[df.fbpMod == mod]
        _check(fw.isi_calc(d.ffmc, d.ws, mod), d.InitialSpreadIndex, 1e-3 * d.InitialSpreadIndex.max())
    b = pd.read_csv(D / "BuildupIndex.csv")
    _check(fw.bui_calc(b.dmc, b.dc), b.BuildupIndex, 0.01)
    f = pd.read_csv(D / "FireWeatherIndex.csv")
    _check(fw.fwi_calc(f.isi, f.bui), f.FireWeatherIndex, 0.01)


def test_daily_series_vs_cffdrs_fwi():
    """cffdrs test_fwi (Van Wagner & Pickett 1985 standard test data, 48 days from 13 April) -> outputs."""
    df = pd.read_csv(D / "fwi_test_data.csv")
    s = fw.fwi_series(df.TEMP, df.RH, df.WS, df.PREC, df.MON, lat=float(df.LAT[0]))
    for k in ("FFMC", "DMC", "DC", "ISI", "BUI", "FWI"):
        np.testing.assert_allclose(s[k.lower()], df[k].values, atol=0.051, err_msg=k)  # table rounded to 0.1
    np.testing.assert_allclose(s["dsr"], df.DSR.values, atol=0.006)


def test_van_wagner_pickett_day1_hand():
    # Petawawa test day 1 (13 Apr): T=17, RH=42, W=25, r=0 from FFMC 85, DMC 6, DC 15
    s = fw.fwi_series([17], [42], [25], [0], [4], lat=46)
    assert round(s["dmc"][0], 1) == 8.5 and round(s["dc"][0], 1) == 19.0
    assert abs(s["ffmc"][0] - 87.7) < 0.15 and abs(s["fwi"][0] - 10.1) < 0.15


def test_noon_hour_and_danger():
    assert fw.noon_hour_utc(0) == 12 and fw.noon_hour_utc(-120) == 20 and fw.noon_hour_utc(150) == 2
    assert fw.danger_class(3) == "low" and fw.danger_class(35) == "extreme"


def test_weather_grid_interp():
    la, lo = np.array([0.0, 1.0]), np.array([10.0, 11.0])
    t = np.array(["2026-01-01T00", "2026-01-01T01"], dtype="datetime64[h]")
    temp = np.array([[[0, 10], [20, 30]], [[10, 20], [30, 40]]], float)
    ws = np.full((2, 2, 2), 10.0)
    wd = np.array([[[350, 10], [350, 10]]] * 2, float)       # north-ish everywhere -> mean ~0 deg
    g = fw.WeatherGrid(la, lo, t, {"temperature_2m": temp, "wind_speed_10m": ws, "wind_direction_10m": wd})
    r = g.interp(0.5, 10.5, "2026-01-01T00:30")
    assert abs(r["temperature_2m"] - 20.0) < 1e-9
    assert min(r["wind_direction_10m"], 360 - r["wind_direction_10m"]) < 1e-6
    assert abs(g.interp(0, 10, "2026-01-01T00")["temperature_2m"]) < 1e-9


@pytest.mark.skipif(not NET, reason="network test; set NET=1")
def test_net_grid_and_fwi():
    st = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=6)).strftime("%Y-%m-%dT%H")
    g = fw.get_weather_grid(39.5, -121.5, 10, st, 12, n=3)
    assert g.arrays["temperature_2m"].shape == (12, 3, 3)
    assert np.isfinite(g.arrays["wind_speed_10m"]).all()
    r = fw.fwi_for(39.5, -121.5, spinup_days=30)
    assert 0 <= r["ffmc"] <= 101 and len(r["series"]) >= 30
