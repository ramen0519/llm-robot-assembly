"""Offline runner contracts only: no paid API or physical-success evidence."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
for candidate in (HERE.parent / 'scripts', HERE.parent / 'grasp_experience',
                  HERE.parent / 'grasp_retrieval', HERE):
    if candidate.is_dir():
        sys.path.insert(0, str(candidate))

import generate_grasp_lesson as runner
import test_grasp_lessons as lesson_cases
from export_grasp_experience import digest
from grasp_lessons import TOOL_NAME
from llm_runtime import LlmConfig


class FixtureClient:
    """Explicitly synthetic transport; never uses the OpenAI SDK."""
    def __init__(self, body, *, failure=None):
        self.body, self.failure = body, failure
        self.count_requests, self.generation_requests = [], []
        self.responses = SimpleNamespace(
            input_tokens=SimpleNamespace(count=self.count), create=self.create)

    def count(self, **request):
        self.count_requests.append(deepcopy(request))
        if self.failure == 'count':
            raise RuntimeError('PRIVATE_EXCEPTION_MESSAGE_MUST_NOT_BE_LOGGED')
        return SimpleNamespace(input_tokens=100, _request_id='fixture_count')

    def create(self, **request):
        self.generation_requests.append(deepcopy(request))
        if self.failure == 'generation':
            raise RuntimeError('PRIVATE_EXCEPTION_MESSAGE_MUST_NOT_BE_LOGGED')
        return SimpleNamespace(_request_id='fixture_generation',
                               model_dump=lambda **kwargs: deepcopy(self.body))


class GraspLessonRunnerTests(unittest.TestCase):
    def setUp(self):
        # Reuse authored exporter/retriever fixtures, never the user's run data.
        self.fixture = lesson_cases.GraspLessonTests(methodName='runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.root, self.memory = self.fixture.root, self.fixture.memory
        self.packet = self.fixture.packet()
        self.config = LlmConfig(enabled=True, model='fixture-model',
            max_model_requests=5, max_input_tokens=12000, max_output_tokens=2048,
            max_total_tokens=60000, max_cost_usd='0.02', input_usd_per_million='0.10',
            output_usd_per_million='0.50', cached_input_usd_per_million='0.01',
            cache_write_30m_usd_per_million='0.125', reasoning_effort='low',
            pricing_source='https://example.invalid/offline-fixture', pricing_as_of='2026-11-10')
        self.client_factory = self.enterContext(patch('llm_runtime.create_openai_client',
            side_effect=AssertionError('Offline tests must never initialize a real client.')))

    def body(self, decision='propose_lesson'):
        return {'id': 'fixture_response', 'model': 'fixture-model',
            'status': 'completed', 'error': None,
            'usage': {'input_tokens': 100, 'output_tokens': 50, 'total_tokens': 150},
            'output': [{'type': 'function_call', 'name': TOOL_NAME, 'status': 'completed',
                'arguments': json.dumps(lesson_cases.draft_for(self.packet, decision=decision),
                                        ensure_ascii=False)}]}

    def execute(self, client, name='run', *, config=None):
        log_dir = self.root / name
        result = runner.generate_once(self.packet, config or self.config, log_dir=log_dir,
            memory_dir=self.memory, output_dir=self.root / (name + '_lessons'), client=client)
        self.assertEqual(result['API_calls'], 0)
        self.assertEqual(result['http_requests'], 0)
        self.assertEqual(result['new_physics_runs'], 0)
        self.assertEqual(result['transport'], 'injected_test_client')
        self.assertFalse(result['execution_authorized'])
        self.assertFalse(result['automatic_skill_registration'])
        self.client_factory.assert_not_called()
        self.assertEqual(self.read(log_dir / 'result.json'), result)
        self.assertNotIn('PRIVATE_EXCEPTION_MESSAGE_MUST_NOT_BE_LOGGED',
                         json.dumps(result, ensure_ascii=False))
        return result, log_dir

    @staticmethod
    def read(path):
        return json.loads(Path(path).read_text(encoding='utf-8'))

    def test_fixture_save_has_exact_request_hashes_and_no_actual_api_claim(self):
        client = FixtureClient(self.body())
        result, logs = self.execute(client)
        self.assertEqual(result['status'], 'lesson_saved')
        self.assertEqual(len(client.count_requests), 1)
        self.assertEqual(len(client.generation_requests), 1)
        count_request = self.read(logs / 'request.json')
        generation_request = self.read(logs / 'generation_request.json')
        self.assertEqual(client.count_requests[0], count_request)
        self.assertEqual(client.generation_requests[0], generation_request)
        self.assertEqual(generation_request, {**count_request, 'max_output_tokens': 2048,
                                             'store': False, 'service_tier': 'default'})
        self.assertNotIn('store', count_request)
        self.assertEqual(result['request_hash'], digest(count_request))
        self.assertEqual(result['generation_request_hash'], digest(generation_request))
        self.assertNotEqual(result['request_hash'], result['generation_request_hash'])
        generation = result['generation']
        self.assertEqual(generation['method'], 'offline_fixture')
        self.assertEqual(generation['packet_id'], self.packet['packet_id'])
        self.assertEqual(generation['response_hash'], digest(self.read(logs / 'response.json')))
        self.assertEqual(generation['generation_request_hash'], digest(generation_request))
        self.assertEqual(result['simulated_model_requests'], 1)
        self.assertEqual(result['simulated_http_requests'], 2)
        lesson = self.read(result['lesson_path'])
        self.assertEqual(lesson['generation']['method'], 'offline_fixture')
        self.assertFalse(lesson['new_physics_validation'])
        events = [json.loads(row) for row in (logs / 'api_events.jsonl').read_text().splitlines()]
        start = next(row for row in events if row['event'] == 'model_request_started')
        self.assertEqual(start['request_hash'], digest(generation_request))
        transport = next(row for row in events if row['event'] == 'run_transport')
        self.assertFalse(transport['actual_api_requests'])

    def test_unknown_usage_and_transport_errors_stop_without_retry(self):
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
                summary = result['simulated_ledger']
                self.assertTrue(summary['halted'])
                self.assertEqual(summary['automatic_retries'], 0)
                self.assertEqual(summary['unknown_usage_requests'], 0 if failure == 'count' else 1)
                if failure != 'count':
                    self.assertIsNone(summary['api_cost'])
                    self.assertGreater(summary['unresolved_cost_reserve_usd'], 0)
                self.assertIsNone(result['lesson_path'])
                if failure == 'missing_usage':
                    self.assertTrue((logs / 'response.json').is_file())
                self.assertNotIn('PRIVATE_EXCEPTION_MESSAGE_MUST_NOT_BE_LOGGED',
                                 (logs / 'api_events.jsonl').read_text())

    def test_invalid_response_is_settled_before_rejection(self):
        body = self.body()
        body['output'] = []
        result, logs = self.execute(FixtureClient(body))
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['error']['stage'], 'draft_validation')
        summary = result['simulated_ledger']
        self.assertEqual(summary['unknown_usage_requests'], 0)
        self.assertEqual(summary['unresolved_cost_reserve_usd'], 0)
        self.assertGreater(summary['known_api_cost_estimate_usd'], 0)
        self.assertEqual(self.read(logs / 'response.json'), body)
        self.assertIsNone(result['lesson_path'])

    def test_abstention_is_settled_and_never_published(self):
        result, logs = self.execute(FixtureClient(self.body('abstain')))
        self.assertEqual(result['status'], 'abstained')
        self.assertEqual(result['generation']['method'], 'offline_fixture')
        self.assertGreater(result['simulated_ledger']['known_api_cost_estimate_usd'], 0)
        self.assertEqual(result['simulated_ledger']['unknown_usage_requests'], 0)
        self.assertIsNone(result['lesson_path'])
        self.assertFalse((self.root / 'run_lessons').exists())
        self.assertEqual(self.read(logs / 'draft.json')['decision'], 'abstain')

    def test_preview_and_no_evidence_never_initialize_sdk_client(self):
        import builtins
        original_import = builtins.__import__

        def forbid_sdk(name, *args, **kwargs):
            if name == 'openai' or name.startswith('openai.'):
                raise AssertionError('Preview must not import the paid API SDK.')
            return original_import(name, *args, **kwargs)

        empty = self.root / 'empty_memory'
        empty.mkdir()
        with patch('builtins.__import__', side_effect=forbid_sdk):
            for memory, expected in ((self.memory, 'preview'), (empty, 'no_evidence')):
                with self.subTest(expected=expected):
                    result = runner.run_job(run_dir=self.fixture.current, part='post_1',
                        failure_code='PLANNED_CONTACT', memory_dir=memory,
                        output_root=self.root / 'preview_logs')
                    self.assertEqual(result['status'], expected)
                    self.assertEqual(result['API_calls'], 0)
                    self.assertEqual(result['http_requests'], 0)
                    self.assertFalse((Path(result['run_dir']) / 'api_events.jsonl').exists())
        self.client_factory.assert_not_called()

    def test_budget_never_expands_and_too_small_reserve_prevents_all_requests(self):
        bounded = runner.bounded_config(self.config)
        self.assertEqual(bounded.max_model_requests, 1)
        self.assertEqual(bounded.max_cost_usd, '0.01')
        self.assertEqual(bounded.max_total_tokens, 12000 + 2048)
        small = replace(self.config, max_input_tokens=200, max_output_tokens=100,
                        max_total_tokens=100, max_cost_usd='0.001')
        limited = runner.bounded_config(small)
        self.assertEqual(limited.max_total_tokens, 100)
        self.assertEqual(limited.max_cost_usd, '0.001')
        client = FixtureClient(self.body())
        result, _ = self.execute(client, config=small)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['error']['stage'], 'budget_reservation')
        self.assertEqual(client.count_requests, [])
        self.assertEqual(client.generation_requests, [])


if __name__ == '__main__':
    unittest.main()
