"""Offline unit tests for the Monte Carlo burn frequency ensemble module.

    .venv/bin/python -m pytest backend/test_monte_carlo.py -v
"""
import unittest
import numpy as np
import pandas as pd
from fastapi.testclient import TestClient

from backend import app as backend_app
from backend import monte_carlo
from backend import physics_spread as ps

START = pd.Timestamp("2025-06-01 12:00")


def make_mock_weather(hours=24):
    idx = pd.date_range(START - pd.Timedelta(hours=ps.SPINUP_H), START + pd.Timedelta(hours=hours), freq="h")
    return pd.DataFrame({
        "temp_c": 28.0,
        "rh": 25.0,
        "wind_kmh": 22.0,
        "wind_dir": 270.0,
        "gust_kmh": 32.0,
        "rain_mm": 0.0,
    }, index=idx)


def flat_elev(la, lo):
    return np.zeros_like(la)


class MonteCarloUnitTests(unittest.TestCase):
    def test_presets(self):
        presets = monte_carlo.PRESETS
        self.assertIn("standard", presets)
        self.assertIn("deep_uncertainty", presets)
        for pid, spec in presets.items():
            self.assertGreater(spec["members"], 0)
            self.assertGreater(spec["wind_noise_pct"], 0)
            self.assertGreater(spec["wind_dir_noise_deg"], 0)

    def test_perturb_weather_bounds(self):
        wx = make_mock_weather(24)
        rng = np.random.default_rng(123)
        wx_pert = monte_carlo._perturb_weather(wx, 20.0, 30.0, 15.0, rng)

        self.assertEqual(len(wx_pert), len(wx))
        self.assertTrue((wx_pert["wind_kmh"] >= 1.0).all())
        self.assertTrue((wx_pert["rh"] >= 5.0).all())
        self.assertTrue((wx_pert["rh"] <= 98.0).all())
        # Perturbed values should differ from constant baseline
        self.assertFalse(np.allclose(wx_pert["wind_dir"], wx["wind_dir"]))

    def test_run_ensemble_mock_weather(self):
        wx = make_mock_weather(12)
        res = monte_carlo.run_ensemble(
            lat=-15.0,
            lon=130.0,
            start=START,
            hours=12,
            members=10,
            fuel="savanna",
            wx=wx,
            elev_fn=flat_elev,
            seed=42,
        )
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["ensemble_stats"]["members_run"], 10)
        self.assertIn("area_distribution_km2", res["ensemble_stats"])

        dist = res["ensemble_stats"]["area_distribution_km2"]
        self.assertLessEqual(dist["p10"], dist["p50_median"])
        self.assertLessEqual(dist["p50_median"], dist["p90"])

        # Check frequency bands
        bands = res["frequency_bands"]
        self.assertEqual(len(bands), 4)

        # Monotonicity check: Area of 20% frequency band must be >= Area of 95% band
        area_20 = [b["area_km2"] for b in bands if b["threshold_percent"] == 20][0]
        area_95 = [b["area_km2"] for b in bands if b["threshold_percent"] == 95][0]
        self.assertGreaterEqual(area_20, area_95)

        # Wording check
        self.assertIn("częstość", res["wording_and_disclaimer"]["definition"].lower())
        self.assertIn("nie jest", res["wording_and_disclaimer"]["uncalibrated_notice"].lower())

        # Reproducibility check
        self.assertIn("reproducibility", res)
        self.assertEqual(res["reproducibility"]["seed"], 42)
        res_repeat = monte_carlo.run_ensemble(
            lat=-15.0,
            lon=130.0,
            start=START,
            hours=12,
            members=10,
            fuel="savanna",
            wx=wx,
            elev_fn=flat_elev,
            seed=42,
        )
        self.assertEqual(
            res["ensemble_stats"]["area_distribution_km2"]["p50_median"],
            res_repeat["ensemble_stats"]["area_distribution_km2"]["p50_median"],
        )


class MonteCarloAPITests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(backend_app.app)

    def test_api_presets(self):
        resp = self.client.get("/montecarlo/presets")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("presets", data)
        self.assertGreaterEqual(len(data["presets"]), 3)

    def test_api_post_run_mocked(self):
        import unittest.mock as mock
        mock_wx = make_mock_weather(24)
        with mock.patch("backend.physics_spread.fetch_weather", return_value=(mock_wx, {"sources": [], "errors": []})), \
             mock.patch("backend.physics_spread.fetch_elevation", return_value=(np.zeros((15, 15)), {"flat": True, "errors": []})):
            payload = {
                "lat": -15.0,
                "lon": 130.0,
                "start": str(START),
                "hours": 12,
                "members": 10,
                "preset": "standard",
                "seed": 42,
            }
            resp = self.client.post("/montecarlo/run", json=payload)
            self.assertEqual(resp.status_code, 200, resp.text)
            data = resp.json()
            self.assertEqual(data["status"], "success")
            self.assertIn("frequency_bands", data)
            self.assertIn("reproducibility", data)
            self.assertEqual(data["reproducibility"]["seed"], 42)

    def test_api_get_run_mocked(self):
        import unittest.mock as mock
        mock_wx = make_mock_weather(24)
        with mock.patch("backend.physics_spread.fetch_weather", return_value=(mock_wx, {"sources": [], "errors": []})), \
             mock.patch("backend.physics_spread.fetch_elevation", return_value=(np.zeros((15, 15)), {"flat": True, "errors": []})):
            resp = self.client.get(f"/montecarlo/run?lat=-15.0&lon=130.0&start={START.isoformat()}&hours=12&members=10&seed=42")
            self.assertEqual(resp.status_code, 200, resp.text)
            data = resp.json()
            self.assertEqual(data["status"], "success")
            self.assertIn("frequency_bands", data)
            self.assertEqual(data["reproducibility"]["seed"], 42)
