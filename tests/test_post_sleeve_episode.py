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
from run_frame_assembly import execute_trial
from test_openai_policy import response_for, settings


class PostSleeveEpisodeTests(unittest.TestCase):
    def run_with_fixture(self, *, stop=False):
        requests = []

        def handler(request):
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
                patch('llm_runtime.load_llm_config', return_value=settings(max_model_requests=3)):
            result = execute_trial(product_count=3, mode='single', post_sleeve_llm_config=Path('offline.toml'),
                                   output_root=Path(tmp), empty_approach_strategy='direct_then_rrt')
        make_client.assert_called_once()
        self.assertTrue(client.is_closed())
        return result, requests

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
        self.assertEqual(result['api_calls'], 3)
        self.assertEqual([p['part_id'] for p in requests], ['post_1'] * 3)
        post, sleeve = (result['part_episodes'][k] for k in ('post_1', 'sleeve_1'))
        self.assertEqual(post['start']['time_s'], 0.)
        self.assertEqual(post['start']['installed'], [])
        self.assertGreater(post['end']['time_s'], 0.)
        self.assertEqual(post['end'], sleeve['start'])
        self.assertEqual(sleeve['start']['installed'], ['post_1'])
        self.assertNotIn('end', sleeve)
        self.assertNotEqual(post['start']['parts']['post_1'], post['end']['parts']['post_1'])
        self.assertEqual(result['graph_states']['sleeve_1']['completed'], [])
