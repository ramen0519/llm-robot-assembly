"""Bounded continuation, evidenced learning and UI regressions; no API calls."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import json
import sys
import tempfile
import unittest
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from assembly_v2.budget import Budget
from assembly_v2.continuation import MotionSlice,SliceExpired
from assembly_v2.skills import MeshSkills
from assembly_v2.policy import Astra,Scripted
from assembly_v2.learning import grounded_development_guidance,retrieval_outcomes
from assembly_v2.execution import run
from assembly_v2.ui import create_app,user_summary
from assembly_v12.context import History
from assembly_v12.contracts import write


class PrototypeFlow(unittest.TestCase):
    def test_supply_screen_prefers_goal_pose_only_with_robust_support(self):
        import trimesh
        from assembly_v2.preparation import choose_initial
        for width,expect_upright in ((22,True),(2,False)):
            mesh=trimesh.creation.box(extents=[width/1000,.022,.064])
            transforms,prob=mesh.compute_stable_poses()
            asset={'center_mass_m':mesh.center_mass.tolist(),'stable_poses':[
                {'transform':t.tolist(),'probability':float(p)} for t,p in zip(transforms,prob)]}
            rotation,note=choose_initial(asset,np.eye(3),mesh)
            self.assertEqual(np.allclose(rotation,np.eye(3)),expect_upright)
            self.assertEqual(note['physical_settling'],'not_run')
            json.dumps(note,allow_nan=False)

    def test_scripted_candidate_selection_considers_installed_tool_direction(self):
        ctx={'part_id':'p','target_id':'goal','observation_revision':1,'allowed_skills':['pick'],
             'candidate_attempts':0,'grasp_candidates':[
                {'candidate_id':'above','approach_family':'top','motion_hint':{'goal_top_approach_deviation_rad':1.57,'tcp_orientation_change_rad':0}},
                {'candidate_id':'side','approach_family':'side','motion_hint':{'goal_top_approach_deviation_rad':0,'tcp_orientation_change_rad':1.57}}]}
        self.assertEqual(Scripted().propose(ctx)['grasp']['candidate_id'],'side')
        self.assertEqual(Scripted(approach='top').propose(ctx)['grasp']['candidate_id'],'above')

    def test_local_slice_is_distinct_from_global_deadline_and_cancellation(self):
        now=[0.];parent=Budget(10,clock=lambda:now[0]);cut=MotionSlice(parent,2)
        now[0]=3
        with self.assertRaises(SliceExpired):cut.check('motion')
        self.assertEqual(parent.deadline,10);self.assertEqual(parent.remaining(),7)
        now[0]=10
        try:cut.check()
        except TimeoutError as exc:self.assertNotIsInstance(exc,SliceExpired)
        else:self.fail('global deadline ignored')
        now[0]=0;parent=Budget(10,clock=lambda:now[0],cancelled=lambda:True)
        try:MotionSlice(parent,2).check()
        except TimeoutError as exc:
            self.assertNotIsInstance(exc,SliceExpired);self.assertIn('cancelled:',str(exc))
        else:self.fail('cancellation ignored')

    def test_copy_release_collision_changes_recipe_without_waiving_revalidation(self):
        ctx={'part_id':'p','target_id':'goal','observation_revision':2,'allowed_skills':['pick'],
             'candidate_attempts':1,'grasp_candidates':[
                {'candidate_id':'first','approach_family':'top','motion_hint':{}},
                {'candidate_id':'second','approach_family':'top','motion_hint':{}}],
             'failure':{'failure_code':'UNEXPECTED_CONTACT','source':'mujoco_preflight_copy','phase':'release'}}
        action=Scripted().propose(ctx)
        self.assertEqual(action['grasp']['candidate_id'],'first')
        self.assertEqual(action['motion_plan']['release_mode'],'limited_open_then_retreat')
        ctx['motion_plan_contract']={'mode':'fixed'}
        action=Scripted().propose(ctx)
        self.assertIsNone(action['motion_plan']);self.assertEqual(action['grasp']['candidate_id'],'second')

    def test_slice_reobserves_and_replans_at_current_state_without_budget_reset(self):
        parent=Budget(10);calls=[];observations=[];holds=[]
        s=SimpleNamespace(prepared={},budget=parent,source='live_simulation',active='p',
            recorder=None,data=SimpleNamespace(time=0.),continuation_events=[],
            checkpoint=lambda label:{'label':label},fresh_hold=lambda:holds.append('measured'))
        s.motion=SimpleNamespace(stage='transport',budget=parent,sample=lambda **kw:observations.append(s.data.time) or {'measured':True})
        def once(target,**kw):
            calls.append(s.data.time);s.motion.budget=MotionSlice(parent,2)
            if len(calls)==1:
                s.data.time=4.;raise SliceExpired('motion_slice:transport')
            return 'arrived'
        s._move_once=once
        deadline=parent.deadline
        self.assertEqual(MeshSkills.move(s,np.eye(4),grasp=True),'arrived')
        self.assertEqual(calls,[0.,4.]);self.assertEqual(observations,[4.]);self.assertEqual(holds,['measured'])
        self.assertIs(s.motion.budget,parent);self.assertEqual(parent.deadline,deadline)
        self.assertEqual(s.continuation_events[0]['status'],'reobserved_replan')
        self.assertTrue(s.continuation_events[0]['original_slip_reference_preserved'])

    def test_global_timeout_is_never_automatically_continued(self):
        parent=Budget(10);calls=[]
        def once(*a,**kw):calls.append(1);raise TimeoutError('deadline:motion')
        s=SimpleNamespace(prepared={},budget=parent,motion=SimpleNamespace(budget=parent),_move_once=once)
        with self.assertRaisesRegex(TimeoutError,'deadline:'):MeshSkills.move(s,np.eye(4))
        self.assertEqual(len(calls),1)

    def test_release_requires_original_saved_goal(self):
        goal=np.eye(4);changed=goal.copy();changed[0,3]=.01
        s=SimpleNamespace(active='p',prepared={'parts':{'p':{'target':goal.tolist()}}},
            motion=SimpleNamespace(held_pose=None),release_context={'goal':changed.tolist()})
        with self.assertRaisesRegex(ValueError,'CAD goal changed'):MeshSkills.finish_release(s,'test')
        s.release_context=None
        with self.assertRaisesRegex(ValueError,'No authenticated release'):MeshSkills.finish_release(s,'test')

    def incident(self):
        grasp={'position_mm':[0,0,8.123456789],'quaternion_xyzw':[0,1,0,0],'opening_width_mm':30.}
        return {'split':'development','registration':'eligible','installation_evidence':'installed',
            'events':[{'id':'failed','executed':False,'failure':{'failure_code':'PLANNED_CONTACT'}},
                {'id':'picked','executed':True,'failure':None,'proposal':{'skill':'pick'},'resolved_grasp':grasp},
                {'id':'installed','executed':True,'failure':None,'proposal':{'skill':'insert'}}]}

    def test_offline_card_requires_measured_resolved_development_evidence(self):
        incident=self.incident();card=grounded_development_guidance(incident)
        self.assertEqual(card['action'],'pick');self.assertIn('failed',card['evidence_refs'])
        self.assertEqual(card['parameters'][0]['value'],[0,0,8.123456789])
        for updates in ({'split':'collection'},{'registration':'pending'}):
            with self.assertRaises(ValueError):grounded_development_guidance({**incident,**updates})
        incident['events'][1]['executed']=False;incident['events'][2]['executed']=False
        with self.assertRaises(ValueError):grounded_development_guidance(incident)

    def test_development_writer_cannot_use_shared_or_research_memory(self):
        from assembly_v2.memory import DEFAULT_MEMORY
        with tempfile.TemporaryDirectory() as temp:
            for kw in ({'split':'collection','policy_kind':'scripted','memory_root':temp},
                       {'split':'development','policy_kind':'astra','memory_root':temp},
                       {'split':'development','policy_kind':'scripted','memory_root':DEFAULT_MEMORY}):
                with self.assertRaisesRegex(ValueError,'isolated memory'):
                    run({},Path(temp)/'run',development_learning=True,**kw)

    def test_retrieval_does_not_claim_effect_when_not_executed_or_failed(self):
        base={'id':'e','part_id':'p','retrieved':['case'],'policy_source':'scripted'}
        rows=retrieval_outcomes([{**base,'executed':False},{**base,'executed':True,'failure':{'x':1}},
            {**base,'executed':True,'preflight':{'accepted':True}}],{'p'})
        self.assertEqual([r['status'] for r in rows],['retrieved_not_executed','revalidation_failed','executed_and_installation_preserved'])
        self.assertTrue(all(not r['causal_benefit_proven'] for r in rows))

    def test_invalid_model_proposal_gets_one_counted_repair_in_same_history(self):
        with tempfile.TemporaryDirectory() as temp:
            h=History.__new__(History);h.path=Path(temp)/'history.jsonl';h.items=[];h.pending=None
            logs=[];ledger=SimpleNamespace(record=lambda *a,**kw:logs.append((a,kw)))
            agent=Astra(SimpleNamespace(model='unit-only',reasoning_effort='high'),None,ledger,h,{'common':'test','assembly':'test'},Budget(10))
            context={'part_id':'p','target_id':'goal','observation_revision':1,'active_role':'assembly','allowed_skills':['stop']}
            calls=[]
            def call(req,role):
                calls.append(req);value={'skill':'pick','part_id':'p','target_id':'goal','observation_revision':1,'reason':'invalid action','grasp':None,'motion_plan':None}
                return {'output':[{'type':'function_call','name':'propose_assembly_action','call_id':'c'+str(len(calls)),
                    'arguments':json.dumps(value)}]}
            agent.call=call
            with self.assertRaises(ValueError):agent.propose(context)
            self.assertEqual(len(calls),2);self.assertIs(agent.ledger,ledger);self.assertIs(agent.history,h)
            self.assertIsNone(h.pending);self.assertEqual(len(logs),1)
            self.assertEqual(sum(x.get('type')=='function_call_output' for x in h.items),2)

    def test_ui_status_separates_scripted_native_success_from_astra(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);job='a'*32;folder=root/job;folder.mkdir()
            write(folder/'result.json',{'status':'physical_success','policy_source':'scripted','api_calls':0,
                  'installed':['p'],'required':['p'],'experience_summary':{'registered':1}})
            app=create_app(root);client=app.test_client();v=client.get('/api/jobs/'+job).get_json()['summary']
            self.assertEqual(v['decision'],'개발용 규칙 실행');self.assertEqual(v['installed'],1)
            self.assertEqual(v['experience']['registered'],1)
            self.assertEqual(client.post('/api/cancel/'+job,json={}).status_code,403)

    def test_ui_does_not_call_an_invalid_or_unsettled_response_a_model_decision(self):
        value={'result':{'status':'failed','policy_source':'astra','api_calls':1,'policy_decision_evaluated':False}}
        self.assertEqual(user_summary(value)['decision'],'Astra 호출·판단 미완료')
        value['result']['policy_decision_evaluated']=True
        self.assertEqual(user_summary(value)['decision'],'실제 Astra 판단')


if __name__=='__main__':unittest.main()
