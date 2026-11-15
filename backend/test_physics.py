"""Tests for the physics spread simulator (no network: weather and terrain are mocked).

    .venv/bin/python -m pytest backend/test_physics.py -q
"""
from __future__ import annotations

import os
import time

import numpy as np
import pandas as pd
import pytest

os.environ.setdefault("EQUIFIRE_NO_LOAD", "1")

from backend import physics_spread as ps  # noqa: E402

START = pd.Timestamp("2023-06-01 00:00")


def make_wx(hours=72, wind=20.0, wdir=270.0, rh=25.0, temp=28.0, rain=0.0, start=START):
    idx = pd.date_range(start - pd.Timedelta(hours=ps.SPINUP_H), start + pd.Timedelta(hours=hours), freq="h")
    return pd.DataFrame({"temp_c": temp, "rh": rh, "wind_kmh": wind, "wind_dir": wdir, "gust_kmh": wind * 1.5,
                         "rain_mm": rain}, index=idx)


def flat(LA, LO):
    return np.zeros_like(LA)


def no_net(*a, **k):
    raise RuntimeError("network disabled in tests")


def run(**kw):
    args = dict(lat=-15.0, lon=130.0, start=START, hours=24, step_min=60, fuel="savanna", wx=make_wx(),
                elev_fn=flat, envelope=False, getter=no_net)
    args.update(kw)
    return ps.simulate(**args)


# ------------------------------------------------------------------ physics pieces
def test_rothermel_reference_values():
    # BehavePlus-like magnitudes for Anderson models at 6/7/8 % dead moisture, 5 mph mid-flame wind
    def ros(fuel):
        r = ps.rothermel(fuel, 0.06, 0.07, 0.08)
        return r["R0"] * (1 + ps.phi_wind(r, 5 * 88)) * 0.3048  # m/min
    assert 20 < ros("grass") < 40      # FM1 ~ 80-95 ch/h
    assert 8 < ros("savanna") < 18     # FM2 ~ 35-40 ch/h
    assert 1.5 < ros("forest") < 4.5   # FM10 ~ 8 ch/h


def test_moisture_of_extinction_stops_spread():
    assert ps.rothermel("grass", 0.13, 0.14, 0.15)["R0"] == 0
    assert ps.rothermel("grass", 0.05, 0.06, 0.07)["R0"] > 0


def test_emc_monotonic_in_rh():
    e = ps.emc_simard(np.full(5, 25.0), np.array([5, 20, 40, 60, 90]))
    assert np.all(np.diff(e) > 0)
    assert 1 < e[0] < 3 and 15 < e[-1] < 30


def test_rain_wets_fuel():
    wx = make_wx(hours=24)
    wx.iloc[50:53, wx.columns.get_loc("rain_mm")] = 5.0
    fm = ps.fuel_moisture(wx)
    assert fm["m1h"].iloc[52] > 25 > fm["m1h"].iloc[49]


def test_length_to_breadth():
    assert ps.length_to_breadth(0) == pytest.approx(1.0, abs=0.01)
    assert 2.5 < ps.length_to_breadth(5) < 4.0   # FARSITE (Finney 1998) value ~3.2
    assert ps.length_to_breadth(60) == 8.0


def test_choose_fuel_labelled_assumption():
    assert ps.choose_fuel(60, "auto")[0] == "forest"
    assert ps.choose_fuel(-15, "auto")[0] == "savanna"
    assert "ASSUMPTION" in ps.choose_fuel(35, "auto")[1]
    assert ps.choose_fuel(35, "grass")[0] == "grass"


# ------------------------------------------------------------------ simulator
def test_wind_drives_ellipse_downwind():
    r = run(keep_masks=True)
    mask, grid = r["frames"][-1]["_mask"]
    ys, xs = np.nonzero(mask)
    cx = grid.n // 2
    assert xs.mean() > cx + 2          # wind FROM 270 (west) -> spreads east
    assert (xs.max() - cx) > 3 * (cx - xs.min() + 1)
    assert abs(ys.mean() - cx) < 2


