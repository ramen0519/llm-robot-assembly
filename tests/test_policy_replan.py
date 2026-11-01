"""Policy-selected replanning: real physics, local guards, and offline tool contracts."""
from contextlib import ExitStack
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import mujoco
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from assembly_policy import ScriptedPolicy, allowed_skills, validate_proposal
from failure_report import FailureCode, FailureEvent
from openai_policy import build_request, parse_proposal
from run_assembly_graph import execute_trial


class RecordingPolicy(ScriptedPolicy):
    def __init__(self, stop_at_replan=False):
        super().__init__()
        self.contexts, self.stop_at_replan = [], stop_at_replan

    def propose(self, context):
        self.contexts.append(deepcopy(context))
        action = super().propose(context)
        if self.stop_at_replan and 'replan_insert' in context['allowed_skills']:
            action.update(skill='stop', reason='Offline control: decline the offered replan.')
        return action


class PolicyReplanTests(unittest.TestCase):
    def test_replan_and_alignment_recovery_preserve_intent_and_physics(self):
        policy = RecordingPolicy()
        with ExitStack() as stack, tempfile.TemporaryDirectory() as temporary:
            def observer(skills):
                model, data = skills.model, skills.data
                real_step, real_forward = mujoco.mj_step, mujoco.mj_forward
                last_q, last_v = data.qpos.copy(), data.qvel.copy()
                equalities, fixed = model.eq_data.copy(), model.body_pos.copy()
                def unchanged():
                    for actual, expected in ((data.qpos, last_q), (data.qvel, last_v),
                                             (model.eq_data, equalities), (model.body_pos, fixed)):
                        if not np.array_equal(actual, expected):
                            raise AssertionError('Live state changed outside physics.')
                    self.assertFalse(np.any(model.body_gravcomp) or np.any(data.xfrc_applied) or np.any(data.qfrc_applied))
                    self.assertEqual(model.neq, 3)  # Gripper equalities only.
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
            result = execute_trial(mode='single', transfer_obstacle=True, transport_replan_budget=1,
                scenario='alignment_offset', policies={'single': policy}, observer=observer, output_root=temporary)
            events = [json.loads(line) for line in (Path(result['run_dir'])/'graph_events.jsonl').read_text().splitlines()]
        self.assertEqual(result['status'], 'success', result['error'])
        state = result['graph_state']
        self.assertEqual((state['transport_replans'], state['recoveries'], state['policy_calls']), (1, 1, 6))
        self.assertEqual([e['action']['skill'] for e in events if e['node'] == 'decide'],
                         ['pick', 'insert', 'replan_insert', 'recover_alignment', 'insert', 'finish'])
        self.assertEqual([f['failure_code'] for f in state['failures']],
                         ['PLANNED_CONTACT', 'ALIGNMENT_OUT_OF_TOLERANCE'])
        direct, replanned = policy.contexts[1:3]
        self.assertGreater(replanned['observation_revision'], direct['observation_revision'])
        self.assertEqual(replanned['observation']['simulation_time_s'], direct['observation']['simulation_time_s'])
        self.assertEqual(replanned['allowed_skills'], ['replan_insert', 'stop'])
        evidence = replanned['transport_replan']
        self.assertFalse(evidence['transport_command_executed'])
        self.assertTrue(evidence['same_state_confirmed'])
        self.assertEqual(evidence['alignment_offset_m'], [.006, 0.])
        self.assertEqual(result['policy_replan_attempts'][0]['status'], 'failed')
        self.assertTrue(result['policy_replan_attempts'][0]['transport_executed'])
        self.assertEqual(result['transport_attempts'][0]['planning']['planner'], 'RRT-Connect')
        self.assertTrue(all(state['verification']['checks'].values()))
        self.assertEqual((result['robot_installed_parts'], result['api_calls']), (1, 0))

    def test_successful_replan_is_verified_as_one_insertion(self):
        with tempfile.TemporaryDirectory() as temporary:
            result = execute_trial(mode='single', transfer_obstacle=True, transport_replan_budget=1, output_root=temporary)
        self.assertEqual(result['status'], 'success', result['error'])
        state = result['graph_state']
        self.assertEqual(state['completed'], ['pick', 'insert'])
        self.assertEqual((state['transport_replans'], state['recoveries'], state['policy_calls']), (1, 0, 4))
        self.assertIsNone(state['pending_failure'])
        self.assertEqual(result['policy_replan_attempts'][0]['status'], 'success')
        self.assertTrue(all(state['verification']['checks'].values()))

    def test_stop_and_stale_records_never_execute_or_replay(self):
        policy, holder = RecordingPolicy(stop_at_replan=True), {}
        with tempfile.TemporaryDirectory() as temporary:
            result = execute_trial(mode='single', transfer_obstacle=True, transport_replan_budget=1,
                policies={'single': policy}, observer=lambda s: holder.update(skills=s), output_root=temporary)
        self.assertEqual(result['status'], 'stopped')
        self.assertEqual(result['robot_installed_parts'], 0)
        self.assertEqual(result['graph_state']['transport_replans'], 0)
        self.assertEqual(result['transport_attempts'], [])
        skills = holder['skills']
        record = skills._rejected_insert
        self.assertTrue(record.available(skills, record.error))
        self.assertFalse(record.available(skills, FailureEvent(FailureCode.PLANNED_CONTACT, 'Forged report')))
        self.assertEqual(skills.data.time, record.before['time'])
        self.assertNotIn('insert_lower', [p['stage'] for p in skills.motion.plans])
        # Deliberate local invalidation tests, never counted as physical successes.
        changes = [(skills.data, 'ctrl'), (skills.model, 'geom_pos'),
                   (skills.model, 'geom_size'), (skills.motion, 'reference'),
                   (skills.verifier, 'target_position'), (skills.verifier, 'target_rotation')]
        with patch.object(skills.motion, 'execute', side_effect=AssertionError('Must not execute')):
            for owner, field in changes:
                with self.subTest(field=field):
                    array = getattr(owner, field)
                    original = array.copy()
                    array.flat[0] += .01
                    skills._rejected_insert = record
                    try:
                        self.assertFalse(skills.insert_replan_available())
                        with self.assertRaises(FailureEvent):
                            skills.replan_insert(record.intent['part_id'], record.intent['target_id'])
                        self.assertIsNone(skills._rejected_insert)
                    finally:
                        array[:] = original
            for owner, field, value in [(skills.data, 'time', skills.data.time + .002),
                                        (skills.motion, 'held_pose', None),
                                        (skills.verifier, 'mating_enabled', True),
                                        (skills.motion, 'stage', 'insert_lower')]:
                with self.subTest(field=field):
                    original = getattr(owner, field)
                    setattr(owner, field, value)
                    try:
                        self.assertFalse(record.available(skills))
                    finally:
                        setattr(owner, field, original)
            with self.assertRaises(FailureEvent):
                skills.replan_insert(record.intent['part_id'], record.intent['target_id'])

    def test_failed_planner_spends_budget_and_does_not_loop(self):
        # Fault injection tests stopping behavior; not evidence of planner success.
        failure = FailureEvent(FailureCode.MOTION_PATH_NOT_FOUND, 'Injected exhausted search budget.')
        with tempfile.TemporaryDirectory() as temporary, patch('loaded_transport.plan_tcp_path', side_effect=failure) as search:
            result = execute_trial(mode='single', transfer_obstacle=True, transport_replan_budget=1, output_root=temporary)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(search.call_count, 1)
        self.assertEqual(result['graph_state']['transport_replans'], 1)
        self.assertEqual(result['graph_state']['policy_calls'], 3)
        self.assertEqual(result['graph_state']['failures'][-1]['failure_code'], 'MOTION_PATH_NOT_FOUND')
        attempt = result['policy_replan_attempts'][0]
        self.assertEqual(attempt['start_time_s'], attempt['end_time_s'])
        self.assertFalse(attempt['transport_executed'])
        self.assertEqual(result['robot_installed_parts'], 0)

    def test_interrupted_execution_is_never_reported_as_unexecuted(self):
        def observer(skills):
            original = skills.motion.execute
            def execute(plan, **kwargs):
                if skills.motion.stage == 'insert_transfer':
                    # One real simulation step then a deliberate executor fault.
                    skills.motion.step(**kwargs)
                    raise FailureEvent(FailureCode.TCP_NOT_REACHED, 'Injected interruption after starting execution.')
                return original(plan, **kwargs)
            skills.motion.execute = execute
        with tempfile.TemporaryDirectory() as temporary:
            result = execute_trial(mode='single', transfer_obstacle=True, transport_replan_budget=1,
                                   observer=observer, output_root=temporary)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['graph_state']['transport_replans'], 1)
        self.assertEqual(result['graph_state']['policy_calls'], 3)
        attempt = result['policy_replan_attempts'][0]
        self.assertTrue(attempt['transport_executed'])
        self.assertFalse(attempt['transport_completed'])
        self.assertGreater(attempt['end_time_s'], attempt['start_time_s'])
        self.assertEqual(result['robot_installed_parts'], 0)

    def test_lowering_contact_is_not_another_transport_replan(self):
        def observer(skills):
            original = skills.stage
            def stage(name):
                original(name)
                if name == 'insert_lower':
                    # Explicit obstacle fault only; no robot/product pose changes.
                    obstacle = skills.model.body('assembly_transfer_obstacle').id
                    skills.model.body_pos[obstacle] = skills.verifier.target_position + [0., 0., -.025]
                    mujoco.mj_forward(skills.model, skills.data)
            skills.stage = stage
        with tempfile.TemporaryDirectory() as temporary:
            result = execute_trial(mode='single', transfer_obstacle=True, transport_replan_budget=1,
                                   observer=observer, output_root=temporary)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['graph_state']['failures'][-1]['phase'], 'insert_lower/preflight')
        self.assertEqual(result['graph_state']['transport_replans'], 1)
        self.assertEqual(len(result['policy_replan_attempts']), 1)
        self.assertEqual(result['robot_installed_parts'], 0)

    def test_action_budget_and_configuration_boundaries(self):
        failure = {'failure_code': 'PLANNED_CONTACT'}
        self.assertEqual(allowed_skills(['pick'], failure, 0, 1, transport_replan_budget=1), ['stop'])
        for count, budget, expected in [(0, 1, ['replan_insert', 'stop']), (0, 0, ['stop']), (1, 1, ['stop'])]:
            self.assertEqual(allowed_skills(['pick'], failure, 0, 1, transport_replan_available=True,
                             transport_replans=count, transport_replan_budget=budget), expected)
        for options in ({'transport_replan_budget': 2}, {'transport_replan_budget': True},
                        {'transport_replan_budget': 1, 'transport_strategy': 'direct_then_rrt'},
                        {'transport_replan_budget': 1, 'recipe': {}}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                execute_trial(**options)

    def test_offline_api_contract_offers_replan_only_when_permitted(self):
        context = {'task_id': 'contract-only', 'part_id': 'post_1', 'target_id': 'socket_1',
            'observation_revision': 3, 'allowed_skills': ['replan_insert', 'stop'],
            'failure': {'failure_code': 'PLANNED_CONTACT'}, 'failures': [],
            'transport_replan_budget_remaining': 1, 'transport_replans': 0,
            'transport_replan': {'transport_command_executed': False, 'alignment_offset_m': [.006, 0.]},
            'observation': {'valid': True, 'source': 'sim_ground_truth', 'simulation_time_s': 31.,
                            'tcp': {}, 'workcell': {'part': {}, 'target': {}}}}
        request = build_request(SimpleNamespace(model='offline-contract', reasoning_effort=None), context)
        self.assertEqual(request['tools'][0]['parameters']['properties']['skill']['enum'], ['replan_insert', 'stop'])
        packet = json.loads(request['input'])
        self.assertEqual(packet['transport_replan'], context['transport_replan'])
        self.assertEqual(packet['transport_replan_budget_remaining'], 1)
        proposal = {'skill': 'replan_insert', 'part_id': 'post_1', 'target_id': 'socket_1',
                    'observation_revision': 3, 'reason': 'Offline contract fixture.'}
        response = {'status': 'completed', 'error': None, 'output': [{'type': 'function_call',
            'name': 'propose_assembly_action', 'arguments': json.dumps(proposal)}]}
        self.assertEqual(parse_proposal(response, context), proposal)
        with self.assertRaises(FailureEvent):
            parse_proposal(response, context | {'allowed_skills': ['stop']})
        with self.assertRaises(FailureEvent):
            validate_proposal(proposal | {'observation_revision': 2}, context)


if __name__ == '__main__':
    unittest.main()
