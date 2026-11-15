import unittest
import numpy as np
from equifire.alerts1km import within_radius, eval_alerts
from alerts1km import choose_threshold


class AlertRadiusTests(unittest.TestCase):
    def test_kilometres_and_latitude(self):
        # 0.03 degrees east is 3.34 km at equator, 1.67 km at 60 N.
        q, truth = np.array([[0, 3]]), np.array([[0, 0]])
        self.assertFalse(within_radius(q, truth, 9000, 18000, 3)[0])
        self.assertTrue(within_radius(q, truth, 15000, 18000, 3)[0])
        self.assertTrue(within_radius(np.array([[0, 2]]), truth, 9000, 18000, 3)[0])
        self.assertFalse(within_radius(q, np.empty((0, 2)), 9000, 18000, 3)[0])

    def test_eastern_longitude_with_int16_coordinates(self):
        self.assertTrue(within_radius(np.array([[0, 0]], dtype=np.int16),
                                      np.array([[0, 1]], dtype=np.int16),
                                      9000, 32950, 3)[0])

    def test_recall_uses_same_radius_as_labels(self):
        mp = dict(cy=np.array([0]), cx=np.array([0]), y0=9000, x0=18000, radius_km=3)
        for h in (1, 3):
            mp[f'new{h}'] = np.array([[0, 2], [0, 4]])
            mp[f'y{h}'] = np.array([1])
        metrics = eval_alerts([mp], [np.array([True])])[1]
        self.assertEqual(metrics['precision'], 1)
        self.assertEqual(metrics['recall'], 0.5)

    def test_threshold_keeps_ties_and_maximizes_hits(self):
        scores = np.array([.9, .8, .8, .6, .5])
        labels = np.array([1, 0, 1, 1, 0])
        threshold = choose_threshold(labels, scores, .7, min_alerts=2)
        self.assertAlmostEqual(threshold, .6)
        self.assertGreaterEqual(labels[scores >= threshold].mean(), .7)


if __name__ == '__main__':
    unittest.main()