def test_area_monotonic_and_frames():
    r = run(step_min=30)
    a = [f["burned_km2"] for f in r["frames"]]
    assert len(r["frames"]) == 24 * 2 + 1
    assert all(b >= x for x, b in zip(a, a[1:]))
    assert a[-1] > a[0] > 0
    f = r["frames"][10]
    assert f["burned"]["type"] == "MultiPolygon" and f["estimate"] is True
    assert set(f["weather"]) >= {"wind_kmh", "wind_dir_deg", "temp_c", "rh", "rain_mm"}
    assert r["fuel"]["assumption"] is True and r["ignition"]["estimate"] is True
    assert "Rothermel" in r["method"]["rate_of_spread"]


def test_stronger_wind_bigger_fire():
    a1 = run(wx=make_wx(wind=10))["summary"]["burned_km2"]
    a2 = run(wx=make_wx(wind=30))["summary"]["burned_km2"]
    assert a2 > 1.5 * a1


def test_rain_and_humidity_stop_spread():
    wet = run(wx=make_wx(rh=95, rain=2.0))
    assert wet["summary"]["burned_km2"] == wet["summary"]["initial_km2"]
    assert wet["summary"]["stalled_hours"] == 24


def test_upslope_faster_than_downslope():
    def ramp(LA, LO):   # rises to the north: 20 % grade
        return (LA - LA.mean()) / ps.M_TO_DEG * 0.2
    r = run(wx=make_wx(wind=0.0), elev_fn=ramp, keep_masks=True)
    mask, grid = r["frames"][-1]["_mask"]
    ys, _ = np.nonzero(mask)
    c = grid.n // 2
    assert ys.max() - c > 1.5 * (c - ys.min())


def test_envelope_ordering():
    s = run(envelope=True)["summary"]["envelope_km2"]
    assert s["low"] <= s["central"] <= s["high"]


def test_speed_72h():
    t = time.time()
    run(hours=72, fuel="grass", wx=make_wx(hours=72, wind=25), envelope=True)
    assert time.time() - t < 3.0


def test_polygon_area_close_to_cells():
    r = run(keep_masks=True)
    mask, grid = r["frames"][-1]["_mask"]
    ring = np.array(r["frames"][-1]["burned"]["coordinates"][0][0])
    x = (ring[:, 0] - grid.clon) / grid.kx
    y = (ring[:, 1] - grid.clat) / grid.ky
    area = 0.5 * abs(np.dot(x, np.roll(y, 1)) - np.dot(y, np.roll(x, 1))) / 1e6
    assert area == pytest.approx(r["frames"][-1]["burned_km2"], rel=0.15)


def test_weather_failure_degrades():
    wx, info = ps.fetch_weather(10, 10, START, 24, getter=no_net)
    assert info["fallback"] is True and info["errors"]
    assert len(wx) == 24 + ps.SPINUP_H + 1


def test_weather_api_selection():
    calls = []

    def fake(url, params, ttl):
        calls.append(url)
        times = pd.date_range(params["start_date"], pd.Timestamp(params["end_date"]) + pd.Timedelta(hours=23), freq="h")
        n = len(times)
        return {"hourly": {"time": [t.strftime("%Y-%m-%dT%H:%M") for t in times], "temperature_2m": [20] * n,
                           "relative_humidity_2m": [40] * n, "wind_speed_10m": [10] * n,
                           "wind_direction_10m": [90] * n, "wind_gusts_10m": [20] * n,
                           "precipitation": [0] * n}}, False
    now = pd.Timestamp("2026-11-14 12:00")
    wx, info = ps.fetch_weather(0, 0, pd.Timestamp("2023-06-01"), 24, now=now, getter=fake)
    assert calls == [ps.ARCHIVE_URL] and not info["fallback"] and wx["temp_c"].eq(20).all()
    calls.clear()
    ps.fetch_weather(0, 0, now, 24, now=now, getter=fake)
    assert calls == [ps.FORECAST_URL]


def test_resolve_ignition_live_and_fallback():
    df = pd.DataFrame({"lat": [50.0, 50.01, 52.0], "lon": [-120.0, -120.01, -120.0], "frp": [10, 20, 5],
                       "t": pd.to_datetime(["2026-11-14 10:00"] * 3), "sat": ["N20", "N21", "N20"]})
    ign = ps.resolve_ignition("live", 50.0, -120.0, live_loader=lambda: (df, []))
    assert ign["mode"] == "live" and ign["n_detections"] == 2 and ign["estimate"]
    ign = ps.resolve_ignition("live", 10.0, 10.0, live_loader=lambda: (df, []))
    assert ign["mode"] == "point" and "no FIRMS" in ign["fallback_reason"]
    ign = ps.resolve_ignition("archive", 10.0, 10.0, state=None)
    assert ign["mode"] == "point" and ign["fallback_reason"]


