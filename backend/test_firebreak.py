"""Offline tests for the What-If Firebreak Sandbox module.

    .venv/bin/python -m pytest backend/test_firebreak.py -v
"""
import json
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from shapely.geometry import LineString

from backend import app as backend_app
from backend import firebreak

SIM_PATH = Path(__file__).resolve().parent.parent / "cache" / "demo" / "flin_flon" / "simulate.json"


class FirebreakUnitTests(unittest.TestCase):
    def test_presets(self):
        presets = firebreak.PRESETS
        self.assertIn("handline", presets)
        self.assertIn("dozer_double", presets)
        self.assertIn("retardant_heavy", presets)
        for pid, spec in presets.items():
            self.assertGreater(spec["width_m"], 0)
            self.assertGreater(spec["efficacy"], 0)
            self.assertLessEqual(spec["efficacy"], 1.0)
            self.assertTrue(spec["color"].startswith("#"))

    def test_flame_length_and_spotting(self):
        # Forest should have higher flame length than grass at same wind
        fl_forest = firebreak.estimate_flame_length("forest", 20.0)
        fl_grass = firebreak.estimate_flame_length("grass", 20.0)
        self.assertGreater(fl_forest, fl_grass)

        # Higher wind gives higher spotting distance
        spot_low = firebreak.estimate_spotting_distance("forest", 10.0, fl_forest)
        spot_high = firebreak.estimate_spotting_distance("forest", 40.0, fl_forest)
        self.assertGreater(spot_high, spot_low)

    def test_critical_width_attack_angle(self):
        # Head-on attack (0 deg) requires wider barrier than flank attack (80 deg)
        w_head = firebreak.critical_break_width(flame_length_m=3.0, wind_kmh=25.0, attack_angle_deg=0.0)
        w_flank = firebreak.critical_break_width(flame_length_m=3.0, wind_kmh=25.0, attack_angle_deg=80.0)
        self.assertGreater(w_head, w_flank)

    def test_line_hold_probability_monotonic(self):
        # A 30 m line holds better than a 2 m line against the same attack
        p_narrow = firebreak.line_hold_probability(width_m=2.0, crit_width_m=8.0, spotting_dist_m=30.0, efficacy=0.9)
        p_wide = firebreak.line_hold_probability(width_m=30.0, crit_width_m=8.0, spotting_dist_m=30.0, efficacy=0.9)
        self.assertGreater(p_wide, p_narrow)

    def test_evaluate_firebreak_geojson(self):
        # Construct a simple line in Manitoba
        line = LineString([(-101.75, 54.95), (-101.70, 54.95)])
        res = firebreak.evaluate_firebreak(
            barrier_geom=line,
            fuel="forest",
            wind_kmh=20.0,
            wind_dir_deg=270.0,
            preset_id="dozer_double",
        )
        self.assertIn(res["status"], ["holding", "breached"])
        self.assertTrue(0.0 <= res["hold_probability"] <= 1.0)
        self.assertGreater(res["barrier_length_m"], 100.0)
        self.assertEqual(res["barrier_width_m"], 12.0)
        self.assertGreater(len(res["segments"]), 0)
        self.assertIn("tactical_summary", res)
        self.assertGreater(res["impact"]["co2_prevented_tons"], 0)

    @unittest.skipUnless(SIM_PATH.exists(), "demo simulation not found")
    def test_evaluate_with_simulation_payload(self):
        sim_data = json.loads(SIM_PATH.read_text())
        # Line cutting north of ignition
        line = LineString([(-101.76, 54.98), (-101.68, 54.98)])
        res = firebreak.evaluate_firebreak(
            barrier_geom=line,
            fuel="forest",
            preset_id="retardant_heavy",
            simulation_payload=sim_data,
        )
        self.assertIn(res["status"], ["holding", "breached"])
        self.assertGreater(res["impact"]["area_saved_ha"], 0)
        self.assertGreater(res["impact"]["co2_prevented_tons"], 0)


class FirebreakAPITests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(backend_app.app)

    def test_api_presets(self):
        resp = self.client.get("/firebreak/presets")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("presets", data)
        self.assertGreaterEqual(len(data["presets"]), 5)

    def test_api_get_evaluate(self):
        resp = self.client.get("/firebreak/evaluate?line=-101.75,54.95;-101.70,54.95&fuel=forest&wind_kmh=15")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("hold_probability", data)
        self.assertIn("tactical_summary", data)

    def test_api_post_evaluate(self):
        payload = {
            "type": "Feature",
            "geometry": {
                "type": "LineString",
                "coordinates": [[-101.75, 54.95], [-101.70, 54.95]],
            },
            "properties": {},
        }
        resp = self.client.post("/firebreak/evaluate?preset=dozer_double&fuel=shrub&wind_kmh=25", json=payload)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["fuel_model"], "shrub")
        self.assertEqual(data["barrier_width_m"], 12.0)
