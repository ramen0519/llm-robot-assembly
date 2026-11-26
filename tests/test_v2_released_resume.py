from pathlib import Path
from copy import deepcopy
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from assembly_v2.budget import Budget,DevelopmentBudget
from assembly_v2.resume import validate_boundary


class ReleasedResume(unittest.TestCase):
    def setUp(self):
        self.checkpoint={'source':'live_simulation','state_spec':'mjSTATE_INTEGRATION',
            'model_sha256':'model','installed':['a'], 'completion_context':{'retreat_complete':True},
            'controller':{'held_pose':None,'slip_reference':None,'gripper_command':0},'release_context':None}
        self.result={'installed':['a']}
        self.prepared={'order':['a','b'],'parts':{'a':{'target':[[1,0,0,0],[0,1,0,0],[0,0,1,0],[0,0,0,1]]}},'completion_profile_hash':'profile'}
        e={'part_id':'a','installation_verified':True,'executed':True,'source':'live_simulation',
            'verification':{'part_ids':['a'],'parts':{},'model_sha256':'model'}}
        self.proofs={'a':{'event':e}}

    def test_uncertain_or_copy_state_rejected(self):
        for label in ('held','slip','closed','retreat','copy','hard','release'):
            with self.subTest(label=label):
                c=deepcopy(self.checkpoint);r=deepcopy(self.result)
                if label=='held':c['controller']['held_pose']=[1]
                elif label=='slip':c['controller']['slip_reference']=[1]
                elif label=='closed':c['controller']['gripper_command']=128
                elif label=='retreat':c['completion_context']['retreat_complete']=False
                elif label=='copy':c['source']='mujoco_preflight_copy'
                elif label=='hard':r['hard_execution_violation']={'code':'contact'}
                else:c['release_context']={'opening_complete':False,'retreat_complete':False}
                with self.assertRaises(ValueError):validate_boundary(c,r,self.prepared,self.proofs)

    def test_pose_alone_cannot_prove_installation(self):
        with self.assertRaises(ValueError):validate_boundary(self.checkpoint,self.result,self.prepared,{})
        p=deepcopy(self.proofs);p['a']['event']['source']='mujoco_preflight_copy'
        with self.assertRaises(ValueError):validate_boundary(self.checkpoint,self.result,self.prepared,p)

    def test_completed_release_can_resume_only_with_installation_evidence(self):
        c=deepcopy(self.checkpoint);c['release_context']={'opening_complete':True,'retreat_complete':True}
        with patch('assembly_v2.resume.valid_evidence',return_value=True):
            self.assertEqual(validate_boundary(c,self.result,self.prepared,self.proofs),{'a'})
            p=deepcopy(self.proofs);p['a']['event']['verification']['model_sha256']='different'
            with self.assertRaises(ValueError):validate_boundary(c,self.result,self.prepared,p)
        with patch('assembly_v2.resume.valid_evidence',return_value=False):
            with self.assertRaises(ValueError):validate_boundary(c,self.result,self.prepared,self.proofs)

    def test_no_global_limit_still_honors_cancel_and_local_timeout(self):
        now=[0.];cancel=[False]
        b=DevelopmentBudget(clock=lambda:now[0],cancelled=lambda:cancel[0])
        now[0]=1000000.;b.check();self.assertEqual(b.remaining(15),15)
        self.assertIsNone(b.seconds_for_report())
        import json
        self.assertEqual(json.dumps({'wall_seconds':b.seconds_for_report()},allow_nan=False),'{"wall_seconds": null}')
        cancel[0]=True
        with self.assertRaisesRegex(TimeoutError,'cancelled'):b.check('physics_step')
        finite=Budget(3,clock=lambda:now[0]);now[0]+=4
        with self.assertRaisesRegex(TimeoutError,'deadline'):finite.check()

    def test_direct_executor_uses_default_memory_without_enabling_learning(self):
        from assembly_v2 import execution
        p={'order':['a'],'completion_profile_hash':'profile'}
        with tempfile.TemporaryDirectory() as tmp, \
             patch('assembly_v2.validation.check_prepared'), \
             patch.object(execution,'source_identity',return_value={}), \
             patch.object(execution,'inventory',return_value={}), \
             patch.object(execution,'Library') as library, \
             patch.object(execution,'check_reference',return_value={'status':'failed'}), \
             patch.object(execution,'report'), \
             patch.object(execution,'audit',return_value={'status':'passed'}), \
             patch.object(execution,'seal_bundle',return_value={}):
            library.return_value.snapshot={}
            r=execution.run(p,tmp,policy_kind='scripted')
            self.assertEqual(r['status'],'reference_invalid')
            self.assertEqual(library.call_args.args[0],execution.DEFAULT_MEMORY)
            self.assertEqual(r['api_calls'],0)

    def test_unbounded_mode_is_rejected_for_paid_or_research_runs(self):
        from assembly_v2.execution import run
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError,'API-free development only'):
                run({'order':[]},tmp,policy_kind='astra',no_wall_time_limit=True)


if __name__=='__main__':unittest.main()
