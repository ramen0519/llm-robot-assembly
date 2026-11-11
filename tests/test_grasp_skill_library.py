"""Portable catalogue tests using authored, temporary records and no physics or API."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'scripts'))

import grasp_skill_library as library
from export_grasp_experience import CHECKS, _post_contexts, digest
from grasp_skill_runtime import bind_rejection, make_observation
from test_retrieve_grasp_experience import config as part_config


class SkillLibraryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.store = self.root / 'catalogue'
        self.parent = {'candidate_id': digest('fixture-parent'), 'code_sha256': digest('parent-code')}
        self.revision = {'revision_id': digest('fixture-revision'), 'code_sha256': digest('revision-code'),
                         'parent_candidate_id': self.parent['candidate_id']}
        # Generation and the memory/lesson validators have their own tests. Here
        # only those upstream dependencies are stubbed. All admission, scope,
        # rejection binding, final measurement and catalogue checks run for real.
        self.enterContext(patch.object(library, 'validate_candidate', autospec=True))
        self.enterContext(patch.object(library, 'validate_revision', autospec=True))

    def observation(self, episode, revision, *, size=None, installed=None, holding=False):
        raw = part_config(size=size or [.02, .02, .06])
        return make_observation(episode_id=episode, revision=revision,
            condition_context=_post_contexts(raw)['post_1'], simulator_version=raw['simulator_version'],
            simulation_time_s=3., measurements={'holding_part': holding, 'supported': True,
                'left_contact': False, 'right_contact': False, 'upright_cosine': 1.,
                'linear_speed_m_s': 0., 'angular_speed_rad_s': 0., 'pad_gap_mm': 85.},
            state_fingerprint=digest('fixture-state'), provenance='mujoco_live_snapshot',
            installed_parts=[] if installed is None else installed)

    def context(self, episode, *, trial_edits=None, **obs_edits):
        old = self.observation(episode, 1, **obs_edits)
        trial = {'grasp': {'x_mm': 0., 'y_mm': 0., 'z_mm': 1., 'jaw_yaw_deg': 0.},
            'accepted': False, 'source': 'mujoco_preflight_copy', 'observation_revision': 1,
            'live_start_s': 3., 'copy_end_s': 55., 'failure_code': 'PLANNED_CONTACT',
            'phase': 'insert_lower/preflight', 'held_pad_geometry': {
                'predicted_lowest_pad_height_relative_to_rim_mm': -2.37},
            'violations': [{'geoms': ['gripper_right_pad1', 'assembly_base_wall_fixture']}],
            'trace_summary': {'trace_complete': True, 'pick_completed': True}}
        trial.update(trial_edits or {})
        return self.observation(episode, 2, **obs_edits), bind_rejection(old, trial)

    def documents(self, episode='verified-fixture'):
        obs, rejection = self.context(episode)
        part = obs['condition_context']['config']['part']['id']
        target = obs['condition_context']['config']['target']['id']
        depth = obs['condition_context']['joint']['nominal_insertion_depth_m']
        grasp = {'x_mm': 0., 'y_mm': 0., 'z_mm': 4.37, 'jaw_yaw_deg': 0.}
        checks = dict.fromkeys(CHECKS | {'declared_mating_support'}, True)
        row = {'simulation_time_s': 70., 'supported': True, 'fixture_supported': True,
            'left_contact': False, 'right_contact': False, 'pad_gap_mm': 85.,
            'part_speed_m_s': .00001, 'part_angular_speed_rad_s': .0001, 'penetration_m': .0001,
            'target_position_error_m': .00008, 'target_orientation_error_rad': .001,
            'fixture_contact_force_n': .2, 'insertion_depth_m': depth + .00012}
        final = {'measurement_domain': 'original_simulation', 'installed_part': part, 'target_id': target,
            'read_only_measurements': True, 'other_parts_preserved': True, 'checks': checks, 'row': row,
            'simulation_time_s': 70., 'insertion_window_measured_depth_m': depth + .0001,
            'insertion_window_max_position_error_m': .00012}
        trial = {'grasp': grasp, 'accepted': True, 'source': 'mujoco_preflight_copy',
            'live_start_s': 3., 'copy_end_s': 70., 'observation_revision': 2,
            'checks': checks, 'minimum_final_depth_m': depth + .0001,
            'trace_summary': {'trace_complete': True, 'pick_completed': True}, 'trace_paths': {}}
        proposal = {'kind': 'post_grasp_proposal', 'grasp': grasp, 'part_id': part, 'target_id': target,
            'episode_id': episode, 'observation_revision': 2, 'observation_id': obs['observation_id'],
            'candidate_id': self.revision['revision_id'], 'code_sha256': self.revision['code_sha256'],
            'rejection_id': rejection['rejection_id'], 'requires_current_scene_preflight': True,
            'execution_authorized': False}
        result = {'schema': 'post_grasp_revision_trial/v1', 'status': 'original_simulation_verified',
            'environment': 'simulation', 'episode_id': episode, 'simulator_version': obs['simulator_version'],
            'current_rejection_id': rejection['rejection_id'], 'original_execution_requested': True,
            'original_execution_started': True, 'original_stage': 'verified', 'original_scene_assembly_success': True,
            'input_artifacts_unchanged': True, 'original_state_unchanged_through_preflights': True,
            'original_installed_parts': [part], 'revision_id': self.revision['revision_id'],
            'code_sha256': self.revision['code_sha256'], 'parent_candidate_id': self.parent['candidate_id'],
            'parent_code_sha256': self.parent['code_sha256'], 'original_final_verification': deepcopy(final)}
        return {'result.json': result, 'original_final_verification.json': final,
            'parent_candidate_snapshot.json': self.parent, 'revision_snapshot.json': self.revision,
            'revision_observation.json': obs, 'reproduced_rejection_binding.json': rejection,
            'revision/result.json': {'status': 'preflight_accepted', 'original_state_unchanged': True,
                'original_scene_assembly_executed': False, 'trial': trial,
                'evaluation': {'status': 'proposed', 'candidate_id': self.revision['revision_id'],
                    'code_sha256': self.revision['code_sha256'], 'proposal': proposal}},
            'original_pick_intent.json': {'environment': 'simulation', 'start_time_s': 3., 'grasp': grasp,
                'part_id': part, 'target_id': target, 'observation_revision': 2,
                'observation_id': obs['observation_id'], 'preflight_trace': {}},
            'original_insert_result.json': {'skill': 'insert_post/v1', 'status': 'success', 'part_id': part,
                'target_id': target, 'minimum_final_depth_m': depth + .0001,
                'maximum_final_position_error_m': .00012, 'checks': checks,
                'observation': {'source': 'sim_ground_truth', 'simulation_time_s': 70., 'part': {'id': part}}}}

    def admit(self, docs, label='input'):
        folder = self.root / label
        for name, payload in docs.items():
            target = folder / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(payload, allow_nan=False), encoding='utf-8')
        return library.admit_verified_run(folder, self.store)

    def test_repeated_admission_is_idempotent_and_search_distinguishes_memory_from_skill(self):
        docs = self.documents()
        identity = self.admit(docs)
        self.assertEqual(self.admit(docs), identity)
        snapshot = library.SkillLibrary(self.store)
        self.assertEqual((snapshot.skill_count, snapshot.document_count), (1, 2))
        self.assertEqual({c['kind'] for c in snapshot.search('post insertion')}, {'memory', 'skill'})
        self.assertEqual(len(list(self.store.glob('*.json'))), 1)
        self.assertEqual(list(self.store.glob('*.tmp')), [])
        self.assertEqual(snapshot.search('unrelatedterm'), [])
        again = library.SkillLibrary(self.store)
        self.assertEqual(again.snapshot_id, snapshot.snapshot_id)

    def test_running_snapshot_remains_frozen_and_returned_copies_are_independent(self):
        first = self.admit(self.documents())
        old = library.SkillLibrary(self.store)
        second = self.admit(self.documents('another-verified-fixture'), 'second_input')
        current = library.SkillLibrary(self.store)
        self.assertNotEqual(first, second)
        self.assertNotEqual(current.snapshot_id, old.snapshot_id)
        self.assertEqual((old.skill_count, current.skill_count), (1, 2))
        with self.assertRaises(KeyError):
            old.read(second)
        card = old.read(first)
        card['scope']['installed_parts'].append('mutated')
        self.assertEqual(old.read(first)['scope']['installed_parts'], [])
        obs, rejection = self.context('new-episode')
        p, r = old.resolve(first, obs, rejection)
        self.assertEqual((p, r), (self.parent, self.revision))
        r['code_sha256'] = 'mutated'
        self.assertEqual(old.resolve(first, obs, rejection)[1], self.revision)

    def test_digest_mismatch_on_disk_rejected_without_changing_pinned_snapshot(self):
        identity = self.admit(self.documents())
        old = library.SkillLibrary(self.store)
        path = next(self.store.glob('*.json'))
        entry = json.loads(path.read_text(encoding='utf-8'))
        entry['observed']['depth_mm'] = 999
        path.write_text(json.dumps(entry), encoding='utf-8')
        with self.assertRaises(ValueError):
            library.SkillLibrary(self.store)
        self.assertNotEqual(old.read(identity)['evidence']['depth_mm'], 999)

    def test_resolve_rejects_memory_wrong_scope_source_episode_and_failed_preconditions(self):
        identity = self.admit(self.documents())
        snapshot = library.SkillLibrary(self.store)
        obs, rejection = self.context('new')
        for bad_id in (identity.replace('skill:', 'memory:'), 'skill:missing'):
            with self.subTest(document=bad_id), self.assertRaises(ValueError):
                snapshot.resolve(bad_id, obs, rejection)
        cases = [('same episode', 'verified-fixture', {}), ('different size', 'new', {'size': [.03, .02, .06]}),
                 ('installed parts', 'new', {'installed': ['post_2']}), ('holding', 'new', {'holding': True}),
                 ('missing projection', 'new', {'trial_edits': {'held_pad_geometry': {}}}),
                 ('different phase', 'new', {'trial_edits': {'phase': 'pick_lift'}}),
                 ('different contacts', 'new', {'trial_edits': {'violations': []}}),
                 ('pick not completed', 'new', {'trial_edits': {'trace_summary': {'pick_completed': False, 'trace_complete': True}}}),
                 ('incomplete trace', 'new', {'trial_edits': {'trace_summary': {'pick_completed': True, 'trace_complete': False}}})]
        for label, episode, updates in cases:
            with self.subTest(case=label), self.assertRaises(ValueError):
                snapshot.resolve(identity, *self.context(episode, **updates))
        broken = deepcopy(rejection)
        broken['trial']['grasp']['z_mm'] += 1
        with self.assertRaises(ValueError):
            snapshot.resolve(identity, obs, broken)

    def test_admission_rejects_partial_original_execution_and_inconsistent_bindings(self):
        changes = [('result.json', 'original_scene_assembly_success', False),
            ('result.json', 'original_stage', 'pick'), ('result.json', 'original_execution_started', False),
            ('original_pick_intent.json', 'observation_id', 'another-observation'),
            ('original_pick_intent.json', 'part_id', 'post_2'), ('revision/result.json', 'original_state_unchanged', False),
            ('original_insert_result.json', 'skill', 'pick/v1'),
            ('original_insert_result.json', 'minimum_final_depth_m', .009)]
        for filename, key, value in changes:
            with self.subTest(file=filename, field=key):
                docs = self.documents()
                docs[filename][key] = value
                with self.assertRaises(ValueError):
                    self.admit(docs)
        self.assertEqual(list(self.store.glob('*.json')), [])

    def test_admission_checks_the_independent_sample_even_when_success_flags_agree(self):
        for kind, key, value in [('row', 'target_position_error_m', .002), ('row', 'insertion_depth_m', .001),
                ('row', 'left_contact', True), ('row', 'fixture_supported', False),
                ('summary', 'insertion_window_max_position_error_m', False)]:
            with self.subTest(kind=kind, key=key):
                docs = self.documents()
                final = docs['original_final_verification.json']
                (final['row'] if kind == 'row' else final)[key] = value
                if kind == 'summary':
                    docs['original_insert_result.json']['maximum_final_position_error_m'] = value
                # Both files agree on the changed value; mismatched copies alone
                # cannot catch contradictions between raw measurements and checks.
                docs['result.json']['original_final_verification'] = deepcopy(final)
                with self.assertRaises(ValueError):
                    self.admit(docs)
        self.assertEqual(list(self.store.glob('*.json')), [])


if __name__ == '__main__':
    unittest.main()