# ------------------------------------------------------------------ API
@pytest.fixture()
def client(monkeypatch):
    from fastapi.testclient import TestClient
    from backend import app as appmod
    real = ps.simulate

    def fake_sim(lat, lon, start, hours, step_min, **kw):
        kw.update(wx=make_wx(hours=hours, start=start), elev_fn=flat, getter=no_net)
        return real(lat, lon, start, hours, step_min, **kw)
    monkeypatch.setattr(appmod._phys, "simulate", fake_sim)
    monkeypatch.setattr(appmod._phys, "_get_json", no_net)
    monkeypatch.setattr(appmod._phys, "fetch_weather_grid", lambda *a, **k: (None, {"errors": ["offline"]}))
    monkeypatch.setattr(appmod._phys, "fwi_summary", lambda *a, **k: {"error": "offline test"})
    monkeypatch.setattr(ps.LandscapeProvider, "get", lambda self: None)
    appmod._SIM_CACHE.clear()
    return TestClient(appmod.app)


def test_simulate_endpoint(client):
    r = client.get("/simulate", params={"lat": -15, "lon": 130, "start": "2023-06-01T00:00", "hours": 12,
                                        "step_min": 120})
    assert r.status_code == 200
    j = r.json()
    assert len(j["frames"]) == 7 and j["ignition"]["mode"] == "point" and j["summary"]["burned_km2"] > 0
    assert client.get("/simulate", params={"lat": 0, "lon": 0, "ignition": "x"}).status_code == 400
    assert client.get("/simulate", params={"lat": 0, "lon": 0, "start": "nope"}).status_code == 400


def test_simulate_weather_endpoint_degrades(client):
    r = client.get("/simulate/weather", params={"lat": -15, "lon": 130, "start": "2023-06-01T00:00", "hours": 6})
    assert r.status_code == 200
    j = r.json()
    assert len(j["hours"]) == 7 and j["weather"]["fallback"] is True


# ------------------------------------------------------------------ v2: envelope, multi-fire, calibration
def test_envelope_ordering_tiny_point_fire_every_frame():
    # small forest point fire: before the fix the coarse envelope grid gave low = high > central
    r = run(lat=55.0, lon=-110.0, fuel="forest", hours=48, wx=make_wx(hours=48, wind=10), envelope=True)
    assert r["summary"]["envelope_grid"] == "same as central"
    for f in r["frames"]:
        e = f["envelope_km2"]
        assert e["low"] <= f["burned_km2"] <= e["high"]
    assert r["frames"][0]["envelope_km2"]["low"] == r["frames"][0]["burned_km2"]   # same ignition area


def test_envelope_coarse_grid_rescaled(monkeypatch):
    monkeypatch.setattr(ps, "ENV_SAME_GRID_CELLS", 0)       # force the coarse-grid path
    r = run(lat=55.0, lon=-110.0, fuel="forest", hours=24, wx=make_wx(hours=24, wind=10), envelope=True)
    assert r["summary"]["envelope_grid"].startswith("2x coarser")
    f0 = r["frames"][0]
    assert f0["envelope_km2"]["low"] == f0["envelope_km2"]["high"] == f0["burned_km2"]
    for f in r["frames"]:
        assert f["envelope_km2"]["low"] <= f["burned_km2"] <= f["envelope_km2"]["high"]


def two_fire_ignition(sep_deg=0.04):
    return {"mode": "live", "lats": np.array([-15.0, -15.001, -15.0, -15.0]),
            "lons": np.array([130.0, 130.001, 130.0 + sep_deg, 130.001 + sep_deg]), "estimate": True}


