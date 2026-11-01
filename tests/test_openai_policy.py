"""Official SDK against an in-memory HTTP transport, never a real model service."""
from dataclasses import asdict, replace
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import httpx2
from openai import OpenAI

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from assembly_graph import run_assembly_graph
from failure_report import FailureCode, FailureEvent
from insertion_scene import build_insertion_scene
from insertion_skills import InsertionSkills
from llm_runtime import ApiLedger, LlmConfig, create_openai_client, load_llm_config
from openai_policy import OpenAIAssemblyPolicy, build_request, compact_context, parse_proposal
from pick_place_graph import _snapshot
from preview_llm_request import preview
from run_assembly_graph import execute_trial


def settings(**changes):
    return replace(LlmConfig(enabled=True, model='offline-test-model', max_cost_usd='1',
                            input_usd_per_million='1', output_usd_per_million='2',
                            pricing_source='https://example.invalid/test-prices', pricing_as_of='2026-11-01'), **changes)


def context():
    # Contract fixture only; physical integration below reads actual MuJoCo state.
    return {'task_id':'test', 'part_id':'post_1', 'target_id':'socket_1', 'active_role':'assembly',
            'observation_revision':1, 'completed':[], 'allowed_skills':['pick','stop'],
            'recovery_budget_remaining':1, 'failure':None, 'failures':[], 'experiences':[],
            'observation':{'source':'sim_ground_truth', 'valid':True, 'simulation_time_s':3.,
                           'tcp':{'frame':'world','position_m':[0.,0.,1.]},
                           'workcell':{'part':{'id':'post_1'},'target':{'id':'socket_1'}}}}


def response_for(body):
    c=json.loads(body['input'])
    action={key:c[key] for key in ('part_id','target_id','observation_revision')}
    action.update(skill=c['allowed_skills'][0],reason='Offline HTTP fixture; not model reasoning.')
    return {'id':'resp_offline_fixture', 'object':'response', 'created_at':0,
            'status':'completed', 'error':None, 'model':'offline-test-model',
            'output':[{'type':'function_call','name':'propose_assembly_action','arguments':json.dumps(action),
                       'call_id':'call_offline_fixture','status':'completed'}],
            'usage':{'input_tokens':500,'output_tokens':200,'total_tokens':700,
                     'input_tokens_details':{'cached_tokens':50,'cache_write_tokens':0},
                     'output_tokens_details':{'reasoning_tokens':100}}}


class OpenAIPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root=Path(self.temporary.name)
        self.serial=0

    def policy(self, *, config=None, mutate=None, failure=None, count=1000):
        config=config or settings()
        requests=[]
        def handler(request):
            body=json.loads(request.content)
            requests.append((request.url.path,body))
            if request.url.path.endswith('/input_tokens'):
                return httpx2.Response(200,json={'object':'response.input_tokens','input_tokens':count})
            self.assertTrue(request.url.path.endswith('/responses'))
            if failure:
                raise failure('Never expose request secret test-not-a-credential',request=request)
            response=response_for(body)
            if mutate:
                mutate(response)
            return httpx2.Response(200,json=response,headers={'x-request-id':'offline-transport-request'})
        client=OpenAI(api_key='test-not-a-credential',base_url='https://api.openai.com/v1',max_retries=0,
                      timeout=1.,http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))
        self.addCleanup(client.close)
        self.serial+=1
        ledger=ApiLedger(config,self.root/f'events_{self.serial}.jsonl')
        return OpenAIAssemblyPolicy(config,client,ledger),requests

    def test_sdk_request_and_usage_are_explicit_and_reasoning_is_not_double_counted(self):
        policy,requests=self.policy()
        proposal=policy.propose(context())
        self.assertEqual(proposal['skill'],'pick')
        self.assertEqual(len(requests),2)
        count_request,generation=requests[0][1],requests[1][1]
        self.assertEqual(count_request,{k:v for k,v in generation.items() if k not in ('max_output_tokens','store','service_tier')})
        self.assertFalse(generation['store'])
        self.assertFalse(generation['parallel_tool_calls'])
        self.assertEqual(generation['service_tier'],'default')
        self.assertEqual(generation['tools'][0]['parameters']['additionalProperties'],False)
        summary=policy.ledger.summary()
        self.assertEqual(summary['api_calls'],1)
        self.assertEqual(summary['http_requests'],2)
        self.assertEqual(summary['output_tokens'],200)
        self.assertEqual(summary['reasoning_tokens'],100)
        self.assertAlmostEqual(summary['api_cost'],.0009)
        self.assertNotIn('test-not-a-credential',policy.ledger.path.read_text())

    def test_bad_actions_and_refusals_still_count_usage_but_never_return_a_command(self):
        def change_action(response,**changes):
            action=json.loads(response['output'][0]['arguments'])
            action.update(changes)
            response['output'][0]['arguments']=json.dumps(action)
        mutations=[lambda r:change_action(r,skill='finish'),lambda r:change_action(r,observation_revision=0),
                   lambda r:change_action(r,observation_revision=True),lambda r:change_action(r,code='arbitrary code'),
                   lambda r:change_action(r,reason='x'*513),lambda r:r.update(status='incomplete'),
                   lambda r:r.update(output=[{'type':'message','content':[{'type':'refusal','refusal':'no'}]}]),
                   lambda r:r['output'].append(r['output'][0].copy())]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                policy,requests=self.policy(mutate=mutate)
                with self.assertRaises(FailureEvent) as caught:
                    policy.propose(context())
                self.assertEqual(caught.exception.code,FailureCode.POLICY_RESPONSE_INVALID)
                self.assertEqual(policy.ledger.summary()['input_tokens'],500)
                self.assertTrue(policy.ledger.halted)
                self.assertEqual(len(requests),2)

    def test_missing_usage_keeps_reservation_and_stops_further_requests(self):
        policy,requests=self.policy(mutate=lambda r:r.update(usage=None))
        with self.assertRaises(FailureEvent) as caught:
            policy.propose(context())
        self.assertEqual(caught.exception.code,FailureCode.POLICY_USAGE_UNKNOWN)
        summary=policy.ledger.summary()
        self.assertIsNone(summary['api_cost'])
        self.assertIsNone(summary['output_tokens'])
        self.assertGreater(summary['unresolved_cost_reserve_usd'],0)
        with self.assertRaises(FailureEvent):
            policy.propose(context())
        self.assertEqual(len(requests),2)

    def test_timeout_is_not_retried_or_reported_as_free(self):
        policy,requests=self.policy(failure=httpx2.ReadTimeout)
        with self.assertRaises(FailureEvent) as caught:
            policy.propose(context())
        self.assertEqual(caught.exception.code,FailureCode.POLICY_REQUEST_FAILED)
        self.assertEqual(len(requests),2)
        self.assertEqual(policy.ledger.summary()['unknown_usage_requests'],1)
        self.assertIsNone(policy.ledger.summary()['api_cost'])
        self.assertNotIn('test-not-a-credential',policy.ledger.path.read_text())
        self.assertNotIn('test-not-a-credential',str(caught.exception))

    def test_zero_available_budget_stops_before_even_the_count_request(self):
        for config in (settings(max_cost_usd='0.0001'), settings(max_total_tokens=1)):
            policy,requests=self.policy(config=config)
            with self.assertRaises(FailureEvent) as caught:
                policy.propose(context())
            self.assertEqual(caught.exception.code,FailureCode.POLICY_BUDGET_EXCEEDED)
            self.assertEqual(requests,[])

    def test_request_limit_applies_to_all_calls_of_the_same_policy(self):
        policy,requests=self.policy(config=settings(max_model_requests=1))
        policy.propose(context())
        with self.assertRaises(FailureEvent):
            policy.propose(context())
        self.assertEqual(len(requests),2)

    def test_oversized_input_rejected_after_count_before_generation(self):
        policy,requests=self.policy(count=12001)
        with self.assertRaises(FailureEvent) as caught:
            policy.propose(context())
        self.assertEqual(caught.exception.code,FailureCode.POLICY_BUDGET_EXCEEDED)
        self.assertEqual(len(requests),1)
        self.assertEqual(policy.ledger.summary()['api_calls'],0)

    def test_duplicate_json_fields_are_not_silently_accepted(self):
        response=response_for(build_request(settings(),context()))
        response['output'][0]['arguments']='{"skill":"stop",'+response['output'][0]['arguments'][1:]
        with self.assertRaises(ValueError):
            parse_proposal(response,context())

    def test_disabled_example_never_constructs_a_network_client(self):
        config=load_llm_config(Path(__file__).resolve().parents[1]/'config/llm.example.toml')
        with patch('openai.OpenAI') as client:
            with self.assertRaises(ValueError):
                create_openai_client(config)
            client.assert_not_called()

    def test_live_client_disables_sdk_retries_and_ignores_an_unrelated_base_url(self):
        with patch.dict('os.environ', {'OPENAI_API_KEY':'test-not-a-credential','OPENAI_BASE_URL':'https://example.invalid'}):
            client=create_openai_client(settings())
        self.addCleanup(client.close)
        self.assertEqual(client.max_retries,0)
        self.assertEqual(str(client.base_url),'https://api.openai.com/v1/')

    def test_inconsistent_usage_and_unpriced_cache_writes_remain_unknown(self):
        for mutate in (lambda r:r['usage'].update(total_tokens=1),
                       lambda r:r['usage']['input_tokens_details'].update(cache_write_tokens=1)):
            policy,requests=self.policy(mutate=mutate)
            with self.assertRaises(FailureEvent) as caught: policy.propose(context())
            self.assertEqual(caught.exception.code,FailureCode.POLICY_USAGE_UNKNOWN)
            self.assertIsNone(policy.ledger.summary()['api_cost'])

    def test_journal_cannot_be_reopened_with_a_fresh_budget(self):
        path=self.root/'one_episode.jsonl'
        ApiLedger(settings(),path)
        with self.assertRaises(FileExistsError): ApiLedger(settings(),path)

    def test_invalid_or_huge_context_rejected_before_http(self):
        for kind in ('invalid','huge'):
            c=context()
            if kind=='invalid': c['observation']['valid']=False
            else: c['experiences']=[{'message':'x'*40000}]
            policy,requests=self.policy()
            with self.assertRaises(FailureEvent): policy.propose(c)
            self.assertEqual(requests,[])

    def test_preview_uses_actual_observation_and_no_network_or_assembly(self):
        with patch('openai.OpenAI',side_effect=AssertionError('No client during preview')):
            result=preview(output_root=self.root/'preview')
        self.assertEqual(result['status'],'preview_only')
        self.assertEqual(result['api_calls'],0)
        self.assertEqual(result['robot_installed_parts'],0)
        packet=json.loads(json.loads((Path(result['run_dir'])/'request.json').read_text())['input'])
        self.assertEqual(packet['observation']['source'],'sim_ground_truth')
        self.assertNotIn('qpos',packet['observation'])

    def run_physical_graph(self, policy):
        model,data,config,joint=build_insertion_scene()
        skills=InsertionSkills(model,data,config,joint)
        def failure(exc):
            return {'status':'failed','failure_code':getattr(exc,'code',FailureCode.UNCLASSIFIED_ERROR).value,'message':str(exc)}
        state=run_assembly_graph(skills,failure_handler=failure,mode='single',scenario='alignment_offset',policies={'single':policy})
        return state,skills

    def test_invalid_response_stops_graph_before_physical_pick_and_counts_attempt(self):
        def early_finish(r):
            action=json.loads(r['output'][0]['arguments']); action['skill']='finish'
            r['output'][0]['arguments']=json.dumps(action)
        policy,requests=self.policy(mutate=early_finish)
        state,skills=self.run_physical_graph(policy)
        self.assertEqual(state['status'],'failed')
        self.assertEqual(state['policy_calls'],1)
        self.assertEqual(state['completed'],[])
        self.assertAlmostEqual(skills.data.time,3.)
        self.assertEqual(state['failures'][0]['failure_code'],'POLICY_RESPONSE_INVALID')

    def test_mock_http_contract_can_drive_real_physics_but_is_not_a_live_llm_trial(self):
        policy,requests=self.policy()
        state,skills=self.run_physical_graph(policy)
        self.assertEqual(state['status'],'success',state['error'])
        self.assertEqual(state['recoveries'],1)
        self.assertEqual(state['policy_calls'],5)
        self.assertEqual(len(requests),10)
        self.assertEqual(policy.ledger.summary()['api_calls'],5)  # Mocked request attempts only.
        self.assertTrue(all(state['verification']['checks'].values()))
        self.assertGreater(skills.data.time,80.)

    def test_runner_persists_failed_api_usage_instead_of_offline_zero_metrics(self):
        policy,requests=self.policy(failure=httpx2.ReadTimeout)
        config_path=self.root/'local.llm.toml'
        config_path.write_text('[llm]\n'+'\n'.join(f'{k} = {json.dumps(v)}' for k,v in asdict(settings()).items()),encoding='utf-8')
        with patch('llm_runtime.create_openai_client',return_value=policy.client):
            result=execute_trial(mode='single',llm_config_path=config_path,output_root=self.root/'runner')
        self.assertEqual(result['status'],'failed')
        self.assertEqual(result['policy_kind'],'openai_responses')
        self.assertEqual(result['api_calls'],1)
        self.assertIsNone(result['api_cost'])
        self.assertEqual(result['llm_usage']['unknown_usage_requests'],1)
        self.assertEqual(result['graph_state']['policy_calls'],1)
        self.assertEqual(result['robot_installed_parts'],0)
        self.assertAlmostEqual(result['simulated_seconds'],3.)
        saved=json.loads((Path(result['run_dir'])/'result.json').read_text())
        self.assertIsNone(saved['api_cost'])
        self.assertEqual(len(requests),2)


if __name__=='__main__':
    unittest.main()
