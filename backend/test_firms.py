"""Tests for FIRMS live endpoints and area validation."""
from __future__ import annotations

import os
os.environ.setdefault("EQUIFIRE_NO_LOAD", "1")

from unittest.mock import patch
from fastapi.testclient import TestClient
import pytest

from backend import app as appmod
from backend import firms


SAMPLE_CSV = """latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,instrument,confidence,version,bright_ti5,frp,daynight
-6.57332,-51.10035,301.21,0.5,0.66,2026-11-14,408,N20,VIIRS,n,2.0NRT,282.21,4.96,N
-6.57400,-51.10100,315.40,0.5,0.66,2026-11-14,930,N20,VIIRS,n,2.0NRT,285.50,8.20,D
-6.86081,-48.85042,304.12,0.35,0.56,2026-11-14,408,N20,VIIRS,n,2.0NRT,285.86,1.89,N
"""


@pytest.fixture
def client():
    return TestClient(appmod.app)


def test_area_calculation():
    # 0.1 x 0.1 degree at equator is approx 123 km²
    a = firms.geodesic_area_km2(0.0, 0.0, 0.1, 0.1)
    assert 120 < a < 125

    # 1.0 x 1.0 degree at equator is approx 12,363 km² (within the 25,000 km² limit)
    big_a = firms.geodesic_area_km2(0.0, 0.0, 1.0, 1.0)
    assert big_a > 12000


def test_parse_fires_last_seen():
    spots = firms.parse_fires_last_seen(SAMPLE_CSV, spot_grid_deg=0.01)
    assert len(spots) == 2
    # The spot near (-6.57, -51.1) had 2 detections, latest at 09:30 with FRP 8.20
    s1 = next(s for s in spots if abs(s["lat"] - (-6.57)) < 0.05)
    assert s1["last_seen_time"] == "2026-11-14T09:30:00+00:00"
    assert s1["detections_24h"] == 2
    assert s1["max_frp_24h"] == 8.2
    assert s1["daynight"] == "Day"


def test_firms_last24h_area_limit(client):
    # A huge bbox exceeding 25,000 km² (e.g. 5x5 degrees) should return 400
    r = client.get("/firms/last24h?bbox=-55,-15,-50,-10")
    assert r.status_code == 400
    assert "exceeding the maximum limit of 25000 km²" in r.json()["detail"]


def test_firms_last24h_mocked(client):
    # Small bbox (~122 km²)
    small_box = "-51.15,-6.65,-51.05,-6.55"
    with patch("backend.firms.fetch_firms_csv", return_value=SAMPLE_CSV):
        r = client.get(f"/firms/last24h?bbox={small_box}")
        assert r.status_code == 200
        data = r.json()
        assert data["type"] == "FeatureCollection"
        assert len(data["features"]) == 2
        summary = data["summary"]
        assert summary["total_spots"] == 2
        assert summary["area_km2"] <= 200.0
        assert summary["is_live_24h"] is True


def test_firms_date_mocked(client):
    small_box = "-51.15,-6.65,-51.05,-6.55"
    with patch("backend.firms.fetch_firms_csv", return_value=SAMPLE_CSV):
        r = client.get(f"/firms/date?date=2026-11-14&bbox={small_box}")
        assert r.status_code == 200
        data = r.json()
        assert data["summary"]["date"] == "2026-11-14"
