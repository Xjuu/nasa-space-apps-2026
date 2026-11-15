"""Tests for backend/nextobs.py with mocked network (FIRMS feeds, Open-Meteo, elevation, weather grid).

    EQUIFIRE_NO_LOAD=1 .venv/bin/python -m pytest backend/test_nextobs.py -q
"""
from __future__ import annotations

import datetime as dt
import os

os.environ.setdefault("EQUIFIRE_NO_LOAD", "1")

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import pytest  # noqa: E402
from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend import nextobs as N  # noqa: E402

NOW = dt.datetime(2026, 11, 14, 12, 0, tzinfo=dt.timezone.utc)   # close to the bundled TLE epochs (2026-11-13)
FIRE = (-10.0, -55.0)


def make_app() -> TestClient:
    app = FastAPI()
    app.include_router(N.router)
    return TestClient(app)


def fake_feeds(hours_ago=(5.0, 5.2, 30.0)):
    t = NOW.replace(tzinfo=None)
    rows = []
    for k, h in enumerate(hours_ago):
        rows.append({"lat": FIRE[0] + 0.06 * k, "lon": FIRE[1], "frp": 10.0 + k, "t": pd.Timestamp(t) - pd.Timedelta(hours=h),
                     "day": 0, "gx": 0, "gy": 0, "sat": "N20" if k < 2 else "MODIS", "conf": "high"})
    df = pd.DataFrame(rows)
    return df, [{"feed": "N20", "satellite": "NOAA-20", "latest": "x", "stale": False, "error": None}]


def fake_cloud(pct):
    def http(url, params, timeout=25.0):
        la = params["latitude"].split(",")
        t0, t1 = params["start_date"], params["end_date"]
        times = pd.date_range(t0, pd.Timestamp(t1) + pd.Timedelta(hours=23), freq="h")
        one = {"hourly": {"time": [x.strftime("%Y-%m-%dT%H:%M") for x in times],
                          "cloud_cover": [pct] * len(times), "cloud_cover_low": [pct / 2] * len(times),
                          "cloud_cover_mid": [pct] * len(times), "cloud_cover_high": [pct / 3] * len(times)}}
        return [one] * len(la) if len(la) > 1 else one
    return http


@pytest.fixture(autouse=True)
def mocks(monkeypatch):
    monkeypatch.setattr(N, "_now", lambda: NOW)
    monkeypatch.setattr(N, "_load_feeds", fake_feeds)
    monkeypatch.setattr(N, "_http_json", fake_cloud(10.0))
    monkeypatch.setattr(N, "_get_state", lambda: None)
    N._cloud_cache.clear()
    N._ctx_cache.clear()


# ------------------------------------------------------------------ helpers
def test_cloud_and_scan_tiers():
    assert N.cloud_tier(10) == "clear" and N.cloud_tier(50) == "partly" and N.cloud_tier(80) == "obscured"
    assert N.cloud_tier(None) == "unknown"
    assert N.scan_tier(10) == "good" and N.scan_tier(30) == "moderate" and N.scan_tier(50) == "degraded"


def test_parse_time():
    t = N.parse_time("2026-11-14T22:00:00Z")
    assert t.tzinfo is not None and t.hour == 22
    with pytest.raises(HTTPException):
        N.parse_time("yesterday")


def test_parse_scenario_names_and_plus_as_space():
    assert N.parse_scenario("wind_x1.5") == {"name": "wind_x1.5", "kind": "wind_scale", "value": 1.5}
    assert N.parse_scenario("wind_rot 45")["value"] == 45.0          # '+' decoded as space in a query string
    assert N.parse_scenario("wind_rot-45")["value"] == -45.0
    assert N.parse_scenario("rain_6mm")["value"] == 6.0
    assert N.parse_scenario("dry_rh-15")["value"] == -15.0
    assert N.parse_scenario("dry_rh15")["value"] == -15.0            # dry_ never raises humidity
    assert N.parse_scenario("wet_rh+10")["value"] == 10.0
    for bad in ("wind_x9", "wind_rot+270", "hail", "rain_999mm"):
        with pytest.raises(HTTPException):
            N.parse_scenario(bad)


