"""Quick API tests on small synthetic data (no full dataset needed).

    .venv/bin/python -m pytest backend/test_app.py -q
    EQUIFIRE_SLOW=1 .venv/bin/python -m pytest backend/test_app.py -q   # also load the real dataset
"""
from __future__ import annotations

import datetime as dt
import os
import xml.etree.ElementTree as ET

os.environ.setdefault("EQUIFIRE_NO_LOAD", "1")

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend import app as appmod  # noqa: E402
from backend.data import State, day_of  # noqa: E402
from backend.geo import lonlat_to_cell  # noqa: E402

from equifire import direct  # noqa: E402

HAS_MODEL = direct.MODEL_F.exists()
FIRE_LAT, FIRE_LON, FIRE_DATE = -10.0, -55.0, "2020-08-15"
ROUTINE = (20.5, 80.5)      # burns every year in ISO week 10
QUIET = (25.5, 85.5)        # burns only in 2021, ISO week 30


def _row(day, gx, gy, frp=20.0, viirs=True):
    return {"day": day, "gx": gx, "gy": gy, "n_v": 3 if viirs else 0, "ov_v": 1 if viirs else 0,
            "frp_v": frp if viirs else 0.0, "area_v": 0.4 if viirs else 0.0,
            "n_m": 0 if viirs else 1, "ov_m": 0 if viirs else 1, "frp_m": 0.0 if viirs else frp * 2,
            "frp_mh": 0.0 if viirs else frp, "area_mh": 0.0 if viirs else 0.4, "frp_max": frp}


def synthetic_daily() -> pd.DataFrame:
    rows = []
    far = lonlat_to_cell(-40.0, 140.0)
    rows += [_row(day_of("2013-01-01"), *far), _row(day_of("2021-12-31"), *far)]   # set VIIRS date range
    # routine: 5 cell-days in ISO week 10 every year 2013..2021
    gx, gy = lonlat_to_cell(*ROUTINE)
    for y in range(2013, 2022):
        mon = dt.date.fromisocalendar(y, 10, 1)
        rows += [_row(day_of((mon + dt.timedelta(days=i)).isoformat()), gx + i, gy) for i in range(5)]
    # quiet cell with a big off-season outbreak in 2021 week 30
    gx, gy = lonlat_to_cell(*QUIET)
    mon = dt.date.fromisocalendar(2021, 30, 1)
    rows += [_row(day_of((mon + dt.timedelta(days=d)).isoformat()), gx + i, gy + j)
             for d in range(5) for i in range(3) for j in range(2)]
    # a growing fire front moving east, plus one MODIS-only cell
    cx, cy = lonlat_to_cell(FIRE_LAT, FIRE_LON)
    t0 = day_of(FIRE_DATE)
    for d in range(-6, 4):
        w = max(1, 3 + d)
        rows += [_row(t0 + d, cx + i, cy + j, frp=30.0 + 5 * i) for i in range(w) for j in range(-1, 2)]
    rows.append(_row(t0, cx - 3, cy + 3, viirs=False))
    df = pd.DataFrame(rows).drop_duplicates(["day", "gx", "gy"])
    return df


@pytest.fixture(scope="module")
def client():
    with TestClient(appmod.app) as c:
        yield c


@pytest.fixture()
def loaded(client):
    old = appmod.store.state, appmod.store.fire_index, appmod.store.status
    appmod.store.set_state(State.from_daily(synthetic_daily()))
    yield client
    appmod.store.state, appmod.store.fire_index, appmod.store.status = old


# ------------------------------------------------------------------ no data needed
def test_health_before_load(client):
    r = client.get("/health")
    assert r.status_code == 200
    j = r.json()
    assert set(j) >= {"status", "data_loaded", "model_loaded", "data"}


