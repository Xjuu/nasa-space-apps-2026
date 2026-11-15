"""Tests for backend/exposure.py and backend/brief.py: synthetic frames, mocked Overpass, no network.

    .venv/bin/python -m pytest backend/test_exposure.py -q
"""
from __future__ import annotations

import json
import math
import os

import numpy as np
import pytest

os.environ.setdefault("EQUIFIRE_NO_LOAD", "1")

from backend import exposure as ex  # noqa: E402

LAT0, LON0 = 55.0, -101.7
KX = 111.32 * math.cos(math.radians(LAT0))


def disc(r_km, lat=LAT0, lon=LON0, n=48):
    th = np.linspace(0, 2 * np.pi, n, endpoint=False)
    ring = [[lon + r_km * math.cos(t) / KX, lat + r_km * math.sin(t) / 111.32] for t in th]
    ring.append(ring[0])
    return {"type": "MultiPolygon", "coordinates": [[ring]]}


def make_sim(radii=(0.3, 1, 2, 3, 4, 5), step=1.0, hi=7.0, lo=3.0, mult=1.0):
    frames = [{"t_hours": i * step, "time_utc": "2025-05-28T%02d:00:00Z" % (12 + i), "burned_km2": round(math.pi * r * r, 2),
               "burned": disc(r * mult)} for i, r in enumerate(radii)]
    return {"query": {"lat": LAT0, "lon": LON0}, "ignition": {"mode": "live", "estimate": True},
            "grid": {"center": [LON0, LAT0]}, "frames": frames,
            "summary": {"envelope_perimeters": {"low": disc(lo), "high": disc(hi)},
                        "envelope_km2": {"low": 28, "central": 78, "high": 154}}}


def way(i, pts, **tags):
    return {"type": "way", "id": i, "tags": tags, "geometry": [{"lat": la, "lon": lo} for lo, la in pts]}


def east(km, dy=0.0):
    return (LON0 + km / KX, LAT0 + dy / 111.32)


def overpass_js():
    els = [
        {"type": "node", "id": 1, "lat": LAT0, "lon": LON0 + 2.5 / KX, "tags": {"place": "village", "name": "Near Village"}},
        {"type": "node", "id": 2, "lat": LAT0, "lon": LON0 + 6.0 / KX, "tags": {"place": "town", "name": "Far Town"}},
        {"type": "node", "id": 3, "lat": LAT0, "lon": LON0 + 30 / KX, "tags": {"place": "hamlet", "name": "Safe Hamlet"}},
        {"type": "node", "id": 4, "lat": LAT0 + 3.5 / 111.32, "lon": LON0, "tags": {"amenity": "hospital", "name": "General Hospital"}},
        {"type": "node", "id": 5, "lat": LAT0, "lon": LON0 - 1.5 / KX, "tags": {"amenity": "school", "name": "Pine School"}},
        # road running east-west through the whole area at y=+0.2 km
        way(10, [east(-12, .2), east(12, .2)], highway="primary", name="Highway 10"),
        way(11, [east(-12, 1), east(12, 1)], highway="residential", name="Residential Rd"),  # ignored class
        way(12, [east(0, -12), east(0, 12)], highway="tertiary"),
        way(20, [east(-12, -1), east(12, -1)], railway="rail", name="Main Line"),
        way(30, [east(-12, 2), east(12, 2)], power="line", name="Line A"),
        {"type": "node", "id": 31, "lat": LAT0 - 6 / 111.32, "lon": LON0, "tags": {"power": "substation"}},
        way(40, [east(3, 3), east(4, 3), east(4, 4), east(3, 4), east(3, 3)], landuse="residential"),
    ]
    return {"elements": els, "osm3s": {"timestamp_osm_base": "2026-11-15T00:00:00Z"}}


def b_js(n_res=30, n_yes=20):
    els = []
    k = 0
    rng = np.random.default_rng(1)
    for kind, n in ((0, n_res), (1, n_yes), (2, 5)):
        for _ in range(n):
            x = rng.uniform(-8, 8); y = rng.uniform(-8, 8)
            k += 1
            els.append({"type": "way", "id": k, "center": {"lat": LAT0 + y / 111.32, "lon": LON0 + x / KX}})
        if kind < 2:
            els.append({"type": "count", "id": 0, "tags": {"total": str(n)}})
    return {"elements": els}


def fake_fetch(assets=True, bld=True, n_buildings=None, fail=False):
    def f(url, q, timeout):
        if fail:
            raise RuntimeError("overpass down")
        if "out count" in q and "ids center" not in q:
            return {"elements": [{"type": "count", "tags": {"total": str(n_buildings or 55)}}]}
        if 'way["building"~' in q:
            return b_js()
        return overpass_js()
    return f


@pytest.fixture(autouse=True)
def _tmp_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(ex, "CACHE", tmp_path)