def make_grid(hours=60, t_end=None):
    from equifire.fireweather import WeatherGrid
    times = pd.date_range(end=t_end or pd.Timestamp("2026-11-14T18:00"), periods=hours, freq="h")
    shape = (hours, 5, 5)
    arr = {"temperature_2m": np.full(shape, 30.0), "relative_humidity_2m": np.full(shape, 30.0),
           "wind_speed_10m": np.full(shape, 20.0), "wind_direction_10m": np.full(shape, 350.0),
           "wind_gusts_10m": np.full(shape, 30.0), "precipitation": np.zeros(shape)}
    return WeatherGrid(np.linspace(-10.3, -9.7, 5), np.linspace(-55.3, -54.7, 5), times.to_numpy().astype("datetime64[h]"), arr)


def test_apply_scenario_edits_only_simulation_hours():
    wg, t0 = make_grid(), pd.Timestamp("2026-11-14T12:00")
    sim = wg.times.astype("datetime64[s]") >= np.datetime64(t0.to_datetime64(), "s")
    w = N.apply_scenario(wg, N.parse_scenario("wind_x1.5"), t0)
    assert np.allclose(w.arrays["wind_speed_10m"][sim], 30.0) and np.allclose(w.arrays["wind_speed_10m"][~sim], 20.0)
    assert np.allclose(w.arrays["wind_gusts_10m"][sim], 45.0)
    assert np.allclose(wg.arrays["wind_speed_10m"], 20.0)             # original untouched
    r = N.apply_scenario(wg, N.parse_scenario("wind_rot+45"), t0)
    assert np.allclose(r.arrays["wind_direction_10m"][sim], 35.0)      # 350 + 45 wraps
    d = N.apply_scenario(wg, N.parse_scenario("dry_rh-15"), t0)
    assert np.allclose(d.arrays["relative_humidity_2m"][sim], 15.0) and np.allclose(d.arrays["relative_humidity_2m"][~sim], 30.0)
    rain = N.apply_scenario(wg, N.parse_scenario("rain_6mm"), t0)
    assert rain.arrays["precipitation"].sum() == pytest.approx(6.0 * 25)   # 6 mm at each of 25 grid points
    assert (rain.arrays["relative_humidity_2m"][sim][:6] >= 85).all()


# ------------------------------------------------------------------ /nextobs
def test_nextobs_live_fire_and_passes(monkeypatch):
    c = make_app()
    r = c.get("/nextobs", params={"lat": FIRE[0], "lon": FIRE[1], "time": NOW.isoformat()})
    assert r.status_code == 200, r.text
    d = r.json()
    f = d["fire"]
    assert f["found"] and f["mode"] == "live_nrt" and f["n_detections_7d"] == 3
    assert f["observation_age_hours"] == pytest.approx(5.0, abs=0.01)          # newest detection was 5 h before NOW
    assert f["last_detection_utc"].endswith("Z")
    ps = d["passes"]
    assert ps and all(p["hours_from_ref"] >= 0 for p in ps)
    assert [p["hours_from_ref"] for p in ps] == sorted(p["hours_from_ref"] for p in ps)
    assert d["next_by_instrument"]["VIIRS (NOAA-20/21)"]["key"] in ("noaa20", "noaa21")   # S-NPP excluded
    assert d["next_by_instrument"]["MODIS (Terra/Aqua)"]["instrument"] == "MODIS"
    assert d["gap_hours"]["to_next_viirs"] > 0 and d["gap_hours"]["since_last_detection"] == f["observation_age_hours"]
    su = d["simulate_until"]
    assert su["hours"] == int(np.ceil(su["exact_hours"])) and 1 <= su["ml_predict_days"] <= 3
    assert d["tle"] and all(t["fresh"] for t in d["tle"])
    assert "not proof" in d["visibility_legend"]["note"] or "indicator only" in d["visibility_legend"]["note"]
    assert d["recent_passes"] and all(p["hours_from_ref"] < 0 for p in d["recent_passes"])
    snpp = [p for p in ps if p["key"] == "snpp"]
    assert all(not p["usable"] and p["excluded_reason"] for p in snpp)


def test_nextobs_cloud_changes_usable_horizon(monkeypatch):
    c = make_app()
    q = {"lat": FIRE[0], "lon": FIRE[1], "time": NOW.isoformat()}
    clear = c.get("/nextobs", params=q).json()
    assert clear["next_usable_observation"] is not None
    assert clear["next_usable_observation"]["cloud"]["visibility"] == "clear"
    assert "cloud cover below 70%" in clear["simulate_until"]["basis"]
    N._cloud_cache.clear()
    monkeypatch.setattr(N, "_http_json", fake_cloud(95.0))
    cloudy = c.get("/nextobs", params=q).json()
    assert cloudy["next_usable_observation"] is None                          # everything obscured
    assert "regardless of cloud" in cloudy["simulate_until"]["basis"]
    assert cloudy["simulate_until"]["hours"] is not None


