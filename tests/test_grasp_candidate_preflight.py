"""Synthetic connector contracts. Physics/evaluation boundaries are mocked explicitly."""
from copy import deepcopy
from contextlib import ExitStack
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

import grasp_candidate_preflight as connector
from export_grasp_experience import CHECKS
from grasp_skill_runtime import bind_rejection
import test_grasp_skill_runtime as runtime_fixtures


class GraspCandidatePreflightTests(unittest.TestCase):
    def setUp(self):
        self.fixture = runtime_fixtures.GraspSkillRuntimeTests()
        self.fixture.setUp()
        self.root, self.memory = self.fixture.root, self.fixture.memory
        self.candidate = self.fixture.candidate
        # Authored metadata exercises the live-only boundary, never real observations.
        self.observation = self.fixture.make_observation(provenance='mujoco_live_snapshot')
        self.rejection = bind_rejection(self.observation, self.fixture.make_trial(self.observation))
        self.grasp = {'x_mm': 0., 'y_mm': 0., 'z_mm': 1., 'jaw_yaw_deg': 0.}
        cfg = self.observation['condition_context']['config']
        self.proposal = {'kind': 'post_grasp_proposal', 'grasp': deepcopy(self.grasp),
            'part_id': cfg['part']['id'], 'target_id': cfg['target']['id'],
            'episode_id': self.observation['episode_id'], 'observation_revision': self.observation['revision'],
            'observation_id': self.observation['observation_id'], 'candidate_id': self.candidate['candidate_id'],
            'code_sha256': self.candidate['code_sha256'], 'rejection_id': self.rejection['rejection_id'],
            'requires_current_scene_preflight': True, 'execution_authorized': False}
        self.evaluation = {'status': 'proposed', 'proposal': deepcopy(self.proposal),
                           'execution_authorized': False, 'test_provenance': 'synthetic_evaluator_result'}
        self.source = {'schema': 'synthetic_source_state_fixture', 'simulation_time_s': 3.,
                       'integration_sha256': 'a' * 64, 'model_sha256': 'b' * 64,
                       'host_sha256': 'c' * 64, 'host': {'fixture_only': True}}
        self.skills = SimpleNamespace(test_provenance='synthetic_no_physics')
        self.folder = self.root / 'comparison'
        self.reader = Mock(return_value=deepcopy(self.observation))
        capture_patch = patch.object(connector, 'capture_observation', return_value=deepcopy(self.observation))
        self.capture = capture_patch.start()
        self.addCleanup(capture_patch.stop)

    def tearDown(self):
        self.fixture.tearDown()

    def trial(self, *, accepted=False, grasp=None, clearance=-2.37, support='declared_mating_support'):
        value = {'grasp': deepcopy(self.grasp if grasp is None else grasp),
            'source': 'mujoco_preflight_copy', 'live_start_s': self.observation['simulation_time_s'],
            'copy_end_s': 52., 'observation_revision': self.observation['revision'],
            'accepted': accepted, 'held_pad_geometry': {
                'predicted_lowest_pad_height_relative_to_rim_mm': clearance},
            'trace_summary': {'test_provenance': 'synthetic_trace'}, 'trace_paths': {}}
        if accepted:
            value['checks'] = {k: True for k in CHECKS | {support}}
            value['minimum_final_depth_m'] = .0161
        else:
            value.update(failure_code='PLANNED_CONTACT', phase='insert_descent',
                         message='Synthetic contact, not a simulator measurement.',
                         violations=[{'geoms': ['finger_pad', 'socket_wall'], 'signed_distance_m': -.0006}])
        return value

    def run_connector(self, **changes):
        arguments = {'current_reader': self.reader, 'rejection': self.rejection,
                     'remaining_proposals': 2, 'log_dir': self.folder,
                     'memory_dir': self.memory, **changes}
        observation = arguments.pop('observation', self.observation)
        candidate = arguments.pop('candidate', self.candidate)
        return connector.preflight_candidate(self.skills, candidate, observation, **arguments)

    def boundaries(self, *, evaluation=None, trial=None, state_sequence=None):
        stack = ExitStack()
        state = stack.enter_context(patch.object(connector, 'source_state',
            **({'side_effect': state_sequence} if state_sequence is not None else {'return_value': deepcopy(self.source)})))
        evaluate = stack.enter_context(patch.object(connector, 'evaluate_candidate',
            return_value=deepcopy(self.evaluation if evaluation is None else evaluation)))
        checker = stack.enter_context(patch.object(connector, 'run_recorded_preflight',
            return_value=deepcopy(self.trial() if trial is None else trial)))
        self.addCleanup(stack.close)
        return state, evaluate, checker

    def saved_result(self):
        return json.loads((self.folder / 'result.json').read_text(encoding='utf-8'))

    def assert_no_assembly_claim(self, result):
        self.assertFalse(result['original_scene_assembly_executed'])
        self.assertFalse(result['automatic_skill_registration'])
        self.assertEqual(result['API_calls'], 0)

    def test_proposal_is_submitted_unchanged_and_copy_rejection_is_saved(self):
        before = deepcopy((self.candidate, self.observation, self.rejection, self.evaluation))
        state, evaluate, checker = self.boundaries()
        result = self.run_connector()
        self.assertEqual(result['status'], 'preflight_rejected')
        self.assertTrue(result['original_state_unchanged'])
        self.assertEqual(result['trial']['grasp'], self.grasp)
        evaluate.assert_called_once()
        checker.assert_called_once_with(self.skills, self.grasp,
            observation_revision=self.observation['revision'], log_dir=self.folder / 'preflight')
        submitted = json.loads((self.folder / 'submitted_grasp.json').read_text(encoding='utf-8'))
        self.assertEqual(submitted['grasp'], self.grasp)
        self.assertEqual(submitted['rejection_id'], self.rejection['rejection_id'])
        self.assertEqual(self.saved_result(), result)
        self.assertEqual(state.call_count, 3)
        self.assertEqual(self.capture.call_count, 2)
        self.capture.assert_called_with(self.skills, episode_id=self.observation['episode_id'],
                                         revision=self.observation['revision'])
        self.assertEqual((self.candidate, self.observation, self.rejection, self.evaluation), before)
        self.assert_no_assembly_claim(result)

    def test_accepted_copy_trial_still_does_not_claim_original_scene_assembly(self):
        self.boundaries(trial=self.trial(accepted=True))
        result = self.run_connector()
        self.assertEqual(result['status'], 'preflight_accepted')
        self.assertTrue(all(result['trial']['checks'].values()))
        self.assertEqual(self.saved_result(), result)
        self.assert_no_assembly_claim(result)

    def test_non_proposed_evaluations_never_reach_physics_checker(self):
        for index, status in enumerate(('abstained', 'rejected')):
            self.folder = self.root / ('not_proposed_' + str(index))
            evaluation = {'status': status, 'reason': 'synthetic fixture', 'proposal': None}
            with patch.object(connector, 'source_state', return_value=self.source), \
                 patch.object(connector, 'evaluate_candidate', return_value=evaluation), \
                 patch.object(connector, 'run_recorded_preflight') as checker:
                result = self.run_connector()
            self.assertEqual(result['status'], 'proposal_not_available')
            self.assertIsNone(result['trial'])
            checker.assert_not_called()
            self.assertFalse((self.folder / 'submitted_grasp.json').exists())
            self.assertEqual(self.saved_result(), result)
            self.assert_no_assembly_claim(result)

    def test_offline_observation_mode_is_rejected_before_evaluation(self):
        _, evaluate, checker = self.boundaries()
        with self.assertRaises(ValueError):
            self.run_connector(observation=self.fixture.observation)
        evaluate.assert_not_called()
        checker.assert_not_called()
        self.assertEqual(self.saved_result()['status'], 'error')

    def test_stale_observation_after_evaluation_is_not_submitted(self):
        self.reader.return_value = self.fixture.make_observation(provenance='mujoco_live_snapshot', revision=2)
        _, _, checker = self.boundaries()
        with self.assertRaises(ValueError):
            self.run_connector()
        checker.assert_not_called()
        self.assertFalse((self.folder / 'submitted_grasp.json').exists())
        self.assertEqual(self.saved_result()['status'], 'error')
        self.assertTrue(self.saved_result()['original_state_unchanged'])

    def test_observation_from_different_executor_is_rejected_even_when_reader_agrees(self):
        self.capture.return_value = self.fixture.make_observation(
            provenance='mujoco_live_snapshot', state_fingerprint='d' * 64)
        _, evaluate, checker = self.boundaries()
        with self.assertRaisesRegex(ValueError, 'different executor'):
            self.run_connector()
        evaluate.assert_not_called()
        checker.assert_not_called()
        self.reader.assert_not_called()
        self.assertEqual(self.saved_result()['status'], 'error')
        self.assertFalse((self.folder / 'submitted_grasp.json').exists())

    def test_executor_change_during_calculation_is_rejected_even_with_stale_reader(self):
        changed = self.fixture.make_observation(provenance='mujoco_live_snapshot', state_fingerprint='d' * 64)
        self.capture.side_effect = [deepcopy(self.observation), changed]
        _, evaluate, checker = self.boundaries()
        with self.assertRaisesRegex(ValueError, 'Source scene changed'):
            self.run_connector()
        evaluate.assert_called_once()
        checker.assert_not_called()
        self.reader.assert_called_once()
        self.assertEqual(self.saved_result()['status'], 'error')
        self.assertEqual(self.saved_result()['evaluation'], self.evaluation)
        self.assertFalse((self.folder / 'submitted_grasp.json').exists())

    def test_source_change_before_check_is_rejected_and_both_states_are_recorded(self):
        changed = {**self.source, 'integration_sha256': 'd' * 64}
        _, _, checker = self.boundaries(state_sequence=[self.source, changed, changed])
        with self.assertRaises(ValueError):
            self.run_connector()
        checker.assert_not_called()
        result = self.saved_result()
        self.assertEqual(result['status'], 'error')
        self.assertEqual(result['error']['type'], 'SourceStateChanged')
        self.assertFalse(result['original_state_unchanged'])
        self.assertEqual(json.loads((self.folder / 'source_before.json').read_text()), self.source)
        self.assertEqual(json.loads((self.folder / 'source_after.json').read_text()), changed)

    def test_source_change_after_copy_check_overrides_apparent_acceptance(self):
        changed = {**self.source, 'host_sha256': 'd' * 64}
        self.boundaries(trial=self.trial(accepted=True), state_sequence=[self.source, self.source, changed])
        with self.assertRaises(ValueError):
            self.run_connector()
        result = self.saved_result()
        self.assertEqual(result['status'], 'error')
        self.assertTrue(result['trial']['accepted'])
        self.assertFalse(result['original_state_unchanged'])
        self.assert_no_assembly_claim(result)

    def test_candidate_source_lesson_change_is_rejected_before_evaluation(self):
        _, evaluate, checker = self.boundaries()
        self.fixture.lesson_path.write_bytes(self.fixture.lesson_path.read_bytes() + b'\n')
        with self.assertRaises(ValueError):
            self.run_connector()
        evaluate.assert_not_called()
        checker.assert_not_called()
        self.assertEqual(self.saved_result()['status'], 'error')

    def test_proposal_binding_and_permission_changes_do_not_reach_checker(self):
        for index, (key, value) in enumerate((('part_id', 'other_part'), ('target_id', 'other_target'),
            ('candidate_id', 'other_candidate'), ('code_sha256', 'd' * 64), ('episode_id', 'another'),
            ('observation_id', 'd' * 64), ('observation_revision', True), ('rejection_id', 'd' * 64),
            ('execution_authorized', 0), ('execution_authorized', True), ('requires_current_scene_preflight', 1))):
            self.folder = self.root / ('binding_' + str(index))
            evaluation = deepcopy(self.evaluation)
            evaluation['proposal'][key] = value
            with patch.object(connector, 'source_state', return_value=self.source), \
                 patch.object(connector, 'evaluate_candidate', return_value=evaluation), \
                 patch.object(connector, 'run_recorded_preflight') as checker:
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    self.run_connector()
            checker.assert_not_called()
            self.assertEqual(self.saved_result()['status'], 'error')

    def test_proposal_bounds_are_revalidated_without_clipping(self):
        evaluation = deepcopy(self.evaluation)
        evaluation['proposal']['grasp']['z_mm'] = 30.001
        _, _, checker = self.boundaries(evaluation=evaluation)
        with self.assertRaises(ValueError):
            self.run_connector()
        checker.assert_not_called()
        self.assertEqual(self.saved_result()['status'], 'error')
        self.assertFalse((self.folder / 'submitted_grasp.json').exists())

    def test_checker_failure_and_keyboard_interrupt_preserve_result_and_reraise(self):
        for index, error in enumerate((ValueError('synthetic detector failure'), KeyboardInterrupt())):
            self.folder = self.root / ('checker_error_' + str(index))
            with patch.object(connector, 'source_state', return_value=self.source), \
                 patch.object(connector, 'evaluate_candidate', return_value=self.evaluation), \
                 patch.object(connector, 'run_recorded_preflight', side_effect=error):
                with self.assertRaises(type(error)) as raised:
                    self.run_connector()
            self.assertIs(raised.exception, error)
            result = self.saved_result()
            self.assertEqual(result['status'], 'error')
            self.assertEqual(result['error']['type'], type(error).__name__)
            self.assertIsNone(result['trial'])
            self.assertTrue(result['original_state_unchanged'])
            self.assertTrue((self.folder / 'submitted_grasp.json').exists())
            self.assert_no_assembly_claim(result)

    def test_malformed_returned_trial_is_retained_for_diagnosis_and_not_accepted(self):
        bad = self.trial(accepted=True)
        bad['checks']['released'] = False
        self.boundaries(trial=bad)
        with self.assertRaises(ValueError):
            self.run_connector()
        result = self.saved_result()
        self.assertEqual(result['status'], 'error')
        self.assertEqual(result['trial'], bad)
        self.assert_no_assembly_claim(result)

    def test_existing_log_directory_is_not_reused_or_overwritten(self):
        self.folder.mkdir()
        marker = self.folder / 'result.json'
        marker.write_text('{"existing":true}', encoding='utf-8')
        _, evaluate, checker = self.boundaries()
        with self.assertRaises(FileExistsError):
            self.run_connector()
        self.assertEqual(marker.read_text(), '{"existing":true}')
        evaluate.assert_not_called()
        checker.assert_not_called()

    def test_checked_trial_requires_matching_source_grasp_revision_and_times(self):
        for key, value in [('source', 'offline_fixture'), ('accepted', 1),
                           ('grasp', {**self.grasp, 'z_mm': 2}), ('observation_revision', True),
                           ('observation_revision', 2), ('live_start_s', True), ('live_start_s', 4.),
                           ('copy_end_s', True), ('copy_end_s', float('nan')), ('copy_end_s', 2.)]:
            trial = self.trial()
            trial[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                connector.checked_trial(trial, self.grasp, self.observation)
        for key in ('failure_code', 'phase'):
            trial = self.trial(); trial.pop(key)
            with self.subTest(missing=key), self.assertRaises(ValueError):
                connector.checked_trial(trial, self.grasp, self.observation)

    def test_checked_trial_does_not_accept_boolean_grasp_via_numeric_equality(self):
        trial = self.trial()
        trial['grasp']['z_mm'] = True  # True == the requested numeric 1.0 in Python.
        with self.assertRaises(ValueError):
            connector.checked_trial(trial, self.grasp, self.observation)

    def test_accepted_trial_needs_all_exact_true_checks_one_support_and_nominal_depth(self):
        for support in ('socket_floor_support', 'declared_mating_support'):
            trial = self.trial(accepted=True, support=support)
            self.assertEqual(connector.checked_trial(trial, self.grasp, self.observation), trial)
        for mode in ('missing_check', 'false_check', 'integer_check', 'extra_check',
                     'both_supports', 'no_support', 'missing_depth', 'nan_depth', 'bool_depth',
                     'short_depth', 'long_depth', 'failure_code', 'failure_phase'):
            trial = self.trial(accepted=True)
            if mode == 'missing_check':
                trial['checks'].pop('released')
            elif mode == 'false_check':
                trial['checks']['released'] = False
            elif mode == 'integer_check':
                trial['checks']['released'] = 1
            elif mode == 'extra_check':
                trial['checks']['invented_check'] = True
            elif mode == 'both_supports':
                trial['checks']['socket_floor_support'] = True
            elif mode == 'no_support':
                trial['checks'].pop('declared_mating_support')
            elif mode == 'missing_depth':
                trial.pop('minimum_final_depth_m')
            elif mode == 'nan_depth':
                trial['minimum_final_depth_m'] = float('nan')
            elif mode == 'bool_depth':
                trial['minimum_final_depth_m'] = True
            elif mode == 'short_depth':
                trial['minimum_final_depth_m'] = .014
            elif mode == 'long_depth':
                trial['minimum_final_depth_m'] = .018
            elif mode == 'failure_code':
                trial['failure_code'] = 'PLANNED_CONTACT'
            else:
                trial['phase'] = 'insert_descent'
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                connector.checked_trial(trial, self.grasp, self.observation)

    def test_positive_clearance_delta_does_not_turn_rejection_into_success(self):
        first = self.trial(grasp={**self.grasp, 'z_mm': 0}, clearance=-3.37)
        second = self.trial(clearance=-2.37)
        before = deepcopy((first, second))
        result = connector.compare_trials(first, second)
        self.assertEqual(result['outcome'], 'candidate_still_rejected')
        self.assertAlmostEqual(result['predicted_clearance_delta_mm'], 1.)
        self.assertEqual(result['parameter_delta'], {'x_mm': 0., 'y_mm': 0., 'z_mm': 1., 'jaw_yaw_deg': 0.})
        self.assertTrue(result['same_source_start_time'])
        self.assertTrue(result['same_failure_code_and_phase'])
        self.assertFalse(result['actual_original_scene_assembly_success'])
        self.assertIsNone(result['executed_collision_force_comparison'])
        self.assertIn('not_measured_seated_clearance', result['clearance_interpretation'])
        self.assertIn('not_an_improvement_percentage', result['first_contact_distance_interpretation'])
        self.assertEqual((first, second), before)

    def test_positive_absolute_clearance_and_missing_measurements_are_not_success_rules(self):
        first = self.trial(clearance=-3.)
        second = self.trial(clearance=5.)
        self.assertEqual(connector.compare_trials(first, second)['outcome'], 'candidate_still_rejected')
        for value in (None, True, float('nan'), float('inf'), '5'):
            second['held_pad_geometry']['predicted_lowest_pad_height_relative_to_rim_mm'] = value
            comparison = connector.compare_trials(first, second)
            self.assertIsNone(comparison['predicted_clearance_delta_mm'])
            self.assertIsNone(comparison['modified']['predicted_pad_to_rim_clearance_mm'])
            self.assertFalse(comparison['actual_original_scene_assembly_success'])

    def test_copy_acceptance_is_scoped_and_changed_failure_phase_is_preserved(self):
        first, second = self.trial(), self.trial(accepted=True)
        comparison = connector.compare_trials(first, second)
        self.assertEqual(comparison['outcome'], 'resolved_in_copy_preflight')
        self.assertFalse(comparison['actual_original_scene_assembly_success'])
        second = self.trial()
        second['phase'] = 'pick_close'
        second['failure_code'] = 'GRASP_NOT_CONFIRMED'
        second['live_start_s'] += 1
        comparison = connector.compare_trials(first, second)
        self.assertFalse(comparison['same_failure_code_and_phase'])
        self.assertFalse(comparison['same_source_start_time'])
        self.assertEqual(comparison['modified']['failure_phase'], 'pick_close')


if __name__ == '__main__':
    unittest.main()