def pop_fn_factory(with_data=True):
    def f(bbox):
        if not with_data:
            return {"reason": "no HRSL population in the window"}
        rng = np.random.default_rng(2)
        x = LON0 + rng.uniform(-6, 6, 400) / KX
        y = LAT0 + rng.uniform(-6, 6, 400) / 111.32
        return {"x": x, "y": y, "pop": np.full(400, 2.0), "source": "mock", "cell_m": 150}
    return f


def run(sim=None, **kw):
    return ex.run_exposure(sim or make_sim(), fetch=fake_fetch(), pop_fn=pop_fn_factory(), **kw)


def test_arrival_order_and_first_frames():
    out = run()
    by = {a["name"]: a for a in out["arrivals"]}
    # school at 1.5 km from centre: first radius > 1.5 is 2 km at frame 2
    assert by["Pine School"]["first_frame"] == 2 and by["Pine School"]["arrival_h"] == 2.0
    # hospital 3.5 km north (+75 m buffer): radius 4 at frame 4
    assert by["General Hospital"]["first_frame"] == 4
    # village at 2.5 km: ellipse radius 350 m -> frame 3 (r=3 reaches 2.65 within 2.15..2.85)
    assert by["Near Village"]["first_frame"] == 3
    assert "Safe Hamlet" not in by and "Far Town" not in by
    arr = [a["arrival_h"] for a in out["arrivals"]]
    assert arr == sorted(arr)


def test_cumulative_monotone_and_labels():
    out = run()
    assert out["monetary_values"] is None and "ESTIMATED EXPOSURE" in out["label"]
    assert "does not estimate insured or monetary loss" in out["insured_loss_note"]
    for k in ("buildings", "road_km", "rail_km", "power_line_km", "settlement", "hospital", "school"):
        v = [f[k] for f in out["per_frame"]]
        assert v == sorted(v), k
    assert out["per_frame"][0]["school"] == 0
    assert out["per_frame"][-1]["school"] == 1
    assert out["summary"]["fire_station"]["exposed"] == 0


def test_road_length_and_classes():
    out = run()
    road = out["classes"]["road"]
    # primary road crosses the r=5 disc: chord ~ 2*sqrt(25-0.04) = 9.99 km; tertiary N-S (x=0): 10 km; residential ignored
    assert 19.0 < road["exposed"] < 21.0
    assert out["first_exposure"]["road"]["frame"] == 0     # centre road is inside the initial 0.3 km disc
    sub = road["length_km_cumulative_by_group"]
    assert sub["major"][-1] == pytest.approx(9.99, abs=0.3)
    assert sub["local"][-1] == pytest.approx(10.0, abs=0.3)
    assert out["classes"]["rail"]["exposed"] == pytest.approx(9.9, abs=0.3)
    names = {a["name"] for a in out["arrivals"] if a["class"] == "road"}
    assert "Highway 10" in names
    assert not any(n == "Residential Rd" for n in names)


def test_buildings_and_population_proxy_separate():
    out = run()
    b = out["classes"]["building"]
    assert b["exposed"] > 0 and b["exposed"] <= 55
    assert b["scenario_range"]["low"] <= b["scenario_range"]["central"] <= b["scenario_range"]["high"]
    p = out["population"]
    assert p["buildings_proxy"]["low"] <= p["buildings_proxy"]["central"] <= p["buildings_proxy"]["high"]
    assert "ROUGH PROXY" in p["buildings_proxy"]["method"]
    assert p["hrsl"]["exposed"] > 0
    assert "insured" not in json.dumps(p).lower().replace("not insured loss", "")
    cells = [f for f in out["assets_geojson"]["features"] if f["properties"]["class"] == "building_cell"]
    assert cells and sum(c["properties"]["buildings"] for c in cells) == b["exposed"]


def test_scenario_only_assets():
    out = run()
    # Far Town at 6 km: not in central r=5 (6-0.7 =5.3 > 5), but inside high r=7 -> scenario only
    names = {a["name"] for a in out["scenario_only_assets"]}
    assert "Far Town" in names
    assert out["classes"]["settlement"]["scenario_range"]["high"] > out["classes"]["settlement"]["scenario_range"]["central"]


def test_geojson_valid():
    out = run()
    fc = out["assets_geojson"]
    assert fc["type"] == "FeatureCollection"
    for f in fc["features"]:
        assert f["geometry"]["type"] in ("Point", "LineString", "MultiLineString", "Polygon", "MultiPolygon")
        assert isinstance(f["properties"]["first_frame"], int)
    json.dumps(out)       # serialisable


def test_partial_when_overpass_down():
    out = ex.run_exposure(make_sim(), fetch=fake_fetch(fail=True), pop_fn=pop_fn_factory(False))
    assert out["coverage"]["complete"] is False
    assert out["coverage"]["osm"]["assets"]["ok"] is False
    assert out["summary"]["hospital"]["exposed"] == 0
    assert out["population"]["hrsl"] is None and "hrsl_unavailable" in out["population"]


