"""Host gates and bounded calculation only; no API or MuJoCo physics execution."""
from copy import deepcopy
import json
import math
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
if not (PROJECT / 'scripts' / 'grasp_lessons.py').is_file():
    PROJECT = Path(r'C:\dev\llm-robot-assembly')
for folder in (PROJECT / 'tests', PROJECT / 'scripts', HERE.parent / 'scripts', HERE):
    if folder.is_dir():
        sys.path.insert(0, str(folder))

import grasp_skill_runtime as runtime
import grasp_skill_candidates as candidates
import test_grasp_skill_candidates as candidate_fixtures
from test_retrieve_grasp_experience import fixture as run_fixture
from export_grasp_experience import build_export, digest, save_export
from grasp_lessons import save_lesson


def measures(**changes):
    return {'supported': True, 'holding_part': False, 'left_contact': False,
            'right_contact': False, 'upright_cosine': 1., 'linear_speed_m_s': 0.,
            'angular_speed_rad_s': 0., 'pad_gap_mm': 85., **changes}


def returned(output):
    """Synthetic wrapper result; this helper never executes candidate source."""
    return {'status': 'returned', 'output': deepcopy(output), 'error': None,
            'steps': 10, 'execution_attempted': True, 'child_launched': False,
            'test_provenance': 'mock_wrapper_result'}


def reseal(value, field):
    value[field] = digest({k: v for k, v in value.items() if k != field})
    return value


class GraspSkillRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.fixture = candidate_fixtures.GraspSkillCandidateTests()
        self.fixture.setUp()
        self.root, self.memory = self.fixture.root, self.fixture.memory
        source = self.fixture.lesson_fixture
        # The older common fixture is 100 mm tall. Build actual 60 mm source evidence
        # before export so the runtime's supported dimension gate is exercised honestly.
        config, events, result = run_fixture(size=[.02, .02, .06])
        config['initial_installed'] = []
        result['part_episodes'] = {'post_1': {'start': {'installed': []}}}
        source.export_path.unlink()
        source.write_run('current', config, events, result)
        self.source_run = source.write_run('historical', config, events, result)
        self.export_path, _ = save_export(build_export(self.source_run), self.memory)
        self.lesson_path, _ = save_lesson(source.lesson(), memory_dir=self.memory,
                                          output_dir=self.root / 'runtime_lessons')
        self.packet = candidates.prepare_input(self.lesson_path, memory_dir=self.memory)
        self.candidate = self.make_candidate()
        self.condition = deepcopy(self.packet['lesson']['evidence_packet']['cases'][0]['contexts'][0])
        self.simulator_version = self.packet['lesson']['evidence_packet']['search']['query']['simulator_version']
        self.observation = self.make_observation()
        self.trial = self.make_trial(self.observation)
        self.rejection = runtime.bind_rejection(self.observation, self.trial)
        self.good_grasp = {'x_mm': 0., 'y_mm': 0., 'z_mm': 4., 'jaw_yaw_deg': 0.}

    def tearDown(self):
        self.fixture.tearDown()

    def make_candidate(self, source=None):
        draft = candidate_fixtures.draft_for(self.packet)
        if source is not None:
            draft['source_code'] = source
        return candidates.build_candidate(self.packet, draft, generation={'method': 'offline_fixture'})

    def make_observation(self, **changes):
        args = {'episode_id': 'offline-current-episode', 'revision': 1,
                'condition_context': self.condition, 'simulator_version': self.simulator_version,
                'simulation_time_s': 3., 'measurements': measures(), 'state_fingerprint': 'a' * 64,
                'provenance': 'offline_fixture', 'installed_parts': [], **changes}
        return runtime.make_observation(**args)

    def make_trial(self, obs, **changes):
        return {'grasp': {'x_mm': 0., 'y_mm': 0., 'z_mm': 0., 'jaw_yaw_deg': 0.},
                'source': 'mujoco_preflight_copy' if obs['provenance'] == 'mujoco_live_snapshot' else 'offline_fixture',
                'accepted': False, 'phase': 'insert_descent', 'failure_code': 'PLANNED_CONTACT',
                'observation_revision': obs['revision'], 'live_start_s': obs['simulation_time_s'], **changes}

    def evaluate(self, **changes):
        args = {'current_reader': lambda: deepcopy(self.observation), 'rejection': self.rejection,
                'remaining_proposals': 2, 'memory_dir': self.memory, **changes}
        observation = args.pop('observation', self.observation)
        candidate = args.pop('candidate', self.candidate)
        return runtime.evaluate_candidate(candidate, observation, **args)

    def assert_no_execution_permission(self, result):
        self.assertFalse(result['execution_authorized'])
        self.assertFalse(result['automatic_skill_registration'])
        self.assertFalse(result['physics_validated'])
        self.assertEqual(result['API_calls'], 0)
        self.assertEqual(result['robot_commands'], 0)
        if result['proposal'] is not None:
            self.assertFalse(result['proposal']['execution_authorized'])
            self.assertTrue(result['proposal']['requires_current_scene_preflight'])

    def test_observation_seals_exact_conditions_with_explicit_fixture_provenance(self):
        runtime.validate_observation(self.observation)
        self.assertEqual(self.observation['condition_context']['config']['part']['size_m'], [.02, .02, .06])
        self.assertEqual(self.observation['installed_parts'], [])
        self.assertEqual(self.observation['provenance'], 'offline_fixture')
        self.assertEqual(self.observation['observation_source'], 'offline_fixture')
        self.assertEqual(self.observation['stability_evidence'], 'instantaneous_measurement_not_settling_window')
        self.assertNotIn('scene_config_ref', self.observation['condition_context'])
        self.assertEqual(self.observation, self.make_observation())
        self.assertEqual(self.observation['observation_id'], digest({k: v for k, v in self.observation.items() if k != 'observation_id'}))

    def test_measurements_require_exact_boolean_numeric_fields(self):
        variants = []
        for key in ('supported', 'holding_part', 'left_contact', 'right_contact'):
            variants += [(key, measures(**{key: 1})), (key, measures(**{key: None}))]
        for key in ('upright_cosine', 'linear_speed_m_s', 'angular_speed_rad_s', 'pad_gap_mm'):
            variants += [(key, measures(**{key: True})), (key, measures(**{key: float('nan')})),
                         (key, measures(**{key: float('inf')}))]
        extra = measures(); extra['invented_sensor'] = 1
        missing = measures(); missing.pop('supported')
        variants += [('extra', extra), ('missing', missing), ('negative_speed', measures(linear_speed_m_s=-1)),
                     ('cosine_outside', measures(upright_cosine=1.1))]
        for key, values in variants:
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.make_observation(measurements=values)

    def test_observation_metadata_and_installed_parts_are_strict(self):
        for key, value in [('episode_id', ''), ('revision', True), ('revision', 0),
                           ('simulation_time_s', -1), ('simulation_time_s', True),
                           ('simulator_version', ''), ('state_fingerprint', 'not-a-hash'),
                           ('provenance', 'invented'), ('installed_parts', ['b', 'a']),
                           ('installed_parts', ['a', 'a']), ('installed_parts', False),
                           ('installed_parts', 0), ('installed_parts', {}), ('installed_parts', ())]:
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                self.make_observation(**{key: value})

    def test_context_units_robot_and_condition_hash_are_checked(self):
        for mode in ('frame', 'units', 'robot', 'hash'):
            context = deepcopy(self.condition)
            if mode == 'frame':
                context['grasp_frame'] = 'world'
            elif mode == 'units':
                context['grasp_position_unit'] = 'm'
            elif mode == 'robot':
                context['config']['robot']['model'] = 'ur3'
            else:
                context['active_part_condition_id'] = '0' * 64
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                self.make_observation(condition_context=context)

    def test_bind_rejection_requires_false_accepted_matching_revision_time_and_origin(self):
        for key, value in [('accepted', True), ('accepted', 0), ('source', 'mujoco_preflight_copy'),
                           ('observation_revision', True), ('observation_revision', 2),
                           ('live_start_s', 2.), ('failure_code', 'not_uppercase'),
                           ('phase', ''), ('grasp', {'x_mm': 0})]:
            trial = self.make_trial(self.observation, **{key: value})
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                runtime.bind_rejection(self.observation, trial)
        copied = deepcopy(self.trial)
        bound = runtime.bind_rejection(self.observation, copied)
        copied['grasp']['z_mm'] = 99
        self.assertEqual(bound['trial']['grasp']['z_mm'], 0)

    def test_fixture_rejection_cannot_be_relabelled_as_live_simulation(self):
        # Constructing metadata does not run MuJoCo; it tests only origin separation.
        live = self.make_observation(provenance='mujoco_live_snapshot')
        with self.assertRaises(ValueError):
            runtime.bind_rejection(live, self.trial)
        bound = runtime.bind_rejection(live, self.make_trial(live))
        self.assertEqual(bound['trial']['source'], 'mujoco_preflight_copy')
        with patch.object(runtime, 'execute_source') as execute:
            result = self.evaluate(observation=live, current_reader=lambda: live)
        self.assertEqual(result['reason'], 'rejection_from_different_state')
        execute.assert_not_called()

    def test_mocked_proposal_uses_host_ids_revision_bounds_and_exact_context(self):
        reader = Mock(side_effect=[deepcopy(self.observation), deepcopy(self.observation)])
        with patch.object(runtime, 'execute_source', return_value=returned(self.good_grasp)) as execute:
            result = self.evaluate(current_reader=reader)
        self.assertEqual(result['status'], 'proposed')
        self.assertEqual(result['matched_case_ids'], ['C1'])
        proposal = result['proposal']
        self.assertEqual(proposal['grasp'], self.good_grasp)
        self.assertEqual(proposal['part_id'], 'post_1')
        self.assertEqual(proposal['target_id'], 'socket_1')
        self.assertEqual(proposal['observation_revision'], 1)
        self.assertEqual(proposal['observation_id'], self.observation['observation_id'])
        self.assertEqual(proposal['rejection_id'], self.rejection['rejection_id'])
        self.assertEqual(reader.call_count, 2)
        execute.assert_called_once()
        self.assertEqual(execute.call_args.args[1], {
            'part_size_xyz_mm': [20., 20., 60.], 'previous_grasp': self.trial['grasp'],
            'failure_code': 'PLANNED_CONTACT', 'failure_phase': 'insert_descent',
            'holding_part': False, 'observation_is_current': True, 'remaining_proposals': 2})
        self.assert_no_execution_permission(result)

    def test_real_child_vm_calculates_fixture_proposal_without_physics(self):
        result = self.evaluate(timeout_s=5.)
        self.assertEqual(result['status'], 'proposed', result)
        self.assertEqual(result['proposal']['grasp'], self.good_grasp)
        self.assertTrue(result['vm']['execution_attempted'])
        self.assertTrue(result['vm']['child_launched'])
        self.assert_no_execution_permission(result)

    def test_real_child_vm_none_is_abstention_not_error(self):
        source = '''def propose(ctx):
    size = ctx['part_size_xyz_mm']
    previous = ctx['previous_grasp']
    if size[2] < 0:
        return {'x_mm': 0, 'y_mm': 0, 'z_mm': previous['z_mm'], 'jaw_yaw_deg': 0}
    return None
'''
        result = self.evaluate(candidate=self.make_candidate(source), timeout_s=5.)
        self.assertEqual(result['status'], 'abstained', result)
        self.assertEqual(result['reason'], 'candidate_returned_none')
        self.assertIsNone(result['proposal'])
        self.assertEqual(result['vm']['status'], 'returned')
        self.assertTrue(result['vm']['child_launched'])
        self.assert_no_execution_permission(result)

    def test_pre_read_rejects_changed_state_even_at_same_simulation_time(self):
        for mode in ('state', 'revision', 'episode'):
            changed = self.make_observation(**({'state_fingerprint': 'b' * 64} if mode == 'state'
                else {'revision': 2} if mode == 'revision' else {'episode_id': 'other-episode'}))
            with self.subTest(mode=mode), patch.object(runtime, 'execute_source') as execute:
                result = self.evaluate(current_reader=lambda: changed)
                self.assertEqual(result['reason'], 'stale_observation')
                self.assertIsNone(result['proposal'])
                execute.assert_not_called()

    def test_post_read_discards_calculated_output_when_state_changes(self):
        changed = self.make_observation(state_fingerprint='b' * 64)
        reader = Mock(side_effect=[self.observation, changed])
        with patch.object(runtime, 'execute_source', return_value=returned(self.good_grasp)) as execute:
            result = self.evaluate(current_reader=reader)
        self.assertEqual(result['status'], 'rejected')
        self.assertEqual(result['reason'], 'observation_changed_during_calculation')
        self.assertIsNone(result['proposal'])
        execute.assert_called_once()
        self.assert_no_execution_permission(result)

    def test_fresh_reobservation_revision_accepts_same_bound_rejection_state(self):
        newer = self.make_observation(revision=2)
        with patch.object(runtime, 'execute_source', return_value=returned(self.good_grasp)):
            result = self.evaluate(observation=newer, current_reader=lambda: newer)
        self.assertEqual(result['status'], 'proposed')
        self.assertEqual(result['proposal']['observation_revision'], 2)
        self.assertEqual(result['proposal']['rejection_id'], self.rejection['rejection_id'])

    def test_rejection_cannot_come_from_future_or_changed_state(self):
        for mode, old in [('future', self.make_observation(revision=2)),
                          ('state', self.make_observation(state_fingerprint='b' * 64)),
                          ('episode', self.make_observation(episode_id='older-other-episode'))]:
            rejection = runtime.bind_rejection(old, self.make_trial(old))
            with self.subTest(mode=mode), patch.object(runtime, 'execute_source') as execute:
                result = self.evaluate(rejection=rejection)
                self.assertEqual(result['reason'], 'rejection_from_different_state')
                execute.assert_not_called()

    def test_host_enforces_budget_exact_integer_and_never_calls_vm_when_exhausted(self):
        for budget in (True, -1, 4, 1.5, '1'):
            with self.subTest(budget=budget), patch.object(runtime, 'execute_source') as execute:
                result = self.evaluate(remaining_proposals=budget)
                self.assertEqual(result['status'], 'rejected')
                self.assertIsNone(result['proposal'])
                execute.assert_not_called()
        with patch.object(runtime, 'execute_source') as execute:
            result = self.evaluate(remaining_proposals=0)
        self.assertEqual((result['status'], result['reason']), ('abstained', 'proposal_budget_exhausted'))
        execute.assert_not_called()

    def test_host_gates_cannot_be_bypassed_by_candidate_without_safety_branches(self):
        source = '''def propose(ctx):
    size = ctx['part_size_xyz_mm']
    previous = ctx['previous_grasp']
    return {'x_mm': 0, 'y_mm': 0, 'z_mm': previous['z_mm'] + 1, 'jaw_yaw_deg': 0}
'''
        unguarded = self.make_candidate(source)
        for changed, reason in [({'holding_part': True}, 'holding_part'),
                                ({'left_contact': True}, 'holding_part'),
                                ({'right_contact': True}, 'holding_part'),
                                ({'supported': False}, 'part_not_supported'),
                                ({'upright_cosine': math.cos(math.radians(2))}, 'part_not_upright'),
                                ({'linear_speed_m_s': .001}, 'part_not_stationary'),
                                ({'angular_speed_rad_s': .01}, 'part_not_stationary'),
                                ({'pad_gap_mm': 79.99}, 'gripper_not_open')]:
            obs = self.make_observation(measurements=measures(**changed))
            with self.subTest(changed=changed), patch.object(runtime, 'execute_source') as execute:
                result = self.evaluate(candidate=unguarded, observation=obs, current_reader=lambda: obs)
                self.assertEqual((result['status'], result['reason']), ('abstained', reason))
                self.assertIsNone(result['proposal'])
                execute.assert_not_called()

    def test_matching_geometry_is_insufficient_when_other_recorded_conditions_differ(self):
        for mode in ('spawn', 'depth', 'installed'):
            context = deepcopy(self.condition)
            installed = []
            if mode == 'spawn':
                context['config']['part']['spawn_xy_table_m'][0] += .001
            elif mode == 'depth':
                context['joint']['nominal_insertion_depth_m'] += .001
            else:
                installed = ['post_2']
            context['active_part_condition_id'] = digest({'config': context['config'], 'joint': context['joint'],
                                                         'simulator_version': self.simulator_version})
            obs = self.make_observation(condition_context=context, installed_parts=installed)
            with self.subTest(mode=mode), patch.object(runtime, 'execute_source') as execute:
                result = self.evaluate(observation=obs, current_reader=lambda: obs)
                self.assertEqual(result['reason'], 'outside_recorded_conditions')
                execute.assert_not_called()

    def test_unsupported_height_is_rejected_before_vm(self):
        context = deepcopy(self.condition)
        context['config']['part']['size_m'][2] = .1
        context['active_part_condition_id'] = digest({'config': context['config'], 'joint': context['joint'],
                                                     'simulator_version': self.simulator_version})
        obs = self.make_observation(condition_context=context)
        with patch.object(runtime, 'execute_source') as execute:
            result = self.evaluate(observation=obs, current_reader=lambda: obs)
        self.assertEqual(result['reason'], 'unsupported_post_dimensions')
        execute.assert_not_called()

    def test_unknown_historical_installed_parts_are_not_treated_as_empty(self):
        config, events, result = run_fixture(size=[.02, .02, .06])
        # No initial_installed and no part-episode start record: unknown stays unknown.
        self.export_path.unlink()
        source = self.fixture.lesson_fixture
        run = source.write_run('unknown_installed_history', config, events, result)
        save_export(build_export(run), self.memory)
        lesson_path, _ = save_lesson(source.lesson(), memory_dir=self.memory,
                                     output_dir=self.root / 'unknown_history_lessons')
        packet = candidates.prepare_input(lesson_path, memory_dir=self.memory)
        case = packet['lesson']['evidence_packet']['cases'][0]
        self.assertIsNone(case['source_conditions']['part_episode_start_installed'])
        candidate = candidates.build_candidate(packet, candidate_fixtures.draft_for(packet),
                                                generation={'method': 'offline_fixture'})
        with patch.object(runtime, 'execute_source') as execute:
            result = self.evaluate(candidate=candidate)
        self.assertEqual((result['status'], result['reason']), ('rejected', 'outside_recorded_conditions'))
        execute.assert_not_called()

    def test_missing_rejection_and_wrong_failure_context_abstain(self):
        with patch.object(runtime, 'execute_source') as execute:
            result = self.evaluate(rejection=None)
        self.assertEqual((result['status'], result['reason']), ('abstained', 'no_current_rejection'))
        execute.assert_not_called()
        for changes in ({'phase': 'approach_lower/preflight'}, {'failure_code': 'GRASP_NOT_CONFIRMED'}):
            rejection = runtime.bind_rejection(self.observation, self.make_trial(self.observation, **changes))
            with self.subTest(changes=changes), patch.object(runtime, 'execute_source') as execute:
                result = self.evaluate(rejection=rejection)
                self.assertEqual((result['status'], result['reason']), ('abstained', 'failure_context_mismatch'))
                execute.assert_not_called()

    def test_modified_observation_rejection_and_candidate_are_not_evaluated(self):
        obs = deepcopy(self.observation); obs['measurements']['supported'] = False
        rejection = deepcopy(self.rejection); rejection['trial']['grasp']['z_mm'] = 99
        candidate = deepcopy(self.candidate); candidate['source_code'] += '\n# changed\n'
        for changes in ({'observation': obs}, {'rejection': rejection}, {'candidate': candidate}):
            with self.subTest(changes=tuple(changes)), patch.object(runtime, 'execute_source') as execute:
                result = self.evaluate(**changes)
                self.assertEqual(result['status'], 'rejected')
                self.assertIsNone(result['proposal'])
                execute.assert_not_called()

    def test_changed_memory_or_lesson_is_rejected_before_vm(self):
        originals = {self.export_path: self.export_path.read_bytes(), self.lesson_path: self.lesson_path.read_bytes()}
        for target in originals:
            target.write_bytes(originals[target] + (b'\n' if target == self.lesson_path else b'broken-json'))
            with self.subTest(target=target.name), patch.object(runtime, 'execute_source') as execute:
                result = self.evaluate()
                self.assertEqual(result['reason'], 'candidate_validation_failed')
                self.assertIsNone(result['proposal'])
                execute.assert_not_called()
            target.write_bytes(originals[target])

    def test_vm_errors_timeout_step_limit_never_become_proposals(self):
        for error in ('runtime_error', 'step_limit', 'timeout'):
            vm = {'status': 'error', 'output': None, 'error': {'code': error},
                  'steps': None, 'execution_attempted': True, 'child_launched': False}
            with self.subTest(error=error), patch.object(runtime, 'execute_source', return_value=vm):
                result = self.evaluate()
                self.assertEqual((result['status'], result['reason']), ('rejected', 'candidate_calculation_failed'))
                self.assertIsNone(result['proposal'])
                self.assertEqual(result['vm']['error']['code'], error)
                self.assert_no_execution_permission(result)

    def test_generated_output_requires_exact_finite_numeric_fields_and_current_bounds(self):
        invalid = [[], 'pick', {'x_mm': 0}, {**self.good_grasp, 'execute': True}]
        for field, value in [('z_mm', True), ('z_mm', float('nan')), ('z_mm', float('inf')),
                             ('x_mm', 10.001), ('y_mm', -10.001), ('z_mm', 30.001),
                             ('z_mm', -30.001), ('jaw_yaw_deg', 180.001), ('jaw_yaw_deg', -180.001)]:
            invalid.append({**self.good_grasp, field: value})
        for output in invalid:
            with self.subTest(output=output), patch.object(runtime, 'execute_source', return_value=returned(output)):
                result = self.evaluate()
                self.assertEqual((result['status'], result['reason']), ('rejected', 'output_validation_failed'))
                self.assertIsNone(result['proposal'])
                self.assert_no_execution_permission(result)

    def test_boundary_output_is_a_proposal_without_clipping_or_physical_success_claim(self):
        output = {'x_mm': 10., 'y_mm': -10., 'z_mm': 30., 'jaw_yaw_deg': -180.}
        with patch.object(runtime, 'execute_source', return_value=returned(output)):
            result = self.evaluate()
        self.assertEqual(result['status'], 'proposed')
        self.assertEqual(result['proposal']['grasp'], output)
        self.assert_no_execution_permission(result)

    def test_reader_failures_are_rejections_and_do_not_release_output(self):
        for side_effect, reason in [(RuntimeError('fixture read failure'), 'current_observation_read_failed'),
                                    ([self.observation, RuntimeError('fixture post-read failure')],
                                     'post_calculation_observation_failed')]:
            with self.subTest(reason=reason), patch.object(runtime, 'execute_source', return_value=returned(self.good_grasp)):
                result = self.evaluate(current_reader=Mock(side_effect=side_effect))
                self.assertEqual((result['status'], result['reason']), ('rejected', reason))
                self.assertIsNone(result['proposal'])

    def test_evaluation_preserves_candidate_observation_rejection_and_source_files(self):
        objects = deepcopy((self.candidate, self.observation, self.rejection))
        files = {p: p.read_bytes() for p in (self.export_path, self.lesson_path)}
        with patch.object(runtime, 'execute_source', return_value=returned(self.good_grasp)):
            result = self.evaluate()
        self.assertEqual(result['status'], 'proposed')
        self.assertEqual((self.candidate, self.observation, self.rejection), objects)
        self.assertEqual({p: p.read_bytes() for p in files}, files)


if __name__ == '__main__':
    unittest.main()
