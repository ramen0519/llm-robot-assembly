"""Shared episode accounting with an in-memory transport. Never sends a network request."""
from dataclasses import replace
from decimal import Decimal
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from failure_report import FailureEvent
from grasp_skill_selector import select_skill
from llm_runtime import ApiLedger, load_llm_config
from openai_policy import OpenAIAssemblyPolicy


class Documents:
    snapshot_id = 'fixture-snapshot'

    def search(self, query):
        return [{'document_id': 'skill-1', 'kind': 'skill', 'title': 'Fixture'}]

    def read(self, document_id):
        return {'document_id': document_id, 'kind': 'skill'}


class MemoryClient:
    def __init__(self):
        self.counts, self.generations, self.operations = [], [], []
        self.usage = {'input_tokens': 100, 'output_tokens': 50, 'total_tokens': 150}
        self.drop_response = False
        self.responses = SimpleNamespace(input_tokens=SimpleNamespace(count=self.count), create=self.create)

    def count(self, **request):
        self.counts.append(request)
        return SimpleNamespace(input_tokens=self.usage['input_tokens'])

    def create(self, **request):
        self.generations.append(request)
        if self.drop_response:
            raise RuntimeError('Synthetic transport interruption.')
        if request['tools'][0]['name'] == 'propose_assembly_action':
            context = json.loads(request['input'])
            name = 'propose_assembly_action'
            args = {k: context[k] for k in ('part_id', 'target_id', 'observation_revision')}
            args.update(skill='stop', reason='Synthetic assembly decision.')
        else:
            name, args = self.operations.pop(0)
        body = {'id': 'fixture', 'model': 'gpt-6-astra', 'status': 'completed', 'error': None,
            'usage': dict(self.usage), 'output': [{'type': 'function_call', 'name': name,
                                                  'arguments': json.dumps(args)}]}
        return SimpleNamespace(model_dump=lambda **kwargs: body)


def assembly_context():
    return {'task_id': 'fixture', 'part_id': 'post_1', 'target_id': 'socket', 'active_role': 'assembly',
        'observation_revision': 1, 'completed': [], 'allowed_skills': ['pick', 'stop'],
        'recovery_budget_remaining': 1, 'failure': None, 'failures': [], 'experiences': [],
        'observation': {'source': 'sim_ground_truth', 'valid': True, 'simulation_time_s': 3.,
            'tcp': {'frame': 'world', 'position_m': [0., 0., 1.]},
            'workcell': {'part': {'id': 'post_1'}, 'target': {'id': 'socket'}}}}


class SharedSelectorLedgerTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.config = load_llm_config(ROOT / 'config/llm.astra-high-graph-recovery.toml')
        self.ledger = ApiLedger(self.config, self.root / 'episode.jsonl')
        self.client = MemoryClient()
        self.policy = OpenAIAssemblyPolicy(self.config, self.client, self.ledger)
        self.docs = Documents()
        self.enterContext(patch('grasp_skill_selector.create_openai_client', side_effect=AssertionError('Unexpected SDK client.')))

    def select(self, operations, folder='selection'):
        self.client.operations = list(operations)
        return select_skill({'failure': 'fixture'}, self.docs, config=self.policy.config,
            client=self.policy.client, shared_ledger=self.policy.ledger, log_dir=self.root / folder)

    def test_profile_caps_and_full_request_reservation(self):
        c = self.config.validate(live=True)
        self.assertEqual((c.model, c.reasoning_effort), ('gpt-6-astra', 'high'))
        self.assertEqual((c.max_model_requests, c.max_input_tokens, c.max_output_tokens,
            c.max_total_tokens, Decimal(c.max_cost_usd)), (9, 8000, 2048, 90432, Decimal('.70')))
        self.assertEqual(c.request_reserve_usd(), Decimal('.2024'))

    def test_one_ledger_and_client_account_for_both_policy_and_selection(self):
        self.policy.propose(assembly_context())
        result = self.select([('search_library', {'query': 'post'}),
            ('read_documents', {'document_ids': ['skill-1']}),
            ('select_skill', {'document_id': 'skill-1', 'reason': 'Fixture.'})])
        self.assertEqual((result['status'], result['selected_id']), ('selected', 'skill-1'))
        self.assertEqual(result['ledger_scope'], 'episode')
        self.assertEqual((result['episode_calls_before'], result['episode_calls_after'], result['API_calls']), (1, 4, 3))
        self.assertEqual(result['ledger']['api_calls'], 4)
        self.assertFalse((self.root / 'selection/api_events.jsonl').exists())
        self.policy.propose(assembly_context())
        self.assertEqual((len(self.client.counts), len(self.client.generations)), (5, 5))
        summary = self.ledger.summary()
        self.assertEqual((summary['input_tokens'], summary['output_tokens'], summary['http_requests']), (500, 250, 10))
        self.assertAlmostEqual(summary['known_api_cost_estimate_usd'], .0175)

    def test_four_local_rounds_and_fewer_when_episode_has_only_one_request_left(self):
        operations = [('search_library', {'query': 'post'}), ('search_library', {'query': 'wall'}),
            ('read_documents', {'document_ids': ['skill-1']}),
            ('select_skill', {'document_id': 'skill-1', 'reason': 'Fixture.'})]
        result = self.select(operations)
        self.assertEqual(result['status'], 'selected')
        turns = [json.loads(r['input'])['remaining_rounds_including_this_one'] for r in self.client.generations]
        self.assertEqual(turns, [4, 3, 2, 1])
        for _ in range(4):
            self.policy.propose(assembly_context())
        last = self.select([('abstain', {'reason': 'Last remaining call.'})], 'last')
        self.assertEqual((last['status'], last['API_calls'], last['episode_calls_after']), ('abstained', 1, 9))
        self.assertEqual([t['name'] for t in self.client.generations[-1]['tools']], ['abstain'])
        exhausted = self.select([], 'exhausted')
        self.assertEqual((exhausted['status'], exhausted['API_calls']), ('error', 0))
        self.assertEqual(len(self.client.counts), 9)

    def test_episode_cost_accumulated_before_selection_can_prevent_even_a_count(self):
        self.client.usage = {'input_tokens': 8000, 'output_tokens': 2048, 'total_tokens': 10048}
        for _ in range(3):
            self.policy.propose(assembly_context())
        self.assertAlmostEqual(self.ledger.summary()['known_api_cost_estimate_usd'], .5472)
        result = self.select([('abstain', {'reason': 'Unreachable.'})])
        self.assertEqual((result['status'], result['API_calls']), ('error', 0))
        self.assertEqual((len(self.client.counts), len(self.client.generations)), (3, 3))

    def test_shared_profile_and_explicit_client_required_before_requests(self):
        with self.assertRaises(ValueError):
            select_skill({}, self.docs, config=replace(self.config, max_model_requests=8), client=self.client,
                         shared_ledger=self.ledger, log_dir=self.root / 'different_profile')
        with self.assertRaises(ValueError):
            select_skill({}, self.docs, config=self.config, shared_ledger=self.ledger,
                         log_dir=self.root / 'missing_client')
        self.assertEqual(self.client.counts, [])

    def test_uncertain_shared_generation_halts_following_assembly_requests(self):
        self.client.drop_response = True
        result = self.select([])
        summary = self.ledger.summary()
        self.assertEqual((result['status'], result['API_calls']), ('error', 1))
        self.assertEqual(summary['unknown_usage_requests'], 1)
        self.assertGreater(summary['unresolved_cost_reserve_usd'], 0)
        self.assertTrue(summary['halted'])
        self.client.drop_response = False
        with self.assertRaises(FailureEvent):
            self.policy.propose(assembly_context())
        self.assertEqual((len(self.client.counts), len(self.client.generations)), (1, 1))

    def test_standalone_fixture_keeps_existing_usage_contract(self):
        config = replace(self.config, max_model_requests=4)
        self.client.operations = [('abstain', {'reason': 'Offline.'})]
        result = select_skill({}, self.docs, config=config, client=self.client, log_dir=self.root / 'standalone')
        self.assertEqual((result['status'], result['transport'], result['API_calls']), ('abstained', 'fixture', 0))
        self.assertEqual(result['simulated_ledger']['api_calls'], 1)
        self.assertIsNone(result['ledger'])
        self.assertEqual(self.ledger.model_requests, 0)


if __name__ == '__main__':
    unittest.main()
