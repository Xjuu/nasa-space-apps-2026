import unittest
from unittest.mock import patch
from scipy import ndimage
import numpy as np
from equifire import direct, spread_policy as policy
from equifire.short_spread import event_weights


class SpreadPolicyTests(unittest.TestCase):
    def test_equal_event_mass_preserves_sampling_corrections(self):
        raw=np.array([1.,3.,2.,2.,2.])
        groups=np.array([0,0,1,1,1])
        horizon=np.array([1,1,3,3,3])
        w=event_weights(raw,groups,horizon)
        self.assertAlmostEqual(float(w[:2].sum()),float(w[2:].sum()),places=6)
        self.assertAlmostEqual(float(w[1]/w[0]),3.)
        self.assertAlmostEqual(float(w.mean()),1.)

    def test_neighbour_training_changes_only_day1_target(self):
        from equifire.short_spread import neighbourhood_rows
        A=np.zeros((direct.HIST+4,direct.S,direct.S),np.float32)
        A[direct.HIST,direct.W,direct.W]=1
        A[direct.HIST+1,direct.W,direct.W+1]=1
        distance=ndimage.distance_transform_cdt(A[direct.HIST]==0,metric='chessboard')
        X=np.arange(direct.S**2,dtype=np.float32)[:,None]
        with patch.object(direct,'_case_inputs',return_value=(A,(X,distance,A[direct.HIST]))):
            rows,y,w=neighbourhood_rows(None,[(8000,0,0)],None,['marker'],0)
        self.assertEqual(int(y[rows[:,-1]==1].sum()),5)
        self.assertEqual(int(y[rows[:,-1]==2].sum()),1)
        self.assertEqual(int(y[rows[:,-1]==3].sum()),1)
        totals=[float(w[rows[:,-1]==k].sum()) for k in (1,2,3)]
        np.testing.assert_allclose(totals,totals[0],rtol=1e-6)

    def test_tolerant_metric_matches_existing_definition(self):
        from walk_forward import _tol
        rng=np.random.default_rng(8)
        for _ in range(20):
            pred=rng.random((7,7))<.1
            truth=rng.random((7,7))<.1
            self.assertAlmostEqual(policy.tolerant_score(pred,truth),_tol(pred,truth))
        empty=np.zeros((7,7),bool)
        self.assertEqual(policy.tolerant_score(empty,empty),1.)

    def test_no_wrapping_at_grid_edges(self):
        nb=policy.neighbours((3,3))
        self.assertEqual(set(nb[0][nb[0]>=0]),{0,1,3,4})
        self.assertEqual(set(nb[8][nb[8]>=0]),{4,5,7,8})

    def test_policy_respects_eligible_cells_and_empty_probability(self):
        P=np.full((5,5),np.nan)
        P[2,2]=.8
        mask=policy.tolerant_select(P)
        self.assertEqual(int(mask.sum()),1)
        self.assertTrue(mask[2,2])
        P[2,2]=.01
        self.assertFalse(policy.tolerant_select(P).any())
        P[2,2]=.8
        self.assertFalse(policy.tolerant_select(P,gate=1).any())

    def test_summary_features_never_read_future_outcomes(self):
        rng=np.random.default_rng(1)
        A=(rng.random((direct.HIST+4,direct.S,direct.S))<.1).astype(float)
        P=rng.random((3,direct.S,direct.S)).astype(np.float32)
        first=policy.summary_features(P,A,(8000,3000,1200))
        A[direct.HIST+1:]=1000
        np.testing.assert_array_equal(first,policy.summary_features(P,A,(8000,3000,1200)))


if __name__=='__main__':unittest.main()
