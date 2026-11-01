"""Independent physical success/failure checks for the first robot-assembled joint."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import mujoco
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from failure_report import FailureCode, FailureEvent
from insertion_scene import build_insertion_scene
from insertion_skills import InsertionSkills
from run_insertion import execute_trial


class InsertionTests(unittest.TestCase):
    def build(self):
        model, data, config, joint = build_insertion_scene()
        return InsertionSkills(model, data, config, joint)

    def test_physical_insertion_without_weld_teleport_or_external_force(self):
        skills = self.build()
        model, data = skills.model, skills.data
        last_q, last_v = data.qpos.copy(), data.qvel.copy()
        step, forward = mujoco.mj_step, mujoco.mj_forward
        def guarded_step(m, d, *args, **kwargs):
            nonlocal last_q, last_v
            if d is data:
                np.testing.assert_array_equal(d.qpos, last_q)
                np.testing.assert_array_equal(d.qvel, last_v)
            step(m, d, *args, **kwargs)
            if d is data:
                last_q, last_v = d.qpos.copy(), d.qvel.copy()
        def guarded_forward(m, d):
            if d is data:
                np.testing.assert_array_equal(d.qpos, last_q)
                np.testing.assert_array_equal(d.qvel, last_v)
            forward(m, d)
        with patch.object(mujoco, 'mj_step', side_effect=guarded_step), patch.object(mujoco, 'mj_forward', side_effect=guarded_forward):
            skills.settle()
            skills.pick('post_1')
            result = skills.insert('post_1', 'socket_1')
        self.assertTrue(all(result['checks'].values()))
        self.assertGreater(result['minimum_final_depth_m'], .015)
        self.assertEqual((model.nq, model.neq), (21, 3))
        np.testing.assert_array_equal(data.xfrc_applied, 0)
        np.testing.assert_array_equal(data.qfrc_applied, 0)
        np.testing.assert_array_equal(model.body_gravcomp, 0)

    def test_contact_permission_is_pair_specific_and_stage_gated(self):
        skills = self.build()
        v = skills.verifier
        post_floor = {v.part_geom, v.floor_geom}
        self.assertFalse(v.contact_policy(post_floor, allow_support=True, allow_grasp=True)[0])
        v.mating_enabled = True
        self.assertTrue(v.contact_policy(post_floor, allow_support=True, allow_grasp=True)[0])
        self.assertFalse(v.contact_policy({next(iter(v.pads)), v.floor_geom}, allow_support=True, allow_grasp=True)[0])

    def test_on_table_is_not_assembled_even_if_target_position_is_misconfigured(self):
        skills = self.build()
        skills.settle()
        skills.verifier.target_position = skills.data.body(skills.verifier.part_body).xpos.copy()
        rows = skills.motion.hold(1., allow_support=True, allow_grasp=False)
        with self.assertRaises(FailureEvent) as caught:
            skills.verifier.verify_insertion(rows)
        self.assertEqual(caught.exception.code, FailureCode.PLACEMENT_NOT_CONFIRMED)
        self.assertFalse(caught.exception.violations[0]['checks']['socket_floor_support'])

    def test_empty_verification_window_is_rejected(self):
        with self.assertRaises(FailureEvent):
            self.build().verifier.verify_insertion([])

    def test_gripper_failure_records_measured_state_and_does_not_insert(self):
        stages = []
        def fault(stage, skills):
            stages.append(stage)
            if stage == 'pick_close':
                actuator = skills.model.actuator('gripper_fingers_actuator').id
                skills.model.actuator_ctrlrange[actuator] = [0., 0.]
        with tempfile.TemporaryDirectory() as folder:
            result = execute_trial(output_root=folder, on_stage=fault)
            report = json.loads((Path(result['run_dir']) / 'failure_report.json').read_text())
        self.assertEqual(result['status'], 'failed')
        self.assertNotIn('insert_transfer', stages)
        self.assertEqual(report['failure_code'], 'GRASP_NOT_CONFIRMED')
        self.assertEqual(report['validation_profile'], 'post_socket_insertion/v1')
        self.assertIsNotNone(report['actual']['contacts'])
        self.assertEqual(result['robot_installed_parts'], 0)


if __name__ == '__main__':
    unittest.main()
