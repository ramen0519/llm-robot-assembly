"""Exercise real LangGraph routing with synthetic skills and decisions."""
from copy import deepcopy
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import assembly_graph as graph


def grasp(z):
    return {'x_mm': 0., 'y_mm': 0., 'z_mm': z, 'jaw_yaw_deg': 0.}


class FixtureSkills:
    def __init__(self):
        self.config = {'part': {'id': 'post_1'}, 'target': {'id': 'socket'}}
        self.data = SimpleNamespace(time=0.)
        self.motion = SimpleNamespace(reference=None, held_pose=None)
        self.verifier = SimpleNamespace(sample=self.sample, check_held=lambda *args: None)
        self.commands = []

    def settle(self):
        self.data.time += 1

    def grasp_geometry(self):
        return {'fixture': True}

    def check_grasp(self, value):
        return {'accepted': value['z_mm'] >= 4., 'grasp': deepcopy(value)}

    def sample(self, *args, **kwargs):
        return {'supported': self.motion.held_pose is None, 'part_bottom_clearance_m': .1}

    def pick(self, part, *, grasp):
        self.commands.append(('pick', deepcopy(grasp)))
        self.motion.held_pose = 'fixture'
        self.data.time += 1
        return {'skill': 'pick/v1', 'status': 'success'}

    def insert(self, part, target, **kwargs):
        self.commands.append(('insert', target))
        self.motion.held_pose = None
        self.data.time += 1
        return {'skill': 'insert_post/v1', 'status': 'success'}

    def insertion_evidence(self, rows):
        return {'checks': {'fixture_post_verified': self.motion.held_pose is None}}


class FixturePolicy:
    def __init__(self):
        self.contexts = []

    def propose(self, context):
        self.contexts.append(deepcopy(context))
        action = {key: context[key] for key in ('part_id', 'target_id', 'observation_revision')}
        action.update(skill=context['allowed_skills'][0], reason='Synthetic decision.')
        if 'grasp_task' in context:
            action['grasp'] = grasp(5. if context['grasp_task']['previous_trials'] else 1.)
        return action


class FixtureRecovery:
    def __init__(self, *, eligible=True, decision='proposed', repair_z=4.37, stale=False):
        self.allow, self.decision, self.repair_z, self.stale = eligible, decision, repair_z, stale
        self.checks, self.observations, self.searches = [], [], []

    def check(self, skills, state, action):
        self.checks.append(deepcopy(action))
        trial = skills.check_grasp(action['grasp'])
        return {**trial, 'observation_revision': state['observation_revision'],
            'trace_summary': {'sample_count': 10, 'trace_complete': True, 'stages': ['large fixture detail']},
            'trace_paths': {'terminal': 'fixture/terminal.json'}}

    def eligible(self, skills, state):
        self.observations.append((state['observation_revision'], state['grasp_trials'][-1]['observation_revision']))
        return self.allow

    def recover(self, skills, state):
        self.searches.append(state['observation_revision'])
        if self.stale:
            skills.data.time += 1
        if self.decision == 'error':
            raise ValueError('Synthetic selection failure.')
        result = {'status': self.decision, 'reason': 'Synthetic recovery decision.',
                  'receipt': {'selected_id': 'fixture' if self.decision == 'proposed' else None}}
        if self.decision == 'proposed':
            result['grasp'] = grasp(self.repair_z)
        return result


class GraphGraspRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.skills, self.policy, self.events = FixtureSkills(), FixturePolicy(), []
        self.enterContext(patch.object(graph, '_snapshot', lambda skills: {'time': skills.data.time}))

    def invoke(self, adapter=None):
        return graph.run_assembly_graph(self.skills, mode='single', policies={'single': self.policy},
            on_event=self.events.append, propose_grasp=True, finish_on_verified_insert=True,
            failure_handler=lambda error: {'status': 'failed', 'failure_code': 'FIXTURE', 'message': str(error)},
            grasp_recovery=adapter)

    def test_proposal_has_explicit_node_fresh_observation_and_exactly_one_new_check(self):
        adapter = FixtureRecovery()
        result = self.invoke(adapter)
        self.assertEqual(result['status'], 'success', result['error'])
        names = [event['node'] for event in self.events]
        self.assertEqual(names, ['settle','observe','decide','check_grasp','observe','recover_grasp',
                                 'check_grasp','execute','verify','observe','decide','execute','verify'])
        self.assertEqual(adapter.observations, [(2, 1)])
        self.assertEqual(adapter.searches, [2])
        self.assertEqual([a['grasp'] for a in adapter.checks], [grasp(1.), grasp(4.37)])
        self.assertEqual(self.skills.commands, [('pick', grasp(4.37)), ('insert','socket')])
        self.assertEqual(result['completed'], ['pick','insert'])
        self.assertEqual(result['grasp_recovery_attempts'], 1)
        self.assertEqual(result['policy_calls'], 2)
        event = self.events[names.index('recover_grasp')]
        self.assertEqual(event['grasp_recovery_receipt'], {'selected_id': 'fixture'})
        self.assertEqual(event['action']['observation_revision'], 2)
        self.assertIn('stages', result['grasp_trials'][0]['trace_summary'])
        self.assertNotIn('stages', self.events[3]['grasp_trial']['trace_summary'])

    def test_ineligible_rejection_goes_to_normal_decision_with_compact_evidence(self):
        adapter = FixtureRecovery(eligible=False)
        result = self.invoke(adapter)
        self.assertEqual(result['status'], 'success')
        self.assertEqual(adapter.searches, [])
        self.assertNotIn('recover_grasp', [e['node'] for e in self.events])
        previous = self.policy.contexts[1]['grasp_task']['previous_trials'][0]
        self.assertFalse(previous['accepted'])
        self.assertNotIn('trace_paths', previous)
        self.assertNotIn('stages', previous['trace_summary'])
        self.assertEqual(self.skills.commands[0], ('pick', grasp(5.)))

    def test_abstention_stops_at_recovery_node_without_a_pick(self):
        adapter = FixtureRecovery(decision='abstained')
        result = self.invoke(adapter)
        self.assertEqual((result['status'], result['action']['skill']), ('stopped','stop'))
        self.assertEqual(self.events[-1]['node'], 'recover_grasp')
        self.assertEqual((len(adapter.checks), len(adapter.searches)), (1,1))
        self.assertEqual(self.skills.commands, [])

    def test_rejected_repair_never_triggers_second_search_or_executes_rejected_grasp(self):
        adapter = FixtureRecovery(repair_z=2.)
        result = self.invoke(adapter)
        self.assertEqual(result['status'], 'success')
        self.assertEqual(adapter.searches, [2])
        self.assertEqual([trial['accepted'] for trial in result['grasp_trials']], [False, False, True])
        self.assertEqual(self.skills.commands[0], ('pick', grasp(5.)))

    def test_selector_error_or_scene_change_cannot_reach_original_executor(self):
        for options in ({'decision': 'error'}, {'stale': True}):
            with self.subTest(options=options):
                self.skills, self.policy, self.events = FixtureSkills(), FixturePolicy(), []
                adapter = FixtureRecovery(**options)
                result = self.invoke(adapter)
                self.assertEqual(result['status'], 'failed')
                self.assertEqual(result['grasp_recovery_attempts'], 1)
                self.assertEqual(len(adapter.checks), 1)
                self.assertEqual(self.skills.commands, [])

    def test_recorder_cannot_supply_approval_for_a_different_revision(self):
        adapter = FixtureRecovery()
        checked = adapter.check
        adapter.check = lambda *args: {**checked(*args), 'observation_revision': 999}
        result = self.invoke(adapter)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(self.skills.commands, [])
        self.assertEqual(adapter.searches, [])

    def test_existing_graph_without_adapter_keeps_original_reproposal_behavior(self):
        result = self.invoke()
        self.assertEqual(result['status'], 'success')
        self.assertEqual(self.skills.commands[0], ('pick', grasp(5.)))
        self.assertNotIn('grasp_recovery_receipt', result)
        self.assertNotIn('recover_grasp', [e['node'] for e in self.events])


if __name__ == '__main__':
    unittest.main()
