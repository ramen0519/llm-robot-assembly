"""Unexecuted-grasp resume preserves the measured scene, history and global accounting."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import httpx2
from openai import OpenAI

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from run_frame_assembly import execute_trial
from test_openai_policy import response_for, settings


class FrameResumeTests(unittest.TestCase):
    def run_fixture(self, root, config, *, resume=None, stop=False, stop_after=0):
        requests=[]
        def handler(request):
            if request.url.path.endswith('/input_tokens'):
                return httpx2.Response(200,json={'object':'response.input_tokens','input_tokens':1000})
            body=json.loads(request.content)
            requests.append(json.loads(body['input']))
            response=response_for(body)
            action=json.loads(response['output'][0]['arguments'])
            stopping = stop and len(requests) > stop_after
            action.update(skill='stop' if stopping else 'pick',grasp=None if stopping else
                          dict(x_mm=0,y_mm=0,z_mm=100,jaw_yaw_deg=0))
            response['output'][0]['arguments']=json.dumps(action)
            return httpx2.Response(200,json=response)
        client=OpenAI(api_key='offline-test',max_retries=0,
                      http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))
        with patch('llm_runtime.create_openai_client',return_value=client) as create, \
                patch('llm_runtime.load_llm_config',return_value=config):
            result=execute_trial(product_count=2,mode='single',llm_config=Path('offline.toml'),
                                 output_root=root,resume_run=resume)
        client.close()
        return result,requests,create.call_count

    def test_resume_reobserves_and_keeps_failed_trials_and_global_requests(self):
        with tempfile.TemporaryDirectory() as tmp:
            config=settings(max_model_requests=5)
            parent,_,_=self.run_fixture(Path(tmp),config)
            self.assertEqual(parent['status'],'stopped')
            self.assertEqual(parent['api_calls'],3)
            result,requests,_=self.run_fixture(Path(tmp),config,resume=parent['run_dir'],stop=True,stop_after=1)
            self.assertEqual(result['status'],'stopped')
            self.assertEqual(result['api_calls'],5)
            self.assertEqual(result['samples'],parent['samples'])
            self.assertEqual(result['simulated_seconds'],parent['simulated_seconds'])
            self.assertEqual(result['robot_installed_parts'],0)
            self.assertEqual(requests[0]['grasp_task']['attempts_remaining'],3)
            self.assertEqual(requests[1]['grasp_task']['attempts_remaining'],2)
            self.assertEqual(len(requests[0]['grasp_task']['previous_trials']),3)
            self.assertEqual(requests[0]['observation_revision'],4)
            self.assertEqual(result['observation'],parent['observation'])
            self.assertEqual(result['resume']['restored_boundary'],parent['resume_checkpoint'])
            self.assertEqual(Path(result['run_dir'],'skill_trace.csv').read_bytes(),
                             Path(parent['run_dir'],'skill_trace.csv').read_bytes())
            records=[json.loads(line) for line in Path(result['run_dir'],'llm_events.jsonl').read_text().splitlines()]
            self.assertEqual(sum(e['event']=='proposal_validated' for e in records),5)

    def test_spent_budget_is_not_replenished(self):
        with tempfile.TemporaryDirectory() as tmp:
            config=settings(max_model_requests=3)
            parent,_,_=self.run_fixture(Path(tmp),config)
            result,requests,clients=self.run_fixture(Path(tmp),config,resume=parent['run_dir'],stop=True)
            self.assertEqual(result['status'],'failed')
            self.assertEqual(result['api_calls'],3)
            self.assertEqual(requests,[])
            self.assertEqual(clients,0)
            self.assertIn('remaining episode budget',result['error'])

    def test_rejects_corrupt_checkpoint_or_executed_action_before_client(self):
        with tempfile.TemporaryDirectory() as tmp:
            config=settings(max_model_requests=4)
            parent,_,_=self.run_fixture(Path(tmp),config)
            path=Path(parent['run_dir'],'result.json')
            original=path.read_text()
            for problem in ('checkpoint','executed','accounting'):
                changed=json.loads(original)
                if problem=='checkpoint':
                    changed['resume_checkpoint']['integration_state'][0]+=1
                elif problem=='executed':
                    changed['graph_states']['post_1']['completed']=['pick']
                else:
                    changed['llm_usage']['cached_input_tokens']=changed['llm_usage']['input_tokens']+1
                path.write_text(json.dumps(changed))
                result,requests,clients=self.run_fixture(Path(tmp),config,resume=parent['run_dir'],stop=True)
                self.assertEqual(result['status'],'failed')
                self.assertEqual(requests,[])
                self.assertEqual(clients,0)


if __name__=='__main__':
    unittest.main()
