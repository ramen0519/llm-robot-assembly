"""Offline boundary tests for a native plan consumed by the live-policy frame runner."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import httpx2
from openai import OpenAI

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import run_frame_assembly as frame
from export_planner_input import export_bundle
from failure_report import write_json
from run_asap_native import EXPECTED_COMMIT, assess_candidate, prepare_normalized
from test_openai_policy import response_for, settings


class NativeLiveFrameTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.root = Path(cls.temporary.name)
        folder = export_bundle(cls.root / 'fixture')
        _, manifest = prepare_normalized(folder, 3)
        _, _, registry = frame.build_frame_scene(3)
        # Synthetic solver transitions. Their reverse intentionally differs from registry order.
        sequence = ['1', '2']
        remaining = ['0', '1', '2']
        edges = []
        for part in sequence:
            after = [name for name in remaining if name != part]
            edges.append({'before': remaining, 'after': after, 'sim_info': {
                'part_move': part, 'feasible': True, 'base_part': '0', 'parts_fix': [],
                'pose': None, 'action': [0., 0., 1.]}})
            remaining = after
        report = {'schema': 'asap_native_dfs/v1', 'source_commit': EXPECTED_COMMIT,
                  'input_sha256': manifest['input_sha256'], 'remaining': remaining,
                  'stats': {'success': True, 'sequence': sequence}, 'selected_edges': edges}
        report_file = folder / 'native_report.json'
        write_json(report_file, report)
        cls.report_sha = hashlib.sha256(report_file.read_bytes()).hexdigest()
        candidate = assess_candidate(report, manifest, registry)
        candidate['source_report_sha256'] = cls.report_sha
        cls.candidate = folder / 'assembly_candidate.json'
        write_json(cls.candidate, candidate)

    def run_live(self, **options):
        defaults = dict(product_count=3, mode='single', llm_config=Path('offline.toml'),
                        planner_candidate=self.candidate, output_root=self.root / 'episodes')
        with patch('llm_runtime.load_llm_config', return_value=settings()), \
             patch('planner_candidate_input.ROOT', self.root):
            return frame.execute_trial(**(defaults | options))

    def test_reversed_native_order_directs_first_request_and_rejects_wrong_part(self):
        packets = []

        def reply(request):
            if request.url.path.endswith('/input_tokens'):
                return httpx2.Response(200, json={'object': 'response.input_tokens', 'input_tokens': 1000})
            body = json.loads(request.content)
            packets.append(json.loads(body['input']))
            response = response_for(body)
            call = response['output'][0]
            action = json.loads(call['arguments'])
            action.update(skill='pick', part_id='post_1',
                          grasp={'x_mm': 0, 'y_mm': 0, 'z_mm': 12, 'jaw_yaw_deg': 0})
            call['arguments'] = json.dumps(action)
            return httpx2.Response(200, json=response)

        client = OpenAI(api_key='offline-fixture', max_retries=0,
                        http_client=httpx2.Client(transport=httpx2.MockTransport(reply)))
        self.addCleanup(client.close)
        with patch('llm_runtime.create_openai_client', return_value=client), \
             patch.object(frame.FrameSkills, 'settle', autospec=True), \
             patch.object(frame.FrameSkills, 'check_grasp', autospec=True) as check, \
             patch.object(frame.FrameSkills, 'pick', autospec=True) as pick, \
             patch.object(frame.FrameSkills, 'insert', autospec=True) as insert:
            result = self.run_live()
        self.assertEqual(result['requested_sequence'], ['post_2', 'post_1'])
        self.assertEqual(result['planner_provenance']['source_report_sha256'], self.report_sha)
        self.assertEqual(result['planner_provenance']['candidate_sha256'],
                         hashlib.sha256(self.candidate.read_bytes()).hexdigest())
        self.assertTrue(result['planner_provenance']['candidate_is_not_robot_success'])
        resolved = json.loads((Path(result['run_dir']) / 'resolved_config.json').read_text(encoding='utf-8'))
        self.assertEqual(resolved['sequence'], result['requested_sequence'])
        self.assertEqual(resolved['planner_provenance'], result['planner_provenance'])
        self.assertEqual([packet['part_id'] for packet in packets], ['post_2'])
        self.assertEqual(result['api_calls'], 1)  # In-memory transport receives exactly one generation.
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['failures'][-1]['failure_code'], 'POLICY_RESPONSE_INVALID')
        self.assertEqual(result['execution_order'], [])
        self.assertEqual(result['samples'], 0)
        check.assert_not_called()
        pick.assert_not_called()
        insert.assert_not_called()

    def test_wrong_scope_or_bad_report_hash_stops_before_client_and_motion(self):
        bad = json.loads(self.candidate.read_text(encoding='utf-8'))
        bad['source_report_sha256'] = '0' * 64
        bad_path = self.candidate.with_name('bad_candidate.json')
        write_json(bad_path, bad)
        for options in ({'product_count': 4}, {'planner_candidate': bad_path}):
            with self.subTest(options=options), patch('llm_runtime.create_openai_client') as create, \
                 patch.object(frame, 'FrameSkills') as skills:
                result = self.run_live(**options)
                self.assertEqual(result['status'], 'failed')
                self.assertIn('provenance/product scope', result['error'])
                self.assertIsNone(result['planner_provenance'])
                self.assertIsNone(result['llm_usage'])
                self.assertEqual(result['api_calls'], 0)
                self.assertEqual(result['samples'], 0)
                self.assertEqual(result['part_episodes'], {})
                create.assert_not_called()
                skills.assert_not_called()

    def test_overrides_or_mixed_policy_stop_before_admission(self):
        for options in ({'target_parts': ['post_1']}, {'resume_run': self.root}, {'mode': 'scripted'},
                        {'policies': {'single': object()}},
                        {'llm_config': None, 'post_sleeve_llm_config': Path('offline-pair.toml')}):
            with self.subTest(options=options), patch.object(frame, 'build_frame_scene') as build, \
                 patch('llm_runtime.create_openai_client') as create:
                with self.assertRaises(ValueError):
                    self.run_live(**options)
                build.assert_not_called()
                create.assert_not_called()


if __name__ == '__main__':
    unittest.main()
