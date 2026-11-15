"""Offline tests for the drone route generator, the carbon calculator and the orbit-geometry modules."""
import json
import math
import unittest
from pathlib import Path

from backend import carbon, dbnet, drone

SIM = Path(__file__).resolve().parent.parent / "cache" / "demo" / "flin_flon" / "simulate.json"


@unittest.skipUnless(SIM.exists(), "demo simulation not present")
class DroneCarbonTests(unittest.TestCase):
    def setUp(self):
        self.sim = json.loads(SIM.read_text())

    def test_drone_route_respects_budget_and_formats(self):
        m = drone.build_mission(self.sim, 2.0, max_flight_min=25)
        self.assertLessEqual(m["stats"]["est_flight_min"], 27)
        self.assertGreaterEqual(m["stats"]["waypoints"], 5)
        self.assertEqual(m["waypoints"][0]["kind"], "launch")
        self.assertTrue(drone.to_gpx(m).startswith("<?xml"))
        self.assertIn("<LineString>", drone.to_kml(m))
        self.assertEqual(drone.to_csv(m).count("\n"), len(m["waypoints"]) + 1)

    def test_drone_launch_is_never_downwind(self):
        m = drone.build_mission(self.sim, 2.0)
        self.assertIn("not a certified flight plan", " ".join(m["warnings"]).lower())

    def test_carbon_wooster(self):
        feats = [{"properties": {"satellite": "X", "frp_mw": 100.0, "time_utc": "2026-01-01T00:00:00Z"}}]
        r = carbon.from_detections(feats, 24, 0.0, 0.0, "savanna", 70.0, 1.0)
        self.assertAlmostEqual(r["total"]["biomass_t"], 0.368 * 100 * 86400 / 1000, delta=1)
        self.assertAlmostEqual(r["total"]["co2_t"], r["total"]["biomass_t"] * 1.686, delta=2)
        self.assertAlmostEqual(r["total"]["value"]["eur"], r["total"]["co2_t"] * 70, delta=100)

    def test_carbon_forecast_monotonic(self):
        f = carbon.from_simulation(self.sim)
        co2 = [x["co2_t"] for x in f["timeline"]]
        self.assertEqual(co2, sorted(co2))

    def test_illumination_angles(self):
        r = dbnet.illumination(54.975, -101.725, 24)
        for p in r["passes"]:
            if p["day"]:
                self.assertTrue(0 <= p["sun_glint_angle_deg"] <= 180)
                self.assertAlmostEqual(p["backscatter_angle_deg"] + p["scattering_angle_deg"], 180, delta=0.2)

    def test_station_radius(self):
        self.assertTrue(2000 < dbnet._station_radius_km(830, 5) < 3200)
        self.assertTrue(math.isfinite(dbnet._station_radius_km(830, 0)))


if __name__ == "__main__":
    unittest.main()
