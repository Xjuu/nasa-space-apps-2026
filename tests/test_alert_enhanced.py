import unittest
from unittest.mock import patch
import numpy as np
from equifire import alerts1km as A


def records(future_x=23):
    day = np.array([20000, 20000, 19999, 19999, 19800, 20001])
    rec = dict(day=day, iy=np.full(6, 9020), ix=18000 + np.array([20, 21, 19, 20, 22, future_x]))
    for name in ('n', 'hi', 'night'):
        rec[name] = np.ones(6)
    rec['lo'] = np.zeros(6)
    rec['frp'] = np.full(6, 30.)
    rec['frpmax'] = np.full(6, 30.)
    return A._sort_recs(rec)


class EnhancedTests(unittest.TestCase):
    def features(self, future_x=23, labels=True, enhanced=True):
        return A.featurize(A.RecIndex(records(future_x)), 20000, 9000, 18000,
                           50, 50, 1., with_labels=labels, radius_km=3, enhanced=enhanced)

    def test_future_detections_cannot_change_features(self):
        original = self.features()
        altered = self.features(future_x=30)
        live = self.features(labels=False)
        np.testing.assert_equal(original['X'], altered['X'])
        np.testing.assert_equal(original['X'], live['X'])
        old = self.features(enhanced=False)
        np.testing.assert_equal(original['X'][:, :len(A.FEATURES)], old['X'])

    def test_map_window_does_not_change_location_features(self):
        index = A.RecIndex(records())
        small = A.feature_window(index, 20000, 9015, 18015, 15, 15, 1.)
        large = A.feature_window(index, 20000, 9000, 18000, 50, 50, 1.)
        lookup = {(int(y + large['y0']), int(x + large['x0'])): row
                  for y, x, row in zip(large['cy'], large['cx'], large['X'])}
        for y, x, row in zip(small['cy'], small['cx'], small['X']):
            np.testing.assert_equal(row, lookup[(int(y + small['y0']), int(x + small['x0']))])
        self.assertEqual(len(lookup), len(large['X']))

    def test_front_distance_and_recurrence(self):
        r = self.features()
        i = np.flatnonzero((r['cy'] == 20) & (r['cx'] == 22))[0]
        columns = A.FEATURES + A.EXTRA_FEATURES
        f = dict(zip(columns, r['X'][i]))
        self.assertAlmostEqual(f['dist_km'], 1.11195, places=3)
        self.assertAlmostEqual(f['dist_change_km'], 1.11195, places=3)
        self.assertEqual(f['burn365_days'], 1)
        self.assertEqual(f['nearest_frp'], 30)
        self.assertEqual(f['nearest_hi_frac'], 1)
        self.assertFalse(np.isinf(r['X']).any())

    def test_inverse_sampling_weights_represent_full_candidate_count(self):
        day = int((np.datetime64('2020-06-01') - A.EPOCH).astype(int))
        cur = records()
        cur = {k: v[:5].copy() for k, v in cur.items()}
        cur['day'][:] = day
        fake = {'X': np.zeros((100, len(A.FEATURES))),
                'labels': {1: np.zeros(100), 3: np.zeros(100)}}
        with patch.object(A, 'load_country_year', side_effect=lambda c,y: cur if y == 2020 else None), \
             patch.object(A, 'featurize', return_value=fake):
            r = A.build_cases('Example', 2020, 1, 10)
        self.assertEqual(len(r['X']), 10)
        self.assertEqual(r['weights'].sum(), 100)


if __name__ == '__main__':
    unittest.main()
