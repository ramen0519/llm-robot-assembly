"""RRT-Connect path proof with real geometry and real servo-driven motion."""
from pathlib import Path
import sys
import tempfile
import unittest
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from assembly_skills import AssemblySkills
from failure_report import FailureCode, FailureEvent
from joint_path_planner import plan_joint_path
from run_motion_detour import build_probe, prepare_obstacle, execute_trial
from view_tcp import read_tcp_pose


class JointPathPlannerTests(unittest.TestCase):
    def build(self):
        model, data, config = build_probe()
        return AssemblySkills(model, data, config)

    def test_real_rrt_detour_reaches_tcp_without_any_forbidden_contact(self):
        with tempfile.TemporaryDirectory() as temporary:
            result = execute_trial(output_root=temporary)
        self.assertEqual(result['status'], 'success', result['error'])
        self.assertTrue(result['direct_cartesian_path_rejected'])
        self.assertTrue(result['stale_obstacle_plan_rejected'])
        self.assertEqual(result['planner']['planner'], 'RRT-Connect')
        self.assertGreater(result['planner']['rejected_queries'], 0)
        self.assertLess(result['maximum_final_tcp_error_m'], .001)
        self.assertFalse(result['assembly_success'])

    def test_search_budget_exhaustion_is_unknown_and_does_not_move_robot(self):
        skills = self.build()
        plan, _, _, _ = prepare_obstacle(skills)
        q, v, controls, moment = skills.data.qpos.copy(), skills.data.qvel.copy(), skills.data.ctrl.copy(), float(skills.data.time)
        with self.assertRaises(FailureEvent) as caught:
            plan_joint_path(skills.motion, plan.arm_knots[-1], max_time_s=1e-9)
        self.assertEqual(caught.exception.code, FailureCode.MOTION_PATH_NOT_FOUND)
        np.testing.assert_array_equal(skills.data.qpos, q)
        np.testing.assert_array_equal(skills.data.qvel, v)
        np.testing.assert_array_equal(skills.data.ctrl, controls)
        self.assertEqual(skills.data.time, moment)

    def test_changes_to_velocity_control_or_mocap_invalidate_plan_before_execution(self):
        skills = self.build()
        skills.settle()
        p, r = read_tcp_pose(skills.data)
        plan = skills.motion.plan(p + [0, 0, .02], r, allow_support=True, allow_grasp=False)
        moment = float(skills.data.time)
        for field in ('qvel', 'ctrl', 'mocap_pos', 'mocap_quat'):
            with self.subTest(field=field):
                value = getattr(skills.data, field)
                original = value.copy()
                value.flat[0] += .001
                with self.assertRaises(FailureEvent) as caught:
                    skills.motion.execute(plan, allow_support=True, allow_grasp=False)
                self.assertEqual(caught.exception.code, FailureCode.INPUT_OR_PRECONDITION_INVALID)
                self.assertEqual(skills.data.time, moment)
                value[:] = original

    def test_invalid_joint_goal_is_rejected_before_simulation(self):
        skills = self.build()
        for goal in ([0.] * 5, [float('nan')] * 6, [100.] * 6):
            with self.assertRaises(FailureEvent):
                plan_joint_path(skills.motion, goal)
        self.assertEqual(skills.data.time, 0.)


if __name__ == '__main__':
    unittest.main()
