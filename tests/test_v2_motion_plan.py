from pathlib import Path
from copy import deepcopy
import sys,unittest,math
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from assembly_v2.motion_plan import DEFAULT,resolve,validate,contract
from assembly_v2.policy import validate_action,action_schema
from assembly_v2.memory import validate_guidance
from assembly_v2.skills import MeshSkills

class MotionPlanContract(unittest.TestCase):
    def test_bounds_unknown_fields_and_nonfinite_rejected(self):
        for field,value in [('lift_height_mm',50),('approach_distance_mm',121),('preinstall_extra_mm',-1),
                             ('lift_height_mm',True),('lift_height_mm',float('nan')),('release_mode','teleport')]:
            bad={**DEFAULT,field:value}
            with self.assertRaises(ValueError):validate(bad)
        with self.assertRaises(ValueError):validate({**DEFAULT,'force_limit_n':100})
        with self.assertRaises(ValueError):validate({**DEFAULT,'transport_via_goal_offsets_mm':[[0,0,0]]*3})
        with self.assertRaises(ValueError):validate({**DEFAULT,'transport_via_goal_offsets_mm':[[250,250,250]]})
    def test_null_inherits_but_fixed_mode_cannot_propose(self):
        plan=resolve({**DEFAULT,'lift_height_mm':80})
        self.assertEqual(resolve(None,current=plan),plan)
        self.assertEqual(resolve({k:None for k in DEFAULT},current=plan),plan)
        with self.assertRaises(ValueError):resolve(plan,mode='fixed')
        self.assertEqual(resolve(None,mode='fixed'),DEFAULT)
    def test_defined_insertion_axis_is_immutable(self):
        op={'kind':'linear_insertion','axis_world':[0,0,1]}
        with self.assertRaises(ValueError):validate({**DEFAULT,'preinstall_axis_world':[1,0,0]},operation=op)
        with self.assertRaises(ValueError):validate({**DEFAULT,'preinstall_axis_world':[0,0,2]})
        validate({**DEFAULT,'preinstall_axis_world':[0,0,1]},operation=op)
    def test_policy_contract_rejects_stale_wrong_mode_or_action_plan(self):
        context={'allowed_skills':['lift','stop'],'part_id':'x','target_id':'cad_goal:x','observation_revision':2,
                 'motion_plan_contract':contract('bounded'),'operation':{'kind':'generic_goal_placement'}}
        value={'skill':'lift','part_id':'x','target_id':'cad_goal:x','observation_revision':2,'reason':'관측된 들기 실패에 대응','grasp':None,'motion_plan':{**DEFAULT,'lift_height_mm':80}}
        validate_action(value,context)
        for changed in ({**value,'skill':'stop'},{**value,'observation_revision':1}):
            with self.assertRaises(ValueError):validate_action(changed,context)
        with self.assertRaises(ValueError):validate_action(value,{**context,'motion_plan_contract':contract('fixed')})
        self.assertEqual(action_schema(context),action_schema({**context,'allowed_skills':['stop']}))
    def test_configuring_invalid_recipe_does_not_change_current(self):
        skills=MeshSkills.__new__(MeshSkills);skills.active='x';skills.prepared={'parts':{'x':{'operation':{'kind':'generic_goal_placement'}}}}
        skills.motion_plan=deepcopy(DEFAULT);skills.motion_plan_source='default'
        with self.assertRaises(ValueError):skills.configure_motion_plan({**DEFAULT,'lift_height_mm':10000})
        self.assertEqual(skills.motion_plan,DEFAULT)

    def test_plan_enabled_context_cannot_omit_field(self):
        context={'allowed_skills':['stop'],'part_id':'x','target_id':'g','observation_revision':1,
                 'motion_plan_contract':contract('bounded')}
        legacy={'skill':'stop','part_id':'x','target_id':'g','observation_revision':1,'reason':'stop','grasp':None}
        with self.assertRaises(ValueError):validate_action(legacy,context)

    def test_recovery_proposals_and_physical_actions_count_separately(self):
        from assembly_v2.artifacts import counters
        rows=[{'part_id':'x','proposal':{'skill':'lift'},'executed':executed} for executed in (False,True)]
        result=counters(rows)
        self.assertEqual(result['lift_recovery_proposals'],2)
        self.assertEqual(result['lift_recovery_actions'],1)
        self.assertEqual(result['retries'],0)  # Historical metric is grasp-candidate retries.

    def test_old_record_metrics_remain_auditable_without_rewriting_evidence(self):
        from assembly_v2.artifacts import counters,metric_issues
        old=counters([])
        for key in ('lift_recovery_proposals','lift_recovery_actions','physical_recovery_actions'):
            old.pop(key)
        self.assertEqual(metric_issues({'metrics':old},[]),[])
        self.assertTrue(metric_issues({'metrics':old,'motion_plan_mode':'bounded'},[]))
        self.assertTrue(metric_issues({'metrics':{**old,'retries':1}},[]))

class MotionPlanGuidance(unittest.TestCase):
    def incident(self):
        plan=resolve({**DEFAULT,'lift_height_mm':80})
        failed={'id':'f','executed':False,'failure':{'failure_code':'IK_NOT_CONVERGED'},'proposal':{'skill':'lift'}}
        lifted={'id':'l','executed':True,'failure':None,'proposal':{'skill':'lift'},'resolved_motion_plan':plan}
        installed={'id':'i','executed':True,'failure':None,'proposal':{'skill':'insert'}}
        incident={'events':[failed,lifted,installed],'installation_evidence':'i'}
        card={'summary':'이 조건에서 실제 80mm 들기 뒤 설치가 검증됨. 새 장면에서 재검사.',
              'action':'lift','parameters':[],'evidence_refs':['f','l','i'],
              'checks':['current_geometry','grasp_copy','installation_copy','main_completion_window'],
              'motion_plan':{'event_id':'l','parameters':plan}}
        return incident,card
    def test_requires_exact_executed_plan_failure_and_installation(self):
        i,c=self.incident();self.assertTrue(validate_guidance(c,i))
        for variation in ('value','unexecuted','missing_install','wrong_event'):
            inc,card=self.incident()
            if variation=='value':card['motion_plan']['parameters']={**card['motion_plan']['parameters'],'lift_height_mm':90}
            elif variation=='unexecuted':inc['events'][1]['executed']=False
            elif variation=='missing_install':card['evidence_refs'].remove('i')
            else:card['motion_plan']['event_id']='f'
            with self.assertRaises(ValueError):validate_guidance(card,inc)

if __name__=='__main__':unittest.main()
