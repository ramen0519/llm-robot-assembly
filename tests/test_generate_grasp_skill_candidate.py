"""Offline candidate-runner contracts; no generated code, API or physics runs."""
import builtins
from contextlib import redirect_stdout
from dataclasses import replace
import hashlib
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
for candidate in (HERE.parent / 'scripts', HERE):
    if candidate.is_dir():
        sys.path.insert(0, str(candidate))

import generate_grasp_skill_candidate as runner
import test_grasp_skill_candidates as candidate_cases
from test_generate_grasp_lesson import FixtureClient
from export_grasp_experience import digest
from grasp_skill_candidates import TOOL_NAME
from llm_runtime import LlmConfig


class GraspSkillCandidateRunnerTests(unittest.TestCase):
    def setUp(self):
        self.fixture = candidate_cases.GraspSkillCandidateTests(methodName='runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.root, self.memory = self.fixture.root, self.fixture.memory
        self.packet, self.lesson_path = self.fixture.packet, self.fixture.lesson_path
        self.config = LlmConfig(enabled=True, model='fixture-model',
            max_model_requests=5, max_input_tokens=12000, max_output_tokens=2048,
            max_total_tokens=60000, max_cost_usd='0.02', input_usd_per_million='0.10',
            output_usd_per_million='0.50', cached_input_usd_per_million='0.01',
            cache_write_30m_usd_per_million='0.125', reasoning_effort='low',
            pricing_source='https://example.invalid/offline-fixture', pricing_as_of='2026-11-10')
        self.client_factory = self.enterContext(patch('llm_runtime.create_openai_client',
            side_effect=AssertionError('Offline tests must not initialize a real client.')))

    @staticmethod
    def read(path):
        return json.loads(Path(path).read_text(encoding='utf-8'))

    def body(self, *, decision='propose_candidate', code=None):
        draft = candidate_cases.draft_for(self.packet, decision=decision)
        if code is not None:
            draft['source_code'] = code
        return {'id': 'fixture_candidate_response', 'model': 'fixture-model',
            'status': 'completed', 'error': None,
            'usage': {'input_tokens': 100, 'output_tokens': 50, 'total_tokens': 150},
            'output': [{'type': 'function_call', 'name': TOOL_NAME, 'status': 'completed',
                        'arguments': json.dumps(draft, ensure_ascii=False)}]}

    def execute(self, client, name='run', *, config=None):
        logs = self.root / name
        result = runner.generate_once(self.packet, config or self.config, log_dir=logs,
            memory_dir=self.memory, output_dir=self.root / (name + '_candidates'), client=client)
        self.assertEqual(result['transport'], 'injected_test_client')
        self.assertEqual(result['API_calls'], 0)
        self.assertEqual(result['http_requests'], 0)
        self.assertEqual(result['new_physics_runs'], 0)
        self.assertEqual(result['generated_code_executions'], 0)
        self.assertFalse(result['execution_authorized'])
        self.assertFalse(result['automatic_skill_registration'])
        self.assertEqual(self.read(logs / 'result.json'), result)
        self.assertNotIn('PRIVATE_EXCEPTION_MESSAGE_MUST_NOT_BE_LOGGED', json.dumps(result))
        self.client_factory.assert_not_called()
        return result, logs

    def test_saved_fixture_has_exact_payload_hashes_and_unexecuted_review_copy(self):
        client = FixtureClient(self.body())
        result, logs = self.execute(client)
        self.assertEqual(result['status'], 'candidate_saved')
        request = self.read(logs / 'request.json')
        generation_request = self.read(logs / 'generation_request.json')
        self.assertEqual(client.count_requests, [request])
        self.assertEqual(client.generation_requests, [generation_request])
        self.assertEqual(generation_request, {**request, 'max_output_tokens': 2048,
                                             'store': False, 'service_tier': 'default'})
        self.assertEqual(result['request_hash'], digest(request))
        self.assertEqual(result['generation_request_hash'], digest(generation_request))
        self.assertNotEqual(result['request_hash'], result['generation_request_hash'])
        generation = result['generation']
        self.assertEqual(generation['method'], 'offline_fixture')
        self.assertEqual(generation['packet_id'], self.packet['packet_id'])
        self.assertEqual(generation['response_hash'], digest(self.read(logs / 'response.json')))
        self.assertEqual(result['simulated_model_requests'], 1)
        self.assertEqual(result['simulated_http_requests'], 2)
        candidate = self.read(result['candidate_path'])
        self.assertEqual(candidate['generation']['method'], 'offline_fixture')
        self.assertFalse(candidate['code_executed'])
        self.assertFalse(candidate['new_physics_validation'])
        self.assertFalse(candidate['automatic_skill_registration'])
        self.assertEqual(candidate['static_validation']['status'], 'static_contract_passed')
        source = Path(result['candidate_source_path']).read_bytes()
        self.assertEqual(source, candidate['source_code'].encode('utf-8'))
        self.assertEqual(hashlib.sha256(source).hexdigest(), result['code_sha256'])
        self.assertEqual(candidate['code_sha256'], result['code_sha256'])

    def test_ast_rejection_retains_response_and_settles_usage_without_publication(self):
        client = FixtureClient(self.body(code='import os\ndef propose(ctx):\n    return None\n'))
        result, logs = self.execute(client)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['error']['stage'], 'draft_validation')
        self.assertGreater(result['simulated_ledger']['known_api_cost_estimate_usd'], 0)
        self.assertEqual(result['simulated_ledger']['unknown_usage_requests'], 0)
        self.assertEqual(len(client.generation_requests), 1)
        self.assertTrue((logs / 'response.json').is_file())
        self.assertFalse((logs / 'candidate.py').exists())
        self.assertIsNone(result['candidate_path'])

    def test_unknown_usage_and_transport_errors_preserve_budget_and_never_retry(self):
        for failure in ('count', 'generation', 'missing_usage'):
            with self.subTest(failure=failure):
                body = self.body()
                if failure == 'missing_usage':
                    body.pop('usage')
                client = FixtureClient(body, failure=failure)
                result, logs = self.execute(client, failure)
                self.assertEqual(result['status'], 'failed')
                self.assertEqual(len(client.count_requests), 1)
                self.assertEqual(len(client.generation_requests), 0 if failure == 'count' else 1)
                ledger = result['simulated_ledger']
                self.assertEqual(ledger['automatic_retries'], 0)
                self.assertTrue(ledger['halted'])
                self.assertEqual(ledger['unknown_usage_requests'], 0 if failure == 'count' else 1)
                if failure != 'count':
                    self.assertIsNone(ledger['api_cost'])
                    self.assertGreater(ledger['unresolved_cost_reserve_usd'], 0)
                self.assertIsNone(result['candidate_path'])
                self.assertFalse((logs / 'candidate.py').exists())
                self.assertNotIn('PRIVATE_EXCEPTION_MESSAGE_MUST_NOT_BE_LOGGED',
                                 (logs / 'api_events.jsonl').read_text(encoding='utf-8'))

    def test_abstention_is_charged_without_source_copy_or_candidate(self):
        result, logs = self.execute(FixtureClient(self.body(decision='abstain')))
        self.assertEqual(result['status'], 'abstained')
        self.assertGreater(result['simulated_ledger']['known_api_cost_estimate_usd'], 0)
        self.assertEqual(result['simulated_ledger']['unknown_usage_requests'], 0)
        self.assertIsNone(result['candidate_path'])
        self.assertIsNone(result['candidate_source_path'])
        self.assertFalse((logs / 'candidate.py').exists())
        self.assertFalse((self.root / 'run_candidates').exists())

    def test_cli_preview_never_initializes_client_or_imports_sdk(self):
        original_import = builtins.__import__

        def forbid_sdk(name, *args, **kwargs):
            if name == 'openai' or name.startswith('openai.'):
                raise AssertionError('Preview must not import the paid API SDK.')
            return original_import(name, *args, **kwargs)

        for explicit in (False, True):
            with self.subTest(explicit_preview=explicit):
                output = io.StringIO()
                args = ['--lesson', str(self.lesson_path), '--memory-dir', str(self.memory),
                        '--output-root', str(self.root / 'preview_logs')]
                if explicit:
                    args.append('--preview')
                with patch('builtins.__import__', side_effect=forbid_sdk), redirect_stdout(output):
                    code = runner.main(args)
                result = json.loads(output.getvalue())
                self.assertEqual(code, 0)
                self.assertEqual(result['status'], 'preview')
                self.assertEqual(result['API_calls'], 0)
                self.assertEqual(result['generated_code_executions'], 0)
                self.assertFalse((Path(result['run_dir']) / 'api_events.jsonl').exists())
        self.client_factory.assert_not_called()

    def test_changed_lesson_is_rejected_before_any_request(self):
        self.lesson_path.write_bytes(self.lesson_path.read_bytes() + b'\n')
        client = FixtureClient(self.body())
        result, _ = self.execute(client)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['error']['stage'], 'request_preparation')
        self.assertEqual(client.count_requests, [])
        self.assertEqual(client.generation_requests, [])
        self.assertIsNone(result['candidate_path'])

    def test_source_changed_during_generation_blocks_save_after_charging(self):
        client = FixtureClient(self.body())
        original_create = client.responses.create

        def mutate_fixture(**request):
            self.lesson_path.write_bytes(self.lesson_path.read_bytes() + b'\n')
            return original_create(**request)

        client.responses.create = mutate_fixture
        result, _ = self.execute(client)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['error']['stage'], 'candidate_publication')
        self.assertGreater(result['simulated_ledger']['known_api_cost_estimate_usd'], 0)
        self.assertEqual(result['simulated_ledger']['unknown_usage_requests'], 0)
        self.assertIsNone(result['candidate_path'])

    def test_insufficient_existing_budget_prevents_both_count_and_generation(self):
        config = replace(self.config, max_total_tokens=100)
        client = FixtureClient(self.body())
        result, _ = self.execute(client, config=config)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['error']['stage'], 'budget_reservation')
        self.assertEqual(client.count_requests, [])
        self.assertEqual(client.generation_requests, [])


if __name__ == '__main__':
    unittest.main()
