"""Real graph routing and synthetic motion/selection to test ownership boundaries offline."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import assembly_graph as graph
from test_graph_grasp_recovery import FixturePolicy, FixtureRecovery, FixtureSkills, grasp


class Skills(FixtureSkills):
    def __init__(self, part):
        super().__init__()
        self.config['part']['id'] = part
        self.results = []
        self.wrong_approach = False

    def check_grasp(self, value):
        trial = super().check_grasp(value)
        trial['source'] = 'mujoco_preflight_copy'
        if not trial['accepted']:
            trial.update(failure_code='PLANNED_CONTACT', phase='insert_lower/preflight')
        return trial

    def pick(self, part, *, grasp):
        result = super().pick(part, grasp=grasp)
        result['part_id'] = part
        self.results.extend([{'skill': 'approach_component/v1', 'status': 'success', 'part_id': part,
                              'proposed_grasp': None if self.wrong_approach else deepcopy(grasp)}, result])
        return result


class Recovery(FixtureRecovery):
    def recover(self, skills, state):
        self.received = deepcopy(state['agent_handoffs'][-1])
        proposal = super().recover(skills, state)
        if proposal['status'] == 'proposed':
            proposal['receipt'].update(selection_status='selected', proposed_grasp=proposal['grasp'],
                                       observation_revision=state['observation_revision'])
        return proposal


class RoleTransferTests(unittest.TestCase):
    def run_case(self, adapter=None, *, part='post_1', after_event=None, setup=None):
        self.skills, self.assembly, self.recovery = Skills(part), FixturePolicy(), FixturePolicy()
        if setup:
            setup(self.skills)
        self.events = []

        def emit(event):
            self.events.append(event)
            if after_event:
                after_event(self.skills, event)

        def stamp(skills):
            # A stable stand-in; the production function hashes the integration/model/host state.
            return {'simulation_time_s': skills.data.time, 'integration_sha256': str(skills.data.time),
                    'host_sha256': str(skills.motion.held_pose), 'model_sha256': 'fixture'}

        with patch.object(graph, '_snapshot', lambda skills: {'time': skills.data.time}), \
             patch.object(graph, '_handoff_scene', stamp):
            return graph.run_assembly_graph(self.skills, mode='roles',
                policies={'assembly': self.assembly, 'recovery': self.recovery},
                on_event=emit, propose_grasp=True, finish_on_verified_insert=True, grasp_recovery=adapter,
                failure_handler=lambda exc: {'status': 'failed', 'failure_code': 'FIXTURE', 'message': str(exc)})

    def test_selected_skill_owns_repair_and_pick_then_assembly_decides_insert(self):
        adapter = Recovery()
        result = self.run_case(adapter)
        self.assertEqual(result['status'], 'success', result['error'])
        outgoing, returning = result['agent_handoffs']
        self.assertEqual(adapter.received, outgoing)
        self.assertEqual((outgoing['from'], outgoing['to'], outgoing['part_id']), ('assembly', 'recovery', 'post_1'))
        self.assertEqual(outgoing['observation_revision'], 2)
        self.assertEqual(outgoing['proof']['rejected_revision'], 1)
        self.assertEqual(outgoing['proof']['recovery_method'], 'verified_skill_search')
        self.assertEqual((returning['from'], returning['to'], returning['part_id']), ('recovery', 'assembly', 'post_1'))
        self.assertEqual(returning['observation_revision'], 3)
        self.assertEqual(returning['proof']['preflight_revision'], 2)
        self.assertTrue(returning['proof']['held_and_lifted'])
        self.assertEqual(returning['proof']['selected_id'], 'fixture')
        self.assertEqual(returning['proof']['accepted_grasp'], grasp(4.37))
        self.assertEqual([e['handoff'] for e in self.events if 'handoff' in e], result['agent_handoffs'])
        nodes = [(e['node'], e['active_role']) for e in self.events]
        start = nodes.index(('recover_grasp', 'recovery'))
        self.assertEqual(nodes[start:start+5], [('recover_grasp', 'recovery'), ('check_grasp', 'recovery'),
                         ('execute', 'recovery'), ('verify', 'recovery'), ('observe', 'assembly')])
        self.assertEqual(self.assembly.contexts[-1]['handoff'], returning)
        self.assertNotIn('handoff', self.assembly.contexts[0])
        self.assertEqual(self.recovery.contexts, [])

    def test_sleeve_recovery_agent_revises_numeric_grasp_then_returns_to_assembly(self):
        result = self.run_case(part='sleeve_1')
        self.assertEqual(result['status'], 'success', result['error'])
        outgoing, returning = result['agent_handoffs']
        self.assertEqual(outgoing['proof']['recovery_method'], 'numeric_grasp_revision')
        self.assertEqual(outgoing['part_id'], 'sleeve_1')
        self.assertIsNone(returning['proof']['selected_id'])
        self.assertEqual(len(self.recovery.contexts), 1)
        ctx = self.recovery.contexts[0]
        self.assertEqual(ctx['active_role'], 'recovery')
        self.assertEqual(ctx['allowed_skills'], ['pick', 'stop'])
        self.assertEqual(ctx['grasp_task']['previous_trials'][0]['grasp'], grasp(1.))
        self.assertEqual(self.skills.commands, [('pick', grasp(5.)), ('insert', 'socket')])
        self.assertEqual(self.assembly.contexts[-1]['handoff'], returning)

    def test_ineligible_skill_uses_numeric_recovery_without_library_search(self):
        adapter = Recovery(eligible=False)
        result = self.run_case(adapter)
        self.assertEqual(result['status'], 'success', result['error'])
        self.assertEqual(adapter.searches, [])
        self.assertEqual(result['agent_handoffs'][0]['proof']['recovery_method'], 'numeric_grasp_revision')
        self.assertEqual(len(self.recovery.contexts), 1)

    def test_numeric_recovery_can_stop_or_exhaust_proposal_budget_without_original_pick(self):
        from post_grasp import MAX_PROPOSALS
        propose = FixturePolicy.propose
        for stop in (True, False):
            def repair(policy, context):
                action = propose(policy, context)
                if context['active_role'] == 'recovery':
                    action.update(skill='stop' if stop else 'pick', grasp=None if stop else grasp(1.))
                return action

            with self.subTest(stop=stop), patch.object(FixturePolicy, 'propose', repair):
                result = self.run_case(part='sleeve_1')
                self.assertEqual(result['status'], 'stopped', result['error'])
                self.assertEqual([h['to'] for h in result['agent_handoffs']], ['recovery'])
                self.assertEqual(len(result['grasp_trials']), 1 if stop else MAX_PROPOSALS)
                self.assertEqual(self.skills.commands, [])

    def test_selector_abstention_error_or_rejected_repair_never_return(self):
        for adapter, status in [(Recovery(decision='abstained'), 'stopped'),
                                (Recovery(decision='error'), 'failed'), (Recovery(repair_z=1.), 'stopped')]:
            with self.subTest(status=status, decision=adapter.decision):
                result = self.run_case(adapter)
                self.assertEqual(result['status'], status, result['error'])
                self.assertEqual([h['to'] for h in result['agent_handoffs']], ['recovery'])
                self.assertEqual(result['active_role'], 'recovery')
                self.assertEqual(self.skills.commands, [])
                self.assertEqual(len(self.assembly.contexts), 1)

    def test_failed_original_pick_verification_or_raced_boundary_never_return(self):
        def disturb(skills, event):
            if event['node'] == 'verify' and event['completed'] == ['pick']:
                skills.data.time += .001

        for kwargs in ({'setup': lambda skills: setattr(skills, 'wrong_approach', True)}, {'after_event': disturb}):
            with self.subTest(kwargs=kwargs):
                result = self.run_case(Recovery(), **kwargs)
                self.assertEqual(result['status'], 'failed')
                self.assertEqual([h['to'] for h in result['agent_handoffs']], ['recovery'])
                self.assertEqual(self.skills.commands, [('pick', grasp(4.37))])

    def test_fatal_rejection_cannot_start_role_recovery(self):
        def fatal(skills):
            check = skills.check_grasp
            skills.check_grasp = lambda grasp: {**check(grasp), 'failure_code': 'SIMULATION_INVALID'}

        result = self.run_case(setup=fatal)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['agent_handoffs'], [])
        self.assertEqual(self.recovery.contexts, [])
        self.assertEqual(self.skills.commands, [])


if __name__ == '__main__':
    unittest.main()
