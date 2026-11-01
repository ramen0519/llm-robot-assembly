"""Two-post policy replans with an episode budget and retained physical state."""
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
from failure_report import FailureCode, FailureEvent
from assembly_policy import ScriptedPolicy
from frame_assembly_scene import build_frame_scene
from frame_assembly_skills import FrameSkills
from run_frame_assembly import execute_trial
from workcell_scene import part_name


OPTIONS = dict(product_count=3, mode='single', scenario='first_alignment_offset',
               transfer_obstacle=True, empty_approach_strategy='direct_then_rrt')


class FramePolicyReplanTests(unittest.TestCase):
    def test_two_replans_share_the_episode_and_preserve_installed_parts(self):
        holder = {}
        with ExitStack() as stack, tempfile.TemporaryDirectory() as temporary:
            def observer(skills):
                if holder:
                    self.assertIs(skills.model, holder['model'])
                    self.assertIs(skills.data, holder['data'])
                    self.assertEqual(skills.installed, {'post_1'})
                    self.assertGreater(skills.data.time, 0.)
                    return
                model, data = skills.model, skills.data
                holder.update(model=model, data=data, q=data.qpos.copy(), v=data.qvel.copy())
                equalities, fixed = model.eq_data.copy(), model.body_pos.copy()
                real_step, real_forward = mujoco.mj_step, mujoco.mj_forward
                def unchanged():
                    for actual, expected in ((data.qpos, holder['q']), (data.qvel, holder['v']),
                                             (model.eq_data, equalities), (model.body_pos, fixed)):
                        if not np.array_equal(actual, expected):
                            raise AssertionError('Live state changed outside physics.')
                    self.assertEqual(model.neq, 3)
                    self.assertFalse(np.any(model.body_gravcomp) or np.any(data.xfrc_applied) or np.any(data.qfrc_applied))
                def step(m, d, *args, **kwargs):
                    if d is data:
                        unchanged()
                    real_step(m, d, *args, **kwargs)
                    if d is data:
                        holder.update(q=data.qpos.copy(), v=data.qvel.copy())
                def forward(m, d):
                    if d is data:
                        unchanged()
                    real_forward(m, d)
                stack.enter_context(patch.object(mujoco, 'mj_step', side_effect=step))
                stack.enter_context(patch.object(mujoco, 'mj_forward', side_effect=forward))
            result = execute_trial(**OPTIONS, transport_replan_budget=2, observer=observer, output_root=temporary)
            run = Path(result['run_dir'])
            events = [json.loads(line) for line in (run/'graph_events.jsonl').read_text().splitlines()]
            with (run/'skill_trace.csv').open(newline='', encoding='utf-8') as stream:
                times = np.array([float(r['simulation_time_s']) for r in csv.DictReader(stream)])
        self.assertEqual(result['status'], 'success', result['error'])
        self.assertEqual(result['completed_installations'], ['post_1', 'post_2'])
        self.assertEqual(result['robot_installed_parts'], 2)
        self.assertFalse(result['ten_part_assembly_complete'])
        self.assertEqual((result['transport_replans'], result['transport_replan_budget_remaining']), (2, 0))
        self.assertEqual((result['recovery_budget_remaining'], result['policy_calls'], result['api_calls']), (0, 10, 0))
        self.assertEqual([result['graph_states'][p]['transport_replans'] for p in ('post_1', 'post_2')], [1, 1])
        self.assertEqual([result['graph_states'][p]['recoveries'] for p in ('post_1', 'post_2')], [1, 0])
        for part in ('post_1', 'post_2'):
            state = result['graph_states'][part]
            self.assertTrue(all(state['verification']['checks'].values()))
            self.assertEqual(len(result['policy_replan_attempts'][part]), 1)
            self.assertTrue(result['policy_replan_attempts'][part][0]['transport_completed'])
            actions = [r['action']['skill'] for r in events if r['active_part_id'] == part and r['node'] == 'decide']
            self.assertEqual(actions.count('replan_insert'), 1)
        previous = result['part_metrics']['post_2']['previous_parts']['post_1']
        self.assertLess(previous['maximum_position_error_m'], .001)
        self.assertLess(previous['maximum_orientation_error_rad'], np.deg2rad(1.))
        remaining = [r['task_transport_replan_budget_remaining'] for r in events]
        self.assertEqual(remaining, sorted(remaining, reverse=True))
        self.assertEqual(set(remaining), {0, 1, 2})
        self.assertTrue(all(r['task_transport_replans'] + r['task_transport_replan_budget_remaining'] == 2 for r in events))
        self.assertTrue(np.all(np.diff(times) >= 0.))
        self.assertLessEqual(np.diff(times).max(), .00200001)

    def test_global_budget_does_not_reset_for_the_second_post(self):
        holder = {}
        def observer(skills):
            if skills.active != 'post_2':
                return
            holder['skills'] = skills
            original = skills.stage
            def stage(name):
                original(name)
                if name == 'insert_transfer':
                    holder.update(time=float(skills.data.time), q=skills.data.qpos.copy(), v=skills.data.qvel.copy())
            skills.stage = stage
        with tempfile.TemporaryDirectory() as temporary:
            result = execute_trial(**OPTIONS, transport_replan_budget=1, observer=observer, output_root=temporary)
            events = [json.loads(line) for line in (Path(result['run_dir'])/'graph_events.jsonl').read_text().splitlines()]
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['completed_installations'], ['post_1'])
        self.assertEqual(result['robot_installed_parts'], 1)
        self.assertEqual((result['transport_replans'], result['transport_replan_budget_remaining']), (1, 0))
        self.assertEqual(result['graph_states']['post_2']['transport_replans'], 0)
        self.assertEqual(result['policy_replan_attempts']['post_2'], [])
        self.assertEqual(result['failures'][-1]['active_part_id'], 'post_2')
        self.assertEqual(result['failures'][-1]['phase'], 'insert_transfer/preflight')
        self.assertEqual(result['failures'][-1]['failure_code'], 'PLANNED_CONTACT')
        second = [r for r in events if r['active_part_id'] == 'post_2']
        self.assertTrue(all(r['task_transport_replan_budget_remaining'] == 0 for r in second))
        self.assertEqual(result['simulated_seconds'], holder['time'])
        np.testing.assert_array_equal(holder['skills'].data.qpos, holder['q'])
        np.testing.assert_array_equal(holder['skills'].data.qvel, holder['v'])
        self.assertNotIn('insert_lower', [p['stage'] for p in holder['skills'].motion.plans])
        self.assertFalse(result['ten_part_assembly_complete'])

    def test_other_parts_and_mating_declarations_invalidate_rejected_plan(self):
        model, data, registry = build_frame_scene(3, transfer_obstacle=True)
        skills = FrameSkills(model, data, 'post_1', registry, set())
        skills.settle()
        skills.pick('post_1')
        with self.assertRaises(FailureEvent) as blocked:
            skills.insert('post_1', skills.config['target']['id'])
        self.assertEqual(blocked.exception.code, FailureCode.PLANNED_CONTACT)
        self.assertTrue(skills.insert_replan_available(blocked.exception))
        skills.installed.add('post_2')
        self.assertFalse(skills.insert_replan_available())
        skills.installed.remove('post_2')
        # Test-only declaration/pose changes are restored without executing motion.
        registry['post_2']['mating_ids'].append('post_1')
        self.assertFalse(skills.insert_replan_available())
        registry['post_2']['mating_ids'].pop()
        joint = model.joint(part_name(registry['post_2']['config']) + '_free').id
        address = model.jnt_qposadr[joint]
        original = float(data.qpos[address])
        data.qpos[address] += .001
        self.assertFalse(skills.insert_replan_available())
        data.qpos[address] = original
        self.assertTrue(skills.insert_replan_available())

    def test_policy_can_stop_second_part_without_spending_replan(self):
        class StopSecond(ScriptedPolicy):
            def propose(self, context):
                action = super().propose(context)
                if context['part_id'] == 'post_2' and 'replan_insert' in context['allowed_skills']:
                    action.update(skill='stop', reason='Offline policy test: stop with the first installation preserved.')
                return action
        with tempfile.TemporaryDirectory() as temporary:
            result = execute_trial(**(OPTIONS | {'scenario': 'nominal'}), transport_replan_budget=2,
                                   policies={'single': StopSecond()}, output_root=temporary)
        self.assertEqual(result['status'], 'stopped')
        self.assertEqual(result['completed_installations'], ['post_1'])
        self.assertEqual((result['transport_replans'], result['transport_replan_budget_remaining']), (1, 1))
        self.assertEqual(result['graph_states']['post_2']['completed'], ['pick'])
        self.assertEqual(result['policy_replan_attempts']['post_2'], [])
        self.assertEqual(result['robot_installed_parts'], 1)
        self.assertEqual(result['api_calls'], 0)

    def test_scope_and_independent_approach_options(self):
        for changes in ({'transport_replan_budget': True}, {'transport_replan_budget': 3},
                        {'transport_replan_budget': 1, 'product_count': 4},
                        {'transport_replan_budget': 1, 'mode': 'scripted'},
                        {'transport_replan_budget': 1, 'transport_strategy': 'direct_then_rrt'},
                        {'empty_approach_strategy': 'unknown'}, {'policies': {'single': object()}}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                execute_trial(**(dict(product_count=3, mode='single') | changes))
        model, data, registry = build_frame_scene(2)
        inherited = FrameSkills(model, data, 'post_1', registry, set(), transport_strategy='direct_then_rrt')
        self.assertEqual(inherited.empty_approach_strategy, 'direct_then_rrt')
        independent = FrameSkills(model, data, 'post_1', registry, set(), empty_approach_strategy='direct_then_rrt')
        self.assertEqual(independent.transport_strategy, 'direct')
        self.assertEqual(independent.empty_approach_strategy, 'direct_then_rrt')
        self.assertEqual(data.time, 0.)


if __name__ == '__main__':
    unittest.main()