def test_fires_labelled_and_merge_detected():
    r = run(lat=-15.0, lon=130.02, hours=48, ignition=two_fire_ignition(), wx=make_wx(hours=48, wind=15))
    assert [f["id"] for f in r["fires"]] == ["A", "B"] and all(f["estimate"] for f in r["fires"])
    assert r["fires"][0]["centroid"][0] < r["fires"][1]["centroid"][0]
    assert len(r["merges"]) == 1
    m = r["merges"][0]
    assert m["fires"] == ["A", "B"] and 0 < m["t_hours"] < 48
    assert 130.0 < m["location"][0] < 130.05 and m["time_utc"].endswith("Z")
    early = next(f for f in r["frames"] if f["t_hours"] < m["t_hours"] and f["t_hours"] > 0)
    late = r["frames"][-1]
    assert early["separate_fires"] == [["A"], ["B"]] and late["separate_fires"] == [["A", "B"]]
    for f in r["frames"]:
        assert sum(x["burned_km2"] for x in f["fires"]) == pytest.approx(f["burned_km2"], abs=0.01)


def test_far_fires_do_not_merge_and_gap_rule():
    r = run(lat=-15.0, lon=130.0, hours=6, ignition=two_fire_ignition(0.3), wx=make_wx(hours=6, wind=5))
    assert len(r["fires"]) == 2 and r["merges"] == []
    assert r["frames"][-1]["separate_fires"] == [["A"], ["B"]]
    g = ps.Grid(0, 0, 5000, 250)
    c = g.n // 2
    ign = [c * g.n + c, c * g.n + c + 4, c * g.n + c + 12]      # gaps 750 m (joins) and 1750 m (separate)
    assert ps.label_fires(g, ign, 1000).tolist() == [0, 0, 1]


def test_point_ignition_single_fire():
    r = run()
    assert len(r["fires"]) == 1 and r["merges"] == [] and r["frames"][-1]["separate_fires"] == [["A"]]


def test_ros_mult_scales_growth():
    a1 = run(hours=24)["summary"]
    a2 = run(hours=24, ros_mult=2.0)["summary"]
    a05 = run(hours=24, ros_mult=0.5)["summary"]
    g = lambda s: s["burned_km2"] - s["initial_km2"]  # noqa: E731
    assert g(a05) < g(a1) < g(a2)


def test_live_radius_default_and_clip():
    df = pd.DataFrame({"lat": [50.0, 50.2, 50.45], "lon": [-120.0, -120.0, -120.0], "frp": [1, 1, 1],
                       "t": pd.to_datetime(["2026-11-14 10:00"] * 3), "sat": ["N20"] * 3})
    assert ps.resolve_ignition("live", 50.0, -120.0, live_loader=lambda: (df, []))["n_detections"] == 2   # 30 km
    assert ps.resolve_ignition("live", 50.0, -120.0, live_loader=lambda: (df, []), radius_km=10)["n_detections"] == 1
    assert ps.resolve_ignition("live", 50.0, -120.0, live_loader=lambda: (df, []),
                               radius_km=500)["radius_km"] == ps.RADIUS_KM_MAX


