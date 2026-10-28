"""Exercise physical TCP motion and failures using real MuJoCo integration."""

import contextlib
import csv
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

import mujoco
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import move_tcp
import ur3e_with_gripper
from view_tcp import read_tcp_pose


class TcpMotionTests(unittest.TestCase):
    def test_physical_arrival_and_incomplete_record_rejection(self):
        model, data = ur3e_with_gripper.load_robot_with_gripper()
        initial = data.qpos.copy()
        plan = move_tcp.prepare_motion(model, data)
        np.testing.assert_array_equal(data.qpos, initial)  # Preflight never moves the live robot.
        with tempfile.TemporaryDirectory() as folder, contextlib.redirect_stdout(io.StringIO()):
            trace = Path(folder) / "motion.csv"
            rows = move_tcp.run_motion(model, data, plan, trace_path=trace)
            self.assertTrue(move_tcp.assess_motion(rows))
            with self.assertRaisesRegex(ValueError, "full, continuous"):
                move_tcp.assess_motion(rows[:-100])
            with trace.open(encoding="utf-8", newline="") as source:
                self.assertEqual(len(list(csv.DictReader(source))), 3501)
        self.assertAlmostEqual(data.time, 7.0, places=8)
        self.assertGreater(float(np.max(np.abs(data.qpos[:6] - initial[:6]))), np.deg2rad(5))
        self.assertGreater(max(r["max_arm_speed_deg_s"] for r in rows), 1.0)
        position, rotation = read_tcp_pose(data)
        self.assertLess(np.linalg.norm(position - plan.target_position), 0.001)
        self.assertLess(np.linalg.norm(rotation - plan.target_rotation), 0.01)
        np.testing.assert_array_equal(model.opt.gravity, [0, 0, -9.81])
        np.testing.assert_array_equal(model.body_gravcomp, 0)
        np.testing.assert_array_equal(data.qfrc_applied, 0)
        np.testing.assert_array_equal(data.xfrc_applied, 0)
        for i, name in enumerate(move_tcp.JOINT_NAMES):
            actuator = model.actuator(name.removesuffix("_joint")).id
            self.assertTrue(model.actuator_forcelimited[actuator])
            lower, upper = model.actuator_forcerange[actuator]
            self.assertTrue(all(lower <= r[f"{name}_force_nm"] <= upper for r in rows))

    def test_without_gravity_compensation_stable_sag_is_not_arrival(self):
        model, data = ur3e_with_gripper.load_robot_with_gripper()
        plan = move_tcp.prepare_motion(model, data)
        with tempfile.TemporaryDirectory() as folder, contextlib.redirect_stdout(io.StringIO()):
            # Disable only the extra feedforward torque; the actual servos and gravity still run.
            with patch.object(move_tcp.ArmPositionController, "gravity_torque", return_value=np.zeros(6)):
                rows = move_tcp.run_motion(model, data, plan, trace_path=Path(folder) / "no_comp.csv")
            self.assertFalse(move_tcp.assess_motion(rows))
        self.assertLess(rows[-1]["max_joint_speed_deg_s"], 0.5)
        self.assertGreater(rows[-1]["tcp_position_error_mm"], 5.0)

    def test_stuck_reference_is_not_mistaken_for_arrival(self):
        model, data = ur3e_with_gripper.load_robot_with_gripper()
        plan = move_tcp.prepare_motion(model, data)
        with tempfile.TemporaryDirectory() as folder, contextlib.redirect_stdout(io.StringIO()):
            # Hold the initial angle while the goal remains 30 mm above it.
            with patch.object(move_tcp, "reference_at", return_value=plan.start_arm.copy()):
                rows = move_tcp.run_motion(model, data, plan, trace_path=Path(folder) / "stuck.csv")
            self.assertFalse(move_tcp.assess_motion(rows))
        self.assertGreater(rows[-1]["tcp_position_error_mm"], 29.0)
        self.assertAlmostEqual(data.time, 7.0, places=8)

    def test_obstacle_introduced_during_motion_stops_the_trial(self):
        with tempfile.TemporaryDirectory() as folder, contextlib.redirect_stdout(io.StringIO()):
            # Test-only mocap obstacle. Start it far away so IK and preflight succeed.
            source_path = ur3e_with_gripper.SCENE_PATH
            tree = ET.parse(source_path)
            root = tree.getroot()
            for include in root.findall("include"):
                include.set("file", (source_path.parent / include.get("file")).resolve().as_posix())
            compiler = root.find("compiler")
            compiler.set("meshdir", (source_path.parent / compiler.get("meshdir")).resolve().as_posix())
            body = ET.SubElement(root.find("worldbody"), "body", name="test_obstacle", mocap="true", pos="2 2 2")
            ET.SubElement(body, "geom", name="test_obstacle_geom", type="sphere", size="0.012")
            fixture = Path(folder) / "scene.xml"
            tree.write(fixture, encoding="unicode")
            with patch.object(ur3e_with_gripper, "SCENE_PATH", fixture):
                model, data = ur3e_with_gripper.load_robot_with_gripper()
            plan = move_tcp.prepare_motion(model, data)
            self.assertEqual(data.ncon, 0)
            obstacle = int(model.body("test_obstacle").mocapid[0])
            real_step = mujoco.mj_step
            inserted = False

            def step_with_obstacle(m, d):
                nonlocal inserted
                real_step(m, d)
                if not inserted and d.time >= 2.0 - 1e-9:
                    # Deliberately place an obstacle into the moving gripper for fault injection.
                    # No robot coordinates or contact arrays are overwritten.
                    d.mocap_pos[obstacle] = d.geom("gripper_left_pad1").xpos
                    inserted = True

            trace = Path(folder) / "blocked.csv"
            with patch.object(move_tcp.mujoco, "mj_step", side_effect=step_with_obstacle):
                with self.assertRaisesRegex(RuntimeError, "contact|Contact"):
                    move_tcp.run_motion(model, data, plan, trace_path=trace)
            self.assertTrue(inserted)
            self.assertGreater(data.ncon, 0)  # MuJoCo itself computed the collision.
            self.assertGreater(data.time, 1.5)
            self.assertLess(data.time, 2.01)
            self.assertGreater(trace.stat().st_size, 0)  # Partial measurements survive the abort.


if __name__ == "__main__":
    unittest.main()
