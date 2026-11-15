"""Offline unit tests for the satellite overpass nowcast module.

    .venv/bin/python -m pytest backend/test_nowcast.py -v
"""
import datetime as dt
import unittest
import numpy as np
import pandas as pd
from fastapi.testclient import TestClient

from backend import app as backend_app
from backend import nowcast
from backend import physics_spread as ps

NOW = dt.datetime(2026, 11, 14, 12, 0, tzinfo=dt.timezone.utc)
START = pd.Timestamp("2026-11-14 12:00")


def make_mock_weather(hours=48):
    idx = pd.date_range(START - pd.Timedelta(hours=ps.SPINUP_H), START + pd.Timedelta(hours=hours), freq="h")
    return pd.DataFrame({
        "temp_c": 24.0,
        "rh": 35.0,
        "wind_kmh": 18.0,
        "wind_dir": 250.0,
        "gust_kmh": 26.0,
        "rain_mm": 0.0,
    }, index=idx)


def flat_elev(la, lo):
    return np.zeros_like(la)


class NowcastUnitTests(unittest.TestCase):
    def test_bearing_math(self):
        self.assertAlmostEqual(nowcast._bearing(0, 1), 0.0)     # North
        self.assertAlmostEqual(nowcast._bearing(1, 0), 90.0)    # East
        self.assertAlmostEqual(nowcast._bearing(0, -1), 180.0)  # South
        self.assertAlmostEqual(nowcast._bearing(-1, 0), 270.0)  # West

        self.assertEqual(nowcast._bearing_name(10), "N")
        self.assertEqual(nowcast._bearing_name(45), "NE")
        self.assertEqual(nowcast._bearing_name(90), "E")

    def test_build_nowcast_offline(self):
        wx = make_mock_weather(24)
        res = nowcast.build_nowcast(
            lat=-15.0,
            lon=130.0,
            start=NOW,
            fuel="savanna",
            wx=wx,
            elev_fn=flat_elev,
        )
        self.assertEqual(res["status"], "success")
        self.assertIn("target_overpass", res)
        self.assertIn("sensor_footprint", res)
        self.assertIn("projected_fire_state", res)
        self.assertIn("sensor_detection_forecast", res)
        self.assertIn("commander_briefing", res)

        # Check sensor footprint
        fp = res["sensor_footprint"]
        self.assertGreater(fp["along_scan_km"], 0.3)
        self.assertGreater(fp["along_track_km"], 0.3)
        self.assertGreater(fp["pixel_area_km2"], 0.1)

        # Check fire projection
        pfs = res["projected_fire_state"]
        self.assertGreater(pfs["front_advance_dist_m"], 0)
        self.assertIn(pfs["front_advance_compass"], ["N", "NE", "E", "SE", "S", "SW", "W", "NW"])

        # Check detection forecast
        det = res["sensor_detection_forecast"]
        self.assertGreaterEqual(det["expected_pixels"], 1)
        self.assertIn(det["detection_confidence"], ["high", "moderate", "low", "unknown"])
        self.assertIn("heuristic_estimated_frp_mw", det)

    def test_nextobs_schema_mapping_cloud_obscuration(self):
        """Verifies mapping of nextobs._pass_record schema with high cloud cover."""
        import unittest.mock as mock
        wx = make_mock_weather(24)

        mock_pass = {
            "satellite": "NOAA-20",
            "instrument": "VIIRS",
            "time_utc": "2026-11-14T14:30:00Z",
            "hours_from_ref": 2.5,
            "scan_angle_deg": 48.5,
            "quality_tier": "poor",
            "pixel_km": [0.75, 1.2],
            "cloud": {
                "cover_pct": 95,
                "low_pct": 80,
                "mid_pct": 60,
                "high_pct": 40,
                "visibility": "obscured",
                "source": "Open-Meteo hourly cloud_cover",
            },
            "usable": False,
        }

        with mock.patch("backend.nextobs.nextobs", return_value={"passes": [mock_pass], "simulate_until": {}}):
            res = nowcast.build_nowcast(lat=-15.0, lon=130.0, start=NOW, fuel="savanna", wx=wx, elev_fn=flat_elev)
            top = res["target_overpass"]
            self.assertEqual(top["cloud"]["cover_pct"], 95)
            self.assertEqual(top["cloud_cover_pct"], 95)
            self.assertEqual(top["cloud"]["visibility"], "obscured")
            self.assertEqual(top["quality_tier"], "poor")

            det = res["sensor_detection_forecast"]
            self.assertEqual(det["detection_confidence"], "low")
            self.assertLess(det["estimated_detection_probability"], 0.2)
            self.assertIn("obscuration", top["visibility_note"].lower())

    def test_unknown_cloud_and_nominal_fallback(self):
        """Verifies that missing cloud data or nominal fallback returns unknown confidence."""
        import unittest.mock as mock
        wx = make_mock_weather(24)

        mock_nominal = [{
            "satellite": "Aqua",
            "instrument": "MODIS",
            "best": dt.datetime(2026, 11, 14, 15, 0, tzinfo=dt.timezone.utc),
        }]

        with mock.patch("backend.nextobs.nextobs", side_effect=RuntimeError("Network offline")), \
             mock.patch("backend.nextobs._nominal_passes", return_value=mock_nominal):
            res = nowcast.build_nowcast(lat=-15.0, lon=130.0, start=NOW, fuel="savanna", wx=wx, elev_fn=flat_elev)
            self.assertTrue(res["provenance"]["is_fallback"])
            self.assertIn("nextobs_exception", res["provenance"]["fallback_reason"])

            top = res["target_overpass"]
            self.assertIsNone(top["cloud"]["cover_pct"])
            self.assertEqual(top["cloud"]["visibility"], "unknown")

            det = res["sensor_detection_forecast"]
            self.assertEqual(det["detection_confidence"], "unknown")
            self.assertIsNone(det["estimated_detection_probability"])


