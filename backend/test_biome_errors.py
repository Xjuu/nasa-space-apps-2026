"""Unit tests for Post-Replay Biome Error Analysis (#27)."""
import unittest

from fastapi.testclient import TestClient

from backend import app as app_module
from backend import biome_errors


class TestBiomeErrors(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app_module.app)

    def test_compute_all_biome_errors_structure(self):
        res = biome_errors.compute_all_biome_errors(objective="iou")
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["biome_count"], 6)
        self.assertEqual(len(res["biomes"]), 6)

        biome_ids = [b["biome_id"] for b in res["biomes"]]
        expected = [
            "boreal_forest",
            "tropical_peatland",
            "flooded_savanna",
            "agricultural_cropland",
            "tropical_ecotone",
            "temperate_grassland",
        ]
        self.assertEqual(sorted(biome_ids), sorted(expected))

    def test_math_invariants(self):
        """Verifies fundamental cell accounting: hits + misses == observed; hits + FP == predicted."""
        res = biome_errors.compute_all_biome_errors(objective="iou")
        for b in res["biomes"]:
            for d in b["days"]:
                c = d["cells"]
                self.assertEqual(c["hits"] + c["misses"], c["observed"], f"Mismatch in {b['biome_id']} day {d['day']}")
                self.assertEqual(c["hits"] + c["false_alarms"], c["predicted"], f"Mismatch in {b['biome_id']} day {d['day']}")

                # Bounds on percentages
                self.assertTrue(0.0 <= d["strict"]["iou_pct"] <= 100.0)
                self.assertTrue(0.0 <= d["strict"]["precision_pct"] <= 100.0)
                self.assertTrue(0.0 <= d["strict"]["recall_pct"] <= 100.0)
                self.assertTrue(0.0 <= d["tolerant_5km"]["within_5km_pct"] <= 100.0)

    def test_cloud_penalty_and_unverified_cells(self):
        """Peatland (Kalimantan) and Ecotone (Roraima) must have significant unverified cell fractions."""
        res = biome_errors.compute_all_biome_errors(objective="iou")
        by_id = {b["incident_id"]: b for b in res["biomes"]}

        kal = by_id["kalimantan_2023"]
        self.assertTrue(kal["cumulative_summary"]["total_unverifiable_cells"] > 0)
        self.assertTrue(kal["cumulative_summary"]["unverifiable_false_alarm_fraction_pct"] > 15.0)

        ror = by_id["roraima_2024"]
        self.assertTrue(ror["cumulative_summary"]["total_unverifiable_cells"] > 0)

        # Pantanal has clear skies in benchmark: 0 unverifiable cells
        pan = by_id["pantanal_2024"]
        self.assertEqual(pan["cumulative_summary"]["total_unverifiable_cells"], 0)

    def test_agricultural_scatter_characteristic(self):
        """Punjab stubble burning has high recall (>90%) but lower strict precision due to discrete fields."""
        res = biome_errors.compute_all_biome_errors(objective="iou")
        by_id = {b["incident_id"]: b for b in res["biomes"]}
        pun = by_id["punjab_stubble_2024"]

        # High recall
        self.assertTrue(pun["cumulative_summary"]["cumulative_strict_recall_pct"] >= 90.0)
        # Moderate precision due to dispersed non-perimeter burning
        self.assertTrue(pun["cumulative_summary"]["cumulative_strict_precision_pct"] <= 45.0)

    def test_comparative_rankings(self):
        res = biome_errors.compute_all_biome_errors(objective="iou")
        rankings = res["comparative_rankings"]
        self.assertEqual(len(rankings["by_day3_strict_iou"]), 6)
        self.assertEqual(len(rankings["by_day3_tolerant_within_5km"]), 6)
        self.assertEqual(len(rankings["by_cloud_masking_penalty"]), 6)

        # Pantanal is top strict day 3 performer
        top_strict = rankings["by_day3_strict_iou"][0]
        self.assertEqual(top_strict["biome_id"], "flooded_savanna")

    def test_api_endpoints(self):
        # 1. Full endpoint
        r1 = self.client.get("/replay/biome-errors")
        self.assertEqual(r1.status_code, 200)
        d1 = r1.json()
        self.assertEqual(d1["status"], "success")
        self.assertEqual(len(d1["biomes"]), 6)

        # 2. Filter by day
        r2 = self.client.get("/replay/biome-errors?day=1")
        self.assertEqual(r2.status_code, 200)
        d2 = r2.json()
        for b in d2["biomes"]:
            self.assertEqual(len(b["days"]), 1)
            self.assertEqual(b["days"][0]["day"], 1)

        # 3. Summary endpoint
        r3 = self.client.get("/replay/biome-errors/summary")
        self.assertEqual(r3.status_code, 200)
        d3 = r3.json()
        self.assertEqual(d3["status"], "success")
        self.assertEqual(len(d3["rows"]), 6)
        self.assertIn("benchmarks", d3)
        for r in d3["rows"]:
            self.assertIn("model_method", r)

    def test_zero_observable_fraction_edge_case(self):
        """Zero observable fraction must evaluate to 0.0, never fallback to 100.0."""
        mock_profile = {
            "incident_name": "Test Zero Obs",
            "incident_name_pl": "Test",
            "biome_id": "test_biome",
            "biome_name": "Test",
            "biome_name_pl": "Test",
            "region": "Test",
            "region_pl": "Test",
            "fuel_regime": "Test",
            "fuel_regime_pl": "Test",
            "dominant_failure_mode": "test",
            "failure_mode_label": "Test",
            "failure_mode_label_pl": "Test",
            "diagnostic_insight": "Test",
            "diagnostic_insight_pl": "Test",
        }
        mock_data = {
            "reveal": {
                "per_day": [{
                    "day": 1,
                    "methods": {
                        "ml_physics": {
                            "strict": {"predicted_cells": 10, "observed_cells": 5, "hits": 2, "misses": 3, "false_alarms": 8},
                            "observable": {"coverage": {"observable_fraction": 0.0}}
                        }
                    }
                }]
            }
        }
        # Monkey patch load_replay_record
        orig_load = biome_errors.load_replay_record
        try:
            biome_errors.load_replay_record = lambda *args, **kwargs: mock_data
            res = biome_errors.extract_incident_biome_metrics("mock_test", mock_profile)
            day1_obs = res["days"][0]["observability"]
            self.assertEqual(day1_obs["observable_fraction_pct"], 0.0)
        finally:
            biome_errors.load_replay_record = orig_load

    def test_model_method_attribution(self):
        """Verifies Kalimantan uses ml-only while other biomes use ml_physics."""
        res = biome_errors.compute_all_biome_errors()
        by_id = {b["incident_id"]: b for b in res["biomes"]}
        self.assertEqual(by_id["kalimantan_2023"]["model_method"], "ml")
        self.assertEqual(by_id["flin_flon_2025"]["model_method"], "ml_physics")
        self.assertEqual(by_id["pantanal_2024"]["model_method"], "ml_physics")
        self.assertEqual(by_id["punjab_stubble_2024"]["model_method"], "ml_physics")
        self.assertEqual(by_id["roraima_2024"]["model_method"], "ml_physics")
        self.assertEqual(by_id["smokehouse_creek_2024"]["model_method"], "ml_physics")

    def test_fixture_fallback_when_cache_empty(self):
        """When CACHE_DIR has no files, data loads seamlessly from reference fixture."""
        from pathlib import Path
        orig_cache = biome_errors.CACHE_DIR
        try:
            biome_errors.CACHE_DIR = Path("/nonexistent/cache/dir")
            res = biome_errors.compute_all_biome_errors()
            self.assertEqual(res["status"], "success")
            self.assertEqual(res["biome_count"], 6)
        finally:
            biome_errors.CACHE_DIR = orig_cache


if __name__ == "__main__":
    unittest.main()
