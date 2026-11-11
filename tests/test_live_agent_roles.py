"""Offline model transport exercises role ownership, shared charging and handoff delivery."""
from decimal import Decimal
import json
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from failure_report import FailureEvent
from grasp_skill_library import SkillLibrary
from llm_runtime import ApiLedger, load_llm_config
from openai_policy import OpenAIAssemblyPolicy, OpenAIRecoveryAgent
from test_openai_policy import context, response_for
from dataclasses import replace


class LiveAgentRoleTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name)
        self.config = replace(load_llm_config(Path(__file__).resolve().parents[1]/'config/llm.astra-high-native-frame.toml'),
                              max_model_requests=2)
        self.ledger = ApiLedger(self.config, self.folder/'events.jsonl')
        self.client = Mock()
        self.client.responses.input_tokens.count.return_value = SimpleNamespace(input_tokens=500)
        self.requests = []
        def respond(**request):
            self.requests.append(request)
            if request['tools'][0]['name'] == 'propose_assembly_action':
                body = response_for(request)
            else:
                body = {'status':'completed','id':'r-offline', 'model':'fixture',
                    'output':[{'type':'function_call','name':'abstain','arguments':'{"reason":"fixture"}'}],
                    'usage':{'input_tokens':500, 'output_tokens':200, 'total_tokens':700}}
            return SimpleNamespace(model_dump=lambda **kwargs: body)
        self.client.responses.create.side_effect = respond
        self.assembly = OpenAIAssemblyPolicy(self.config, self.client, self.ledger, role='assembly')
        self.recovery = OpenAIRecoveryAgent(self.config, self.client, self.ledger)
        self.query = {'part_id':'post_1','observation_revision':2,'handoff':{
            'from':'assembly','to':'recovery','part_id':'post_1','observation_revision':2}}
        self.library = Mock(spec=SkillLibrary, snapshot_id='fixture')

    def test_roles_receive_their_own_inputs_and_share_the_limit(self):
        self.recovery.select_skill(self.query,self.library,log_dir=self.folder/'search')
        current = context()
        current['handoff'] = {'from':'recovery','to':'assembly','part_id':'post_1','proof':{'held':True}}
        self.assembly.propose(current)
        self.assertEqual(json.loads(self.requests[0]['input'])['current']['handoff'], self.query['handoff'])
        self.assertEqual(json.loads(self.requests[1]['input'])['handoff'], current['handoff'])
        self.assertIn('You are the recovery agent',self.requests[0]['instructions'])
        self.assertIn('You are the assembly agent',self.requests[1]['instructions'])
        totals = self.ledger.agent_totals()
        self.assertEqual([totals[r]['api_calls'] for r in ('recovery','assembly')],[1,1])
        self.assertEqual(sum(Decimal(str(s['known_api_cost_estimate_usd'])) for s in totals.values()),self.ledger.known_cost)
        with self.assertRaises(FailureEvent):
            self.assembly.propose(current)
        self.assertEqual(self.client.responses.create.call_count,2)
        events=[json.loads(l) for l in self.ledger.path.read_text(encoding='utf-8').splitlines()]
        self.assertEqual([e['agent_role'] for e in events if e['event']=='model_request_started'], ['recovery','assembly'])

    def test_wrong_role_or_stale_transfer_fails_before_any_model_request(self):
        with self.assertRaises(ValueError):
            self.recovery.propose(context())
        self.query['observation_revision'] = 3
        with self.assertRaises(ValueError):
            self.recovery.select_skill(self.query,self.library,log_dir=self.folder/'search')
        self.assertEqual(self.ledger.http_requests,0)
        self.assertEqual(self.ledger.agent_totals(),{})
