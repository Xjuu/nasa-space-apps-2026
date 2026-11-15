"""Unit tests for the Fire Priority Ranking module (#20).

    .venv/bin/python -m pytest backend/test_fire_priority.py -v
"""
import unittest
from fastapi.testclient import TestClient

from backend import app as backend_app
from backend import fire_priority as fp


class FirePriorityUnitTests(unittest.TestCase):
    def test_presets_exist(self):
        presets = fp.REGIONAL_PRESETS
        self.assertIn("western_canada_boreal", presets)
        self.assertIn("mediterranean_basin", presets)
        for pid, pdata in presets.items():
            self.assertGreaterEqual(len(pdata["incidents"]), 3)
            for inc in pdata["incidents"]:
                self.assertIn("dist_to_settlement_km", inc)
                self.assertIn("ros_m_min", inc)
                self.assertIn("frp_mw", inc)

    def test_calculate_incident_score_critical(self):
        # Extreme fire right at town boundary
        inc_critical = {
            "id": "crit_1",
            "name": "Town Border Wildfire",
            "dist_to_settlement_km": 1.2,
            "ros_m_min": 6.5,
            "frp_mw": 500.0,
            "area_ha": 3000.0,
            "rh_pct": 14.0,
            "wind_kmh": 40.0,
        }
        res = fp.calculate_incident_score(inc_critical)
        self.assertGreaterEqual(res["composite_score"], 75.0)
        self.assertEqual(res["priority_tier"], "P1")
        self.assertIn("KRYTYCZNY", res["priority_label"])
        self.assertGreater(res["sub_scores"]["exposure_score"], 80.0)

    def test_calculate_incident_score_remote_monitoring(self):
        # Remote lightning fire in deep tundra/bog, 50 km from people
        inc_remote = {
            "id": "rem_1",
            "name": "Remote Tundra Fire",
            "dist_to_settlement_km": 55.0,
            "ros_m_min": 1.2,
            "frp_mw": 25.0,
            "area_ha": 200.0,
            "rh_pct": 45.0,
            "wind_kmh": 12.0,
        }
        res = fp.calculate_incident_score(inc_remote)
        self.assertLess(res["composite_score"], 35.0)
        self.assertEqual(res["priority_tier"], "P4")

    def test_rank_incidents_ordering(self):
        incidents = [
            {"id": "low", "name": "Low Priority", "dist_to_settlement_km": 40.0, "ros_m_min": 1.0, "frp_mw": 20.0},
            {"id": "high", "name": "High Priority", "dist_to_settlement_km": 2.0, "ros_m_min": 5.0, "frp_mw": 300.0},
            {"id": "med", "name": "Medium Priority", "dist_to_settlement_km": 10.0, "ros_m_min": 3.0, "frp_mw": 100.0},
        ]
        res = fp.rank_incidents(incidents)
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["incident_count"], 3)
        top = res["top_priority_incidents"]

        # Must be strictly ordered descending
        self.assertEqual(top[0]["id"], "high")
        self.assertEqual(top[0]["priority_rank"], 1)
        self.assertEqual(top[1]["id"], "med")
        self.assertEqual(top[1]["priority_rank"], 2)
        self.assertEqual(top[2]["id"], "low")
        self.assertEqual(top[2]["priority_rank"], 3)

        self.assertGreater(top[0]["composite_score"], top[1]["composite_score"])
        self.assertGreater(top[1]["composite_score"], top[2]["composite_score"])


class FirePriorityAPITests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(backend_app.app)

    def test_api_presets(self):
        resp = self.client.get("/priority/presets")
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertIn("presets", data)
        self.assertGreaterEqual(len(data["presets"]), 2)

    def test_api_get_rank(self):
        resp = self.client.get("/priority/rank?preset=western_canada_boreal")
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["incident_count"], 6)
        self.assertEqual(data["top_priority_incidents"][0]["priority_rank"], 1)

    def test_api_post_rank_custom(self):
        payload = {
            "incidents": [
                {"id": "f1", "name": "Fire Alpha", "dist_to_settlement_km": 3.0, "ros_m_min": 4.5, "frp_mw": 200.0},
                {"id": "f2", "name": "Fire Beta", "dist_to_settlement_km": 25.0, "ros_m_min": 1.5, "frp_mw": 30.0},
            ]
        }
        resp = self.client.post("/priority/rank", json=payload)
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertEqual(data["incident_count"], 2)
        self.assertEqual(data["top_priority_incidents"][0]["id"], "f1")
        self.assertIn("advisory_disclaimer", data)
        self.assertIn("operational_warning", data["advisory_disclaimer"])

    def test_api_post_rank_validation_errors(self):
        # 1. Negative distance
        resp1 = self.client.post("/priority/rank", json={"incidents": [{"dist_to_settlement_km": -100.0}]})
        self.assertEqual(resp1.status_code, 422)

        # 2. Out of bounds RH
        resp2 = self.client.post("/priority/rank", json={"incidents": [{"rh_pct": 140.0}]})
        self.assertEqual(resp2.status_code, 422)

        # 3. Negative ROS
        resp3 = self.client.post("/priority/rank", json={"incidents": [{"ros_m_min": -5.0}]})
        self.assertEqual(resp3.status_code, 422)

        # 4. String element instead of dict/incident object
        resp4 = self.client.post("/priority/rank", json={"incidents": ["not_a_valid_incident"]})
        self.assertEqual(resp4.status_code, 422)

        # 5. Missing incidents list
        resp5 = self.client.post("/priority/rank", json={})
        self.assertEqual(resp5.status_code, 422)

    def test_presets_provenance_and_advisory(self):
        resp = self.client.get("/priority/presets")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        for p in data["presets"]:
            self.assertEqual(p["preset_type"], "illustrative_scenario")
            self.assertTrue(len(p["provenance_note"]) > 0)
