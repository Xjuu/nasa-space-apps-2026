import unittest
from unittest.mock import patch
import tempfile
from pathlib import Path
import pandas as pd
import numpy as np
from equifire.weather import AlertWeather, ALERT_WEATHER_FEATURES


class AlertWeatherTests(unittest.TestCase):
    def test_archive_epoch_matches_fire_epoch(self):
        from equifire.weather import VARS
        frame = pd.DataFrame({v: [1.] for v in VARS})
        frame["day"], frame["ty"], frame["tx"] = 0, 0, 0
        with patch("equifire.weather.cached_frames", return_value=[frame]):
            w = AlertWeather()
        day = int((np.datetime64("2000-01-01") - np.datetime64("1970-01-01")).astype(int))
        np.testing.assert_allclose(w.get(day, 1, 1), 1.)

    def test_offline_csv(self):
        from equifire.weather import VARS
        frame = pd.DataFrame({v: [2.] for v in VARS})
        frame["date"], frame["latitude"], frame["longitude"] = "2020-01-01", 1., 1.
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "weather.csv"
            frame.to_csv(path, index=False)
            w = self.weather()
            w.load_csv(path)
        day = int((np.datetime64("2020-01-01") - np.datetime64("1970-01-01")).astype(int))
        np.testing.assert_allclose(w.get(day, 1, 1), 2.)

    def weather(self):
        w = AlertWeather.__new__(AlertWeather)
        w.rows = {(day, 0, 0): np.array([20., 270., 30., 20., 2.]) for day in range(3, 10)}
        return w

    def test_wind_alignment_history_and_no_future_leakage(self):
        w = self.weather()
        w.rows[(10, 0, 0)] = np.array([999., 0., 999., 999., 999.])
        w.rows[(11, 0, 0)] = np.full(5, 999.)
        f = w.features(10, [1., 1.], [1., 1.], np.array([0., 0.]), np.array([1., -1.]))
        self.assertEqual(f.shape, (2, len(ALERT_WEATHER_FEATURES)))
        np.testing.assert_allclose(f[:, 0], 20)
        np.testing.assert_allclose(f[:, 4], 14)
        np.testing.assert_allclose(f[:, 7], [1., -1.])
        np.testing.assert_allclose(f[:, 8], [20., -20.])
        np.testing.assert_allclose(f[:, -1], 1)

    def test_missing_weather_stays_missing(self):
        w = self.weather()
        f = w.features(10, [50.], [50.], np.array([1.]), np.array([0.]))
        self.assertTrue(np.isnan(f[0, :-1]).all())
        self.assertEqual(f[0, -1], 0)

    def test_incomplete_rain_history_is_not_reported_as_dry(self):
        w = self.weather()
        del w.rows[(4, 0, 0)]
        f = w.features(10, [1.], [1.], np.array([1.]), np.array([0.]))
        self.assertTrue(np.isnan(f[0, 4]))
        self.assertEqual(f[0, 3], 2)


if __name__ == '__main__':
    unittest.main()
