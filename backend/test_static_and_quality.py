"""Unit tests for the Static Thermal Sources Mask (#13) and Observation Quality Index (#15).

    .venv/bin/python -m pytest backend/test_static_and_quality.py -v
"""
import unittest
from fastapi.testclient import TestClient

from backend import app as backend_app
from backend import static_and_quality as sq


class StaticSourcesTests(unittest.TestCase):
    def test_catalog_integrity(self):
        catalog = sq.STATIC_CATALOG
        self.assertGreater(len(catalog), 10)
        for s in catalog:
            self.assertIn("lat", s)
            self.assertIn("lon", s)
            self.assertIn("radius_km", s)
            self.assertIn(s["type"], ["gas_flare", "industrial_metallurgy", "petrochemical_refinery", "volcanic_thermal"])

    def test_screen_static_sources_near_known_facility(self):
        # Flin Flon smelter is at ~54.76, -101.88
        res = sq.screen_static_sources(lat=54.76, lon=-101.88, radius_km=10.0)
        self.assertEqual(res["status"], "success")
        self.assertTrue(res["static_zone_assessment"]["in_known_static_buffer"])
        closest = res["static_zone_assessment"]["closest_source"]
        self.assertIsNotNone(closest)
        self.assertIn("flin flon", closest["name"].lower())
        self.assertEqual(closest["type"], "industrial_metallurgy")

    def test_screen_static_sources_remote_wilderness(self):
        # Remote Canadian shield far from any industrial site or flare
        res = sq.screen_static_sources(lat=58.5, lon=-105.0, radius_km=15.0)
        self.assertEqual(res["status"], "success")
        self.assertFalse(res["static_zone_assessment"]["in_known_static_buffer"])

    def test_detection_filtering_and_false_alarm_reduction(self):
        # 3 hotspots: 2 directly inside Permian Basin flare buffer, 1 outside in wildland
        mock_hotspots = [
            {"lat": 31.95, "lon": -102.15, "frp": 120.0},  # Directly on flare center
            {"lat": 31.96, "lon": -102.16, "frp": 85.0},   # 1 km from flare
            {"lat": 32.50, "lon": -101.50, "frp": 45.0},   # 80 km away (wildfire)
        ]
        res = sq.screen_static_sources(lat=31.95, lon=-102.15, radius_km=40.0, detections=mock_hotspots)
        screening = res["screening_results"]
        self.assertEqual(screening["total_detections"], 3)
        self.assertEqual(screening["flagged_static_count"], 2)
        self.assertEqual(screening["unflagged_candidates_count"], 1)
        self.assertEqual(screening["verified_wildfire_candidates_count"], 1)
        self.assertAlmostEqual(screening["static_source_flag_rate_pct"], 66.7, places=1)
        self.assertAlmostEqual(screening["false_alarm_reduction_pct"], 66.7, places=1)
        self.assertIn("metric_interpretation", res["disclaimer_and_notes"])

    def test_zero_coordinate_handling(self):
        # Detection at Null Island (0.0, 0.0) evaluated near Flin Flon (54.76, -101.88)
        # Must retain (0.0, 0.0) and NOT fallback to center coordinates or falsely flag
        detections = [{"latitude": 0.0, "longitude": 0.0, "frp": 50.0}]
        res = sq.screen_static_sources(lat=54.76, lon=-101.88, radius_km=10.0, detections=detections)
        screening = res["screening_results"]
        self.assertEqual(screening["flagged_static_count"], 0)
        self.assertEqual(screening["unflagged_candidates_count"], 1)
        self.assertEqual(screening["sample_wildfire_candidates"][0]["lat"], 0.0)
        self.assertEqual(screening["sample_wildfire_candidates"][0]["lon"], 0.0)


class ObservationQualityIndexTests(unittest.TestCase):
    def test_optimal_quality_nadir_clear_night_viirs(self):
        res = sq.compute_observation_quality(
            lat=54.0,
            lon=-100.0,
            scan_angle_deg=5.0,
            cloud_cover_pct=0.0,
            instrument="VIIRS",
            is_day=False,
        )
        self.assertEqual(res["status"], "success")
        self.assertGreaterEqual(res["observation_quality_index"], 0.85)
        self.assertEqual(res["quality_tier"], "optimal")
        self.assertTrue(res["cloud_accounted"])

    def test_compromised_quality_edge_overcast_modis_day(self):
        res = sq.compute_observation_quality(
            lat=54.0,
            lon=-100.0,
            scan_angle_deg=55.0,
            cloud_cover_pct=95.0,
            instrument="MODIS",
            is_day=True,
            solar_zenith_deg=20.0,  # High sun glint risk
        )
        self.assertEqual(res["status"], "success")
        self.assertLess(res["observation_quality_index"], 0.40)
        self.assertEqual(res["quality_tier"], "compromised")

    def test_missing_cloud_data_handling(self):
        # Even with nadir scan (0 deg) and night pass (optimal geometry),
        # missing cloud data must cap quality tier at 'good' (never 'optimal')
        res = sq.compute_observation_quality(
            lat=54.0,
            lon=-100.0,
            scan_angle_deg=0.0,
            cloud_cover_pct=None,
            instrument="VIIRS",
            is_day=False,
        )
        self.assertEqual(res["status"], "success")
        self.assertFalse(res["cloud_accounted"])
        self.assertIsNone(res["sub_scores"]["cloud_clarity_score"])
        self.assertIsNotNone(res["observation_quality_index"])
        self.assertEqual(res["quality_tier"], "good")
        self.assertIn("weryfikowan", res["tier_description_pl"].lower())
        self.assertIn("unverified", res["tier_description"].lower())

    def test_terminology_compliance(self):
        res = sq.compute_observation_quality(lat=54.0, lon=-100.0)
        disclaimer = res["terminology_disclaimer"]
        self.assertIn("wskaźnik jakości obserwacji", disclaimer["term_used"].lower())
        self.assertIn("nie jest", disclaimer["warning"].lower())


class ExtraSQAPITests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(backend_app.app)

    def test_api_static_sources_get(self):
        resp = self.client.get("/extra-sq/static-sources?lat=30.52&lon=47.45&radius_km=15")
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertEqual(data["status"], "success")
        self.assertTrue(data["static_zone_assessment"]["in_known_static_buffer"])

    def test_api_quality_index_get(self):
        resp = self.client.get("/extra-sq/quality-index?lat=54.975&lon=-101.725&scan_angle_deg=10&cloud_cover_pct=15")
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertEqual(data["status"], "success")
        self.assertIn("observation_quality_index", data)
        self.assertIn("sub_scores", data)
