"""Verify IK with independent MuJoCo FK and explicit failure cases."""

import math
from pathlib import Path
import sys
import unittest

import mujoco
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from solve_tcp_ik import solve_tcp_pose
from ur3e_with_gripper import load_robot_with_gripper
from view_tcp import read_tcp_pose


class TcpIKTests(unittest.TestCase):
    def test_nearby_pose_preserves_orientation_gripper_and_input_state(self):
        model, initial = load_robot_with_gripper()
        q0, v0, ctrl0, time0 = initial.qpos.copy(), initial.qvel.copy(), initial.ctrl.copy(), initial.time
        p0, r0 = read_tcp_pose(initial)
        result = solve_tcp_pose(model, initial, p0 + [0, 0, 0.03], r0)
        self.assertTrue(result.success)
        # A fresh MuJoCo state verifies the returned angles independently of Mink's data.
        candidate = mujoco.MjData(model)
        candidate.qpos[:] = result.qpos
        mujoco.mj_forward(model, candidate)
        p1, r1 = read_tcp_pose(candidate)
        np.testing.assert_allclose(p1, p0 + [0, 0, 0.03], atol=0.001, rtol=0)
        # Compare each axis to its starting direction, not the solver's error report.
        for axis in range(3):
            self.assertGreater(float(r0[:, axis] @ r1[:, axis]), math.cos(math.radians(0.5)))
        np.testing.assert_allclose(result.qpos[6:], q0[6:], atol=1e-9, rtol=0)
        np.testing.assert_array_equal(initial.qpos, q0)
        np.testing.assert_array_equal(initial.qvel, v0)
        np.testing.assert_array_equal(initial.ctrl, ctrl0)
        self.assertEqual(initial.time, time0)
        self.assertEqual(candidate.time, 0.0)
        self.assertEqual(candidate.ncon, 0)

    def test_iteration_budget_does_not_report_success(self):
        model, initial = load_robot_with_gripper()
        position, rotation = read_tcp_pose(initial)
        result = solve_tcp_pose(model, initial, position + [0, 0, 0.03], rotation, max_iterations=1)
        self.assertFalse(result.success)
        self.assertEqual(result.reason, "iteration_limit")
        self.assertEqual(result.iterations, 1)
        self.assertGreater(result.position_error_m, 0.001)

    def test_restricted_joint_ranges_prevent_a_false_success(self):
        model, initial = load_robot_with_gripper()
        position, rotation = read_tcp_pose(initial)
        # This fixture cannot achieve a 30 mm displacement within these narrow ranges.
        model.jnt_range[:6, 0] = initial.qpos[:6] - 0.001
        model.jnt_range[:6, 1] = initial.qpos[:6] + 0.001
        result = solve_tcp_pose(model, initial, position + [0, 0, 0.03], rotation, max_iterations=20)
        self.assertFalse(result.success)
        self.assertLessEqual(float(np.max(np.abs(result.qpos[:6] - initial.qpos[:6]))), 0.001 + 1e-8)

    def test_detected_floor_collision_is_rejected_even_if_pose_matches(self):
        model, initial = load_robot_with_gripper()
        model.geom_pos[model.geom("preview_floor").id, 2] = 0.4
        mujoco.mj_setConst(model, initial)
        mujoco.mj_resetDataKeyframe(model, initial, model.key("home").id)
        mujoco.mj_forward(model, initial)
        self.assertGreater(initial.ncon, 0)
        position, rotation = read_tcp_pose(initial)
        with self.assertRaisesRegex(RuntimeError, "contact"):
            solve_tcp_pose(model, initial, position, rotation)


if __name__ == "__main__":
    unittest.main()
