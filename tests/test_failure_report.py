"""Validate failure evidence with real MuJoCo motion and explicitly injected faults."""

import contextlib
import csv
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

import mujoco
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import failure_report
from failure_report import FailureCode, FailureEvent, TrialRecorder
import move_tcp
import ur3e_with_gripper


def load_obstacle_scene(folder):
    source = ur3e_with_gripper.SCENE_PATH
    tree = ET.parse(source)
    root = tree.getroot()
    for include in root.findall("include"):
        include.set("file", (source.parent / include.get("file")).resolve().as_posix())
    compiler = root.find("compiler")
    compiler.set("meshdir", (source.parent / compiler.get("meshdir")).resolve().as_posix())
    body = ET.SubElement(root.find("worldbody"), "body", name="test_obstacle", mocap="true", pos="2 2 2")
    ET.SubElement(body, "geom", name="test_obstacle_geom", type="sphere", size="0.012")
    path = Path(folder) / "scene.xml"
    tree.write(path, encoding="unicode")
    with patch.object(ur3e_with_gripper, "SCENE_PATH", path):
        return ur3e_with_gripper.load_robot_with_gripper()


def read_report(result):
    return json.loads(result.failure_path.read_text(encoding="utf-8"))


class FailureReportTests(unittest.TestCase):
    def test_missed_target_and_success_use_separate_runs(self):
        with tempfile.TemporaryDirectory() as folder, contextlib.redirect_stdout(io.StringIO()):
            # Change only the feedforward. Real gravity, servos and mj_step still run.
            with patch.object(move_tcp.ArmPositionController, "gravity_torque", return_value=np.zeros(6)):
                failed = move_tcp.execute_trial(output_root=folder)
            report = read_report(failed)
            original = failed.failure_path.read_bytes()
            passed = move_tcp.execute_trial(output_root=folder)
            self.assertEqual(failed.failure_path.read_bytes(), original)
            self.assertFalse((passed.run_dir / "failure_report.json").exists())
            self.assertEqual(json.loads((passed.run_dir / "result.json").read_text())["status"], "success")
        self.assertEqual(failed.status, "failed")
        self.assertEqual(report["failure_code"], "TCP_NOT_REACHED")
        self.assertEqual(report["phase"], "verify")
        self.assertAlmostEqual(report["simulation_time_s"], 7.0)
        self.assertGreater(report["actual"]["goal_error"]["position_mm"], 5)
        np.testing.assert_allclose(report["goal"]["position_m"], [0.130997996, -0.297500496, 0.177550312], atol=1e-8)
        self.assertEqual(report["actual"]["tcp"]["frame"], "world")
        self.assertEqual(len(report["actual"]["tcp"]["rotation_matrix"]), 3)
        self.assertEqual({v["metric"] for v in report["violations"]},
                         {"tcp_position_error_mm", "tcp_orientation_error_deg"})
        self.assertTrue(all(6.5 - 1e-8 <= v["sample_time_s"] <= 7.0 + 1e-8 for v in report["violations"]))
        self.assertEqual(report["cause_hypotheses"], [])  # Test oracle is not an inferred diagnosis.
        self.assertEqual(report["response"], "stop_without_retry")
        self.assertEqual(passed.status, "success")
        self.assertNotEqual(failed.run_dir, passed.run_dir)
        self.assertIsNone(passed.failure_path)
        self.assertGreater(len(original), 100)

    def test_runtime_contact_preserves_failure_and_previous_sample(self):
        with tempfile.TemporaryDirectory() as folder, contextlib.redirect_stdout(io.StringIO()):
            model, data = load_obstacle_scene(folder)
            obstacle = int(model.body("test_obstacle").mocapid[0])
            real_step = mujoco.mj_step
            step_count = 0

            def insert_obstacle(m, d):
                nonlocal step_count
                real_step(m, d)
                step_count += 1
                if step_count == 1000:
                    d.mocap_pos[obstacle] = d.geom("gripper_left_pad1").xpos

            with patch.object(move_tcp, "load_robot_with_gripper", return_value=(model, data)), \
                    patch.object(move_tcp.mujoco, "mj_step", side_effect=insert_obstacle):
                result = move_tcp.execute_trial(output_root=folder)
            report = read_report(result)
            with (result.run_dir / "tcp_motion.csv").open(encoding="utf-8", newline="") as source:
                trace = list(csv.DictReader(source))
            self.assertEqual(json.loads((result.run_dir / "result.json").read_text())["status"], "failed")
        self.assertEqual(step_count, 1000)  # Not one step is executed after detection.
        self.assertAlmostEqual(data.time, 2.0)
        self.assertEqual(report["phase"], "move")
        self.assertEqual(report["failure_code"], "UNEXPECTED_CONTACT")
        self.assertAlmostEqual(report["simulation_time_s"], 2.0)
        self.assertAlmostEqual(report["last_valid_observation"]["simulation_time_s"], 1.998)
        self.assertAlmostEqual(float(trace[-1]["time_s"]), 1.998)
        self.assertEqual(report["last_valid_observation"]["contacts"], [])
        self.assertTrue(report["actual"]["valid"])  # Valid evidence of an unsuccessful action.
        self.assertIsNone(report["candidate"])
        self.assertEqual(len(report["actual"]["contacts"]), data.ncon)
        self.assertTrue(any("test_obstacle_geom" in [g["geom_name"] for g in c["geoms"]]
                            for c in report["actual"]["contacts"]))
        for saved, actual in zip(report["actual"]["contacts"], data.contact):
            self.assertEqual(saved["signed_distance_m"], actual.dist)
            self.assertEqual(saved["penetration_m"], max(0.0, -actual.dist))
            np.testing.assert_array_equal(saved["position_world_m"], actual.pos)
        np.testing.assert_array_equal(report["actual"]["qpos_rad"], data.qpos)
        np.testing.assert_array_equal(report["actual"]["qvel_rad_s"], data.qvel)
        np.testing.assert_array_equal(report["actual"]["ctrl"], data.ctrl)

    def test_preflight_contact_is_candidate_evidence_not_executed_motion(self):
        with tempfile.TemporaryDirectory() as folder, contextlib.redirect_stdout(io.StringIO()):
            model, data = load_obstacle_scene(folder)
            initial = data.qpos.copy()
            obstacle = int(model.body("test_obstacle").mocapid[0])
            real_check = move_tcp.check_candidate
            calls = 0

            def collide_only_calculation_copy(m, candidate, initial_qpos, frozen, **kwargs):
                nonlocal calls
                calls += 1
                if calls == 50:
                    mujoco.mj_forward(m, candidate)
                    candidate.mocap_pos[obstacle] = candidate.geom("gripper_left_pad1").xpos
                return real_check(m, candidate, initial_qpos, frozen, **kwargs)

            with patch.object(move_tcp, "load_robot_with_gripper", return_value=(model, data)), \
                    patch.object(move_tcp, "check_candidate", side_effect=collide_only_calculation_copy), \
                    patch.object(move_tcp.mujoco, "mj_step") as step:
                result = move_tcp.execute_trial(output_root=folder)
            step.assert_not_called()
            report = read_report(result)
        self.assertEqual(report["failure_code"], "PLANNED_CONTACT")
        self.assertEqual(report["phase"], "preflight")
        self.assertEqual(report["actual"]["contacts"], [])
        self.assertGreater(len(report["candidate"]["contacts"]), 0)
        self.assertEqual(report["candidate"]["source"], "preflight_candidate")
        self.assertIsNone(report["candidate"]["simulation_time_s"])
        self.assertEqual(data.time, 0)
        np.testing.assert_array_equal(data.qpos, initial)

    def test_ik_budget_failure_records_candidate_and_starts_no_motion(self):
        real_solve = move_tcp.solve_tcp_pose
        with tempfile.TemporaryDirectory() as folder, contextlib.redirect_stdout(io.StringIO()):
            with patch.object(move_tcp, "solve_tcp_pose", side_effect=lambda *args: real_solve(*args, max_iterations=1)), \
                    patch.object(move_tcp.mujoco, "mj_step") as step:
                result = move_tcp.execute_trial(output_root=folder)
            report = read_report(result)
            step.assert_not_called()
        self.assertEqual(report["failure_code"], "IK_NOT_CONVERGED")
        self.assertEqual(report["phase"], "ik")
        self.assertEqual(report["simulation_time_s"], 0)
        self.assertEqual(report["candidate"]["source"], "ik_candidate")
        self.assertEqual(report["violations"][0]["iterations"], 1)
        self.assertIsNone(report["last_valid_observation"])

    def test_viewer_close_is_cancelled_and_incomplete_trace_is_separate(self):
        class ClosedViewer:
            def is_running(self):
                return False

        real_run = move_tcp.run_motion
        with tempfile.TemporaryDirectory() as folder, contextlib.redirect_stdout(io.StringIO()):
            with patch.object(move_tcp, "run_motion", side_effect=lambda *a, **kw: real_run(*a, viewer=ClosedViewer(), **kw)), \
                    patch.object(move_tcp.mujoco, "mj_step") as step:
                result = move_tcp.execute_trial(output_root=folder)
            report = read_report(result)
            step.assert_not_called()
        self.assertEqual(result.status, "cancelled")
        self.assertEqual(report["status"], "cancelled")
        self.assertEqual(report["failure_code"], "CANCELLED")
        self.assertEqual(report["phase"], "hold")
        with self.assertRaises(FailureEvent) as caught:
            move_tcp.assess_motion([], raise_on_failure=True)
        self.assertEqual(caught.exception.code, FailureCode.INCOMPLETE_RECORD)

    def test_invalid_state_is_null_not_fake_zero_and_snapshot_does_not_step(self):
        model, data = ur3e_with_gripper.load_robot_with_gripper()
        data.qpos[0] = np.nan
        before = data.qpos.copy()
        with tempfile.TemporaryDirectory() as folder, patch.object(mujoco, "mj_step") as step, \
                patch.object(mujoco, "mj_forward") as forward:
            recorder = TrialRecorder(folder)
            path = recorder.fail(FailureEvent(FailureCode.SIMULATION_INVALID, "Non-finite state."), "move", model, data)
            raw = path.read_text(encoding="utf-8")
            report = json.loads(raw, parse_constant=lambda value: self.fail(f"Invalid JSON constant: {value}"))
            step.assert_not_called()
            forward.assert_not_called()
        self.assertFalse(report["actual"]["valid"])
        self.assertIsNone(report["actual"]["qpos_rad"][0])
        self.assertIsNone(report["actual"]["tcp"])
        self.assertIsNone(report["actual"]["contacts"])
        self.assertIn("non_finite_state", report["actual"]["invalid_reasons"][0])
        np.testing.assert_array_equal(data.qpos, before)
        self.assertEqual(data.time, 0)

    def test_storage_failure_does_not_hide_original_error_or_retry(self):
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as folder, contextlib.redirect_stdout(output):
            with patch.object(move_tcp, "prepare_motion", side_effect=FailureEvent(FailureCode.IK_NOT_CONVERGED, "test IK budget")), \
                    patch.object(failure_report, "write_json", side_effect=OSError("test disk full")), \
                    patch.object(move_tcp.mujoco, "mj_step") as step:
                with self.assertRaisesRegex(OSError, "original failure: test IK budget"):
                    move_tcp.execute_trial(output_root=folder)
            step.assert_not_called()
            self.assertEqual(list(Path(folder).rglob("result.json")), [])
        self.assertIn("test disk full", output.getvalue())
        self.assertIn("test IK budget", output.getvalue())


if __name__ == "__main__":
    unittest.main()
