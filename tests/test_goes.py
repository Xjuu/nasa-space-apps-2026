"""GOES ABI FDC reader: offline tests (projection vs PUG example, mask decoding, synthetic netCDF, scan
thinning, satellite choice) + network test (NET=1)."""
import datetime as dt
import os

import numpy as np
import pytest

from equifire import goes as g

NET = os.environ.get("NET") == "1"


def test_projection_pug_example():
    # GOES-R PUG vol. 3, 4.2.8.1/4.2.8.2 worked example (GOES-East, lon0 = -75)
    lat, lon = g.xy_to_latlon(-0.024052, 0.095340, lon0=-75.0)
    assert abs(lat - 33.846162) < 1e-5 and abs(lon - -84.690932) < 1e-5
    x, y = g.latlon_to_xy(33.846162, -84.690932, lon0=-75.0)
    assert abs(x - -0.024052) < 1e-6 and abs(y - 0.095340) < 1e-6


def test_projection_roundtrip_and_offdisk():
    la = np.array([-50.0, 0.0, 20.0, 45.0]); lo = np.array([-100.0, -137.0, -160.0, -120.0])
    x, y = g.latlon_to_xy(la, lo, lon0=-137.0)
    la2, lo2 = g.xy_to_latlon(x, y, lon0=-137.0)
    np.testing.assert_allclose(la2, la, atol=1e-7); np.testing.assert_allclose(lo2, lo, atol=1e-7)
    assert np.isnan(g.xy_to_latlon(0.16, 0.16)[0])                 # off the earth disk
    assert np.isnan(g.latlon_to_xy(0.0, 60.0, lon0=-75.0)[0])       # far side of the earth


def test_mask_decoding():
    fire, cat, filt = g.decode_mask(np.array([0, 10, 11, 12, 13, 14, 15, 30, 35, 40, 100, 16, 29]))
    assert fire.tolist() == [False] + [True] * 8 + [False] * 4
    assert cat[1] == "good" and cat[3] == "cloudy" and cat[6] == "low" and cat[8] == "low"
    assert filt.tolist()[7:9] == [True, True] and not filt[1]


def test_sats_and_choice():
    assert g.sats_for(dt.datetime(2024, 6, 1)) == {"east": 16, "west": 18}
    assert g.sats_for(dt.datetime(2026, 1, 1)) == {"east": 19, "west": 18}
    assert g.sats_for(dt.datetime(2022, 6, 1))["west"] == 17
    t = dt.datetime(2026, 1, 1)
    assert g.choose_sats((-50, -5, -45, 0), t) == [19]              # Brazil -> East
    assert g.choose_sats((-123, 38, -120, 40), t) == [18]           # California -> West
    assert g.choose_sats((-50, -5, -45, 0), t, "both") == [19, 18]


def test_parse_start_and_thinning(monkeypatch):
    k = "ABI-L2-FDCF/2026/318/12/OR_ABI-L2-FDCF-M6_G19_s20263181210210_e20263181219518_c20263181220006.nc"
    assert g.parse_start(k) == dt.datetime(2026, 11, 14, 12, 10, 21)
    keys = [k.replace("s2026318121", f"s202631812{m}") for m in range(0, 6)]   # 12:00..12:50
    monkeypatch.setattr(g, "list_hour", lambda sat, h, product="FDCF": keys if h.hour == 12 else [])
    sel = g.select_scans(19, dt.datetime(2026, 11, 14, 12), dt.datetime(2026, 11, 14, 13), 30)
    assert [g.parse_start(s).minute for s in sel] == [0, 30]
    assert len(g.select_scans(19, dt.datetime(2026, 11, 14, 12), dt.datetime(2026, 11, 14, 13), 10)) == 6


def test_parse_synthetic_fdc(tmp_path):
    netCDF4 = pytest.importorskip("netCDF4")
    p = tmp_path / "OR_ABI-L2-FDCF-M6_G19_s20263181200210_e1_c1.nc"
    with netCDF4.Dataset(p, "w") as ds:
        ds.createDimension("y", 4); ds.createDimension("x", 4)
        x = ds.createVariable("x", "i2", ("x",)); y = ds.createVariable("y", "i2", ("y",))
        x.scale_factor, x.add_offset = np.float32(5.6e-5), np.float32(-0.024052 - 5.6e-5)
        y.scale_factor, y.add_offset = np.float32(-5.6e-5), np.float32(0.095340 + 5.6e-5)
        x.set_auto_maskandscale(False); y.set_auto_maskandscale(False)
        x[:] = np.arange(4); y[:] = np.arange(4)
        pr = ds.createVariable("goes_imager_projection", "i4")
        pr.perspective_point_height = 35786023.0; pr.semi_major_axis = 6378137.0
        pr.semi_minor_axis = 6356752.31414; pr.longitude_of_projection_origin = -75.0
        m = ds.createVariable("Mask", "i2", ("y", "x"), fill_value=-99)
        mk = np.full((4, 4), 100, np.int16); mk[1, 1] = 10; mk[2, 3] = 35; mk[0, 0] = 40
        m[:] = mk
        pw = ds.createVariable("Power", "f4", ("y", "x"), fill_value=-9.0)
        a = np.full((4, 4), -9.0, np.float32); a[1, 1] = 123.5; pw[:] = a
        for name, sf, off in (("Area", 60.98, 4000.0), ("Temp", 0.05493667, 400.0)):
            v = ds.createVariable(name, "u2", ("y", "x"), fill_value=65535)
            v.set_auto_maskandscale(False)
            v.scale_factor, v.add_offset = np.float32(sf), np.float32(off)
            raw = np.full((4, 4), 65535, np.uint16); raw[1, 1] = 100; v[:] = raw
        dq = ds.createVariable("DQF", "u1", ("y", "x"), fill_value=255)
        dq.set_auto_maskandscale(False)
        dq[:] = np.zeros((4, 4), np.uint8)
    df = g.parse_fdc(p)
    assert len(df) == 2
    r = df[df["mask"] == 10].iloc[0]
    assert abs(r.lat - 33.846162) < 1e-4 and abs(r.lon - -84.690932) < 1e-4
    assert abs(r.frp - 123.5) < 1e-3 and abs(r.area_m2 - (4000 + 100 * 60.98)) < 0.5
    assert abs(r.temp_k - (400 + 100 * 0.05493667)) < 1e-3 and r.category == "good"
    assert 2.0 < r.px_km2 < 20
    r2 = df[df["mask"] == 35].iloc[0]
    assert np.isnan(r2.frp) and r2.category == "low" and r2.filtered


@pytest.mark.skipif(not NET, reason="network test; set NET=1")
def test_net_goes_recent():
    end = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) - dt.timedelta(minutes=30)
    df = g.goes_fires((-80, -30, -40, 5), end - dt.timedelta(minutes=40), end, step_min=60)  # S. America
    assert df.attrs["scans"] >= 1 and len(df) > 0
    gr = g.goes_growth((-80, -30, -40, 5), end - dt.timedelta(minutes=40), end, step_min=60)
    assert gr["scans"].sum() >= 1