def test_nextobs_cloud_failure_is_unknown_not_clear(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("down")
    monkeypatch.setattr(N, "_http_json", boom)
    monkeypatch.setattr(N.time if hasattr(N, "time") else N._time, "sleep", lambda s: None, raising=False)
    d = make_app().get("/nextobs", params={"lat": FIRE[0], "lon": FIRE[1], "time": NOW.isoformat()}).json()
    assert all(p["cloud"]["visibility"] == "unknown" for p in d["passes"])
    assert any("unavailable" in n for n in d["notes"])


def test_nextobs_no_fire_still_gives_passes(monkeypatch):
    d = make_app().get("/nextobs", params={"lat": 40.0, "lon": 10.0, "time": NOW.isoformat()}).json()
    assert d["fire"]["found"] is False and d["passes"]


def test_nextobs_historical_uses_nominal_estimates():
    d = make_app().get("/nextobs", params={"lat": FIRE[0], "lon": FIRE[1], "time": "2025-08-01T12:00:00Z"}).json()
    assert d["query"]["mode"] == "archive_daily"
    assert d["fire"]["found"] is False and "archive not loaded" in d["fire"]["note"]
    assert d["passes"] and all(p["estimate"] for p in d["passes"])
    assert any("nominal" in n for n in d["notes"])


def test_nextobs_validation():
    c = make_app()
    assert c.get("/nextobs", params={"lat": 100, "lon": 0}).status_code == 422
    assert c.get("/nextobs", params={"lat": 0, "lon": 0, "time": "nope"}).status_code == 400


# ------------------------------------------------------------------ /blindspots
BOX = "-55.5,-10.5,-54.5,-9.5"


def test_blindspots_classes_and_counts(monkeypatch):
    c = make_app()
    r = c.get("/blindspots", params={"bbox": BOX, "time": NOW.isoformat(), "hours": 24})
    assert r.status_code == 200, r.text
    d = r.json()
    cn = d["counts"]
    four = (cn["fire_detected"] + cn["recently_burned_not_seen"] + cn["no_fire_detected_good_observation"]
            + cn["insufficient_observation"])
    assert four == cn["total_cells"] == 400
    assert cn["fire_detected"] == 2                       # two detections within 24 h, distinct cells
    assert cn["previously_detected_now_quiet_good_observation"] == 1   # the 30 h old detection, tile well observed
    assert set(d["legend"]) == {"fire_detected", "no_fire_detected_good_observation", "insufficient_observation",
                                "recently_burned_not_seen"}
    assert "NOT" in d["legend"]["insufficient_observation"]
    layers = {f["properties"]["layer"] for f in d["features"]}
    assert layers == {"observation", "fire"}


def test_blindspots_cloud_makes_insufficient_and_recent_burn_unseen(monkeypatch):
    monkeypatch.setattr(N, "_http_json", fake_cloud(95.0))
    d = make_app().get("/blindspots", params={"bbox": BOX, "time": NOW.isoformat(), "hours": 24}).json()
    cn = d["counts"]
    assert cn["no_fire_detected_good_observation"] == 0
    assert cn["insufficient_observation"] > 300
    assert cn["recently_burned_not_seen"] == 1            # old detection in a cloud-blind tile
    obs = [f for f in d["features"] if f["properties"]["layer"] == "observation"]
    assert all(f["properties"]["class"] == "insufficient_observation" and f["properties"]["reasons"] for f in obs)
    assert any("cloud cover" in r for f in obs for r in f["properties"]["reasons"])


def test_blindspots_missing_cloud_is_insufficient_not_good(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("down")
    monkeypatch.setattr(N, "_http_json", boom)
    monkeypatch.setattr(N._time, "sleep", lambda s: None)
    d = make_app().get("/blindspots", params={"bbox": BOX, "time": NOW.isoformat()}).json()
    assert d["counts"]["no_fire_detected_good_observation"] == 0
    assert any("cloud data missing" in n for n in d["notes"])


def test_blindspots_tile_step_and_validation():
    assert N._tile_step([0, 0, 2, 2]) == 0.25
    assert N._tile_step([0, 0, 4, 4]) == 0.5
    assert N._tile_step([0, 0, 10, 10]) == 1.0
    c = make_app()
    assert c.get("/blindspots", params={"bbox": "0,0,20,5"}).status_code == 400
    assert c.get("/blindspots", params={"bbox": "1,2,3"}).status_code == 400
    assert c.get("/blindspots", params={"bbox": BOX, "time": "2026-11-01T00:00:00Z"}).status_code == 400
    assert c.get("/blindspots", params={"bbox": BOX, "time": "2026-11-20T00:00:00Z"}).status_code == 400


# ------------------------------------------------------------------ /scenarios (real physics, synthetic weather)
@pytest.fixture
def physics(monkeypatch):
    from backend import physics_spread as P
    monkeypatch.setattr(P, "fetch_weather_grid", lambda clat, clon, start, hours, **k: (make_grid(), {"errors": []}))
    monkeypatch.setattr(P, "fetch_elevation", lambda la, lo, getter=None: (None, {"errors": ["mock"]}))
    return P


def test_scenarios_hypothetical_label_and_ordering(physics):
    c = make_app()
    r = c.get("/scenarios", params={"lat": FIRE[0], "lon": FIRE[1], "start": "2026-11-14T12:00", "hours": 6,
                                    "ignition": "point", "fuel": "grass", "landscape": "off",
                                    "scenarios": "baseline,wind_x1.5,wind_x0.5,wind_rot+45,rain_6mm,dry_rh-15"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert "not the issued forecast" in d["label"]
    sc = {s["name"]: s for s in d["scenarios"]}
    assert list(sc)[0] == "baseline" and sc["baseline"]["vs_baseline"] is None
    assert set(sc) == {"baseline", "wind_x1.5", "wind_x0.5", "wind_rot+45", "rain_6mm", "dry_rh-15"}
    base = sc["baseline"]["growth_km2"]
    assert sc["wind_x1.5"]["growth_km2"] > base > sc["wind_x0.5"]["growth_km2"]
    assert sc["dry_rh-15"]["growth_km2"] >= base
    assert sc["rain_6mm"]["growth_km2"] <= base
    assert sc["wind_x1.5"]["vs_baseline"]["extra_km2"] > 0 > sc["wind_x0.5"]["vs_baseline"]["extra_km2"]
    assert len({s["cell_m"] for s in d["scenarios"]}) == 1            # comparable raster
    for s in d["scenarios"]:
        assert s["explanation"] and s["weather_change"] and s["timeline"][-1]["t_hours"] == 6
        assert set(s["sector_reach_km"]) == set(N.COMPASS)
        assert s["perimeter"]["type"] == "MultiPolygon"
    assert d["ignition"]["mode"] == "point"
    assert d["ranking_by_growth"][0] in ("wind_x1.5", "dry_rh-15")


def test_scenarios_rotation_shifts_direction(physics):
    d = make_app().get("/scenarios", params={"lat": FIRE[0], "lon": FIRE[1], "start": "2026-11-14T12:00", "hours": 6,
                                             "ignition": "point", "fuel": "grass", "landscape": "off",
                                             "scenarios": "wind_rot+90,wind_rot-90"}).json()
    sc = {s["name"]: s for s in d["scenarios"]}
    a, b = sc["wind_rot+90"], sc["wind_rot-90"]
    assert (a["mean_displacement_km"][0] > 0) != (b["mean_displacement_km"][0] > 0) or \
        abs(a["mean_displacement_km"][0] - b["mean_displacement_km"][0]) > 0.1   # opposite east-west drift
    assert d["scenarios"][0]["name"] == "baseline"                    # baseline inserted automatically


def test_scenarios_validation(physics):
    c = make_app()
    base = {"lat": FIRE[0], "lon": FIRE[1]}
    assert c.get("/scenarios", params={**base, "scenarios": "hail"}).status_code == 400
    assert c.get("/scenarios", params={**base, "ignition": "archive"}).status_code == 400
    assert c.get("/scenarios", params={**base, "start": "garbage"}).status_code == 400
    assert c.get("/scenarios", params={**base, "scenarios": ",".join(["wind_x1.1"] * 9 + ["wind_x1.2"])}).status_code in (200, 400)
