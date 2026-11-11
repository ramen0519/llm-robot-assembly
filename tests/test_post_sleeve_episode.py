"""Two free components, a single physics state and a shared request budget."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import httpx2
from openai import OpenAI

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from run_frame_assembly import execute_trial, episode_boundary, restore_episode_data
from frame_assembly_scene import build_frame_scene
from test_openai_policy import response_for, settings


class PostSleeveEpisodeTests(unittest.TestCase):
    def run_with_fixture(self, *, stop=False, full_frame=False, connection_failure=False):
        requests = []

        def handler(request):
            if connection_failure:
                raise httpx2.ConnectError('Offline connection failure fixture', request=request)
            if request.url.path.endswith('/input_tokens'):
                return httpx2.Response(200, json={'object': 'response.input_tokens', 'input_tokens': 1000})
            body = json.loads(request.content)
            packet = json.loads(body['input'])
            requests.append(packet)
            response = response_for(body)
            call = response['output'][0]
            action = json.loads(call['arguments'])
            if stop:
                action.update(skill='stop', grasp=None)
            elif action['skill'] == 'pick':
                action['grasp'] = dict(x_mm=0, y_mm=0, z_mm=12, jaw_yaw_deg=0)
            call['arguments'] = json.dumps(action)
            return httpx2.Response(200, json=response)

        client = OpenAI(api_key='offline-test', max_retries=0,
                        http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))
        with tempfile.TemporaryDirectory() as tmp, patch('llm_runtime.create_openai_client', return_value=client) as make_client, \
                patch('llm_runtime.load_llm_config', return_value=settings(max_model_requests=2)):
            config_option = ({'llm_config': Path('offline.toml')} if full_frame
                             else {'post_sleeve_llm_config': Path('offline.toml')})
            result = execute_trial(product_count=10 if full_frame else 3, mode='single', **config_option,
                                   output_root=Path(tmp), empty_approach_strategy='direct_then_rrt')
        make_client.assert_called_once()
        self.assertTrue(client.is_closed())
        return result, requests

    def test_full_frame_starts_with_nine_free_parts_and_stops_before_any_installation(self):
        result, requests = self.run_with_fixture(stop=True, full_frame=True)
        initial = result['part_episodes']['post_1']['start']
        expected = {*(f'post_{i}' for i in range(1, 5)), *(f'sleeve_{i}' for i in range(1, 5)), 'cover'}
        self.assertEqual(set(initial['parts']), expected)
        self.assertEqual(initial['installed'], [])
        self.assertEqual(initial['time_s'], 0.)
        model, _, registry = build_frame_scene(10)
        restored = restore_episode_data(model, json.loads(json.dumps(initial)))
        self.assertEqual(episode_boundary(model, restored, registry, set()), initial)
        self.assertEqual(result['status'], 'stopped')
        self.assertEqual(result['robot_installed_parts'], 0)
        self.assertFalse(result['ten_part_assembly_complete'])
        self.assertIsNone(result['final_verification'])
        self.assertEqual(result['api_calls'], 1)
        self.assertEqual(requests[0]['part_id'], 'post_1')
        self.assertTrue(all(not p['installed'] for p in requests[0]['observation']['all_parts'].values()))

    def test_conflicting_live_modes_are_rejected_before_client_construction(self):
        with patch('llm_runtime.create_openai_client') as client:
            for options in ({'mode': 'scripted'}, {'post_sleeve_llm_config': Path('other.toml')},
                            {'planner_candidate': Path('candidate.json'), 'target_parts': ['post_1']},
                            {'policies': {'single': object()}}):
                with self.subTest(options=options), self.assertRaises(ValueError):
                    execute_trial(product_count=10, llm_config=Path('offline.toml'), **{'mode': 'single', **options})
            client.assert_not_called()

    def test_stop_on_post_leaves_sleeve_pending(self):
        result, requests = self.run_with_fixture(stop=True)
        self.assertEqual(result['status'], 'stopped')
        self.assertEqual(result['execution_order'], [])
        self.assertEqual(list(result['part_episodes']), ['post_1'])
        self.assertEqual(result['part_episodes']['post_1']['start']['installed'], [])
        self.assertNotIn('end', result['part_episodes']['post_1'])
        self.assertEqual([p['part_id'] for p in requests], ['post_1'])

    def test_sleeve_starts_in_identical_state_and_cannot_replenish_episode_budget(self):
        result, requests = self.run_with_fixture()
        self.assertEqual(result['execution_order'], ['post_1'], result['error'])
        self.assertEqual(result['failures'][-1]['failure_code'], 'POLICY_BUDGET_EXCEEDED')
        self.assertEqual(result['api_calls'], 2)
        self.assertEqual([p['part_id'] for p in requests], ['post_1'] * 2)
        post, sleeve = (result['part_episodes'][k] for k in ('post_1', 'sleeve_1'))
        self.assertEqual(post['start']['time_s'], 0.)
        self.assertEqual(post['start']['installed'], [])
        self.assertGreater(post['end']['time_s'], 0.)
        self.assertEqual(post['end'], sleeve['start'])
        self.assertEqual(sleeve['start']['installed'], ['post_1'])
        self.assertNotIn('end', sleeve)
        self.assertNotEqual(post['start']['parts']['post_1'], post['end']['parts']['post_1'])
        self.assertEqual(result['graph_states']['sleeve_1']['completed'], [])
        completed = result['graph_states']['post_1']
        self.assertEqual(completed['status'], 'success')
        self.assertEqual(completed['completion_source'], 'physics_verifier')
        self.assertEqual(completed['completed'], ['pick', 'insert'])
        self.assertTrue(all(completed['verification']['checks'].values()))
        self.assertEqual(completed['action']['skill'], 'insert')
        self.assertEqual(result['terminal_checkpoint']['installed'], ['post_1'])

    def test_api_failure_preserves_exact_terminal_physics_without_authorizing_resume(self):
        result, requests = self.run_with_fixture(connection_failure=True, full_frame=True)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['robot_installed_parts'], 0)
        self.assertEqual(result['api_calls'], 0)
        self.assertFalse(requests)
        self.assertIsNone(result['resume_checkpoint'])
        self.assertIsNone(result['checkpoint_error'])
        saved = result['terminal_checkpoint']
        self.assertEqual(saved['time_s'], result['simulated_seconds'])
        self.assertGreater(saved['time_s'], 0.)
        model, _, registry = build_frame_scene(10)
        data = restore_episode_data(model, saved)
        self.assertEqual(episode_boundary(model, data, registry, set()), saved)
