"""Offline HTTP fixtures; no credential, account, or model validation."""
from dataclasses import asdict
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import httpx2
from openai import OpenAI

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from assembly_graph import run_assembly_graph
from check_sim_readiness import diagnose
from failure_report import FailureCode
from insertion_scene import build_insertion_scene
from insertion_skills import InsertionSkills
from llm_runtime import ApiLedger
from openai_policy import OpenAIAssemblyPolicy
from test_openai_policy import settings, response_for


class ApiReadinessTests(unittest.TestCase):
    def test_local_diagnostic_never_uses_network_or_records_key_value(self):
        with tempfile.TemporaryDirectory() as temp:
            config = settings()
            path = Path(temp) / 'local.toml'
            path.write_text('[llm]\n' + '\n'.join(f'{k} = {json.dumps(v)}' for k, v in asdict(config).items()))
            with patch.dict('os.environ', {'OPENAI_API_KEY': 'offline-sentinel-secret'}), \
                 patch('openai.OpenAI', side_effect=AssertionError('No network client permitted')):
                result = diagnose(path, output_root=Path(temp) / 'runs')
            self.assertEqual(result['status'], 'offline_ready')
            self.assertEqual(result['api_calls'], 0)
            self.assertTrue(result['key_present'])
            self.assertNotIn('offline-sentinel-secret', json.dumps(result))
            self.assertIn('key validity', result['not_verified'])

    def test_http_contract_handles_replan_then_alignment_recovery_in_actual_physics(self):
        requests = []
        def handler(request):
            body = json.loads(request.content)
            requests.append((request.url.path, body))
            if request.url.path.endswith('/input_tokens'):
                return httpx2.Response(200, json={'object': 'response.input_tokens', 'input_tokens': 1000})
            return httpx2.Response(200, json=response_for(body))
        with tempfile.TemporaryDirectory() as temp, OpenAI(api_key='offline-test-not-a-key', max_retries=0,
                base_url='https://api.openai.com/v1', http_client=httpx2.Client(transport=httpx2.MockTransport(handler))) as client:
            config = settings()
            ledger = ApiLedger(config, Path(temp) / 'mock_usage.jsonl')
            policy = OpenAIAssemblyPolicy(config, client, ledger)
            model, data, config, joint = build_insertion_scene(transfer_obstacle=True)
            skills = InsertionSkills(model, data, config, joint)
            def failure(error):
                return {'status': 'failed', 'failure_code': getattr(error, 'code', FailureCode.UNCLASSIFIED_ERROR).value,
                        'message': str(error), 'environment': 'simulation'}
            # Text transport fixture only; not a registered skill or skill-effect trial.
            recipe = {'recipe_record_id': 'offline-context-fixture', 'advisory_only': True,
                      'trigger_failure': 'ALIGNMENT_OUT_OF_TOLERANCE', 'steps': ['recover_alignment', 'insert']}
            state = run_assembly_graph(skills, failure_handler=failure, mode='single', scenario='alignment_offset',
                        transport_replan_budget=1, policies={'single': policy}, recipe_summaries=[recipe])
            self.assertEqual(state['status'], 'success', state['error'])
            self.assertEqual((state['transport_replans'], state['recoveries'], state['policy_calls']), (1, 1, 6))
            self.assertTrue(all(state['verification']['checks'].values()))
            self.assertEqual(ledger.summary()['api_calls'], 6)  # Mock attempts, not actual service calls.
            packets = [json.loads(body['input']) for path, body in requests if path.endswith('/responses')]
            self.assertEqual(len(packets), 6)
            self.assertTrue(all(p['recipes'] == [recipe] for p in packets))
            self.assertEqual([p['allowed_skills'][0] for p in packets],
                             ['pick', 'insert', 'replan_insert', 'recover_alignment', 'insert', 'finish'])


if __name__ == '__main__':
    unittest.main()
