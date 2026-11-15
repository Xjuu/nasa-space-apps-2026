"""Tests for GET/POST /stats on the synthetic data of test_app.py.

    .venv/bin/python -m pytest backend/test_stats.py -q
"""
from __future__ import annotations

import os

os.environ.setdefault("EQUIFIRE_NO_LOAD", "1")

import pytest  # noqa: E402

from backend.test_app import FIRE_DATE, FIRE_LAT, FIRE_LON, QUIET, ROUTINE, client, loaded  # noqa: E402,F401


def _box(lat, lon, h=0.5):
    return f"{lon - h},{lat - h},{lon + h},{lat + h}"


def test_stats_routine(loaded):
    r = loaded.get(f"/stats?bbox={_box(*ROUTINE)}&year=2021")
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["years"][0] == 2013 and j["years"][-1] == 2021
    i = j["years"].index(2021)
    assert j["calendar"]["viirs_cell_days"][i][9] == 5          # ISO week 10
    assert j["calendar"]["modis_cell_days"][i][9] is None        # no MODIS in synthetic range
    w10 = j["selected"]["weeks"][9]
    assert w10["value"] == 5 and w10["mean"] == 5 and w10["flag"] is None
    k = j["selected"]["key"]
    assert k["value"] == 5 and k["label"].startswith("routine") and k["pct_vs_mean"] == 0
    assert [y["viirs_cell_days"] for y in j["per_year"]] == [5.0] * 9


def test_stats_outbreak_latlon(loaded):
    j = loaded.get(f"/stats?lat={QUIET[0]}&lon={QUIET[1]}&size_deg=1&year=2021").json()
    w = j["selected"]["weeks"][29]
    assert w["value"] == 30 and w["flag"] == "high" and w["z"] > 2
    assert j["selected"]["key"]["anomalous_weeks"] == [30]
    assert j["selected"]["key"]["label"].startswith("anomaly")


def test_stats_polygon_and_modis(loaded):
    # triangle around the fire front; one MODIS-only cell at (-3, +3) cells is inside the bbox
    poly = {"type": "Polygon", "coordinates": [[[FIRE_LON - 0.5, FIRE_LAT - 0.5], [FIRE_LON + 0.5, FIRE_LAT - 0.5],
                                                [FIRE_LON + 0.5, FIRE_LAT + 0.5], [FIRE_LON - 0.5, FIRE_LAT - 0.5]]]}
    full = loaded.get(f"/stats?bbox={_box(FIRE_LAT, FIRE_LON)}&year=2020").json()
    tri = loaded.post("/stats", json={"geometry": poly, "year": 2020}).json()
    assert tri["aoi"]["polygon"] and 0 < tri["aoi"]["cells_0p05deg"] < full["aoi"]["cells_0p05deg"]
    y = full["years"].index(2020)
    assert full["per_year"][y]["modis_cell_days"] == 1 and full["per_year"][y - 1]["modis_cell_days"] is None
    assert sum(v or 0 for v in full["calendar"]["any_cell_days"][y]) > sum(
        v or 0 for v in tri["calendar"]["any_cell_days"][y]) > 0


@pytest.mark.parametrize("url", ["/stats", "/stats?bbox=0,0,50,50", "/stats?bbox=0,0,1,1&metric=nope",
                                 "/stats?bbox=0,0,1,1&year=1990"])
def test_stats_bad(loaded, url):
    assert loaded.get(url).status_code == 400


def test_stats_post_bad(loaded):
    assert loaded.post("/stats", json={"geometry": {"type": "Point", "coordinates": [0, 0]}}).status_code == 400
    assert loaded.post("/stats", json={}).status_code == 400