@pytest.mark.parametrize("url,code", [
    ("/fires?date=2020-13-01", 400),
    ("/fires?date=yesterday", 400),
    ("/fires?date=2020-08-15&bbox=1,2,3", 400),
    ("/fires?date=2020-08-15&bbox=10,0,5,1", 400),
    ("/fires", 422),
    ("/forecast?lat=95&lon=0&date=2020-08-15", 422),
    ("/forecast?lat=0&lon=0&date=2020-08-15&days=0", 422),
    ("/forecast?lat=0&lon=0&date=2020-08-15&days=99", 422),
    ("/anomaly?lat=0&lon=200&date=2020-08-15", 422),
    ("/overpass?lat=0&lon=0&at=notatime", 400),
    ("/overpass?lat=0&lon=0&hours=1000", 422),
])
def test_bad_input(client, url, code):
    assert client.get(url).status_code == code


def test_not_loaded_is_503(client):
    if appmod.store.state is not None:
        pytest.skip("data loaded")
    r = client.get("/fires?date=2020-08-15")
    assert r.status_code == 503 and "Retry-After" in r.headers


def test_overpass(client):
    r = client.get("/overpass?lat=-10&lon=-55&at=2026-11-14T12:00:00Z&hours=24")
    assert r.status_code == 200
    j = r.json()
    assert [s["satellite"] for s in j["satellites"]] == ["Terra", "Aqua", "Suomi NPP", "NOAA-20", "NOAA-21"]
    assert j["next"] is not None and j["next"]["minutes_until"] >= 0
    assert all(p["max_elevation_deg"] > 0 and p["min_scan_deg"] <= 56.3 for p in j["passes"])
    # every polar orbiter sees a low-latitude point at least once a day (MODIS has small gaps: >= 3 of 5)
    assert sum(bool(s["passes"]) for s in j["satellites"]) >= 3
    assert j["passes"] == sorted(j["passes"], key=lambda p: p["minutes_until"])


# ------------------------------------------------------------------ synthetic data
def test_fires(loaded):
    r = loaded.get(f"/fires?date={FIRE_DATE}")
    assert r.status_code == 200
    j = r.json()
    assert j["type"] == "FeatureCollection"
    burning = [f for f in j["features"] if f["properties"]["kind"] == "burning"]
    assert len(burning) == j["summary"]["burning_cells"] == 3 * 3 + 1
    f = burning[0]
    assert f["geometry"]["type"] == "Polygon" and len(f["geometry"]["coordinates"][0]) == 5
    assert {"frp_viirs_mw", "frp_modis_harmonized_mw", "frp_equiv_mw", "detections_viirs"} <= set(f["properties"])
    modis = [b for b in burning if b["properties"]["detections_modis"] == 1]
    assert len(modis) == 1 and modis[0]["properties"]["frp_modis_harmonized_mw"] == 20.0


def test_fires_bbox_limit_gaps(loaded):
    box = f"{FIRE_LON - 0.01},{FIRE_LAT - 0.2},{FIRE_LON + 0.06},{FIRE_LAT + 0.2}"
    j = loaded.get(f"/fires?date={FIRE_DATE}&bbox={box}").json()
    assert 0 < j["summary"]["burning_cells"] < 10
    j = loaded.get(f"/fires?date={FIRE_DATE}&limit=2").json()
    assert j["summary"]["truncated"] and len(j["features"]) == 2
    # day after the last front: everything from the day before is a "gap"
    last = (dt.date.fromisoformat(FIRE_DATE) + dt.timedelta(days=4)).isoformat()
    j = loaded.get(f"/fires?date={last}&gaps=true").json()
    assert j["summary"]["burning_cells"] == 0 and j["summary"]["gap_cells"] == 6 * 3


def test_fires_out_of_range(loaded):
    assert loaded.get("/fires?date=1999-01-01").status_code == 404


def test_anomaly_routine(loaded):
    d = dt.date.fromisocalendar(2021, 10, 3).isoformat()
    j = loaded.get(f"/anomaly?lat={ROUTINE[0]}&lon={ROUTINE[1]}&date={d}").json()
    assert j["iso_week"] == 10 and j["observed"] == 5 and j["baseline_mean"] == 5
    assert j["label"] == "routine seasonal burning" and j["seasonal_frequency"] == 1.0


