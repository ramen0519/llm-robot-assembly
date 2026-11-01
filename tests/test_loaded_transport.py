"""Real loaded transport, stop conditions and no artificial grasp attachment."""
from contextlib import ExitStack
import csv
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import mujoco
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from assembly_skills import AssemblySkills
from failure_report import FailureCode, FailureEvent
from loaded_transport import plan_loaded_transfer
from run_loaded_detour import build_probe, construct_blocked_transfer, execute_trial
from view_tcp import read_tcp_pose


def physical_state(data):
    return (float(data.time), {name: getattr(data, name).copy()
            for name in ('qpos', 'qvel', 'ctrl', 'mocap_pos', 'mocap_quat')})


class LoadedTransportTests(unittest.TestCase):
    def assert_state_unchanged(self, data, state):
        self.assertEqual(float(data.time), state[0])
        for name, value in state[1].items():
            np.testing.assert_array_equal(getattr(data, name), value)

    def test_physical_detour_and_place_without_overwriting_live_state(self):
        holder = {}
        with ExitStack() as stack, tempfile.TemporaryDirectory() as temporary:
            def observer(skills):
                model, data = skills.model, skills.data
                holder['skills'] = skills
                real_step, real_forward = mujoco.mj_step, mujoco.mj_forward
                last_q, last_v = data.qpos.copy(), data.qvel.copy()
                equality = model.eq_data.copy()

                def verify_unchanged():
                    np.testing.assert_array_equal(data.qpos, last_q)
                    np.testing.assert_array_equal(data.qvel, last_v)
                    np.testing.assert_array_equal(model.eq_data, equality)
                    self.assertEqual(model.neq, 3)  # Existing gripper linkage only.
                    np.testing.assert_array_equal(model.body_gravcomp, 0)
                    np.testing.assert_array_equal(data.xfrc_applied, 0)
                    np.testing.assert_array_equal(data.qfrc_applied, 0)

                def step(m, d, *args, **kwargs):
                    nonlocal last_q, last_v
                    if d is data:
                        verify_unchanged()
                    real_step(m, d, *args, **kwargs)
                    if d is data:
                        last_q, last_v = d.qpos.copy(), d.qvel.copy()

                def forward(m, d):
                    if d is data:
                        verify_unchanged()
                    real_forward(m, d)

                stack.enter_context(patch.object(mujoco, 'mj_step', side_effect=step))
                stack.enter_context(patch.object(mujoco, 'mj_forward', side_effect=forward))

            result = execute_trial(output_root=temporary, observer=observer)
            self.assertEqual(result['status'], 'success', result['error'])
            self.assertEqual(result['planning']['planner'], 'RRT-Connect')
            self.assertTrue(result['planning']['fallback_used'])
            self.assertFalse(result['planning']['physics_executed_during_planning'])
            self.assertEqual(len(result['preflight_failures']), 1)
            self.assertEqual(result['preflight_failures'][0]['failure_code'], 'PLANNED_CONTACT')
            self.assertTrue(result['setup']['robot_only_midpoint_clear'])
            self.assertEqual(result['setup']['blocked_pairs_at_midpoint'],
                             [['loaded_obstacle_geom', 'workcell_part_part_a_geom']])
            self.assertTrue(all(result['placement']['checks'].values()))
            self.assertFalse(result['assembly_success'])
            self.assertEqual(result['robot_installed_parts'], 0)
            self.assertEqual(result['physically_transferred_and_placed_parts'], 1)
            self.assertEqual(result['api_calls'], 0)
            self.assertLess(result['measurements']['maximum_transport_slip_m'], .005)
            with (Path(result['run_dir']) / 'skill_trace.csv').open(newline='', encoding='utf-8') as stream:
                rows = list(csv.DictReader(stream))
            transport = [r for r in rows if r['stage'] == 'transport_transfer']
            self.assertGreater(len(transport), 1000)
            self.assertTrue(all(r['left_contact'] == r['right_contact'] == 'True' for r in transport))
            self.assertTrue(all(r['supported'] == 'False' for r in transport))
            self.assertGreater(min(float(r['part_bottom_clearance_m']) for r in transport), .05)
            times = np.array([float(r['simulation_time_s']) for r in rows])
            self.assertTrue(np.all(np.diff(times) >= 0))
            self.assertLessEqual(float(np.diff(times).max()), .00200001)
            data, model = holder['skills'].data, holder['skills'].model
            part_body = holder['skills'].verifier.part_body
            self.assertEqual(model.jnt_type[model.body(part_body).jntadr[0]], mujoco.mjtJoint.mjJNT_FREE)
            np.testing.assert_array_equal(data.mocap_pos[0], result['setup']['obstacle_center_world_m'])

    def test_direct_only_stops_before_transport_and_keeps_grasp(self):
        holder = {}
        def observer(skills):
            holder['skills'] = skills
            original = skills.stage
            def stage(name):
                original(name)
                if name == 'transport_transfer':
                    holder['start'] = physical_state(skills.data)
            skills.stage = stage
        with tempfile.TemporaryDirectory() as temporary:
            result = execute_trial(strategy='direct', output_root=temporary, observer=observer)
            report = json.loads((Path(result['run_dir']) / 'failure_report.json').read_text(encoding='utf-8'))
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(report['failure_code'], 'PLANNED_CONTACT')
        self.assertIsNotNone(report['candidate'])
        self.assertIsNone(result['placement'])
        self.assertEqual(result['physically_transferred_and_placed_parts'], 0)
        self.assert_state_unchanged(holder['skills'].data, holder['start'])
        skills = holder['skills']
        row = skills.verifier.sample(skills.data, skills.motion.reference, allow_support=False, allow_grasp=True)
        skills.verifier.check_held(skills.data, row, skills.motion.held_pose)
        self.assertTrue(row['left_contact'] and row['right_contact'])

    def test_changed_state_or_exhausted_budget_cannot_trigger_execution(self):
        model, data, config = build_probe()
        skills = AssemblySkills(model, data, config)
        skills.settle()
        skills.pick(config['part']['id'])
        target, rotation, _ = construct_blocked_transfer(skills)
        skills.stage('transport_transfer')
        before = physical_state(data)
        def stale(_):
            data.ctrl[0] += .001  # Deliberate external-state change after preflight.
        with self.assertRaises(FailureEvent) as changed:
            plan_loaded_transfer(skills, target, rotation, on_preflight_failure=stale)
        self.assertEqual(changed.exception.code, FailureCode.INPUT_OR_PRECONDITION_INVALID)
        data.ctrl[:] = before[1]['ctrl']
        self.assert_state_unchanged(data, before)
        with self.assertRaises(FailureEvent) as exhausted:
            plan_loaded_transfer(skills, target, rotation, max_time_s=1e-9)
        self.assertEqual(exhausted.exception.code, FailureCode.MOTION_PATH_NOT_FOUND)
        self.assert_state_unchanged(data, before)
        # Even with a real grasp, detours cannot run during contact insertion.
        skills.stage('insert_lower')
        with self.assertRaises(FailureEvent) as insertion:
            plan_loaded_transfer(skills, target, rotation)
        self.assertEqual(insertion.exception.code, FailureCode.INPUT_OR_PRECONDITION_INVALID)
        self.assert_state_unchanged(data, before)
        skills.stage('insert_transfer')
        skills.verifier.mating_enabled = True
        with self.assertRaises(FailureEvent) as contact:
            plan_loaded_transfer(skills, target, rotation)
        self.assertEqual(contact.exception.code, FailureCode.INPUT_OR_PRECONDITION_INVALID)
        self.assert_state_unchanged(data, before)

    def test_missing_grasp_rejected_without_simulation(self):
        model, data, config = build_probe()
        skills = AssemblySkills(model, data, config)
        skills.stage('transport_transfer')
        target, rotation = read_tcp_pose(data)
        before = physical_state(data)
        with self.assertRaises(FailureEvent) as caught:
            plan_loaded_transfer(skills, target, rotation)
        self.assertEqual(caught.exception.code, FailureCode.INPUT_OR_PRECONDITION_INVALID)
        self.assert_state_unchanged(data, before)

    def test_actual_gripper_open_fault_stops_transport_without_placing(self):
        def observer(skills):
            apply = skills.motion.controller.apply
            def fault(data, reference, gripper_command=0.):
                if skills.motion.stage == 'transport_transfer':
                    gripper_command = 0.
                return apply(data, reference, gripper_command)
            skills.motion.controller.apply = fault
        with tempfile.TemporaryDirectory() as temporary:
            result = execute_trial(output_root=temporary, observer=observer)
            report = json.loads((Path(result['run_dir']) / 'failure_report.json').read_text(encoding='utf-8'))
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(report['failure_code'], 'PART_LOST')
        self.assertEqual(report['phase'], 'transport_transfer')
        self.assertIsNone(result['placement'])
        self.assertEqual(result['physically_transferred_and_placed_parts'], 0)
        self.assertTrue(result['planning']['fallback_used'])


if __name__ == '__main__':
    unittest.main()