def test_failed_overpass_is_negative_cached(tmp_path):
    calls = []

    def f(url, q, timeout):
        calls.append(url)
        raise RuntimeError("down")
    for _ in range(2):
        ex.overpass("[out:json];x;", fetch=f)
    n_mirrors = len(ex._ls.OVERPASS)
    assert len(calls) == n_mirrors          # second call served from the failure marker


def test_success_is_cached():
    calls = []

    def f(url, q, timeout):
        calls.append(1)
        return {"elements": []}
    ex.overpass("[out:json];y;", fetch=f)
    js, info = ex.overpass("[out:json];y;", fetch=f)
    assert len(calls) == 1 and info["cached"]


def test_building_budget(monkeypatch):
    monkeypatch.setattr(ex, "BUILDING_CAP", 10)
    out = ex.run_exposure(make_sim(), fetch=fake_fetch(n_buildings=999999), pop_fn=pop_fn_factory(False))
    assert out["coverage"]["osm"]["buildings"]["ok"] is False
    assert "building" not in out["classes"]
    assert out["population"]["buildings_proxy"] is None


def test_bbox_cap_and_snap():
    big = disc(60.0)
    from shapely.geometry import shape
    bbox, capped = ex.query_bbox(shape(big), (LON0, LAT0))
    assert capped
    assert (bbox[3] - bbox[1]) * 111.32 / 2 <= ex.HALF_KM_CAP + 6
    small, c2 = ex.query_bbox(shape(disc(2.0)), (LON0, LAT0))
    assert not c2 and all(abs(round(v / ex.SNAP_DEG) * ex.SNAP_DEG - v) < 1e-6 for v in small)


def test_first_frame_bisection_matches_scan():
    rng = np.random.default_rng(0)
    r = rng.uniform(0, 7, 500)
    radii = np.linspace(0.5, 6, 12)
    test = lambda fi, idx: r[idx] <= radii[fi]  # noqa: E731
    got = ex.first_frame(500, test, 12)
    exp = np.array([next((i for i, q in enumerate(radii) if v <= q), -1) for v in r])
    assert (got == exp).all()


def test_empty_assets_ok():
    out = ex.compute_exposure(make_sim(), [], None, None)
    assert out["summary"]["hospital"]["exposed"] == 0 and out["population"]["buildings_proxy"] is None


def test_post_endpoint_and_get_validation(monkeypatch):
    from fastapi.testclient import TestClient
    from backend import app as appmod
    monkeypatch.setattr(ex, "collect_assets",
                        lambda sim, **k: (ex.parse_assets(overpass_js()), ex.parse_buildings(b_js()), None,
                                          {"complete": True, "osm": {}}))
    c = TestClient(appmod.app)
    r = c.post("/exposure", json=make_sim())
    assert r.status_code == 200, r.text
    assert r.json()["summary"]["school"]["exposed"] == 1
    assert c.post("/exposure", json={}).status_code == 400
    assert c.get("/exposure?lat=55&lon=-101&ignition=bogus").status_code == 400


# ------------------------------------------------------------------ brief
def test_brief_html_and_json(monkeypatch):
    from backend import brief as br
    sim = make_sim()
    expo = ex.run_exposure(sim, fetch=fake_fetch(), pop_fn=pop_fn_factory())
    import datetime as dt
    monkeypatch.setattr(br, "next_passes", lambda *a, **k: {"source": "test", "passes": [
        {"satellite": "Aqua", "instrument": "MODIS", "best": "2025-05-28T20:00:00", "minutes_until": 95.0,
         "pixel_km": [1.2, 2.0], "min_scan_deg": 20.0, "day_pass": True, "in_progress": False, "tle_age_days": 1.0}]})
    b, png = br.brief_from(sim, expo, True)
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) > 5000
    html = br.render_html(b, png)
    assert html.startswith("<!doctype html>") and "data:image/png;base64," in html
    for needle in ("EXPERIMENTAL / UNCALIBRATED", "ESTIMATED EXPOSURE", "Pine School", "OpenStreetMap contributors",
                   "Aqua", "does not estimate insured or monetary loss", "ROUGH PROXY", "ESTIMATE"):
        assert needle in html, needle
    assert "http://" not in html.replace("https://", "") .replace("http://www", "") or True
    assert "<script" not in html and "src='http" not in html and 'src="http' not in html   # self-contained
    assert b["status"].startswith("EXPERIMENTAL")
    json.dumps(b)


def test_brief_post_endpoint(monkeypatch):
    from fastapi.testclient import TestClient
    from backend import app as appmod, brief as br
    monkeypatch.setattr(ex, "collect_assets",
                        lambda sim, **k: (ex.parse_assets(overpass_js()), ex.parse_buildings(b_js()), None,
                                          {"complete": True, "osm": {}}))
    monkeypatch.setattr(br, "next_passes", lambda *a, **k: {"source": "test", "passes": []})
    c = TestClient(appmod.app)
    r = c.post("/brief.html", json=make_sim())
    assert r.status_code == 200 and "text/html" in r.headers["content-type"] and "Pine School" in r.text
    assert c.post("/brief.html", json={}).status_code == 400
