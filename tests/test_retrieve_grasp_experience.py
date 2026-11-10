"""Offline exporter-to-retrieval contracts, never physical assembly evidence."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest

# The first candidate is used in repository tests/, the second during staging.
HERE = Path(__file__).resolve().parent
for candidate in (HERE.parent / 'scripts', HERE.parent / 'grasp_experience', HERE):
    if candidate.is_dir():
        sys.path.insert(0, str(candidate))
from export_grasp_experience import build_export, digest, save_export, validate_export
from retrieve_grasp_experience import query_from_run, retrieve_experiences


def config(size=None):
    return {'environment': 'simulation', 'observation_source': 'sim_ground_truth',
            'simulator_version': 'synthetic-contract-only-not-physics',
            'policy_kind': 'offline_fixture', 'grasp_mode': 'proposed',
            'config': {'part': {'id': 'post_1', 'shape': 'box',
                                'size_m': list(size or [.02, .02, .1]),
                                'spawn_xy_table_m': [.1, .1]},
                       'target': {'id': 'socket_1', 'xy_table_m': [.2, .2]},
                       'robot': {'model': 'ur3e', 'gripper': 'robotiq_2f85'}},
            'joint': {'product_candidate': 'four_post_frame_v1',
                      'nominal_insertion_depth_m': .016, 'nominal_side_clearance_m': .002}}


def fixture(size=None, *, rejected=True):
    cfg = config(size)

    def selected(skill, revision, z=None):
        return {'skill': skill, 'part_id': 'post_1', 'target_id': 'socket_1',
                'observation_revision': revision, 'grasp': None if z is None else
                    {'x_mm': 0., 'y_mm': 0., 'z_mm': z, 'jaw_yaw_deg': 0.},
                'reason': 'Authored offline fixture.'}

    def row(node, revision, seconds, action=None, completed=None, **extra):
        return {'node': node, 'active_part_id': 'post_1', 'status': 'running',
                'observation_revision': revision, 'simulation_time_s': seconds,
                'action': deepcopy(action), 'completed': list(completed or []),
                'failure_count': 0, **extra}

    def preflight(choice, seconds, accepted):
        return {'source': 'mujoco_preflight_copy', 'accepted': accepted,
                'observation_revision': choice['observation_revision'], 'live_start_s': seconds,
                'grasp': deepcopy(choice['grasp']),
                'failure_code': None if accepted else 'PLANNED_CONTACT',
                'phase': None if accepted else 'insert_descent',
                'violations': [] if accepted else [
                    {'geom1': 'finger_pad', 'geom2': 'socket_wall', 'distance_m': -.001}]}

    events = []
    if rejected:
        old = selected('pick', 1, 0.)
        events += [row('observe', 1, 0.), row('decide', 1, 0., old),
                   row('check_grasp', 1, 0., old, grasp_trial=preflight(old, 0., False))]
    revision = 2 if rejected else 1
    pick, insert = selected('pick', revision, 12.), selected('insert', revision + 1)
    events += [row('observe', revision, .1), row('decide', revision, .1, pick),
               row('check_grasp', revision, .1, pick, grasp_trial=preflight(pick, .1, True)),
               row('execute', revision, 1., pick), row('verify', revision, 1., pick, ['pick']),
               row('observe', revision + 1, 1., pick, ['pick']),
               row('decide', revision + 1, 1., insert, ['pick']),
               row('execute', revision + 1, 2., insert, ['pick']),
               row('verify', revision + 1, 2., insert, ['pick', 'insert'], status='success')]
    checks = {name: True for name in ('released', 'target_position_under_1mm',
        'target_orientation_under_1deg', 'insertion_depth_reached', 'linear_speed_under_1mm_s',
        'angular_speed_under_0_01rad_s', 'penetration_under_1mm', 'socket_floor_support')}
    detail = {'skill': 'insert_post/v1', 'part_id': 'post_1', 'target_id': 'socket_1',
              'status': 'success', 'checks': checks, 'maximum_final_position_error_m': .0002,
              'minimum_final_depth_m': .016, 'observation': {
                  'source': 'sim_ground_truth', 'part': {'id': 'post_1'}, 'simulation_time_s': 2.}}
    result = {'status': 'success', 'error': None, 'robot_installed_parts': 1,
              'results': [deepcopy(detail)], 'graph_state': {'status': 'success',
                  'completed': ['pick', 'insert'], 'failures': [], 'last_result': deepcopy(detail)}}
    return cfg, events, result


def resign_internal_records(bundle):
    """Rebuild checksums/references to test semantics, not just hash detection.

    This edits a temporary authored fixture, never an actual source run.
    Recomputable content hashes are integrity checks, not authentication.
    """
    replacements = {}
    for record in bundle['records']:
        prior_id = record['record_id']
        record['parent_record_ids'] = [replacements.get(value, value)
                                       for value in record['parent_record_ids']]
        record['record_id'] = digest({k: v for k, v in record.items() if k != 'record_id'})
        replacements[prior_id] = record['record_id']
    for attempt in bundle['attempts']:
        attempt['record_ids'] = [replacements.get(value, value) for value in attempt['record_ids']]
        parent = attempt.get('parent_failure_record_id')
        if parent is not None:
            attempt['parent_failure_record_id'] = replacements.get(parent, parent)
    bundle['export_id'] = digest({k: v for k, v in bundle.items() if k != 'export_id'})
    validate_export(bundle)
    return bundle


class GraspRetrievalTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.memory = self.root / 'memory'
        self.memory.mkdir()
        self.current = self.write_run('current', *fixture())

    def tearDown(self):
        self.temporary.cleanup()

    def write_run(self, name, cfg, events, result=None):
        path = self.root / name
        path.mkdir(exist_ok=True)
        (path / 'resolved_config.json').write_text(json.dumps(cfg, allow_nan=False), encoding='utf-8')
        (path / 'graph_events.jsonl').write_text(
            ''.join(json.dumps(e, allow_nan=False) + '\n' for e in events), encoding='utf-8')
        if result is not None:
            (path / 'result.json').write_text(json.dumps(result, allow_nan=False), encoding='utf-8')
        return path

    def source(self, name='source', *, size=None, with_result=True, rejected=True):
        cfg, events, result = fixture(size, rejected=rejected)
        run = self.write_run(name, cfg, events, result if with_result else None)
        bundle = build_export(run)
        save_export(bundle, self.memory)
        return run, bundle

    def query(self, *, code='PLANNED_CONTACT', phase='insert_descent'):
        return query_from_run(self.current, 'post_1', failure_code=code, failure_phase=phase)

    def search(self, query=None, **kwargs):
        return retrieve_experiences(query or self.query(), memory_dir=self.memory, **kwargs)

    def put(self, bundle, name=None, directory=None):
        target = (directory or self.memory) / (name or (bundle['export_id'] + '.json'))
        target.write_text(json.dumps(bundle, allow_nan=False), encoding='utf-8')
        return target

    def test_failed_proposal_and_measured_correction_stay_together_in_one_card(self):
        self.source()
        result = self.search()
        self.assertEqual(result['summary']['returned_cards'], 1)
        card = result['cards'][0]
        self.assertTrue(card['match']['measured_correction_available'])
        self.assertTrue(card['match']['phase_exact'])
        self.assertEqual(len(card['attempts']), 2)
        first, second = card['attempts']
        self.assertEqual(first['preflight_status'], 'rejected')
        self.assertEqual(second['parent_attempt_id'], first['attempt_id'])
        self.assertEqual(second['changed_parameters'], {'z_mm': 12.})
        self.assertTrue(second['recorded_measured_insertion'])
        failure = next(e for e in first['evidence'] if e['stage'] == 'preflight')
        self.assertEqual(failure['contacts'][0]['geom2'], 'socket_wall')
        self.assertEqual(failure['failure_code'], 'PLANNED_CONTACT')
        self.assertFalse(card['execution_authorized'])
        self.assertTrue(card['requires_current_scene_validation'])
        self.assertEqual(result['API_calls'], 0)
        self.assertEqual(result['new_physics_runs'], 0)

    def test_missing_result_remains_graph_claim_without_measured_success(self):
        self.source(with_result=False)
        card = self.search()['cards'][0]
        self.assertFalse(card['match']['measured_correction_available'])
        self.assertEqual(card['source_episode_statuses'], [None])
        self.assertEqual(card['attempts'][1]['insertion_status'], 'graph_verified_without_saved_measurements')
        self.assertFalse(any(a['recorded_measured_insertion'] for a in card['attempts']))

    def test_current_episode_is_excluded_even_if_saved_outcome_is_success(self):
        save_export(build_export(self.current), self.memory)
        self.source('independent')
        result = self.search()
        self.assertEqual([c['source_run_id'] for c in result['cards']], ['independent'])
        self.assertEqual(result['summary']['excluded']['current_episode_excluded'], 1)

    def test_current_run_name_is_excluded_after_configuration_changes(self):
        bundle = build_export(self.current)
        save_export(bundle, self.memory)
        cfg, events, outcome = fixture([.021, .021, .1])
        self.write_run('current', cfg, events, outcome)
        self.assertNotEqual(bundle['source_episode_id'], self.query()['source_episode_id'])
        result = self.search()
        self.assertEqual(result['cards'], [])
        self.assertEqual(result['summary']['excluded']['current_episode_excluded'], 1)

    def test_query_reads_only_configuration_not_target_outcome_or_graph_log(self):
        self.source()
        before = self.query()
        (self.current / 'result.json').write_text('not JSON and must not be read', encoding='utf-8')
        (self.current / 'graph_events.jsonl').write_text('not JSON either', encoding='utf-8')
        after = self.query()
        self.assertEqual(before, after)
        self.assertEqual(len(self.search(after)['cards']), 1)

    def test_source_coordinate_frame_and_units_are_incompatible_not_remapped(self):
        _, original = self.source()
        for field, value in [('grasp_position_unit', 'm'), ('grasp_frame', 'world'),
                             ('jaw_yaw_unit', 'rad')]:
            with self.subTest(field=field):
                changed = deepcopy(original)
                changed['contexts']['post_1'][field] = value
                changed['export_id'] = digest({k: v for k, v in changed.items() if k != 'export_id'})
                isolated = self.root / field
                isolated.mkdir()
                self.put(changed, directory=isolated)
                result = retrieve_experiences(self.query(), memory_dir=isolated)
                self.assertEqual(result['cards'], [])
                self.assertEqual(result['summary']['excluded']['incompatible_robot_or_convention'], 2)

    def test_query_requires_declared_units_robot_and_positive_geometry(self):
        for change in ('units', 'frame', 'gripper', 'size', 'depth', 'clearance'):
            with self.subTest(change=change):
                query = self.query()
                if change == 'units':
                    query['context']['grasp_position_unit'] = 'm'
                elif change == 'frame':
                    query['context']['grasp_frame'] = 'world'
                elif change == 'gripper':
                    query['context']['config']['robot']['gripper'] = 'other'
                elif change == 'size':
                    query['context']['config']['part']['size_m'][0] = 0
                elif change == 'depth':
                    query['context']['joint']['nominal_insertion_depth_m'] = -1
                else:
                    query['context']['joint']['nominal_side_clearance_m'] = -1
                with self.assertRaises(ValueError):
                    self.search(query)

    def test_closer_dimensions_rank_before_distant_success_with_same_failure(self):
        self.source('far', size=[.04, .04, .2])
        self.source('near', size=[.022, .022, .11])
        result = self.search()
        self.assertEqual([c['source_run_id'] for c in result['cards']], ['near', 'far'])
        self.assertGreater(result['cards'][0]['match']['dimension_similarity'],
                           result['cards'][1]['match']['dimension_similarity'])
        self.assertTrue(all(c['match']['class'] == 'related_post_conditions' for c in result['cards']))

    def test_failure_mismatch_returns_zero_without_falling_back_to_unrelated_success(self):
        self.source()
        result = self.search(self.query(code='PART_LOST', phase='pick_lift'))
        self.assertEqual(result['cards'], [])
        self.assertEqual(result['summary']['excluded']['no_matching_failure_code'], 1)

    def test_failure_filter_is_optional_and_phase_requires_a_code(self):
        self.source(rejected=False)
        self.assertEqual(self.search()['cards'], [])
        result = self.search(self.query(code=None, phase=None))
        self.assertEqual(len(result['cards']), 1)
        self.assertFalse(result['cards'][0]['match']['measured_correction_available'])
        with self.assertRaises(ValueError):
            self.query(code=None, phase='insert_descent')

    def test_query_rejects_bad_failure_codes_and_unknown_parts(self):
        for code in ('planned contact', '', 7):
            with self.subTest(code=code), self.assertRaises(ValueError):
                self.query(code=code)
        with self.assertRaises(ValueError):
            query_from_run(self.current, 'sleeve_1')

    def test_at_most_three_cards_with_explicit_remaining_result_count(self):
        for name in ('alpha', 'beta', 'gamma', 'delta', 'epsilon'):
            self.source(name)
        result = self.search()
        self.assertEqual(len(result['cards']), 3)
        self.assertEqual(len({c['source_episode_id'] for c in result['cards']}), 3)
        self.assertEqual(result['summary']['excluded']['result_limit'], 2)
        self.assertEqual(len(self.search(limit=1)['cards']), 1)
        for invalid in (0, 4, True):
            with self.subTest(limit=invalid), self.assertRaises(ValueError):
                self.search(limit=invalid)

    def test_only_one_part_family_from_same_episode_can_fill_results(self):
        cfg, events, result = fixture()
        cfg2, events2, result2 = json.loads(json.dumps(fixture()).replace('post_1', 'post_2').replace('socket_1', 'socket_2'))
        for row in events2:
            row['simulation_time_s'] += 10.
            if 'grasp_trial' in row:
                row['grasp_trial']['live_start_s'] += 10.
        for detail in result2['results']:
            detail['observation']['simulation_time_s'] += 10.
        result2['graph_state']['last_result']['observation']['simulation_time_s'] += 10.
        cfg['registry'] = {'post_1': {'kind': 'post', 'config': cfg['config'], 'joint': cfg['joint']},
                           'post_2': {'kind': 'post', 'config': cfg2['config'], 'joint': cfg2['joint']}}
        result['graph_states'] = {'post_1': result.pop('graph_state'), 'post_2': result2['graph_state']}
        result['results'] += result2['results']
        run = self.write_run('two_posts', cfg, events + events2, result)
        save_export(build_export(run), self.memory)
        search = self.search()
        self.assertEqual(search['summary']['matching_families'], 2)
        self.assertEqual(len(search['cards']), 1)
        self.assertEqual(search['summary']['excluded']['one_card_per_episode'], 1)

    def test_corrupt_hash_is_quarantined_without_changing_valid_evidence(self):
        _, good = self.source('good')
        _, bad = self.source('bad')
        bad_path = self.memory / (bad['export_id'] + '.json')
        bad['summary']['recorded_measured_insertions'] = 99
        bad_path.write_text(json.dumps(bad), encoding='utf-8')
        before = {p.name: p.read_bytes() for p in self.memory.iterdir()}
        result = self.search()
        self.assertEqual([c['source_run_id'] for c in result['cards']], ['good'])
        self.assertEqual(result['summary']['excluded']['invalid_export'], 1)
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.memory.iterdir()})

    def test_rehashed_attempt_summary_cannot_promote_rejection_to_success(self):
        cfg, events, result = fixture()
        original = build_export(self.write_run('summary_source', cfg, events, result))
        cases = [('preflight', 0, 'preflight_status', 'accepted'),
                 ('live_pick', 0, 'live_pick_status', 'graph_verified'),
                 ('first_delta', 0, 'changed_parameters', {'z_mm': 999.}),
                 ('corrected_delta', 1, 'changed_parameters', {'z_mm': 999.})]
        for label, index, field, value in cases:
            with self.subTest(field=label):
                forged = deepcopy(original)
                forged['attempts'][index][field] = value
                resign_internal_records(forged)
                isolated = self.root / label
                isolated.mkdir()
                self.put(forged, directory=isolated)
                result = retrieve_experiences(self.query(), memory_dir=isolated)
                self.assertEqual(result['cards'], [])
                self.assertEqual(result['summary']['excluded']['invalid_export'], 1)
                self.assertIn('Attempt summary disagrees', result['diagnostics'][0]['detail'])

    def test_rehashed_copy_and_live_scene_claims_are_checked_against_stage(self):
        cfg, events, result = fixture()
        original = build_export(self.write_run('scene_source', cfg, events, result))
        for stage, wrong_scene in [('preflight', 'live'), ('execution', 'copy'), ('verification', 'copy')]:
            with self.subTest(stage=stage):
                forged = deepcopy(original)
                selected = next(row for row in forged['records'] if row['stage'] == stage)
                selected['data']['scene'] = wrong_scene
                resign_internal_records(forged)
                isolated = self.root / ('scene_' + stage)
                isolated.mkdir()
                self.put(forged, directory=isolated)
                result = retrieve_experiences(self.query(), memory_dir=isolated)
                self.assertEqual(result['cards'], [])
                self.assertEqual(result['summary']['excluded']['invalid_export'], 1)
                self.assertIn('Copy/live scene evidence mismatch', result['diagnostics'][0]['detail'])

    def test_rehashed_pick_execution_requires_its_own_accepted_preflight_parent(self):
        cfg, events, result = fixture()
        original = build_export(self.write_run('chain_source', cfg, events, result))
        for change in ('missing_parent', 'proposal_parent', 'rejected_preflight'):
            with self.subTest(change=change):
                forged = deepcopy(original)
                executed = next(row for row in forged['records'] if row['stage'] == 'execution'
                                and row['data']['action']['skill'] == 'pick')
                parent = next(row for row in forged['records']
                              if row['record_id'] == executed['parent_record_ids'][0])
                if change == 'missing_parent':
                    executed['parent_record_ids'] = []
                elif change == 'proposal_parent':
                    executed['parent_record_ids'] = list(parent['parent_record_ids'])
                else:
                    parent['data']['status'] = 'rejected'
                    parent['data']['trial']['accepted'] = False
                    corrected = next(a for a in forged['attempts'] if a['attempt_id'] == executed['attempt_id'])
                    corrected['preflight_status'] = 'rejected'
                resign_internal_records(forged)
                isolated = self.root / change
                isolated.mkdir()
                self.put(forged, directory=isolated)
                result = retrieve_experiences(self.query(), memory_dir=isolated)
                self.assertEqual(result['cards'], [])
                self.assertEqual(result['summary']['excluded']['invalid_export'], 1)
                expected = ('Pick execution lacks accepted copy preflight' if change == 'rejected_preflight'
                            else 'Broken proposal/check/execution/verification chain')
                self.assertIn(expected, result['diagnostics'][0]['detail'])

    def test_nonfinite_or_partial_json_is_quarantined(self):
        self.source()
        self.memory.joinpath('bad.json').write_text('{"x":NaN}', encoding='utf-8')
        self.memory.joinpath('partial.json').write_text('{"unfinished":', encoding='utf-8')
        result = self.search()
        self.assertEqual(len(result['cards']), 1)
        self.assertEqual(result['summary']['excluded']['invalid_export'], 2)

    def test_later_result_snapshot_enriches_same_attempt_instead_of_duplicate_examples(self):
        cfg, events, result = fixture()
        run = self.write_run('eventually_complete', cfg, events)
        incomplete = build_export(run)
        save_export(incomplete, self.memory)
        self.write_run('eventually_complete', cfg, events, result)
        complete = build_export(run)
        save_export(complete, self.memory)
        self.assertEqual([a['attempt_id'] for a in incomplete['attempts']],
                         [a['attempt_id'] for a in complete['attempts']])
        search = self.search()
        self.assertEqual(search['summary']['files_scanned'], 2)
        self.assertEqual(search['summary']['unique_attempts'], 2)
        self.assertEqual(len(search['cards']), 1)
        self.assertEqual(len(search['cards'][0]['attempts']), 2)
        self.assertTrue(search['cards'][0]['attempts'][1]['recorded_measured_insertion'])

    def test_conflicting_parent_versions_also_quarantine_successful_descendant(self):
        cfg, events, result = fixture()
        run = self.write_run('conflicting', cfg, events, result)
        first = build_export(run)
        save_export(first, self.memory)
        # The same proposed action has incompatible saved failure facts.
        events[2]['grasp_trial']['phase'] = 'contradictory_phase'
        self.write_run('conflicting', cfg, events, result)
        second = build_export(run)
        save_export(second, self.memory)
        self.assertEqual([a['attempt_id'] for a in first['attempts']],
                         [a['attempt_id'] for a in second['attempts']])
        search = self.search()
        self.assertEqual(search['cards'], [])
        self.assertEqual(search['summary']['excluded']['conflicting_attempt_versions'], 1)
        self.assertEqual(search['summary']['excluded']['missing_or_conflicting_parent'], 1)

    def test_equal_rank_order_is_independent_of_file_names_and_repeat_calls(self):
        bundles = [self.source(name)[1] for name in ('a', 'b', 'c')]
        other = self.root / 'permuted'
        other.mkdir()
        for i, bundle in enumerate(reversed(bundles)):
            self.put(bundle, name=str(i) + '.json', directory=other)
        query = self.query()
        before = deepcopy(query)
        first = self.search(query)
        second = retrieve_experiences(query, memory_dir=other)
        self.assertEqual([c['card_id'] for c in first['cards']], [c['card_id'] for c in second['cards']])
        self.assertEqual(first, self.search(query))
        self.assertEqual(query, before)

    def test_missing_dimensions_and_real_domain_never_become_compatible_queries(self):
        for change in ('missing_size', 'real'):
            with self.subTest(change=change):
                cfg, events, result = fixture()
                if change == 'missing_size':
                    cfg['config']['part'].pop('size_m')
                else:
                    cfg['environment'] = 'real'
                run = self.write_run(change, cfg, events, result)
                with self.assertRaises((ValueError, KeyError)):
                    query_from_run(run, 'post_1')


if __name__ == '__main__':
    unittest.main()
