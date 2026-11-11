"""Synthetic source-only records; no ignored run files, robot motion or API required."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import sleeve_grasp_memory as m


class SleeveMemoryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.source = self.root / 'independent_source'
        self.source.mkdir()
        self.robot = {'model': 'fixture_arm', 'gripper': 'fixture_gripper'}
        self.geom = {k: 'fixture ' + k for k in m.GEOMETRY_KEYS}
        self.geom.update(part_size_xyz_mm=[36, 36, 30], bore_width_mm=24, mating_post_width_mm=20,
                         mating_post_id='post_1', gripper_max_open_mm=85)
        self.low = {'x_mm': 0, 'y_mm': 0, 'z_mm': -5, 'jaw_yaw_deg': 0}
        self.high = {**self.low, 'z_mm': 11}
        checks = dict.fromkeys(m.INSERT_CHECKS, True)
        fit = {'post_id': 'post_1', 'minimum_bore_side_clearance_mm': 1.8, 'minimum_post_extension_past_openings_mm': 12}
        bad = {'accepted': False, 'source': 'mujoco_preflight_copy', 'live_start_s': 2, 'phase': 'pick_close',
               'failure_code': 'UNEXPECTED_CONTACT', 'grasp': self.low, 'observation_revision': 1, 'violations': [
               {'geoms': ['gripper_right_pad1', 'workcell_tabletop'], 'signed_distance_m': -.00001}]}
        good = {'accepted': True, 'source': 'mujoco_preflight_copy', 'live_start_s': 2, 'grasp': self.high,
                'observation_revision': 2}
        proof = {'kind': 'verified_original_pick', 'held_and_lifted': True, 'recovery_method': 'numeric_grasp_revision',
                 'accepted_grasp': self.high, 'preflight_revision': 2, 'source_state': {'simulation_time_s': 20},
                 'part_bottom_clearance_m': .099}
        phase = {'start': {'installed': ['post_1']}, 'end': {'installed': ['post_1', 'sleeve_1']},
                 'executed_approach': {'part_id': 'sleeve_1', 'status': 'success', 'proposed_grasp': self.high}}
        state = {'status': 'success', 'completion_source': 'physics_verifier', 'completed': ['pick', 'insert'], 'grasp_trials': [bad, good],
                 'agent_handoffs': [{'from': 'assembly', 'to': 'recovery', 'part_id': 'sleeve_1', 'observation_revision': 2,
                                     'proof': {'rejected_grasp': self.low, 'phase': 'pick_close', 'failure_code': 'UNEXPECTED_CONTACT'}},
                                    {'from': 'recovery', 'to': 'assembly', 'part_id': 'sleeve_1', 'observation_revision': 3, 'proof': proof}],
                 'verification': {'checks': checks, 'sleeve_fit': fit}}
        self.result = {'status': 'success', 'graph_states': {'sleeve_1': state, 'sleeve_2': 'private-sibling-outcome'},
                       'part_episodes': {'sleeve_1': phase}, 'results': [{'part_id': 'sleeve_1', 'status': 'success',
                       'skill': 'insert_sleeve/v1', 'target_id': 'target', 'checks': checks, 'sleeve_fit': fit,
                       'minimum_final_depth_m': .044}, {'part_id': 'sleeve_2', 'private': 'private-sibling-outcome'}]}
        self.write('result.json', self.result)
        self.write('resolved_config.json', {'environment': 'simulation', 'observation_source': 'sim_ground_truth',
                   'policy_kind': 'openai_responses', 'orchestration': 'roles',
                   'registry': {'sleeve_1': {'kind': 'sleeve', 'requires': ['base', 'post_1'], 'config': {'robot': self.robot},
                                              'geometry_representation': 'compound_boxes'}},
                   'transport_strategy': 'direct_then_rrt', 'empty_approach_strategy': 'direct_then_rrt'})
        packet = {'part_id': 'sleeve_1', 'completed': [], 'observation_revision': 1,
                  'observation': self.observation('sleeve_1'), 'grasp_task': {'geometry': self.geom}}
        events = []
        for i, (owner, skill, grasp) in enumerate((('assembly', 'pick', self.low), ('recovery', 'pick', self.high),
                                                    ('assembly', 'insert', None)), 1):
            ctx = {**packet, 'target_id': 'target', 'active_role': owner, 'observation_revision': i}
            events.append({'event': 'request_prepared', 'request': {'input': json.dumps(ctx)}})
            events.append({'event': 'model_response_received', 'response_status': 'completed', 'response_id': 'fixture_' + str(i), 'agent_role': owner})
            events.append({'event': 'proposal_validated', 'response_id': 'fixture_' + str(i), 'agent_role': owner,
                           'proposal': {'part_id': 'sleeve_1', 'target_id': 'target', 'skill': skill,
                                        'grasp': grasp, 'observation_revision': i}})
        (self.source / 'llm_events.jsonl').write_text('\n'.join(map(json.dumps, events)), encoding='utf-8')
        self.csv = self.source / 'skill_trace.csv'
        self.csv.write_text('active_part_id,stage,simulation_time_s,supported,left_contact,right_contact,'
                            'pad_gap_mm,part_bottom_clearance_m\nsleeve_1,pick_lift,20,False,True,True,36,.099\n',
                            encoding='utf-8')
        self.record = m._admit(self.source)

    def write(self, name, value):
        (self.source / name).write_text(json.dumps(value), encoding='utf-8')

    def observation(self, part):
        mating = part.replace('sleeve_', 'post_')
        return {'source': 'sim_ground_truth', 'part': {'id': part, 'quaternion_xyzw': [0., 0., 0., 1.],
                'linear_speed_m_s': 0., 'angular_speed_rad_s': 0.}, 'all_parts': {
                part: {'installed': False}, mating: {'installed': True}, 'other': {'secret': 'private-sibling-outcome'}}}

    def query(self, part='sleeve_2'):
        skills = SimpleNamespace(active=part, registry={part: {'kind': 'sleeve', 'geometry_representation': 'compound_boxes'}},
                config={'robot': self.robot}, transport_strategy='direct_then_rrt', empty_approach_strategy='direct_then_rrt',
                observe=lambda: self.observation(part), grasp_geometry=lambda: {**self.geom, 'mating_post_id': part.replace('sleeve_', 'post_')},
                motion=SimpleNamespace(held_pose=None), installed={part.replace('sleeve_', 'post_')}, results=[])
        return m.build_query(skills, observation_revision=1, episode_id='new_episode')

    def test_source_admission_atomic_idempotence_and_only_source_exposed(self):
        path, created = m.save_source_experience(self.source, output_dir=self.root / 'memory')
        self.assertTrue(created)
        self.assertEqual(m.save_source_experience(self.source, output_dir=path.parent), (path, False))
        loaded = m.load_experience(path)
        for part in ('sleeve_2', 'sleeve_4'):
            card = m.retrieve_experience(loaded, self.query(part), search_text='tabletop pad contact when closing')
            self.assertEqual(card['record_id'], self.record['experience_id'])
            self.assertEqual((card['rejected']['grasp'], card['repair']['grasp']), (self.low, self.high))
            self.assertTrue(card['original_pick']['left_contact'])
            self.assertTrue(all(card['original_insert']['checks'].values()))
            self.assertNotIn(part, json.dumps(card))
            self.assertNotIn('private-sibling-outcome', json.dumps(card))
            card['repair']['grasp']['z_mm'] = 999
            self.assertEqual(loaded['repair']['grasp']['z_mm'], 11)

    def test_incompatible_shapes_robot_frames_and_strategy_cannot_return_a_card(self):
        for key, changed in [('shape', 'solid'), ('bore_width_mm', 22), ('frame', 'top center'), ('orientation', 'world')]:
            query = self.query()
            query['scope']['geometry'][key] = changed
            self.assertIsNone(m.retrieve_experience(self.record, query))
        for key, value in [('gripper', 'two-other-fingers'), ('transport_strategy', 'direct'), ('environment', 'real')]:
            query = self.query()
            query['scope'][key] = value
            self.assertIsNone(m.retrieve_experience(self.record, query))

    def test_query_requires_upright_unheld_uninstalled_sleeve_and_installed_matching_post(self):
        for key, wrong in [('mating_post_id', 'post_99'), ('mating_post_installed', False), ('part_installed', True),
                           ('empty_gripper', False), ('pre_pick', False), ('source', 'real'), ('linear_speed_m_s', .002),
                           ('angular_speed_rad_s', float('nan')), ('quaternion_xyzw', [1., 0., 0., 0.]),
                           ('observation_revision', True), ('observed_part_id', 'sleeve_1')]:
            with self.subTest(key=key):
                self.assertIsNone(m.retrieve_experience(self.record, self.query() | {key: wrong}))
        self.assertIsNone(m.retrieve_experience(self.record, self.query('sleeve_1') | {'episode_id': self.source.name}))
        self.assertIsNone(m.retrieve_experience(self.record, self.query(), search_text='foreign voltage schedule'))
        self.assertIsNotNone(m.retrieve_experience(self.record, self.query(), search_text='패드 접촉'))

    def test_changed_source_bytes_or_missing_measured_grip_stop_admission(self):
        csv_text = self.csv.read_text(encoding='utf-8')
        self.csv.write_text(csv_text.replace('True,True', 'True,False'), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'bilateral'):
            m.save_source_experience(self.source, output_dir=self.root / 'memory')
        self.assertFalse((self.root / 'memory').exists())
        self.result['graph_states']['sleeve_1']['status'] = 'failed'
        self.write('result.json', self.result)
        with self.assertRaisesRegex(ValueError, 'original verified'):
            m._admit(self.source)

    def test_modified_example_is_rejected_by_filename_pin_or_comparison_to_source(self):
        bad = deepcopy(self.record)
        bad['repair']['grasp']['z_mm'] = -9
        del bad['experience_id']
        bad['experience_id'] = m._digest(bad)
        p = self.root / (bad['experience_id'] + '.json')
        p.write_text(json.dumps(bad), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'pinned'):
            m.load_experience(p, expected_id=self.record['experience_id'])
        with self.assertRaisesRegex(ValueError, 'current source'):
            m.load_experience(p, source_run=self.source)
        stale_name = self.root / (self.record['experience_id'] + '.json')
        stale_name.write_bytes(p.read_bytes())
        with self.assertRaisesRegex(ValueError, 'filename'):
            m.load_experience(stale_name)


if __name__ == '__main__':
    unittest.main()