def test_anomaly_outbreak(loaded):
    d = dt.date.fromisocalendar(2021, 30, 2).isoformat()
    j = loaded.get(f"/anomaly?lat={QUIET[0]}&lon={QUIET[1]}&date={d}").json()
    assert j["observed"] == 30 and j["baseline_mean"] == 0 and j["z"] > 2 and j["label"] == "anomaly"


def test_anomaly_no_activity_and_no_data(loaded):
    j = loaded.get(f"/anomaly?lat={QUIET[0]}&lon={QUIET[1]}&date=2019-03-05").json()
    assert j["label"] == "no fire activity" and j["z"] <= 0
    j = loaded.get(f"/anomaly?lat={QUIET[0]}&lon={QUIET[1]}&date=2026-07-01").json()
    assert j["observed"] is None and j["label"] == "no data for this week"


@pytest.fixture()
def with_model(loaded):
    if not HAS_MODEL:
        pytest.skip("models/spread_direct.json missing")
    if appmod.store.model is None:
        appmod.store.load_model()
    appmod.forecaster._cache.clear()
    return loaded


def test_forecast_geojson(with_model):
    r = with_model.get(f"/forecast?lat={FIRE_LAT}&lon={FIRE_LON}&date={FIRE_DATE}&days=3")
    assert r.status_code == 200, r.text
    j = r.json()
    kinds = {f["properties"]["kind"] for f in j["features"]}
    assert "burning" in kinds and "observed" in kinds
    assert len(j["summary"]["per_day"]) == 3 and j["summary"]["historical"]
    for f in j["features"]:
        p = f["properties"]
        if p["kind"] == "predicted":
            assert 1 <= p["day"] <= 3 and 0 <= p["probability"] <= 1
    assert all(d["iou"] is not None for d in j["summary"]["per_day"])


def test_forecast_snaps_and_404(with_model):
    near = with_model.get(f"/forecast?lat={FIRE_LAT + 0.2}&lon={FIRE_LON}&date={FIRE_DATE}&days=1")
    assert near.status_code == 200
    far = with_model.get(f"/forecast?lat={FIRE_LAT + 5}&lon={FIRE_LON}&date={FIRE_DATE}&days=1")
    assert far.status_code == 404


def test_forecast_cap(with_model):
    r = with_model.get(f"/forecast.cap?lat={FIRE_LAT}&lon={FIRE_LON}&date={FIRE_DATE}&days=3")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/cap+xml")
    ns = {"c": "urn:oasis:names:tc:emergency:cap:1.2"}
    root = ET.fromstring(r.content)
    assert root.tag == "{urn:oasis:names:tc:emergency:cap:1.2}alert"
    assert root.findtext("c:status", namespaces=ns) == "Exercise"
    info = root.find("c:info", ns)
    assert info.findtext("c:category", namespaces=ns) == "Fire"
    assert info.findtext("c:severity", namespaces=ns) in {"Minor", "Moderate", "Severe", "Extreme"}
    polys = [p.text for p in info.iterfind("c:area/c:polygon", ns)]
    assert polys
    for p in polys:
        pts = p.split()
        assert len(pts) >= 4 and pts[0] == pts[-1]
        lat, lon = map(float, pts[0].split(","))
        assert abs(lat - FIRE_LAT) < 2 and abs(lon - FIRE_LON) < 2


# ------------------------------------------------------------------ real data (slow)
@pytest.mark.skipif(os.environ.get("EQUIFIRE_SLOW") != "1", reason="set EQUIFIRE_SLOW=1 to load the real dataset")
def test_real_data_load():
    from backend.data import build_daily
    st = State.from_daily(build_daily())
    assert st.day_max > st.day_min and st.viirs_rows > 0 and len(st.weekly) > 0
    assert len(st.fire_index.day) == st.viirs_rows
    assert np.all(np.diff(st.daily["day"].values) >= 0)
