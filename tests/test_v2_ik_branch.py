from pathlib import Path
import sys,unittest
from types import SimpleNamespace
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from joint_path_planner import equivalent_ik_starts


class IKBranch(unittest.TestCase):
    def test_wrapped_branch_is_scratch_only_and_keeps_nonarm_state(self):
        model=SimpleNamespace(jnt_range=np.tile([-2*np.pi,2*np.pi],(6,1)))
        q=np.array([-2.5,-1.2,.6,-.9,-1.57,6.15,123.,456.]);before=q.copy()
        seeds=equivalent_ik_starts(model,q)
        self.assertEqual(len(seeds),4)
        self.assertAlmostEqual(seeds[1][5],6.15-2*np.pi)
        for seed in seeds:np.testing.assert_array_equal(seed[6:],q[6:])
        np.testing.assert_array_equal(q,before)
        seeds[0][0]=10;np.testing.assert_array_equal(q,before)

    def test_principal_pose_does_not_duplicate_a_seed_or_change_bounds(self):
        model=SimpleNamespace(jnt_range=np.tile([-np.pi,np.pi],(6,1)))
        q=np.array([0,-1,1,-1,-1,0,789.])
        seeds=equivalent_ik_starts(model,q)
        self.assertEqual(len(seeds),4)
        self.assertTrue(all(np.all(np.abs(s[:6])<np.pi) for s in seeds))
        self.assertFalse(np.array_equal(seeds[1],q))


if __name__=='__main__':unittest.main()
