"""Real first-joint assembly with optional free-space transport fallback."""
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
from experience_memory import condition_fingerprint
from failure_report import json_safe
from insertion_scene import build_insertion_scene
from joint_path_planner import plan_tcp_path
from run_assembly_graph import execute_trial


class InsertionDetourTests(unittest.TestCase):
    def test_rrt_transport_to_real_insertion_without_artificial_attachment(self):
        calls = []
        def watch_plan(motion, *args, **kwargs):
            calls.append((motion.stage, motion.verifier.mating_enabled))
            return plan_tcp_path(motion, *args, **kwargs)
        with ExitStack() as stack, tempfile.TemporaryDirectory() as temporary:
            def observer(skills):
                model, data = skills.model, skills.data
                real_step, real_forward = mujoco.mj_step, mujoco.mj_forward
                last_q, last_v = data.qpos.copy(), data.qvel.copy()
                equalities, fixed_positions = model.eq_data.copy(), model.body_pos.copy()
                def unchanged():
                    np.testing.assert_array_equal(data.qpos, last_q)
                    np.testing.assert_array_equal(data.qvel, last_v)
                    np.testing.assert_array_equal(model.eq_data, equalities)
                    np.testing.assert_array_equal(model.body_pos, fixed_positions)
                    self.assertEqual(model.neq, 3)
                    np.testing.assert_array_equal(model.body_gravcomp, 0)
                    np.testing.assert_array_equal(data.xfrc_applied, 0)
                    np.testing.assert_array_equal(data.qfrc_applied, 0)
                def step(m, d, *args, **kwargs):
                    nonlocal last_q, last_v
                    if d is data:
                        unchanged()
                    real_step(m, d, *args, **kwargs)
                    if d is data:
                        last_q, last_v = d.qpos.copy(), d.qvel.copy()
                def forward(m, d):
                    if d is data:
                        unchanged()
                    real_forward(m, d)
                stack.enter_context(patch.object(mujoco, 'mj_step', side_effect=step))
                stack.enter_context(patch.object(mujoco, 'mj_forward', side_effect=forward))
            stack.enter_context(patch('loaded_transport.plan_tcp_path', side_effect=watch_plan))
            result = execute_trial(mode='single', transfer_obstacle=True, transport_strategy='direct_then_rrt',
                                   observer=observer, output_root=temporary)
            self.assertEqual(result['status'], 'success', result['error'])
            self.assertEqual(result['graph_state']['completed'], ['pick', 'insert'])
            self.assertTrue(all(result['graph_state']['verification']['checks'].values()))
            self.assertEqual(result['robot_installed_parts'], 1)
            self.assertEqual(result['api_calls'], 0)
            self.assertEqual(result['policy_kind'], 'scripted')
            self.assertEqual(result['graph_state']['recoveries'], 0)
            self.assertEqual(calls, [('insert_transfer', False)])
            attempt = result['transport_attempts'][0]
            self.assertEqual(attempt['status'], 'executed')
            self.assertTrue(attempt['planning']['fallback_used'])
            self.assertEqual(attempt['planning']['planner'], 'RRT-Connect')
            self.assertLess(result['transfer_metrics']['maximum_slip_m'], .005)
            self.assertEqual(len(result['transport_preflight_failures']), 1)
            report = result['transport_preflight_failures'][0]
            self.assertEqual(report['failure_code'], 'PLANNED_CONTACT')
            self.assertAlmostEqual(report['simulation_time_s'], attempt['start_time_s'])
            self.assertIsNone(report['candidate']['simulation_time_s'])
            with (Path(result['run_dir']) / 'skill_trace.csv').open(newline='', encoding='utf-8') as stream:
                rows = list(csv.DictReader(stream))
            times = np.array([float(r['simulation_time_s']) for r in rows])
            self.assertTrue(np.all(np.diff(times) >= 0.))
            self.assertLessEqual(float(np.diff(times).max()), .00200001)
            transfer = [r for r in rows if r['stage'] == 'insert_transfer']
            self.assertTrue(all(r['left_contact'] == r['right_contact'] == 'True' for r in transfer))
            self.assertTrue(all(r['supported'] == 'False' for r in transfer))
            self.assertGreater(min(float(r['part_bottom_clearance_m']) for r in transfer), .05)

    def test_same_obstacle_without_fallback_stops_before_lowering(self):
        holder, stages = {}, []
        def observer(skills):
            holder['skills'] = skills
            original = skills.stage
            def stage(name):
                stages.append(name)
                original(name)
                if name == 'insert_transfer':
                    holder['time'] = float(skills.data.time)
                    holder['qpos'], holder['qvel'] = skills.data.qpos.copy(), skills.data.qvel.copy()
            skills.stage = stage
        with tempfile.TemporaryDirectory() as temporary:
            result = execute_trial(mode='single', transfer_obstacle=True, transport_strategy='direct',
                                   observer=observer, output_root=temporary)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['graph_state']['failures'][0]['failure_code'], 'PLANNED_CONTACT')
        self.assertEqual(result['graph_state']['completed'], ['pick'])
        self.assertEqual(result['robot_installed_parts'], 0)
        self.assertNotIn('insert_lower', stages)
        self.assertEqual(result['simulated_seconds'], holder['time'])
        np.testing.assert_array_equal(holder['skills'].data.qpos, holder['qpos'])
        np.testing.assert_array_equal(holder['skills'].data.qvel, holder['qvel'])

    def test_detour_then_alignment_recovery_in_same_episode(self):
        with tempfile.TemporaryDirectory() as temporary:
            result = execute_trial(mode='single', transfer_obstacle=True, transport_strategy='direct_then_rrt',
                                   scenario='alignment_offset', output_root=temporary)
            events = [json.loads(line) for line in (Path(result['run_dir'])/'graph_events.jsonl').read_text().splitlines()]
        self.assertEqual(result['status'], 'success', result['error'])
        self.assertEqual(result['graph_state']['recoveries'], 1)
        self.assertEqual(result['graph_state']['failures'][0]['failure_code'], 'ALIGNMENT_OUT_OF_TOLERANCE')
        self.assertEqual([e['action']['skill'] for e in events if e['node'] == 'decide'],
                         ['pick', 'insert', 'recover_alignment', 'insert', 'finish'])
        self.assertEqual(len(result['transport_preflight_failures']), 1)
        self.assertEqual([a['planning']['fallback_used'] for a in result['transport_attempts']], [True, False])
        recovery = next(r for r in result['results'] if r['skill'] == 'recover_alignment/v1')
        self.assertFalse(recovery['reset_used'])
        times = [e['simulation_time_s'] for e in events]
        self.assertEqual(times, sorted(times))
        self.assertTrue(all(result['graph_state']['verification']['checks'].values()))

    def test_obstructed_lowering_stops_instead_of_trying_a_contact_detour(self):
        stages, calls = [], []
        def watch_plan(motion, *args, **kwargs):
            calls.append(motion.stage)
            return plan_tcp_path(motion, *args, **kwargs)
        def observer(skills):
            original = skills.stage
            def stage(name):
                original(name)
                stages.append(name)
                if name == 'insert_lower':
                    # Explicit test fault: move only the obstacle underneath the held
                    # post after transport. No robot/product state is overwritten.
                    obstacle = skills.model.body('assembly_transfer_obstacle').id
                    skills.model.body_pos[obstacle] = skills.verifier.target_position + [0., 0., -.025]
                    mujoco.mj_forward(skills.model, skills.data)
            skills.stage = stage
        with tempfile.TemporaryDirectory() as temporary, patch('loaded_transport.plan_tcp_path', side_effect=watch_plan):
            result = execute_trial(mode='single', transfer_obstacle=True, transport_strategy='direct_then_rrt',
                                   observer=observer, output_root=temporary)
        self.assertEqual(result['status'], 'failed')
        report = result['graph_state']['failures'][-1]
        self.assertEqual(report['failure_code'], 'PLANNED_CONTACT')
        self.assertEqual(report['phase'], 'insert_lower/preflight')
        self.assertEqual(calls, ['insert_transfer'])
        self.assertNotIn('insert_release', stages)
        self.assertEqual(result['robot_installed_parts'], 0)

    def test_clear_scene_with_detour_enabled_keeps_the_direct_path(self):
        with tempfile.TemporaryDirectory() as temporary, patch('loaded_transport.plan_tcp_path', side_effect=AssertionError('Unneeded search')):
            result = execute_trial(mode='single', transport_strategy='direct_then_rrt', output_root=temporary)
        self.assertEqual(result['status'], 'success', result['error'])
        self.assertFalse(result['transport_attempts'][0]['planning']['fallback_used'])
        self.assertEqual(result['transport_attempts'][0]['planning']['planner'], 'checked_cartesian_waypoints')

    def test_obstacle_conditions_and_recipe_scope_are_not_silently_reused(self):
        fingerprints = []
        for obstacle in (False, True):
            model, data, config, joint = build_insertion_scene(transfer_obstacle=obstacle)
            fingerprints.append(condition_fingerprint(json_safe({'config': config, 'joint': joint,
                'environment': 'simulation', 'observation_source': 'sim_ground_truth', 'simulator_version': mujoco.__version__})))
            self.assertEqual(data.time, 0.)
            self.assertEqual('transport_test_obstacle' in joint, obstacle)
        self.assertNotEqual(*fingerprints)
        with self.assertRaises(ValueError):
            execute_trial(recipe={}, transport_strategy='direct_then_rrt')
        with self.assertRaises(ValueError):
            build_insertion_scene(transfer_obstacle='true')


if __name__ == '__main__':
    unittest.main()
