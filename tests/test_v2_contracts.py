"""Adversarial v2 contracts; these are NOT model or physics success evidence."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace
import numpy as np
import trimesh
from scipy.spatial.transform import Rotation
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from assembly_v12.contracts import read,write,digest,sha
from assembly_v2.artifacts import counters,inventory,audit
from assembly_v2.grasp import resolve
from assembly_v2.memory import scope_for,compatible,validate_guidance
from assembly_v2.execution import split_contract
from assembly_v2.skills import MeshSkills,simplify_part_path
from assembly_v2.policy import Astra,validate_action
from assembly_v2.budget import Budget,ControlBudget
from assembly_v2.ui import create_app
from assembly_v2.preparation import source_geometry_check
from llm_runtime import load_llm_config,ApiLedger

ROOT=Path(__file__).resolve().parents[1]

class Contracts(unittest.TestCase):
    def test_retry_is_an_actually_started_additional_candidate(self):
        first={'part_id':'new','proposal':{'skill':'pick'},'policy_source':'astra','candidate_started':True,'preflight':{'accepted':False}}
        self.assertEqual(counters([first])['retries'],0)
        self.assertEqual(counters([first,first])['retries'],1)
        self.assertEqual(counters([{**first,'candidate_started':False}])['candidate_attempts'],0)

    def test_collection_and_evaluation_guards_apply_to_direct_runs(self):
        with self.assertRaises(ValueError):split_contract('collection',True,'astra',None,None,{},'renamed')
        with self.assertRaises(ValueError):split_contract('collection',False,'scripted',None,None,{},'renamed')
        with self.assertRaises(ValueError):split_contract('evaluation',False,'astra',None,None,{},'renamed')
        p={'parts':'test'};s={'id':'frozen'}
        with self.assertRaises(ValueError):split_contract('evaluation',True,'astra',s,{'prepared_sha256':'wrong','lineage_id':'renamed','snapshot_id':'frozen'},p,'renamed')

    def test_contact_conditions_before_similarity_and_ids_not_required(self):
        grasp={'tcp_in_part':np.eye(4).tolist(),'opening_width_mm':30.,'approach_axis_world':[0,0,-1]}
        f={'failure_code':'PLANNED_CONTACT','source':'planner_candidate','phase':'approach_grasp/preflight',
           'violations':[{'roles':['pad','product:old_id']} ]}
        scope=scope_for(f,grasp,'pick','profile')
        other=scope_for({**f,'violations':[{'roles':['product:new_name','pad']}]},grasp,'pick','profile')
        self.assertTrue(compatible(scope,other)[0])
        other['contact_roles']=[['robot','table']];self.assertFalse(compatible(scope,other)[0])
        other=deepcopy(scope);other['contact_roles_observed']=False;self.assertFalse(compatible(scope,other)[0])
        other=deepcopy(scope);other['opening_width_mm']=None;self.assertFalse(compatible(scope,other)[0])

    def test_full_pose_outside_bbox_valid_but_stale_candidate_rejected(self):
        p={'part_id':'p','asset':{'bounds_m':[[-.01]*3,[.01]*3]}}
        value={'mode':'pose','frame':'part_local','position_mm':[0,0,15.],
               'quaternion_xyzw':[0,0,0,1],'opening_width_mm':32.,'parent_candidate_id':None}
        resolved=resolve(value,{'presented':[]},p,np.eye(4),1,'astra')
        self.assertEqual(resolved['source'],'astra_direct_pose')
        with self.assertRaises(ValueError):resolve({**value,'quaternion_xyzw':[0,0,0,2]}, {'presented':[]},p,np.eye(4),1,'astra')
        row={**value,'candidate_id':'old','revision':1,'observed_pose_hash':digest(np.eye(4).tolist())}
        with self.assertRaises(ValueError):resolve({'mode':'candidate','candidate_id':'old'},{'presented':[row]},p,np.eye(4),2,'astra')

    def test_fresh_grasp_transform_does_not_reset_cumulative_slip_reference(self):
        skill=MeshSkills.__new__(MeshSkills);original=(np.zeros(3),np.eye(3));current=(np.array([.002,0,0]),Rotation.from_euler('z',2,degrees=True).as_matrix())
        skill.data=None;skill.motion=SimpleNamespace(held_pose=original,slip_reference=original,sample=lambda **kw:{})
        seen=[];skill.verifier=SimpleNamespace(check_held=lambda d,r,p:seen.append(p),relative_part_pose=lambda d:current)
        tcp=skill.part_to_tcp(np.eye(4))
        np.testing.assert_allclose(tcp[:3,3],-current[1].T@current[0]);self.assertIs(skill.motion.slip_reference,original);self.assertIs(seen[0],original)

    def test_native_path_simplification_preserves_bends_and_rotation(self):
        poses=np.repeat(np.eye(4)[None],101,axis=0);poses[:,2,3]=np.linspace(0,.1,101)
        self.assertEqual(len(simplify_part_path(poses)),2)
        poses[50,0,3]=.02;self.assertGreater(len(simplify_part_path(poses)),2)
        poses[50,:3,:3]=Rotation.from_euler('x',30,degrees=True).as_matrix()
        simplified=simplify_part_path(poses);self.assertTrue(any(np.allclose(x,poses[50]) for x in simplified))

    def test_API_timeout_recomputed_after_count_no_generation_if_expired(self):
        clock=[0.];budget=Budget(5,clock=lambda:clock[0]);config=load_llm_config(ROOT/'config/llm.astra-high.toml')
        called=[]
        class Client:
            def with_options(self,**kw):called.append(kw['timeout']);return self
            @property
            def responses(self):return self
            @property
            def input_tokens(self):return self
            def count(self,**kw):clock[0]=6.;return SimpleNamespace(input_tokens=20)
            def create(self,**kw):raise AssertionError('Must not send a generation after the deadline.')
        with tempfile.TemporaryDirectory() as temp:
            ledger=ApiLedger(config,Path(temp)/'ledger.jsonl');policy=Astra(config,Client(),ledger,None,{},budget)
            with self.assertRaises(TimeoutError):policy.call({'model':'gpt-6-astra'},'single')
            self.assertEqual(ledger.model_requests,0);self.assertEqual(called,[5.])

    def test_control_limit_does_not_reduce_guidance_global_budget(self):
        config=load_llm_config(ROOT/'config/llm.astra-high.toml');control=ControlBudget(2)
        with tempfile.TemporaryDirectory() as temp:
            ledger=ApiLedger(config,Path(temp)/'ledger.jsonl')
            for i in range(13):
                if i<11:control.begin()
                with ledger.agent_turn('single' if i<11 else 'guidance'):
                    ledger.begin_count(str(i));ledger.begin_generation(20,str(i));ledger.settle_usage({'input_tokens':20,'output_tokens':5,'total_tokens':25})
            self.assertEqual(control.used,11);self.assertEqual(ledger.model_requests,13)
            control.begin()
            with self.assertRaises(RuntimeError):control.begin()
            self.assertEqual(ledger.agent_totals()['guidance']['api_calls'],2)

    def test_guidance_cannot_invent_action_value_or_unit(self):
        grasp={'position_mm':[0,0,8.],'quaternion_xyzw':[0,0,0,1],'opening_width_mm':30.}
        failure={'id':'f','failure':{'failure_code':'test'},'executed':False,'proposal':{'skill':'pick'}}
        picked={'id':'p','executed':True,'failure':None,'proposal':{'skill':'pick'},'resolved_grasp':grasp}
        installed={'id':'i','executed':True,'failure':None,'proposal':{'skill':'insert'}}
        incident={'events':[failure,picked,installed],'installation_evidence':'i'}
        card={'summary':'현재 형상에서 이 파지 자세의 간섭을 다시 검사한다.','action':'pick','evidence_refs':['f','p','i'],
          'parameters':[{'name':k,'unit':'xyzw' if k=='quaternion_xyzw' else 'mm','value':np.atleast_1d(v).tolist(),'event_id':'p'} for k,v in grasp.items()],
          'checks':['current_geometry','grasp_copy','installation_copy','main_completion_window']}
        self.assertTrue(validate_guidance(card,incident))
        with self.assertRaises(ValueError):validate_guidance({**card,'action':'나사체결'},incident)
        changed=deepcopy(card);changed['parameters'][0]['value'][2]=80
        with self.assertRaises(ValueError):validate_guidance(changed,incident)
        changed=deepcopy(card);changed['parameters'][0]['unit']='m'
        with self.assertRaises(ValueError):validate_guidance(changed,incident)

    def test_inventory_includes_robot_xml_and_geometry_not_only_python(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'models').mkdir();file=root/'models/robot.xml';file.write_text('<x/>')
            with patch('assembly_v2.artifacts.ROOT',root):
                a=inventory();file.write_text('<changed/>');b=inventory()
            self.assertNotEqual(a['id'],b['id']);self.assertIn('models/robot.xml',a['files'])

    def test_original_units_and_embedded_assembly_transform_are_semantically_checked(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);part=trimesh.creation.box(extents=[.02,.03,.04]);native=part.copy();native.apply_scale(100);native.export(root/'native.obj')
            goal=np.eye(4);goal[:3,:3]=Rotation.from_euler('z',32,degrees=True).as_matrix();goal[:3,3]=[.01,.02,.03]
            mm=goal.copy();mm[:3,3]*=1000
            row={'source_mesh':'source.obj','native_mesh':'native.obj','mesh_coordinate_frame':'assembly','assembly_matrix_mm':mm.tolist()}
            contract={'part_mapping':{'p':row}}
            for unit,factor in [('m',1),('cm',.01),('mm',.001),('inch',.0254)]:
                source=part.copy();source.apply_transform(goal);source.apply_scale(1/factor);source.export(root/'source.obj')
                source_geometry_check(root,contract,{'source_unit':unit},Budget(10))
            with self.assertRaises(ValueError):source_geometry_check(root,contract,{'source_unit':'mm'},Budget(10))
            row['mesh_coordinate_frame']='part_local'
            with self.assertRaises(ValueError):source_geometry_check(root,contract,{'source_unit':'inch'},Budget(10))

    def test_success_audit_rejects_missing_same_window(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp);write(folder/'result.json',{'status':'physical_success','metrics':counters([]),'policy_source':'scripted'})
            write(folder/'prepared.json',{'completion_profile_hash':'approved','order':['a','b']})
            self.assertEqual(audit(folder)['status'],'failed')

    def test_local_ui_requires_csrf_and_reports_real_environment_without_keys(self):
        with tempfile.TemporaryDirectory() as temp:
            app=create_app(Path(temp)/'runs');client=app.test_client()
            self.assertEqual(client.get('/').status_code,200)
            status=client.get('/api/environment').get_json();self.assertIn('API_key_present',status['environment']);self.assertNotIn('OPENAI_API_KEY',json_text(status))
            self.assertEqual(client.post('/api/start',data={'command':'run'}).status_code,403)
            self.assertEqual(client.post('/api/start',data={'csrf':status['csrf'],'command':'delete'}).status_code,400)

def json_text(value):
    import json
    return json.dumps(value)

if __name__=='__main__':unittest.main()
