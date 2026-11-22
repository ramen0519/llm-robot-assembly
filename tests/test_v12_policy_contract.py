"""Offline SDK transport checks only; no Astra/model-quality claim."""
from copy import deepcopy
from pathlib import Path
import json
import sys
import tempfile
import unittest
import httpx2
from openai import OpenAI

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from assembly_v12.context import History,SharedPolicy,measured_packet
from assembly_v12.contracts import read
from assembly_v12.execution import generate_guidance
from llm_runtime import ApiLedger,LlmConfig
from failure_report import FailureEvent


def context(role,revision):
    return {'task_id':'offline','part_id':'1','target_id':'mate_1','active_role':role,
            'observation_revision':revision,'completed':[],'allowed_skills':['stop'],
            'failure':None,'failures':[],'experiences':[],
            'observation':{'source':'sim_ground_truth','valid':True,'simulation_time_s':3.,
                'tcp':{'frame':'world','position_m':[.1,.2,.3]},
                'workcell':{'part':{'id':'1','position_m':[.1,.2,.3],'quaternion_xyzw':[0,0,0,1]},
                    'target':{'id':'mate_1'},'assembly_joint':{'target_part_center_world_m':[.2,.3,.4],
                        'target_part_rotation_world':[[1,0,0],[0,1,0],[0,0,1]]}}}}


class SharedConversation(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.requests=[];self.serial=0
        def handler(request):
            body=json.loads(request.content);self.requests.append(body)
            if request.url.path.endswith('/input_tokens'):
                return httpx2.Response(200,json={'object':'response.input_tokens','input_tokens':400})
            self.serial+=1
            name=body['tools'][0]['name']
            if name=='propose_guidance':
                action={'applies_when':None,'strategy':None,'checks':None,'evidence_refs':[]}
            else:
                packet=json.loads(body['input'][-1]['content'])
                action={k:packet[k] for k in ('part_id','target_id','observation_revision')}
                action.update(skill='stop',reason='Offline SDK fixture.')
            response={'id':f'offline_{self.serial}','object':'response','created_at':0,'status':'completed',
                      'model':'gpt-6-astra','output':[
                        {'type':'reasoning','id':f'reasoning_{self.serial}','summary':[],'encrypted_content':'opaque-offline-fixture'},
                        {'type':'function_call','name':name,'arguments':json.dumps(action),
                         'call_id':f'call_{self.serial}','status':'completed'}],
                      'usage':{'input_tokens':400,'output_tokens':100,'total_tokens':500,
                               'input_tokens_details':{'cached_tokens':20,'cache_write_tokens':0},
                               'output_tokens_details':{'reasoning_tokens':50}}}
            return httpx2.Response(200,json=response)
        self.client=OpenAI(api_key='offline-not-a-credential',max_retries=0,
                          http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))
        self.addCleanup(self.client.close)
        self.cfg=LlmConfig(enabled=True,model='gpt-6-astra',reasoning_effort='high',max_model_requests=2,
                           max_cost_usd='1',input_usd_per_million='1',output_usd_per_million='2',
                           pricing_source='https://example.invalid/fixture',pricing_as_of='2026-11-01')
        self.ledger=ApiLedger(self.cfg,self.root/'ledger.jsonl')
        for name in ('a.png','b.png'):(self.root/name).write_bytes(b'offline-image')
        self.history=History(self.root/'history.jsonl',{'fixture':True},[self.root/'a.png',self.root/'b.png'])
        self.prompts=read(Path(__file__).resolve().parents[1]/'config/policy_prompts.v12.json')

    def policy(self,role):
        return SharedPolicy(self.cfg,self.client,self.ledger,self.history,self.prompts,role)

    def test_role_switch_shares_opaque_reasoning_tool_results_and_budget(self):
        a,r=self.policy('assembly'),self.policy('recovery')
        a.propose(context('assembly',1));self.history.complete({'executed':False,'status':'preflight_rejected'})
        r.propose(context('recovery',2));self.history.complete({'executed':False,'status':'stop'})
        sent=self.requests[-1]
        self.assertFalse(sent['store']);self.assertEqual(sent['truncation'],'disabled')
        self.assertIn('reasoning.encrypted_content',sent['include'])
        self.assertEqual(sum(x['type']=='input_image' for x in sent['input'][0]['content']),2)
        self.assertTrue(any(x.get('encrypted_content')=='opaque-offline-fixture' for x in sent['input']))
        self.assertTrue(any(x.get('type')=='function_call_output' and x['call_id']=='call_1' for x in sent['input']))
        self.assertEqual(self.ledger.agent_totals()['assembly']['api_calls'],1)
        self.assertEqual(self.ledger.agent_totals()['recovery']['api_calls'],1)
        with self.assertRaises(FailureEvent):a.propose(context('assembly',3))
        self.assertEqual(len(self.requests),4)

    def test_single_and_guidance_use_one_usage_ledger(self):
        p=self.policy('single')
        p.propose(context('single',1));self.history.complete({'executed':False,'status':'stop'})
        card=generate_guidance(p,{'events':[]},self.prompts)
        self.assertIsNone(card['strategy'])
        self.assertEqual(self.ledger.agent_totals()['single']['api_calls'],1)
        self.assertEqual(self.ledger.agent_totals()['guidance']['api_calls'],1)
        self.assertEqual(self.ledger.summary()['api_calls'],2)

    def test_deadline_prevents_http_and_stale_role_prevents_http(self):
        p=self.policy('assembly');p.deadline=0
        with self.assertRaises(TimeoutError):p.propose(context('assembly',1))
        with self.assertRaises(ValueError):p.propose(context('recovery',1))
        self.assertEqual(self.requests,[])

    def test_world_pose_packet_has_mm_and_xyzw(self):
        packet=measured_packet(context('single',1))
        self.assertEqual(packet['current_pose']['position_mm'],[100.,200.,300.])
        self.assertEqual(packet['goal_pose']['quaternion_xyzw'],[0.,0.,0.,1.])
        self.assertNotIn('position_m',packet['observation']['tcp'])


if __name__=='__main__':unittest.main()
