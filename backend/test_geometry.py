"""Fast tests for goal 2 (view geometry + adaptive clustering). No dataset load.

    EQUIFIRE_NO_LOAD=1 .venv/bin/python -m pytest backend/test_geometry.py -q
"""
from __future__ import annotations

import datetime as dt
import os

os.environ.setdefault("EQUIFIRE_NO_LOAD", "1")

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend import app as appmod  # noqa: E402
from backend import geometry_api  # noqa: E402
from equifire import clusters as C  # noqa: E402
from equifire import geometry as G  # noqa: E402

client = TestClient(appmod.app)


# ------------------------------------------------------------------ geometry model
def test_modis_footprint_growth_matches_spec():
    s, t = G.pixel_size_model(G.SATS["aqua"], np.array([0.0, 55.0]))
    assert s[0] == pytest.approx(1.0, abs=1e-6) and t[0] == pytest.approx(1.0, abs=1e-6)
    assert s[1] == pytest.approx(4.8, abs=0.1) and t[1] == pytest.approx(2.0, abs=0.05)


def test_viirs_edge_and_inversion_roundtrip():
    s, t = G.pixel_size_model(G.SATS["snpp"], np.array([56.0]))
    assert 0.65 < s[0] < 0.85 and 0.7 < t[0] < 0.85
    for th in (5.0, 25.0, 40.0, 52.0):
        _, tt = G.pixel_size_model(G.SATS["noaa20"], th)
        assert G.scan_angle_from_pixel(G.SATS["noaa20"], None, tt) == pytest.approx(th, abs=0.05)
        ss, _ = G.pixel_size_model(G.SATS["terra"], th)
        assert G.scan_angle_from_pixel(G.SATS["terra"], ss) == pytest.approx(th, abs=0.05)


def test_view_zenith_exceeds_scan_angle():
    assert G.view_zenith(0, 705) == 0
    assert 64 < G.view_zenith(55, 705) < 67


def test_heading_model_polar_orbit():
    asc = float(G.heading_model(0.0, G.SATS["aqua"], True))
    desc = float(G.heading_model(0.0, G.SATS["terra"], False))
    assert 340 < asc < 360 and 180 < desc < 200


def test_archive_detection_uses_pixel_size():
    g = G.detection_geometry(-13.27, -45.56, dt.datetime(2023, 1, 1, 1, 28), "Terra", 4.8, 2.0, "N")
    assert g["geom_source"] == "pixel_size"
    assert g["scan_angle_deg"] > 53 and g["footprint"]["along_scan_km"] == 4.8


def test_nrt_detection_uses_sgp4():
    sat = G.SATS["aqua"]
    ep = G.tle_epoch(sat)
    if ep is None:
        pytest.skip("no Aqua TLE")
    # find any point inside the swath: the sub-satellite point at the TLE epoch, shifted ~300 km cross-track
    from backend import orbits
    t = ep.replace(microsecond=0)
    p = orbits.passes(0.0, 0.0, t, 24)
    aq = [x for x in p if x["key"] == "aqua"][0]["passes"]
    if not aq:
        pytest.skip("no pass")
    best = dt.datetime.fromisoformat(aq[0]["best"])
    g = G.detection_geometry(0.0, 0.0, best - dt.timedelta(minutes=1), "Aqua")
    assert g["geom_source"] == "sgp4"
    assert g["scan_angle_deg"] == pytest.approx(aq[0]["min_scan_deg"], abs=1.0)
    assert 650 < g["altitude_km"] < 750 and g["slant_range_km"] >= g["altitude_km"] - 1


# ------------------------------------------------------------------ clustering
def _edge_fire(scan_km=4.8, track_km=2.0, sat="Aqua", lat0=-10.0, lon0=-55.0):
    """One fire seen at the swath edge: 3 pixels along-scan (E-W) + a bow-tie duplicate along-track."""
    kx = C.KM_PER_DEG * np.cos(np.radians(lat0))
    pts = [(0, 0), (scan_km, 0), (2 * scan_km, 0), (0.0, 1.0)]       # (east km, north km)
    return pd.DataFrame({
        "latitude": [lat0 + n / C.KM_PER_DEG for _, n in pts],
        "longitude": [lon0 + e / kx for e, _ in pts],
        "scan": scan_km, "track": track_km, "acq_date": "2023-09-05", "acq_time": 1705,
        "satellite": sat, "frp": 10.0, "daynight": "D"})


def test_adaptive_merges_edge_pixels_fixed_does_not():
    d = G.add_geometry(_edge_fire())
    assert C.cluster(d, "adaptive").max() + 1 == 1
    assert C.cluster(d, "fixed_nadir").max() + 1 >= 3


def test_adaptive_keeps_separate_nadir_fires():
    d = G.add_geometry(_edge_fire(scan_km=1.0, track_km=1.0))
    d.loc[3, "latitude"] += 5 / C.KM_PER_DEG                          # 5 km away: a different fire
    assert C.cluster(d, "adaptive").max() + 1 == 2


def test_overpasses_are_not_merged():
    a = _edge_fire()
    b = _edge_fire().assign(acq_time=1905)                            # next orbit, same place
    d = G.add_geometry(pd.concat([a, b], ignore_index=True))
    assert C.cluster(d, "adaptive").max() + 1 == 2


# ------------------------------------------------------------------ API
def test_geometry_endpoint_archive():
    r = client.get("/geometry", params={"lat": -10, "lon": -55, "time": "2023-09-05T17:05:00Z",
                                        "sat": "Aqua", "scan": 2.4, "track": 1.5, "daynight": "D"})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["geom_source"] == "pixel_size" and 40 < j["scan_angle_deg"] < 50
    assert {"view_zenith_deg", "slant_range_km", "footprint"} <= set(j)


def test_geometry_endpoint_errors():
    assert client.get("/geometry", params={"lat": 0, "lon": 0, "time": "x", "sat": "Aqua"}).status_code == 400
    assert client.get("/geometry", params={"lat": 0, "lon": 0, "time": "2023-01-01T00:00", "sat": "Landsat"}).status_code == 400


def test_clusters_endpoint(tmp_path, monkeypatch):
    root = tmp_path / "data_country"
    (root / "modis" / "2023").mkdir(parents=True)
    df = _edge_fire().assign(type=0, brightness=330.0)
    df.to_csv(root / "modis" / "2023" / "modis_2023_Testland.csv", index=False)
    monkeypatch.setattr(geometry_api, "_data_root", lambda: root)
    monkeypatch.setattr(geometry_api, "CACHE", tmp_path / "cache")
    geometry_api._bbox_mem.clear()
    r = client.get("/clusters", params={"date": "2023-09-05", "bbox": "-56,-11,-54,-9"})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["n_detections"] == 4 and j["n_clusters"] == 1 and j["n_clusters_fixed_nadir"] >= 3
    p = j["features"][0]["properties"]
    assert p["n_detections"] == 4 and p["scan_angle_deg"] > 50 and p["radius_km"] > 4
    r = client.get("/clusters", params={"date": "2023-09-06", "bbox": "-56,-11,-54,-9", "shape": "polygon"})
    assert r.status_code == 200 and r.json()["n_clusters"] == 0
    assert client.get("/clusters", params={"date": "2023-09-05", "bbox": "-80,-40,-30,10"}).status_code == 400
    geometry_api._bbox_mem.clear()
    geometry_api._files.clear()
