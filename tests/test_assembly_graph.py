from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from assembly_graph import run_assembly_graph
from assembly_policy import ScriptedPolicy, validate_proposal
from failure_report import FailureCode, FailureEvent, capture_observation, json_safe
from insertion_scene import build_insertion_scene
from insertion_skills import InsertionSkills


class AssemblyGraphTests(unittest.TestCase):
    def build(self):
        m, d, c, j = build_insertion_scene()
        return InsertionSkills(m, d, c, j)

    def run_graph(self, skills, **kwargs):
        def failure(exc):
            return {'status': 'failed', 'failure_code': getattr(exc, 'code', FailureCode.UNCLASSIFIED_ERROR).value,
                    'message': str(exc), 'actual': capture_observation(skills.model, skills.data)}
        return run_assembly_graph(skills, failure_handler=failure, **kwargs)

    def test_same_physics_and_contract_for_single_and_role_separated_policies(self):
        states, elapsed = [], []
        for mode in ('single', 'roles'):
            skills = self.build()
            events = []
            state = self.run_graph(skills, mode=mode, scenario='alignment_offset', on_event=events.append)
            self.assertEqual(state['status'], 'success', state['error'])
            self.assertEqual(state['completed'], ['pick', 'insert'])
            self.assertEqual(state['recoveries'], 1)
            self.assertEqual(len(state['failures']), 1)
            self.assertEqual(state['failures'][0]['failure_code'], 'ALIGNMENT_OUT_OF_TOLERANCE')
            self.assertEqual(state['policy_calls'], 5)
            self.assertIsNone(state['error'])
            actions = [e['action']['skill'] for e in events if e['node'] == 'decide']
            self.assertEqual(actions, ['pick', 'insert', 'recover_alignment', 'insert', 'finish'])
            recovery = next(e for e in events if e['node'] == 'decide' and e['action']['skill'] == 'recover_alignment')
            self.assertEqual(recovery['active_role'], 'recovery')
            states.append(state)
            elapsed.append(skills.data.time)
        self.assertAlmostEqual(elapsed[0], elapsed[1], places=9)
        self.assertEqual(states[0]['verification'], states[1]['verification'])

    def test_zero_recovery_budget_stops_on_alignment_failure(self):
        skills = self.build()
        state = self.run_graph(skills, scenario='alignment_offset', recovery_budget=0)
        self.assertEqual(state['status'], 'failed')
        self.assertEqual(state['completed'], ['pick'])
        self.assertEqual(state['recoveries'], 0)
        self.assertEqual(skills.motion.stage, 'insert_alignment_check')

    def test_untrusted_policy_cannot_finish_early_or_execute_code(self):
        class BadPolicy(ScriptedPolicy):
            def propose(self, context):
                result = super().propose(context)
                result['skill'] = 'finish'
                return result
        skills = self.build()
        state = self.run_graph(skills, mode='single', policies={'single': BadPolicy()})
        self.assertEqual(state['status'], 'failed')
        self.assertEqual(state['completed'], [])
        self.assertAlmostEqual(skills.data.time, 3.)
        context = {'allowed_skills': ['pick'], 'part_id': 'p', 'target_id': 't', 'observation_revision': 2}
        good = {'skill': 'pick', 'part_id': 'p', 'target_id': 't', 'observation_revision': 2, 'reason': 'ok'}
        self.assertEqual(validate_proposal(good, context), good)
        for change in ({'observation_revision': 1}, {'observation_revision': True}, {'part_id': 'unknown'},
                       {'skill': 'exec'}, {'code': 'anything'}, {'reason': ''}):
            with self.assertRaises(FailureEvent):
                validate_proposal(good | change, context)

    def test_policy_proposal_requires_current_physical_observation(self):
        skills = self.build()
        def event(row):
            if row['node'] == 'decide':
                skills.motion.hold(.01, allow_support=True, allow_grasp=False)
        state = self.run_graph(skills, on_event=event)
        self.assertEqual(state['status'], 'failed')
        self.assertEqual(state['completed'], [])
        self.assertEqual(state['failures'][0]['failure_code'], 'INPUT_OR_PRECONDITION_INVALID')


if __name__ == '__main__':
    unittest.main()
