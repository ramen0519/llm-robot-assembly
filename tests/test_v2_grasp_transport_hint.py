from pathlib import Path
import sys, unittest
from types import SimpleNamespace
from unittest.mock import patch
import mujoco
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from assembly_v2.grasp_transport_hint import nominal_transport_hint, refinement_trials
from assembly_v2.budget import Budget
from assembly_v2.policy import development_candidate_rank


class GraspTransportHint(unittest.TestCase):
    def test_paired_ik_changes_only_scratch_and_records_large_joint_travel(self):
        model=mujoco.MjModel.from_xml_string('<mujoco><default><joint armature=".01"/></default><worldbody><body><joint/><joint/><joint/><joint/><joint/><joint/><geom size=".1" mass="1"/></body><body pos="1 0 0"><freejoint/><geom size=".1" mass="1"/></body></worldbody></mujoco>')
        data=mujoco.MjData(model);data.qpos[5]=6.1;mujoco.mj_forward(model,data)
        before=np.empty(mujoco.mj_stateSize(model,mujoco.mjtState.mjSTATE_INTEGRATION))
        mujoco.mj_getState(model,data,before,mujoco.mjtState.mjSTATE_INTEGRATION)
        skills=SimpleNamespace(model=model,data=data,budget=Budget(10),active='a',
            prepared={'base_id':'base','profile':{'resources':{'ik_iterations':200}}})
        skills.motion=SimpleNamespace(model=model,data=data,budget=skills.budget)
        screen=SimpleNamespace(skills=skills,goal=np.eye(4))
        calls=[]
        def solve(motion,*args):
            calls.append(motion.data)
            q=motion.data.qpos.copy();q[5]=6.0 if len(calls)==1 else .1
            return iter([(q,1.)])
        with patch('assembly_v2.grasp_transport_hint.body_pose',return_value=np.eye(4)),patch('assembly_v2.grasp_transport_hint._tcp_ik_candidates',side_effect=solve):
            result=nominal_transport_hint(screen,np.eye(4),4.)
        after=np.empty_like(before);mujoco.mj_getState(model,data,after,mujoco.mjtState.mjSTATE_INTEGRATION)
        np.testing.assert_array_equal(before,after)
        np.testing.assert_array_equal(calls[1].qpos[6:],data.qpos[6:])
        self.assertIsNot(calls[1],data)
        self.assertAlmostEqual(result['source_goal_max_joint_distance_rad'],5.9)
        self.assertFalse(result['physical_success'])
        self.assertEqual(result['collision'],'not_run')

    def test_symmetric_choices_precede_larger_feature_retraction(self):
        trials=list(refinement_trials())
        self.assertEqual(trials[:4],[(0.,0.,.5),(180.,0.,.5),(90.,0.,.5),(-90.,0.,.5)])
        self.assertLess(trials.index((180.,0.,2.)),trials.index((0.,0.,2.5)))

    def test_development_rank_prefers_short_loaded_motion_over_small_empty_rotation(self):
        def item(span,empty):
            return {'geometry_pose_adjustment_mm':2.,'motion_hint':{
                'carried_joint_span_rad':span,'tcp_orientation_change_rad':empty,
                'goal_top_approach_deviation_rad':0.}}
        self.assertLess(development_candidate_rank(item(.3,3.14)),development_candidate_rank(item(6.,0.)))

if __name__=='__main__':unittest.main()
