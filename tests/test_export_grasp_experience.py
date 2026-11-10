"""Offline log-contract fixtures; no model calls or physical assembly evidence.

The numeric checks below exercise historical-log import only. They are authored
fixtures and must never be counted as a successful MuJoCo trial.
"""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

# Works in the staging directory and after placement in repository tests/.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import export_grasp_experience as exporter


def fixture_config():
    return {
        'environment': 'simulation', 'observation_source': 'sim_ground_truth',
        'simulator_version': 'synthetic-contract-fixture-not-physics',
        'policy_kind': 'offline_fixture', 'grasp_mode': 'proposed',
        'config': {
            'part': {'id': 'post_1', 'shape': 'box'},
            'target': {'id': 'socket_1'},
            'robot': {'model': 'ur3e', 'gripper': 'robotiq_2f85'},
        },
        'joint': {'product_candidate': 'four_post_frame_v1', 'nominal_insertion_depth_m': .016},
    }


def grasp(z=12.):
    return {'x_mm': 0., 'y_mm': 0., 'z_mm': z, 'jaw_yaw_deg': 0.}


def action(skill, revision, chosen=None):
    return {'skill': skill, 'part_id': 'post_1', 'target_id': 'socket_1',
            'observation_revision': revision, 'grasp': deepcopy(chosen),
            'reason': 'Synthetic fixture, not a model decision.'}


def event(node, revision, time_s, selected=None, *, completed=None, status='running', **extra):
    return {'node': node, 'active_part_id': 'post_1', 'status': status,
            'action': deepcopy(selected), 'observation_revision': revision,
            'simulation_time_s': time_s, 'completed': list(completed or []),
            'failure_count': 0, **extra}


def trial(selected, time_s, accepted):
    return {'grasp': deepcopy(selected['grasp']),
            'observation_revision': selected['observation_revision'],
            'accepted': accepted, 'source': 'mujoco_preflight_copy',
            'live_start_s': time_s,
            'failure': None if accepted else {
                'failure_code': 'PLANNED_CONTACT', 'phase': 'synthetic_preflight',
                'contacts': [{'geom1': 'finger_pad', 'geom2': 'socket_wall'}]},
            'fixture_only': True}


def completed_fixture(*, first_revision=1, time_offset=0.):
    pick = action('pick', first_revision, grasp())
    insert = action('insert', first_revision + 1)
    rows = [
        event('observe', first_revision, time_offset),
        event('decide', first_revision, time_offset, pick),
        event('check_grasp', first_revision, time_offset, pick,
              grasp_trial=trial(pick, time_offset, True)),
        event('execute', first_revision, time_offset + 1., pick),
        event('verify', first_revision, time_offset + 1., pick, completed=['pick']),
        event('observe', first_revision + 1, time_offset + 1., pick, completed=['pick']),
        event('decide', first_revision + 1, time_offset + 1., insert, completed=['pick']),
        event('execute', first_revision + 1, time_offset + 2., insert, completed=['pick']),
        event('verify', first_revision + 1, time_offset + 2., insert,
              completed=['pick', 'insert'], status='success'),
    ]
    checks = {'released': True, 'target_position_under_1mm': True,
              'target_orientation_under_1deg': True, 'insertion_depth_reached': True,
              'linear_speed_under_1mm_s': True, 'angular_speed_under_0_01rad_s': True,
              'penetration_under_1mm': True, 'socket_floor_support': True}
    detail = {'skill': 'insert_post/v1', 'status': 'success', 'part_id': 'post_1',
              'target_id': 'socket_1', 'checks': checks,
              'maximum_final_position_error_m': .0002, 'minimum_final_depth_m': .016,
              'observation': {'source': 'sim_ground_truth', 'part': {'id': 'post_1'},
                              'simulation_time_s': time_offset + 2.}}
    result = {'status': 'success', 'error': None, 'robot_installed_parts': 1,
              'results': [detail], 'graph_state': {
                  'status': 'success', 'completed': ['pick', 'insert'],
                  'failures': [], 'last_result': deepcopy(detail)}}
    return rows, result


class GraspExperienceExportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.run = self.root / 'synthetic_run'
        self.run.mkdir()
        self.config = fixture_config()
        self.events, self.result = completed_fixture()

    def tearDown(self):
        self.temporary.cleanup()

    def write(self, *, with_result=True):
        (self.run / 'resolved_config.json').write_text(
            json.dumps(self.config, ensure_ascii=False, allow_nan=False), encoding='utf-8')
        (self.run / 'graph_events.jsonl').write_text(
            ''.join(json.dumps(row, ensure_ascii=False, allow_nan=False) + '\n' for row in self.events),
            encoding='utf-8')
        if with_result:
            (self.run / 'result.json').write_text(
                json.dumps(self.result, ensure_ascii=False, allow_nan=False), encoding='utf-8')
        return self.run

    def test_complete_fixture_keeps_proposal_copy_live_and_recorded_verification_separate(self):
        bundle = exporter.build_export(self.write())
        exporter.validate_export(bundle)
        self.assertEqual(bundle['summary']['stages'],
                         {'proposal': 2, 'preflight': 1, 'execution': 2, 'verification': 2})
        self.assertEqual(bundle['summary']['recorded_measured_insertions'], 1)
        self.assertFalse(bundle['new_physics_validation'])
        self.assertFalse(bundle['automatic_skill_registration'])
        self.assertEqual(bundle['training_eligibility'], 'unassigned_requires_dataset_manifest')
        attempt = bundle['attempts'][0]
        self.assertEqual(attempt['preflight_status'], 'accepted')
        self.assertEqual(attempt['live_pick_status'], 'graph_verified')
        self.assertEqual(attempt['insertion_status'], 'recorded_measured_pass')
        for row in bundle['records']:
            self.assertEqual(row['domain'], 'mujoco_robot_assembly')
            self.assertEqual(row['evidence_kind'], 'historical_log_projection')
            if row['stage'] == 'preflight':
                self.assertEqual(row['data']['scene'], 'copy')
            if row['stage'] == 'execution':
                self.assertEqual(row['data']['scene'], 'live')
                self.assertIsNone(row['data']['command_submission_receipt'])
                self.assertIsNone(row['data']['command_submission_time_s'])
        verified = bundle['records'][-1]
        self.assertEqual(verified['data']['skill_result'], self.result['results'][0])
        self.assertTrue(verified['measurement_refs'])
        self.assertFalse(verified['data']['new_physics_validation'])

    def test_rejected_preflight_retains_contact_and_does_not_claim_live_execution(self):
        pick = action('pick', 1, grasp(0.))
        self.events = [event('observe', 1, 0.), event('decide', 1, 0., pick),
                       event('check_grasp', 1, 0., pick, grasp_trial=trial(pick, 0., False))]
        bundle = exporter.build_export(self.write(with_result=False))
        self.assertEqual([r['stage'] for r in bundle['records']], ['proposal', 'preflight'])
        self.assertEqual(bundle['attempts'][0]['live_pick_status'], 'not_executed_preflight_rejected')
        self.assertEqual(bundle['summary']['recorded_measured_insertions'], 0)
        self.assertEqual(bundle['records'][1]['data']['trial']['failure']['contacts'][0]['geom2'], 'socket_wall')

    def test_revised_grasp_links_to_exact_rejection_and_records_parameter_change(self):
        rejected = action('pick', 1, grasp(0.))
        prefix = [event('observe', 1, 0.), event('decide', 1, 0., rejected),
                  event('check_grasp', 1, 0., rejected, grasp_trial=trial(rejected, 0., False))]
        self.events, self.result = completed_fixture(first_revision=2, time_offset=.1)
        self.events = prefix + self.events
        bundle = exporter.build_export(self.write())
        first, revised = bundle['attempts']
        rejection = next(r for r in bundle['records'] if r['stage'] == 'preflight'
                         and r['data']['status'] == 'rejected')
        self.assertEqual(revised['parent_failure_record_id'], rejection['record_id'])
        self.assertEqual(revised['changed_parameters'], {'z_mm': 12.})
        proposal = next(r for r in bundle['records'] if r['stage'] == 'proposal'
                        and r['attempt_id'] == revised['attempt_id'])
        self.assertEqual(proposal['parent_record_ids'], [rejection['record_id']])
        self.assertNotEqual(first['attempt_id'], revised['attempt_id'])
        self.assertEqual(bundle['summary']['preflight_rejected'], 1)
        self.assertEqual(bundle['summary']['recorded_measured_insertions'], 1)

    def test_missing_result_keeps_graph_claim_distinct_from_measurements(self):
        bundle = exporter.build_export(self.write(with_result=False))
        self.assertNotIn('result.json', bundle['source_files'])
        self.assertIsNone(bundle['source_episode_status'])
        self.assertEqual(bundle['attempts'][0]['insertion_status'], 'graph_verified_without_saved_measurements')
        self.assertEqual(bundle['summary']['recorded_measured_insertions'], 0)
        self.assertIsNone(bundle['records'][-1]['data']['skill_result'])

    def test_interruption_after_execute_has_no_invented_verification_or_command_receipt(self):
        self.events = self.events[:4]
        bundle = exporter.build_export(self.write(with_result=False))
        self.assertEqual([r['stage'] for r in bundle['records']], ['proposal', 'preflight', 'execution'])
        self.assertEqual(bundle['attempts'][0]['live_pick_status'], 'node_returned')
        self.assertEqual(bundle['attempts'][0]['insertion_status'], 'unknown')
        self.assertIsNone(bundle['records'][-1]['data']['command_submission_receipt'])

    def test_accepted_copy_without_execution_remains_unknown(self):
        self.events = self.events[:3]
        bundle = exporter.build_export(self.write(with_result=False))
        self.assertEqual(bundle['attempts'][0]['preflight_status'], 'accepted')
        self.assertEqual(bundle['attempts'][0]['live_pick_status'], 'unknown')
        self.assertEqual(bundle['summary']['recorded_measured_insertions'], 0)
        self.assertNotIn('execution', bundle['summary']['stages'])

    def test_running_executor_with_new_failure_is_not_normal_return(self):
        self.events = self.events[:4]
        self.events[-1]['failure_count'] = 1
        bundle = exporter.build_export(self.write(with_result=False))
        self.assertEqual(bundle['records'][-1]['data']['node_outcome'], 'reported_failure')
        self.assertEqual(bundle['attempts'][0]['live_pick_status'], 'outcome_unknown')
        self.assertEqual(bundle['summary']['recorded_measured_insertions'], 0)

    def test_multiple_posts_with_restarted_revisions_keep_evidence_separate(self):
        # Deliberately identical grasp values/revisions on two different parts.
        second_events, second_result = completed_fixture(time_offset=10.)
        second_events = json.loads(json.dumps(second_events).replace('post_1', 'post_2').replace('socket_1', 'socket_2'))
        second_result = json.loads(json.dumps(second_result).replace('post_1', 'post_2').replace('socket_1', 'socket_2'))
        first_entry = {'kind': 'post', 'config': deepcopy(self.config['config']),
                       'joint': deepcopy(self.config['joint'])}
        second_entry = json.loads(json.dumps(first_entry).replace('post_1', 'post_2').replace('socket_1', 'socket_2'))
        self.config['registry'] = {'post_1': first_entry, 'post_2': second_entry}
        self.events.extend(second_events)
        self.result['graph_states'] = {'post_1': self.result.pop('graph_state'),
                                       'post_2': second_result['graph_state']}
        self.result['results'].extend(second_result['results'])
        bundle = exporter.build_export(self.write())
        self.assertEqual(bundle['summary']['recorded_measured_insertions'], 2)
        self.assertEqual(len({a['attempt_id'] for a in bundle['attempts']}), 2)
        by_id = {r['record_id']: r for r in bundle['records']}
        for record in bundle['records']:
            self.assertTrue(all(by_id[p]['part_id'] == record['part_id'] for p in record['parent_record_ids']))
        selected = exporter.build_export(self.run, ['post_2'])
        self.assertEqual(selected['summary']['recorded_measured_insertions'], 1)
        self.assertEqual({r['part_id'] for r in selected['records']}, {'post_2'})

    def test_failed_check_without_trial_is_unknown_not_a_fabricated_rejection(self):
        self.events = self.events[:3]
        self.events[-1].pop('grasp_trial')
        self.events[-1].update(status='failed', failure_count=1)
        bundle = exporter.build_export(self.write(with_result=False))
        self.assertEqual(bundle['attempts'][0]['preflight_status'], 'outcome_unknown')
        self.assertEqual(bundle['attempts'][0]['live_pick_status'], 'unknown')
        self.assertEqual(bundle['summary']['preflight_rejected'], 0)
        self.assertEqual(bundle['summary']['preflight_accepted'], 0)
        self.assertIsNone(bundle['records'][-1]['data']['trial'])
        self.assertEqual(bundle['records'][-1]['data']['reason'], 'check_node_failed_without_trial')

    def test_executor_grasp_correspondence_is_recorded_only_when_saved_approach_exists(self):
        initial = exporter.build_export(self.write())
        execution = next(r for r in initial['records'] if r['stage'] == 'execution')
        self.assertEqual(execution['data']['executed_grasp_status'], 'unknown')
        approach = {'skill': 'approach/v1', 'part_id': 'post_1',
                    'proposed_grasp': grasp(), 'observation': {
                        'source': 'sim_ground_truth', 'part': {'id': 'post_1'},
                        'target': {'id': 'socket_1'}, 'simulation_time_s': .5}}
        self.result['results'].insert(0, approach)
        matched = exporter.build_export(self.write())
        execution = next(r for r in matched['records'] if r['stage'] == 'execution')
        self.assertEqual(execution['data']['executed_grasp_status'], 'recorded_match')
        self.assertEqual(execution['data']['recorded_executed_approach'], approach)
        self.assertTrue(execution['measurement_refs'])

    def test_saved_approach_mismatched_grasp_target_or_observed_part_is_rejected(self):
        for change in ('grasp', 'target', 'observed_part'):
            with self.subTest(change=change):
                self.events, self.result = completed_fixture()
                approach = {'skill': 'approach_component/v1', 'part_id': 'post_1',
                            'proposed_grasp': grasp(), 'observation': {
                                'source': 'sim_ground_truth', 'part': {'id': 'post_1'},
                                'target': {'id': 'socket_1'}, 'simulation_time_s': .5}}
                if change == 'grasp':
                    approach['proposed_grasp']['z_mm'] = 3.
                elif change == 'target':
                    approach['observation']['target']['id'] = 'other_socket'
                else:
                    approach['observation']['part']['id'] = 'post_2'
                self.result['part_episodes'] = {'post_1': {'executed_approach': approach}}
                with self.assertRaises(ValueError):
                    exporter.build_export(self.write())

    def test_failed_decide_does_not_republish_retained_stale_action(self):
        stale = deepcopy(self.events[-1]['action'])
        self.events.append(event('decide', 3, 2., stale, completed=['pick', 'insert'], status='failed'))
        self.result.update(status='failed', error='Synthetic provider failure after prior action.')
        bundle = exporter.build_export(self.write())
        self.assertEqual(bundle['summary']['stages']['proposal'], 2)
        self.assertEqual(bundle['summary']['attempts'], 1)
        self.assertEqual(bundle['source_episode_status'], 'failed')
        self.assertEqual(bundle['summary']['recorded_measured_insertions'], 1)
        self.assertEqual(bundle['diagnostics'][0]['code'], 'decision_failed_no_new_proposal')

    def test_physical_pass_record_and_failed_episode_are_not_conflated(self):
        self.result.update(status='failed', error='Synthetic error after recorded insertion.')
        self.result['graph_state']['status'] = 'failed'
        bundle = exporter.build_export(self.write())
        self.assertEqual(bundle['source_episode_status'], 'failed')
        self.assertEqual(bundle['part_summaries'][0]['reported_graph_status'], 'failed')
        self.assertEqual(bundle['summary']['recorded_measured_insertions'], 1)

    def test_action_target_mismatch_or_stale_revision_is_rejected(self):
        for field, value in [('target_id', 'other_socket'), ('observation_revision', 0)]:
            with self.subTest(field=field):
                self.events, self.result = completed_fixture()
                self.events[1]['action'][field] = value
                with self.assertRaises(ValueError):
                    exporter.build_export(self.write())

    def test_saved_insertion_measurements_must_bind_target_and_all_required_checks(self):
        for change in ('target', 'missing_check', 'false_check', 'error_outside_tolerance'):
            with self.subTest(change=change):
                self.events, self.result = completed_fixture()
                detail = self.result['results'][0]
                if change == 'target':
                    detail['target_id'] = 'other_socket'
                elif change == 'missing_check':
                    detail['checks'].pop('released')
                elif change == 'false_check':
                    detail['checks']['released'] = False
                else:
                    detail['maximum_final_position_error_m'] = .003
                self.result['graph_state']['last_result'] = deepcopy(detail)
                with self.assertRaises(ValueError):
                    exporter.build_export(self.write())

    def test_preflight_cannot_be_live_scene_or_change_original_scene_time(self):
        for field, value in [('source', 'live_scene'), ('live_start_s', .25), ('accepted', 'true')]:
            with self.subTest(field=field):
                self.events, self.result = completed_fixture()
                self.events[2]['grasp_trial'][field] = value
                with self.assertRaises(ValueError):
                    exporter.build_export(self.write())

    def test_execute_after_rejected_preflight_is_rejected(self):
        self.events[2]['grasp_trial']['accepted'] = False
        with self.assertRaises(ValueError):
            exporter.build_export(self.write())

    def test_malformed_duplicate_key_and_nonfinite_json_are_rejected(self):
        for raw in ('{"unfinished":', '{"node":"observe","node":"decide"}',
                    '{"value":NaN}', '{"value":Infinity}', '{"value":1e999}'):
            with self.subTest(raw=raw):
                self.write()
                (self.run / 'graph_events.jsonl').write_text(raw + '\n', encoding='utf-8')
                with self.assertRaises(ValueError):
                    exporter.build_export(self.run)

    def test_nonfinite_grasp_is_rejected_before_creating_any_export(self):
        self.write()
        path = self.run / 'graph_events.jsonl'
        rows = deepcopy(self.events)
        rows[1]['action']['grasp']['x_mm'] = float('nan')
        path.write_text(''.join(json.dumps(row) + '\n' for row in rows), encoding='utf-8')
        with self.assertRaises(ValueError):
            exporter.build_export(self.run)

    def test_rejects_resumed_or_non_simulation_logs_and_unknown_part_selection(self):
        for change in ('resume', 'real', 'sensor'):
            with self.subTest(change=change):
                self.config = fixture_config()
                if change == 'resume':
                    self.config['resume'] = {'parent_run': 'not_imported'}
                elif change == 'real':
                    self.config['environment'] = 'real'
                else:
                    self.config['observation_source'] = 'real_camera'
                with self.assertRaises(ValueError):
                    exporter.build_export(self.write())
        self.config = fixture_config()
        self.write()
        for parts in ([], ['post_9'], ['post_1', 'post_1']):
            with self.subTest(parts=parts), self.assertRaises(ValueError):
                exporter.build_export(self.run, part_ids=parts)

    def test_backward_time_and_measured_pass_without_recorded_transition_are_rejected(self):
        for change in ('time', 'transition'):
            with self.subTest(change=change):
                self.events, self.result = completed_fixture()
                if change == 'time':
                    self.events[-1]['simulation_time_s'] = .5
                else:
                    self.events[-1]['completed'] = ['pick']
                with self.assertRaises(ValueError):
                    exporter.build_export(self.write())

    def test_failure_counter_cannot_go_backward_or_be_noninteger(self):
        for change in ('decrease', 'boolean'):
            with self.subTest(change=change):
                self.events, self.result = completed_fixture()
                if change == 'decrease':
                    self.events[1]['failure_count'] = 1
                else:
                    self.events[1]['failure_count'] = True
                with self.assertRaises(ValueError):
                    exporter.build_export(self.write())

    def test_replanned_insert_uses_recorded_insert_verification(self):
        for row in self.events[6:]:
            row['action']['skill'] = 'replan_insert'
        bundle = exporter.build_export(self.write())
        self.assertEqual(bundle['summary']['recorded_measured_insertions'], 1)
        self.assertEqual(bundle['records'][-1]['data']['action_skill'], 'replan_insert')
        self.assertEqual(bundle['records'][-1]['data']['measurement_status'], 'recorded_measured_pass')

    def test_nominal_depth_comes_from_saved_joint_not_a_hardcoded_first_post_value(self):
        self.config['joint']['nominal_insertion_depth_m'] = .025
        detail = self.result['results'][0]
        detail['minimum_final_depth_m'] = .0252
        self.result['graph_state']['last_result'] = deepcopy(detail)
        bundle = exporter.build_export(self.write())
        self.assertEqual(bundle['summary']['recorded_measured_insertions'], 1)

    def test_attempt_identity_survives_later_arrival_of_result_measurements(self):
        before = exporter.build_export(self.write(with_result=False))
        after = exporter.build_export(self.write())
        self.assertEqual(before['source_episode_id'], after['source_episode_id'])
        self.assertEqual(before['attempts'][0]['attempt_id'], after['attempts'][0]['attempt_id'])
        self.assertNotEqual(before['source_snapshot_id'], after['source_snapshot_id'])
        self.assertNotEqual(before['export_id'], after['export_id'])
        self.assertEqual(before['summary']['recorded_measured_insertions'], 0)
        self.assertEqual(after['summary']['recorded_measured_insertions'], 1)

    def test_snapshot_and_output_are_deterministic_and_source_bytes_stay_unchanged(self):
        self.write()
        before = {p.name: p.read_bytes() for p in self.run.iterdir()}
        first = exporter.build_export(self.run)
        second = exporter.build_export(self.run, part_ids=['post_1'])
        self.assertEqual(first, second)
        destination, created = exporter.save_export(first, self.root / 'exports')
        first_bytes = destination.read_bytes()
        again, created_again = exporter.save_export(second, self.root / 'exports')
        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertEqual(destination, again)
        self.assertEqual(destination.read_bytes(), first_bytes)
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.run.iterdir()})
        self.assertEqual(list((self.root / 'exports').glob('*.tmp')), [])

    def test_rejects_output_inside_source_and_direct_legacy_memory_root(self):
        bundle = exporter.build_export(self.write())
        for output in (self.run, self.run / 'nested' / 'export'):
            with self.subTest(output=output), self.assertRaises(ValueError):
                exporter.save_export(bundle, output)
        self.assertFalse((self.run / 'nested').exists())
        with patch.object(exporter, 'ROOT', self.root), self.assertRaises(ValueError):
            exporter.save_export(bundle, self.root / 'runtime' / 'memory' / 'simulation')

    def test_tampered_export_and_existing_file_are_not_overwritten(self):
        bundle = exporter.build_export(self.write())
        tampered = deepcopy(bundle)
        tampered['summary']['recorded_measured_insertions'] = 99
        with self.assertRaises(ValueError):
            exporter.validate_export(tampered)
        destination, _ = exporter.save_export(bundle, self.root / 'exports')
        destination.write_text(json.dumps(tampered), encoding='utf-8')
        before = destination.read_bytes()
        with self.assertRaises(ValueError):
            exporter.save_export(bundle, self.root / 'exports')
        self.assertEqual(destination.read_bytes(), before)

    def test_rehashed_broken_parent_chain_is_rejected(self):
        bundle = exporter.build_export(self.write())
        record = bundle['records'][1]
        record['parent_record_ids'] = ['missing-evidence-id']
        record['record_id'] = exporter.digest({k: v for k, v in record.items() if k != 'record_id'})
        bundle['export_id'] = exporter.digest({k: v for k, v in bundle.items() if k != 'export_id'})
        with self.assertRaises(ValueError):
            exporter.validate_export(bundle)


if __name__ == '__main__':
    unittest.main()
