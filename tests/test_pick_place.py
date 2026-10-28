"""Real MuJoCo pick/place and failure rejection. No mock assembly successes."""

import copy
import json
import math
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import mujoco
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from assembly_skills import AssemblySkills
from failure_report import FailureCode, FailureEvent, capture_observation
from run_pick_place import execute_trial
from view_tcp import read_tcp_pose
import workcell_scene
from workcell_scene import build_workcell, load_config, part_name


class PickPlaceTests(unittest.TestCase):
    def test_default_physical_pick_place_and_no_live_state_overwrite(self):
        config = load_config()
        model, data = build_workcell(config)
        skills = AssemblySkills(model, data, config)
        real_step, real_forward = mujoco.mj_step, mujoco.mj_forward
        last_q, last_v = data.qpos.copy(), data.qvel.copy()
        def guarded_step(m, d, *args, **kwargs):
            nonlocal last_q, last_v
            if d is data:
                np.testing.assert_array_equal(d.qpos, last_q)
                np.testing.assert_array_equal(d.qvel, last_v)
            real_step(m, d, *args, **kwargs)
            if d is data:
                last_q, last_v = d.qpos.copy(), d.qvel.copy()
        def guarded_forward(m, d):
            if d is data:
                np.testing.assert_array_equal(d.qpos, last_q)
                np.testing.assert_array_equal(d.qvel, last_v)
            real_forward(m, d)
        with patch.object(mujoco, "mj_step", side_effect=guarded_step), patch.object(mujoco, "mj_forward", side_effect=guarded_forward):
            skills.settle()
            pick = skills.pick(config["part"]["id"])
            self.assertGreater(pick["minimum_final_clearance_m"], 0.05)
            self.assertLess(pick["maximum_final_slip_m"], 0.005)
            before_place_time = data.time
            place = skills.place(config["part"]["id"], config["target"]["id"])
        self.assertGreater(data.time, before_place_time)
        self.assertTrue(all(place["checks"].values()))
        self.assertLess(place["maximum_final_position_error_m"], 0.003)
        self.assertEqual((model.nq, model.nv, model.neq, model.nu), (21, 20, 3, 7))
        self.assertEqual(model.jnt_type[model.body(part_name(config)).jntadr[0]], mujoco.mjtJoint.mjJNT_FREE)
        np.testing.assert_array_equal(data.xfrc_applied, 0)
        np.testing.assert_array_equal(data.qfrc_applied, 0)
        np.testing.assert_array_equal(model.body_gravcomp, 0)
        observation = capture_observation(model, data)
        self.assertNotIn("qpos_rad", observation)
        self.assertEqual(len(observation["qpos"]), 21)
        self.assertEqual(observation["joint_layout"][-1]["dof_address"], 14)

    def test_transformed_workcell_and_rotated_target(self):
        config = load_config()
        config["table"].update(top_center_world_m=[0.3, -0.2, 0.95], yaw_deg=25.)
        config["target"]["yaw_deg"] = 20.
        original = copy.deepcopy(config)
        model, data = build_workcell(config)
        skills = AssemblySkills(model, data, config)
        skills.settle()
        skills.pick(config["part"]["id"])
        result = skills.place(config["part"]["id"], config["target"]["id"])
        self.assertTrue(all(result["checks"].values()))
        self.assertEqual(config, original)
        self.assertLess(result["maximum_final_orientation_error_rad"], math.radians(3))

    def test_current_state_planning_is_pure_stale_plan_rejected_and_collision_reported(self):
        config = load_config()
        model, data = build_workcell(config)
        skills = AssemblySkills(model, data, config)
        skills.settle()
        motion = skills.motion
        p, r = read_tcp_pose(data)
        q, v, control, sim_time = data.qpos.copy(), data.qvel.copy(), data.ctrl.copy(), data.time
        plan = motion.plan(p + [0, 0, 0.02], r, allow_support=True, allow_grasp=False)
        np.testing.assert_array_equal(q, data.qpos)
        np.testing.assert_array_equal(v, data.qvel)
        np.testing.assert_array_equal(control, data.ctrl)
        self.assertEqual(sim_time, data.time)
        motion.hold(0.01, allow_support=True, allow_grasp=False)
        now = data.time
        with self.assertRaises(FailureEvent) as caught:
            motion.execute(plan, allow_support=True, allow_grasp=False)
        self.assertEqual(caught.exception.code, FailureCode.INPUT_OR_PRECONDITION_INVALID)
        self.assertEqual(data.time, now)
        with self.assertRaises(FailureEvent) as caught:
            motion.plan([p[0], p[1], config["table"]["top_center_world_m"][2]], r,
                        allow_support=True, allow_grasp=False)
        self.assertEqual(caught.exception.code, FailureCode.PLANNED_CONTACT)
        self.assertIsNotNone(caught.exception.candidate)
        self.assertIsNone(caught.exception.candidate["simulation_time_s"])
        self.assertEqual(data.time, now)

    def test_carried_part_is_included_in_nominal_path_collision_checks(self):
        config = load_config()
        original_builder = workcell_scene.build_robot_spec
        def with_obstacle():
            spec = original_builder()
            body = spec.worldbody.add_body(name="test_carried_obstacle", mocap=True, pos=[2, 2, 2])
            body.add_geom(name="test_carried_obstacle_geom", type=mujoco.mjtGeom.mjGEOM_SPHERE,
                          size=[0.002], rgba=[1, 0, 0, 1])
            return spec
        with patch.object(workcell_scene, "build_robot_spec", side_effect=with_obstacle):
            model, data = build_workcell(config)
        skills = AssemblySkills(model, data, config)
        skills.settle()
        skills.pick(config["part"]["id"])
        p, r = read_tcp_pose(data)
        obstacle_pos = data.body(part_name(config)).xpos.copy() + [0.06, 0, -0.019]
        data.mocap_pos[model.body("test_carried_obstacle").mocapid[0]] = obstacle_pos
        mujoco.mj_forward(model, data)
        q, sim_time = data.qpos.copy(), data.time
        with self.assertRaises(FailureEvent) as caught:
            skills.motion.plan(p + [0.06, 0, 0], r, allow_support=False, allow_grasp=True)
        self.assertEqual(caught.exception.code, FailureCode.PLANNED_CONTACT)
        pairs = [{g["geom_name"] for g in c["geoms"]} for c in caught.exception.candidate["contacts"]]
        self.assertIn({"test_carried_obstacle_geom", part_name(config) + "_geom"}, pairs)
        np.testing.assert_array_equal(data.qpos, q)
        self.assertEqual(data.time, sim_time)
        # A physical actuator fault after a successful pick must be detected as lost grasp.
        original_apply = skills.motion.controller.apply
        def open_fault(d, reference, gripper_command=0.):
            original_apply(d, reference, 0.)
        skills.motion.controller.apply = open_fault
        with self.assertRaises(FailureEvent) as lost:
            skills.motion.hold(2., allow_support=False, allow_grasp=True)
        self.assertEqual(lost.exception.code, FailureCode.PART_LOST)

    def test_stuck_open_gripper_records_failure_without_lifting_or_retrying(self):
        holder = {}
        def fault(stage, skills):
            if stage == "pick_close":
                original_apply = skills.motion.controller.apply
                def stuck_open(data, reference, gripper_command=0.):
                    original_apply(data, reference, 0.)
                skills.motion.controller.apply = stuck_open
                holder["skills"] = skills
        with tempfile.TemporaryDirectory() as temp:
            result, run_dir = execute_trial(through=13, output_root=temp, on_stage=fault)
            self.assertEqual(result["status"], "failed")
            report = json.loads((run_dir / "failure_report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["failure_code"], "GRASP_NOT_CONFIRMED")
            self.assertEqual(report["phase"], "pick_close")
            self.assertEqual(report["response"], "stop_without_retry")
            self.assertIn("workcell", report["actual"])
            self.assertLessEqual(report["last_valid_observation"]["simulation_time_s"], report["simulation_time_s"])
            self.assertEqual(holder["skills"].data.time, report["simulation_time_s"])
            self.assertNotIn("pick_lift", [p["stage"] for p in result["plans"]])
            self.assertEqual([r["skill"] for r in result["completed_skills"]], ["approach/v1"])

    def test_runtime_contact_report_preserves_fault_state_and_last_valid_sample(self):
        original_builder, original_step = workcell_scene.build_robot_spec, mujoco.mj_step
        holder = {}
        def with_obstacle():
            spec = original_builder()
            body = spec.worldbody.add_body(name="test_runtime_obstacle", mocap=True, pos=[2, 2, 2])
            body.add_geom(name="test_runtime_obstacle_geom", type=mujoco.mjtGeom.mjGEOM_SPHERE,
                          size=[0.008], rgba=[1, 0, 0, 1])
            return spec
        def remember(stage, skills):
            holder["skills"] = skills
        def inject(m, d):
            original_step(m, d)
            if d.time >= 4.0 - 1e-9:
                mocap_id = m.body("test_runtime_obstacle").mocapid[0]
                d.mocap_pos[mocap_id] = d.geom("gripper_left_pad1").xpos
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(workcell_scene, "build_robot_spec", side_effect=with_obstacle), patch.object(mujoco, "mj_step", side_effect=inject):
                result, run_dir = execute_trial(through=12, output_root=temp, on_stage=remember)
            self.assertEqual(result["status"], "failed")
            report = json.loads((run_dir / "failure_report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["failure_code"], "UNEXPECTED_CONTACT")
            self.assertAlmostEqual(report["simulation_time_s"], 4.0)
            self.assertAlmostEqual(report["last_valid_observation"]["simulation_time_s"], 3.998)
            self.assertEqual(holder["skills"].data.time, report["simulation_time_s"])
            self.assertIsNone(report["candidate"])
            self.assertTrue(any("test_runtime_obstacle_geom" in [g["geom_name"] for g in c["geoms"]]
                                for c in report["actual"]["contacts"]))

    def test_stationary_floating_part_cannot_pass_placement(self):
        config = load_config()
        model, data = build_workcell(config)
        model.opt.gravity[:] = 0  # Physical negative fixture: no table contact despite zero velocity.
        skills = AssemblySkills(model, data, config)
        rows = skills.motion.hold(1., allow_support=True, allow_grasp=False)
        self.assertFalse(any(row["supported"] for row in rows))
        with self.assertRaises(FailureEvent) as caught:
            skills.verifier.verify_placement(rows)
        self.assertEqual(caught.exception.code, FailureCode.PLACEMENT_NOT_CONFIRMED)
        self.assertFalse(caught.exception.violations[0]["checks"]["table_support"])


if __name__ == "__main__":
    unittest.main()