class NowcastAPITests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(backend_app.app)

    def test_api_routes_mounted(self):
        # Smoke test endpoint availability
        resp = self.client.get("/nowcast/pass?lat=-15.0&lon=130.0")
        self.assertNotEqual(resp.status_code, 404)

    def test_api_deterministic_mocked(self):
        """Deterministic test for GET and POST /nowcast/pass without network calls."""
        import unittest.mock as mock
        mock_wx = make_mock_weather(24)
        mock_pass = {
            "satellite": "Aqua",
            "instrument": "MODIS",
            "time_utc": "2026-11-14T14:00:00Z",
            "hours_from_ref": 2.0,
            "scan_angle_deg": 15.0,
            "quality_tier": "good",
            "pixel_km": [1.0, 1.0],
            "cloud": {
                "cover_pct": 10,
                "visibility": "clear",
                "source": "Open-Meteo",
            },
            "usable": True,
        }

        with mock.patch("backend.nextobs.nextobs", return_value={"passes": [mock_pass], "simulate_until": {}}), \
             mock.patch("backend.physics_spread.fetch_weather", return_value=(mock_wx, {"sources": [], "errors": []})), \
             mock.patch("backend.physics_spread.fetch_elevation", return_value=(np.zeros((15, 15)), {"flat": True, "errors": []})):

            # Test GET /nowcast/pass
            resp_get = self.client.get(f"/nowcast/pass?lat=-15.0&lon=130.0&start={START.isoformat()}")
            self.assertEqual(resp_get.status_code, 200, resp_get.text)
            data_get = resp_get.json()
            self.assertEqual(data_get["status"], "success")
            self.assertEqual(data_get["target_overpass"]["cloud_cover_pct"], 10)
            self.assertEqual(data_get["sensor_detection_forecast"]["detection_confidence"], "high")

            # Test POST /nowcast/pass
            payload = {
                "lat": -15.0,
                "lon": 130.0,
                "start": START.isoformat(),
                "fuel": "savanna",
            }
            resp_post = self.client.post("/nowcast/pass", json=payload)
            self.assertEqual(resp_post.status_code, 200, resp_post.text)
            data_post = resp_post.json()
            self.assertEqual(data_post["status"], "success")
            self.assertIn("heuristic_estimated_frp_mw", data_post["sensor_detection_forecast"])
