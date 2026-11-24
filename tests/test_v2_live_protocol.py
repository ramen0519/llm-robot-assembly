"""Regression for the real second-turn Responses serialization failure."""
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from openai.types.responses.response import Response
from openai.types.responses.response_usage import ResponseUsage
from openai.types.responses.response_function_tool_call import ResponseFunctionToolCall
from assembly_v2.policy import Astra
from assembly_v2.budget import Budget
from assembly_v2.storage import data_root,run_root,memory_root
from assembly_v2.packets import observation_packet,guidance_packet
from assembly_v2.execution import run
from assembly_v2.artifacts import audit,counters
from assembly_v2.ui import create_app
from assembly_v2.memory import guidance_schema
from assembly_v12.context import History
from assembly_v12.contracts import read,write
from llm_runtime import load_llm_config,ApiLedger

ROOT=Path(__file__).resolve().parents[1]

class LiveProtocol(unittest.TestCase):
    def test_guidance_schema_constrains_citations_to_actual_event_ids(self):
        incident={'events':[{'id':'f','failure':{'code':'contact'},'executed':False},
            {'id':'p','executed':True,'failure':None,'proposal':{'skill':'pick'}},
            {'id':'i','executed':True,'failure':None,'proposal':{'skill':'insert'}}]}
        props=guidance_schema(incident)['properties']
        self.assertEqual(props['evidence_refs']['items']['enum'],['f','p','i'])
        self.assertEqual(props['parameters']['items']['properties']['event_id']['enum'],['p','i'])
        self.assertNotIn('recover_alignment',props['action']['enum'])
    def test_ui_lists_cli_results_and_only_serves_known_result_folders(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);record=root/'external_runs'/'known';record.mkdir(parents=True)
            write(record/'result.json',{'schema':'assembly_result/v2.0','run_id':'known-run','status':'failed',
               'policy_source':'astra','split':'development'})
            (record/'report.html').write_text('actual saved result',encoding='utf8')
            app=create_app(root/'ui',result_roots=[root/'external_runs']);client=app.test_client()
            rows=client.get('/api/recent').get_json()['runs'];self.assertEqual(len(rows),1)
            self.assertEqual(rows[0]['status'],'failed');self.assertEqual(rows[0]['run_id'],'known-run')
            response=client.get('/results/'+rows[0]['token']+'/report.html')
            self.assertEqual(response.status_code,200);response.close()
            self.assertEqual(client.get('/results/'+'0'*32+'/report.html').status_code,404)
            self.assertNotEqual(client.get('/results/'+rows[0]['token']+'/../../secret').status_code,200)

    def test_explicit_failure_probe_cannot_enter_research_collection(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ValueError,'never research'):
                run({'order':['p']},Path(temp),policy_kind='astra',split='collection',failure_probe={})

    def test_audit_rejects_probe_that_executed_in_main(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp)
            e={'id':'probe','part_id':'p','proposal':{'skill':'pick'},'policy_source':'development_failure_probe',
               'executed':True,'source':'live_simulation','tool_result':{'executed':True,'source':'live_simulation'}}
            write(p/'result.json',{'status':'failed','split':'development','development_failure_probe':True,'metrics':counters([e])})
            (p/'events.jsonl').write_text(__import__('json').dumps(e)+'\n',encoding='utf8')
            self.assertIn('invalid_development_probe_provenance:probe',audit(p)['issues'])

    def test_compact_candidate_table_preserves_all_ids_checks_and_original(self):
        c={'candidate_id':'p:r1:g0','position_mm':[.123456789,0,8],
           'quaternion_xyzw':[0,0,0,1],'opening_width_mm':30.,'approach_family':'top',
           'geometry':{'contact_points_part_m':[[0,.01,0],[0,-.01,0]],
             'contact_normals_part':[[0,1,0],[0,-1,0]],'contact_spacing_m':.02},
           'checks':{'physical_grasp':'not_run','opposing_surfaces':'passed'}}
        context={'observation_revision':1,'grasp_candidates':[c,{**c,'candidate_id':'p:r1:g1'}]}
        out=observation_packet(context)['grasp_candidates']
        self.assertEqual([x[0] for x in out['rows']],['p:r1:g0','p:r1:g1'])
        self.assertEqual(out['common_checks'],c['checks']);self.assertEqual(out['rows'][0][5][0][1],10.)
        self.assertEqual(c['position_mm'][0],.123456789)

    def test_guidance_view_does_not_round_actual_successful_parameters(self):
        g={'position_mm':[.123456789123,0,8],'quaternion_xyzw':[0,0,0,1],'opening_width_mm':30.}
        i={'id':'incident','installation_evidence':'install','events':[{'id':'pick','resolved_grasp':g,'executed':True}]}
        view=guidance_packet(i)
        self.assertEqual(view['events'][0]['applied_grasp']['position_mm'],g['position_mm'])
        self.assertEqual(view['installation_evidence'],'install');self.assertEqual(view['events'][0]['id'],'pick')

    def test_sdk_output_roundtrips_in_shared_history_with_wire_field_names(self):
        call=ResponseFunctionToolCall(type='function_call',name='propose_assembly_action',arguments='{}',
             call_id='call_test',id='fc_test',status='completed',**{'async':False})
        response=Response.model_construct(status='completed',output=[call],
             usage=ResponseUsage(input_tokens=20,output_tokens=5,total_tokens=25,
                input_tokens_details={'cached_tokens':0,'cache_write_tokens':0},output_tokens_details={'reasoning_tokens':0}))
        class Client:
            def with_options(self,**kw):return self
            @property
            def responses(self):return self
            @property
            def input_tokens(self):return self
            def count(self,**kw):return SimpleNamespace(input_tokens=20)
            def create(self,**kw):return response
        config=load_llm_config(ROOT/'config/llm.astra-high.toml')
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);ledger=ApiLedger(config,root/'api.jsonl')
            body=Astra(config,Client(),ledger,None,{},Budget(10)).call({'model':config.model},'assembly')
            history=History.__new__(History);history.path=root/'history.jsonl';history.items=[];history.pending=None
            history.accept(body);history.complete({'status':'held','executed':True})
            self.assertNotIn('async_',history.items[0]);self.assertIs(history.items[0]['async'],False)
            self.assertNotIn('namespace',history.items[0]);self.assertEqual(history.items[1]['call_id'],'call_test')
            self.assertIsNone(history.pending);self.assertEqual(ledger.model_requests,1)

    def test_external_storage_uses_same_root_for_runs_and_live_memory(self):
        with tempfile.TemporaryDirectory() as temp,patch.dict('os.environ',{'ASSEMBLY_DATA_ROOT':temp}):
            self.assertEqual(data_root(),Path(temp));self.assertEqual(run_root(),Path(temp)/'runs')
            self.assertEqual(memory_root(),Path(temp)/'runtime/memory/assembly_v2')
        with patch.dict('os.environ',{'ASSEMBLY_DATA_ROOT':'E:/never-access-this'}):
            with self.assertRaises(ValueError):data_root()

if __name__=='__main__':unittest.main()
