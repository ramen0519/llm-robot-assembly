"""Recovery integration receipts and task-completion gates using local fixtures."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import graph_grasp_recovery as integration
from export_grasp_experience import _post_contexts
from grasp_skill_runtime import bind_rejection, make_observation
from run_frame_assembly import installation_sequence, verify_interference_recovery
from test_retrieve_grasp_experience import config as part_config


def grasp(z):
    return {'x_mm': 0., 'y_mm': 0., 'z_mm': z, 'jaw_yaw_deg': 0.}


class RecoveryAdapterReceiptTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name) / 'recovery'
        self.library = Mock(snapshot_id='frozen-fixture')
        self.parent = {'candidate_id': 'parent-fixture'}
        self.revision = {'revision_id': 'revision-fixture', 'code_sha256': 'a' * 64}
        self.library.resolve.return_value = (self.parent, self.revision)
        self.adapter = integration.GraphGraspRecovery(self.library, object(), object(), object(),
                                                       self.folder, 'fixture-episode')
        raw = part_config(size=[.02, .02, .06])
        context = _post_contexts(raw)['post_1']
        self.skills = SimpleNamespace(config=context['config'], installed=set(),
            motion=SimpleNamespace(held_pose=None), registry={'post_1': {'kind': 'post'}})
        common = {'episode_id': self.adapter.episode_id, 'condition_context': context,
            'simulator_version': raw['simulator_version'], 'simulation_time_s': 3.,
            'state_fingerprint': 'a' * 64, 'provenance': 'mujoco_live_snapshot', 'installed_parts': [],
            'measurements': {'supported': True, 'holding_part': False, 'left_contact': False,
                'right_contact': False, 'upright_cosine': 1., 'linear_speed_m_s': 0.,
                'angular_speed_rad_s': 0., 'pad_gap_mm': 85.}}
        old = make_observation(revision=1, **common)
        self.current = make_observation(revision=2, **common)
        self.trial = {'source': 'mujoco_preflight_copy', 'accepted': False, 'grasp': grasp(1.),
            'observation_revision': 1, 'live_start_s': 3., 'copy_end_s': 55.,
            'failure_code': 'PLANNED_CONTACT', 'phase': 'insert_lower/preflight',
            'violations': [{'geoms': ['gripper_right_pad1', 'assembly_base_wall_fixture']}],
            'held_pad_geometry': {'predicted_lowest_pad_height_relative_to_rim_mm': -2.37},
            'trace_summary': {'trace_complete': True, 'pick_completed': True}}
        self.rejection = bind_rejection(old, self.trial)
        self.adapter.rejection = self.rejection
        self.state = {'observation_revision': 2, 'grasp_trials': [deepcopy(self.trial)]}
        self.enterContext(patch.object(self.adapter, 'observation', return_value=self.current))
        self.enterContext(patch.object(integration, 'source_state', return_value={'synthetic_state': 1}))
        self.decision = {'status': 'selected', 'selected_id': 'skill:fixture', 'API_calls': 0,
            'snapshot_id': self.library.snapshot_id, 'reason': 'Synthetic selector decision.',
            'events': [{'tool': k} for k in ('search_library', 'read_documents', 'select_skill')]}
        self.selector = self.enterContext(patch.object(integration, 'select_skill', return_value=self.decision))
        self.proposal = {'kind': 'post_grasp_proposal', 'candidate_id': self.revision['revision_id'],
            'code_sha256': self.revision['code_sha256'], 'part_id': 'post_1',
            'target_id': context['config']['target']['id'], 'episode_id': self.adapter.episode_id,
            'observation_id': self.current['observation_id'], 'observation_revision': 2,
            'rejection_id': self.rejection['rejection_id'], 'requires_current_scene_preflight': True,
            'execution_authorized': False, 'grasp': grasp(4.37)}

    def read(self, name):
        return json.loads((self.folder / name).read_text(encoding='utf-8'))

    def test_proposed_recovery_writes_two_distinct_receipts_without_overwriting_selection(self):
        evaluated = {'status': 'proposed', 'proposal': self.proposal}
        with patch.object(integration, 'evaluate_candidate', return_value=evaluated):
            answer = self.adapter.recover(self.skills, self.state)
        self.assertEqual(answer['status'], 'proposed')
        selected = self.read('selection_receipt.json')
        decided = self.read('decision_receipt.json')
        self.assertEqual(selected['selected_id'], 'skill:fixture')
        self.assertEqual(selected['rejection_id'], self.rejection['rejection_id'])
        self.assertNotIn('proposed_grasp', selected)
        self.assertNotIn('evaluation_status', selected)
        self.assertEqual(decided, answer['receipt'])
        self.assertEqual(decided['evaluation_status'], 'proposed')
        self.assertEqual(decided['proposed_grasp'], grasp(4.37))
        self.assertEqual(decided['revision_id'], self.revision['revision_id'])
        self.assertEqual(self.read('evaluation.json'), evaluated)
        self.assertEqual(self.adapter.expected, (self.current, grasp(4.37)))
        self.library.resolve.assert_called_once_with('skill:fixture', self.current, self.rejection)
        self.assertIs(self.selector.call_args.kwargs['shared_ledger'], self.adapter.ledger)
        self.assertIs(self.selector.call_args.kwargs['client'], self.adapter.client)

    def test_selected_skill_can_abstain_after_evaluation_and_still_write_both_receipts(self):
        with patch.object(integration, 'evaluate_candidate', return_value={'status': 'abstained', 'reason': 'No valid grasp.'}):
            answer = self.adapter.recover(self.skills, self.state)
        self.assertEqual(answer['status'], 'abstained')
        self.assertEqual(self.read('selection_receipt.json')['selection_status'], 'selected')
        self.assertEqual(self.read('decision_receipt.json')['evaluation_status'], 'abstained')
        self.assertNotIn('proposed_grasp', self.read('decision_receipt.json'))
        self.assertIsNone(self.adapter.expected)


class FrameIntegrationGatesTests(unittest.TestCase):
    def test_target_sequence_checks_dependencies_duplicates_and_unknown_parts(self):
        registry = {'post_1': {'requires': ['base']}, 'post_2': {'requires': ['base']},
                    'sleeve_1': {'requires': ['base', 'post_1']}}
        self.assertEqual(installation_sequence(registry, None), list(registry))
        self.assertEqual(installation_sequence(registry, ('post_1', 'sleeve_1')), ['post_1', 'sleeve_1'])
        for parts in (['sleeve_1', 'post_1'], ['post_1', 'post_1'], ['unknown'], [], 'post_1'):
            with self.subTest(parts=parts), self.assertRaises(ValueError):
                installation_sequence(registry, parts)

    def test_verified_installation_alone_cannot_claim_the_selected_skill_recovered_it(self):
        state = {'status': 'success', 'completion_source': 'physics_verifier',
            'completed': ['pick', 'insert'], 'grasp_recovery_attempts': 1,
            'grasp_recovery_receipt': {'selection_status': 'selected', 'selected_id': 'skill:fixture',
                                       'observation_revision': 2, 'proposed_grasp': grasp(4.37)},
            'grasp_trials': [{'accepted': False, 'grasp': grasp(1.), 'failure_code': 'PLANNED_CONTACT',
                              'phase': 'insert_lower/preflight', 'observation_revision': 1},
                             {'accepted': True, 'grasp': grasp(4.37), 'observation_revision': 2}]}
        approach = {'proposed_grasp': grasp(4.37)}
        verify_interference_recovery(state, approach)
        for field, value in [('selection_status', 'abstained'), ('selected_id', None),
                             ('proposed_grasp', grasp(5.)), ('observation_revision', 1)]:
            altered = deepcopy(state)
            altered['grasp_recovery_receipt'][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                verify_interference_recovery(altered, approach)
        with self.assertRaises(ValueError):
            verify_interference_recovery(state, {'proposed_grasp': grasp(6.)})

    def test_development_grasp_is_used_once_for_the_matching_part_and_delegates_other_decisions(self):
        policy = Mock()
        policy.propose.return_value = {'source': 'delegated'}
        wrapper = integration.DevelopmentFirstGrasp(policy)
        base = {'part_id': 'post_1', 'target_id': 'socket', 'completed': [],
                'observation_revision': 1, 'allowed_skills': ['pick', 'stop'],
                'grasp_task': {'previous_trials': []}}
        for change in ({'part_id': 'post_2'}, {'grasp_task': {'previous_trials': [{'accepted': False}]}},
                       {'completed': ['pick'], 'allowed_skills': ['insert', 'stop']}):
            context = {**base, **change}
            self.assertEqual(wrapper.propose(context), {'source': 'delegated'})
            policy.propose.assert_called_with(context)
            self.assertFalse(wrapper.used)
        answer = wrapper.propose(base)
        self.assertEqual((answer['skill'], answer['grasp'], answer['observation_revision']), ('pick', grasp(1.), 1))
        self.assertTrue(wrapper.used)
        self.assertEqual(policy.propose.call_count, 3)
        self.assertEqual(wrapper.propose(base), {'source': 'delegated'})
        self.assertEqual(policy.propose.call_count, 4)


if __name__ == '__main__':
    unittest.main()