def synth_history(growth_pix_per_day, days=2, t_now=pd.Timestamp("2023-06-03 00:00"), lat=-15.0, lon=130.0):
    """Detections of a fire growing eastward by `growth` new 375 m pixels per day (rows of pixels)."""
    rows = []
    step = ps.PIX_M * ps.M_TO_DEG
    for k in range(days + 1):
        t = t_now - pd.Timedelta(days=days - k) - pd.Timedelta(hours=2)
        n = 4 if k == 0 else growth_pix_per_day
        x0 = 0 if k == 0 else 2 + (k - 1) * math.ceil(growth_pix_per_day / 4) + 0
        for i in range(n):
            rows.append({"lat": lat + (i % 4) * step / 1.0, "lon": lon + (x0 + i // 4) * step / math.cos(math.radians(lat)),
                         "t": t})
    return pd.DataFrame(rows)


import math  # noqa: E402


def cal_run(hist, t_now=pd.Timestamp("2023-06-03 00:00"), **kw):
    wx = make_wx(hours=72 + 72, start=t_now - pd.Timedelta(days=3))
    last = hist[hist["t"] > t_now - pd.Timedelta(hours=24)]
    return ps.calibrate(last["lat"].to_numpy(), last["lon"].to_numpy(), t_now, hist, wx=wx, fuel="savanna",
                        elev_fn=flat, getter=no_net, **kw)


def test_calibration_fits_slow_and_fast_fires():
    slow = cal_run(synth_history(4))
    fast = cal_run(synth_history(400))
    assert slow["applied"] and fast["applied"] and slow["days_used"] == 2
    assert slow["m"] < fast["m"]
    assert ps.CAL_BOUNDS[0] <= slow["m"] <= ps.CAL_BOUNDS[1] and ps.CAL_BOUNDS[0] <= fast["m"] <= ps.CAL_BOUNDS[1]
    assert slow["observed_km2"] == pytest.approx(8 * (ps.PIX_M / 1000) ** 2)
    assert slow["confidence"] in ("low", "medium", "high") and len(slow["runs"]) <= 4


def test_calibration_shrinks_with_weak_evidence():
    strong = cal_run(synth_history(4), sigma_prior=100.0, sigma_model=0.01)
    weak = cal_run(synth_history(4), sigma_prior=0.001)
    assert weak["m"] == pytest.approx(1.0, abs=0.01) and strong["m"] < weak["m"]
    assert strong["m"] == pytest.approx(strong["m_fit"], rel=1e-3)


def test_calibration_no_leakage_and_no_history():
    t_now = pd.Timestamp("2023-06-03 00:00")
    h = synth_history(4)
    future = h.assign(t=h["t"] + pd.Timedelta(days=5), lon=h["lon"] + 0.01)
    a, b = cal_run(h), cal_run(pd.concat([h, future]))
    assert a["observed_km2"] == b["observed_km2"] and a["m"] == b["m"]
    young = h[h["t"] > t_now - pd.Timedelta(hours=24)]
    assert cal_run(young)["applied"] is False and "too young" in cal_run(young)["reason"]
    assert cal_run(h.iloc[0:0])["applied"] is False


def test_forecast_live_calibration_pipeline():
    t_now = pd.Timestamp("2023-06-03 00:00")
    hist = synth_history(40)
    last = hist[hist["t"] > t_now - pd.Timedelta(hours=24)].assign(frp=1.0, sat="N20")
    wx_all = make_wx(hours=72 + 72, start=t_now - pd.Timedelta(days=3))

    def getter(url, params, ttl):
        raise RuntimeError("no network")
    orig = ps.fetch_weather
    try:
        ps.fetch_weather = lambda *a, **k: (wx_all, {"sources": [], "errors": [], "fallback": False})
        r = ps.forecast(-15.0, 130.0, t_now, 24, 120, ignition="live", fuel="savanna", envelope=False, frames=False,
                        live_loader=lambda: (last, []), history_loader=lambda: (hist.assign(sat="N20"), []),
                        getter=getter, elev_fn=flat, weather="point", fwi=False)
        off = ps.forecast(-15.0, 130.0, t_now, 24, 120, ignition="live", fuel="savanna", envelope=False,
                          frames=False, live_loader=lambda: (last, []), calibration="off", getter=getter,
                          elev_fn=flat, weather="point", fwi=False)
    finally:
        ps.fetch_weather = orig
    c = r["calibration"]
    assert c["applied"] and c["days_used"] == 2 and r["ros_multiplier"] == pytest.approx(c["m"], abs=1e-3)
    assert set(c) >= {"m", "observed_km2", "simulated_km2", "days_used", "confidence"}
    assert off["calibration"]["applied"] is False and off["ros_multiplier"] == 1.0
    assert r["fires"] and r["fires"][0]["id"] == "A"


def test_simulate_endpoint_new_params(client):
    r = client.get("/simulate", params={"lat": -15, "lon": 130, "start": "2023-06-01T00:00", "hours": 6,
                                        "radius_km": 45, "calibration": "on"})
    assert r.status_code == 200
    j = r.json()
    assert j["calibration"]["applied"] is False and "point" in j["calibration"]["reason"]
    assert j["fires"][0]["id"] == "A" and j["merges"] == [] and "separate_fires" in j["frames"][0]
    assert client.get("/simulate", params={"lat": 0, "lon": 0, "calibration": "x"}).status_code == 400
    assert client.get("/simulate", params={"lat": 0, "lon": 0, "radius_km": 100}).status_code == 422


def test_speed_72h_multi_fire():
    ign = {"mode": "live", "lats": -15.0 + np.repeat([0.0, 0.05, -0.08], 30) + np.tile(np.arange(30), 3) * 0.003,
           "lons": 130.0 + np.repeat([0.0, 0.1, 0.2], 30), "estimate": True}
    t = time.time()
    r = run(hours=72, ignition=ign, wx=make_wx(hours=72, wind=20), envelope=True)
    assert time.time() - t < 3.0
    assert len(r["fires"]) == 3


# ------------------------------------------------------------------ v3: gridded weather, FWI, GOES, shrinkage
def make_grid(wx, lat=-15.0, lon=130.0, n=5, east_wind_mult=1.0):
    from equifire.fireweather import WeatherGrid
    la, lo = np.linspace(lat - 0.36, lat + 0.36, n), np.linspace(lon - 0.36, lon + 0.36, n)
    arr = {src: np.repeat(np.repeat(wx[dst].to_numpy(float)[:, None, None], n, 1), n, 2)
           for src, dst in ps.WG_VARS.items()}
    arr["wind_speed_10m"] = arr["wind_speed_10m"] * np.linspace(1, east_wind_mult, n)[None, None, :]
    return WeatherGrid(la, lo, wx.index.to_numpy().astype("datetime64[h]"), arr)


def test_uniform_weather_grid_equals_point_weather():
    wx = make_wx(hours=24)
    a = run(wx=wx, envelope=True)
    b = run(wx=None, wx_grid=make_grid(wx), envelope=True)
    assert a["summary"]["envelope_km2"] == b["summary"]["envelope_km2"]
    assert b["weather"]["gridded"]["points"] == 25


def test_weather_grid_spatial_wind_matters():
    wx = make_wx(hours=24)
    a = run(wx=None, wx_grid=make_grid(wx))["summary"]["burned_km2"]
    b = run(wx=None, wx_grid=make_grid(wx, east_wind_mult=2.0))["summary"]["burned_km2"]
    assert b > 1.2 * a


def test_goes_pixel_keys_match_fixed_grid():
    k = ps.goes_pixel_keys([0.0], [-75.0], [16])   # sub-satellite point = centre of the 5424 x 5424 full disk
    assert k == {("G16", 2712, 2712)}
    k2 = ps.goes_pixel_keys([0.0, 0.0], [-75.0, -74.9], [19])
    assert len(k2) == 2 and ps.goes_covered(40, -120) and not ps.goes_covered(-15, 130)


def test_calibration_too_few_pixels_keeps_m1():
    c = cal_run(synth_history(1))
    assert c["applied"] is False and "too few" in c["reason"] and c["m"] == 1.0


def test_calibration_uses_goes_pixels():
    t_now = pd.Timestamp("2023-06-03 00:00")
    h = synth_history(4, lat=40.0, lon=-120.0)
    last = h[h["t"] > t_now - pd.Timedelta(hours=24)]
    wx = make_wx(hours=144, start=t_now - pd.Timedelta(days=3))
    gx, gy = next(iter(ps.goes_pixel_keys([40.0], [-120.0], [18])))[1:]
    rows = [{"time": t_now - pd.Timedelta(hours=40 - i), "sat": "G18", "lat": 40.0 + 0.02 * i, "lon": -120.0,
             "px_x": gx, "px_y": gy - i, "px_km2": 6.0} for i in range(12)]
    goes_df = pd.DataFrame(rows)
    goes_df.attrs["scans"] = 40
    kw = dict(wx=wx, fuel="grass", elev_fn=flat, getter=no_net)
    a = ps.calibrate(last["lat"].to_numpy(), last["lon"].to_numpy(), t_now, h, **kw)
    b = ps.calibrate(last["lat"].to_numpy(), last["lon"].to_numpy(), t_now, h, goes_df=goes_df, **kw)
    assert b["applied"] and b["goes"]["observed_new_pixels"] > 0 and "simulated_goes_new_pixels" in b["runs"][0]
    assert b["m"] != a["m"]


def test_forecast_grid_fwi_offline():
    wx = make_wx(hours=24 + 48, start=START - pd.Timedelta(hours=48))
    calls = {}

    def grid_fn(lat, lon, half_km, t0, hours, n=5):
        calls["grid"] = (half_km, hours, n)
        return make_grid(make_wx(hours=24), lat, lon)

    def fwi_fn(lat, lon, date):
        return {"date": date, "ffmc": 90.0, "dmc": 30, "dc": 300, "isi": 8, "bui": 50, "fwi": 18, "dsr": 5,
                "danger": "high"}
    r = ps.forecast(-15.0, 130.0, START, 24, 60, ignition="point", fuel="savanna", envelope=False, frames=False,
                    getter=no_net, elev_fn=flat, grid_fn=grid_fn, fwi_fn=fwi_fn)
    assert calls["grid"][2] == 5 and r["weather"]["gridded"]["points"] == 25
    assert r["fwi"]["danger"] == "high" and r["fwi"]["ffmc_fine_fuel_moisture_pct"] == pytest.approx(10.8, abs=0.1)
    assert r["fwi"]["model_1h_moisture_pct_at_noon"] is not None
    del wx


def test_forecast_grid_failure_falls_back_to_point():
    def bad_grid(*a, **k):
        raise RuntimeError("offline")
    r = ps.forecast(-15.0, 130.0, START, 6, 60, ignition="point", fuel="savanna", envelope=False, frames=False,
                    getter=no_net, elev_fn=flat, grid_fn=bad_grid, fwi=False)
    assert any("weather grid failed" in e for e in r["weather"]["errors"]) and r["weather"]["fallback"] is True


# Landscape integration: physical behavior, barrier crossing, fallback and geographic alignment.
def land_fixture(lat=-15.0, lon=130.0, half_km=10.0, fuel=1, wall=False, dryness=0.5):
    g = ps.Grid(lat, lon, half_km * 1000, 250)
    shape = (g.n, g.n)
    fm = np.full(shape, fuel, int)
    barrier = np.zeros(shape)
    if wall:
        fm[:, g.n // 2 + 2] = 98
        barrier[:, g.n // 2 + 2] = 1
    return {"fuel_model": fm, "burnable_frac": np.where(fm == 98, 0.0, 1.0),
            "barrier_lines": barrier, "barrier_area": np.zeros(shape),
            "tan_slope": np.zeros(shape), "upslope_deg": np.zeros(shape),
            "dryness": np.full(shape, dryness), "meta": {"layers": {"fuel": {"source": "test"}}}}


def land_provider(**kw):
    return ps.LandscapeProvider(-15, 130, 10, START, fetch_fn=lambda *a, **k: land_fixture(**kw))


def test_landscape_river_blocks_even_long_neighbours():
    r = run(fuel="grass", hours=12, wx=make_wx(hours=12, wind=35), keep_masks=True,
            landscape=land_provider(wall=True))
    mask, g = r["frames"][-1]["_mask"]
    lon, _ = g.centre_lonlat()
    # Water is 500 m east of ignition; the 16-neighbour stencil must not jump over it.
    assert not mask[:, lon > 130 + 400 * g.kx].any()
    assert r["landscape"]["blocked_cells"] > 0
    assert mask.sum() > 1


def test_landscape_fuel_map_changes_spread_and_envelopes():
    grass = run(fuel="auto", landscape=land_provider(fuel=1), envelope=True)
    forest = run(fuel="auto", landscape=land_provider(fuel=8), envelope=True)
    assert grass["summary"]["burned_km2"] > forest["summary"]["burned_km2"]
    for r in (grass, forest):
        assert r["landscape"]["applied"]
        assert r["summary"]["envelope_grid"] == "same as central"
        for frame in r["frames"]:
            assert frame["envelope_km2"]["low"] <= frame["burned_km2"] <= frame["envelope_km2"]["high"]


def test_landscape_dry_live_fuel_increases_spread():
    moist = run(landscape=land_provider(fuel=5, dryness=0.1))
    dry = run(landscape=land_provider(fuel=5, dryness=0.9))
    assert dry["summary"]["burned_km2"] > moist["summary"]["burned_km2"]


def test_landscape_failure_is_reported_and_preserves_fallback():
    provider = ps.LandscapeProvider(-15, 130, 10, START, fetch_fn=no_net)
    r = run(landscape=provider)
    assert r["summary"]["burned_km2"] == run()["summary"]["burned_km2"]
    assert not r["landscape"]["applied"] and "network disabled" in r["landscape"]["error"]


def test_archive_coordinates_are_latitude_then_longitude():
    from equifire.common import cell_center
    from types import SimpleNamespace
    state = SimpleNamespace(viirs_slice=lambda day: pd.DataFrame({"gx": [6000], "gy": [1500]}))
    lat, lon = ps._archive_cells(state, 1)
    expected_lat, expected_lon = cell_center(6000, 1500)
    assert lat[0] == expected_lat and lon[0] == expected_lon


def test_hourly_weather_wind_shift_changes_later_growth():
    straight_wx = make_wx(hours=12, wind=25)
    shift_wx = straight_wx.copy()
    shift_wx.loc[START + pd.Timedelta(hours=6):, 'wind_dir'] = 90
    a = run(hours=12, step_min=180, wx=straight_wx, fuel='grass', keep_masks=True)
    b = run(hours=12, step_min=180, wx=shift_wx, fuel='grass', keep_masks=True)
    assert [f['t_hours'] for f in b['frames']] == [0, 3, 6, 9, 12]
    assert b['weather_timeline'][5]['wind_dir_deg'] == 270
    assert b['weather_timeline'][6]['wind_dir_deg'] == 90
    assert b['weather_timeline'][6]['valid_from_utc'] == '2023-06-01T06:00:00Z'
    ma, ga = a['frames'][-1]['_mask']; mb, gb = b['frames'][-1]['_mask']
    assert ga.n == gb.n and np.array_equal(a['frames'][2]['_mask'][0], b['frames'][2]['_mask'][0])
    assert not np.array_equal(ma, mb)


def test_rain_arriving_at_hour_six_reduces_subsequent_growth():
    wx = make_wx(hours=12, wind=25)
    rain = wx.copy()
    rain.loc[START + pd.Timedelta(hours=6):, 'rain_mm'] = 8
    dry_run = run(hours=12, wx=wx, fuel='grass')
    wet_run = run(hours=12, wx=rain, fuel='grass')
    assert wet_run['summary']['burned_km2'] < dry_run['summary']['burned_km2']
    assert wet_run['weather_timeline'][5]['rain_mm'] == 0
    assert wet_run['weather_timeline'][6]['rain_mm'] == 8
    assert wet_run['weather_timeline'][6]['fuel_moisture_1h_pct'] > dry_run['weather_timeline'][6]['fuel_moisture_1h_pct']


def test_missing_wind_direction_interpolates_across_north():
    frame = pd.DataFrame({'wind_dir': [350., np.nan, 10.], 'wind_kmh': [20., np.nan, 20.]})
    d = ps._interpolate_weather(frame)
    assert min(abs(d.wind_dir[1]), abs(360 - d.wind_dir[1])) < 1e-6


def test_forecast_discloses_archive_weather_replay():
    def bad_grid(*a, **k):
        raise RuntimeError('offline')
    result = ps.forecast(-15, 130, START, 6, 60, getter=no_net, elev_fn=flat, grid_fn=bad_grid, fwi=False)
    assert result['weather']['historical_replay_uses_observed_future'] is True
    assert result['weather']['mode'] == 'fallback_scenario'
    assert len(result['weather_timeline']) == 6


def test_playback_step_never_discards_the_requested_end_time():
    r = run(hours=7, step_min=180, wx=make_wx(hours=7))
    assert [f['t_hours'] for f in r['frames']] == [0, 3, 6, 7]
    assert sum(f['new_km2'] for f in r['frames']) == pytest.approx(r['summary']['burned_km2'], abs=.01)
    short = run(hours=3, step_min=720, wx=make_wx(hours=3))
    assert [f['t_hours'] for f in short['frames']] == [0, 3]


def test_complete_landscape_dem_avoids_discarded_elevation_request(monkeypatch):
    def unused(*a, **k):
        pytest.fail('Full mapped DEM already supplies every terrain cell')
    monkeypatch.setattr(ps, 'terrain', unused)
    result = run(hours=1, landscape=land_provider())
    assert result['grid']['terrain']['landscape_dem']
    assert result['grid']['terrain']['api'] == 'landscape DEM'


def test_partial_landscape_dem_keeps_elevation_fallback(monkeypatch):
    calls = []
    original = ps.terrain
    def terrain(*a, **k):
        calls.append(True)
        return original(*a, **k)
    monkeypatch.setattr(ps, 'terrain', terrain)
    provider = ps.LandscapeProvider(-15,130,1,START,
        fetch_fn=lambda *a, **k: land_fixture(half_km=1))
    result = run(hours=1, landscape=provider)
    assert calls and result['grid']['terrain']['landscape_dem']
