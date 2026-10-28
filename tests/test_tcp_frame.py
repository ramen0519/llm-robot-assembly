"""Check TCP frame conventions, rigid mounting, and incorrect definitions."""

import math
from pathlib import Path
import sys
import unittest

import mujoco
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from move_gripper import command_at, measure_state, check_path_sample, pad_gaps_mm
from ur3e_with_gripper import load_robot_with_gripper
from view_tcp import TCP_SITE_NAME, read_tcp_pose, check_tcp_frame


class TcpFrameTests(unittest.TestCase):
    def test_world_pose_follows_a_known_shoulder_rotation(self):
        model, data = load_robot_with_gripper()
        position0, rotation0 = read_tcp_pose(data)
        joint_id = model.joint("shoulder_pan_joint").id
        pivot = data.xanchor[joint_id].copy()
        np.testing.assert_allclose(data.xaxis[joint_id], [0, 0, 1], atol=1e-12)
        angle = math.radians(30)
        c, s = math.cos(angle), math.sin(angle)
        world_turn = np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])
        # A separate INITIAL pose for forward-kinematics verification, not a trajectory.
        data.qpos[model.jnt_qposadr[joint_id]] += angle
        mujoco.mj_forward(model, data)
        position1, rotation1 = read_tcp_pose(data)
        np.testing.assert_allclose(position1, pivot + world_turn @ (position0 - pivot), atol=1e-10, rtol=0)
        np.testing.assert_allclose(rotation1, world_turn @ rotation0, atol=1e-10, rtol=0)
        self.assertGreater(float(np.linalg.norm(position1 - position0)), 0.1)
        check_tcp_frame(model, data)

    def test_tcp_definition_remains_rigid_during_physical_gripper_closure(self):
        model, data = load_robot_with_gripper()
        initial_arm = data.qpos[:6].copy()
        self.assertGreater(min(pad_gaps_mm(model, data)), 80)
        actuator = model.actuator("gripper_fingers_actuator").id
        for _ in range(round(7.0 / model.opt.timestep)):
            command, phase = command_at(float(data.time))
            data.ctrl[actuator] = command
            mujoco.mj_step(model, data)
            mujoco.mj_forward(model, data)
            check_path_sample(measure_state(model, data, initial_arm, phase))
            check_tcp_frame(model, data)
        self.assertLess(max(pad_gaps_mm(model, data)), 2)

    def test_wrong_site_offset_and_orientation_are_rejected(self):
        for defect in ("offset", "orientation"):
            with self.subTest(defect=defect):
                model, data = load_robot_with_gripper()
                site_id = model.site(TCP_SITE_NAME).id
                position0, rotation0 = read_tcp_pose(data)
                if defect == "offset":
                    model.site_pos[site_id, 0] += 0.01
                else:
                    model.site_quat[site_id] = [math.sqrt(0.5), math.sqrt(0.5), 0, 0]
                # The compiled model optimizes identity rotations; invalidate that shortcut.
                model.site_sameframe[site_id] = mujoco.mjtSameFrame.mjSAMEFRAME_NONE
                mujoco.mj_forward(model, data)
                position1, rotation1 = read_tcp_pose(data)
                if defect == "offset":
                    self.assertGreater(float(np.linalg.norm(position1 - position0)), 0.009)
                else:
                    self.assertGreater(float(np.linalg.norm(rotation1 - rotation0)), 1.0)
                with self.assertRaises(ValueError):
                    check_tcp_frame(model, data)


if __name__ == "__main__":
    unittest.main()
