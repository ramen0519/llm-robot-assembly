from pathlib import Path
import sys,unittest
from copy import deepcopy
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from assembly_v2.policy import Scripted
from assembly_v2.scripted_feedback import remember_pick,shorter_approach


class ScriptedFeedback(unittest.TestCase):
    def context(self):
        return {'part_id':'unseen','target_id':'goal','observation_revision':1,
                'allowed_skills':['pick','stop'],'candidate_attempts':0,
                'current_pose':{'position_mm':[100,200,300],'quaternion_xyzw':[0,0,0,1]},
                'holding_part':None,'motion_plan_contract':{'mode':'bounded'},
                'grasp_candidates':[{'candidate_id':'r1:a','approach_family':'top','position_mm':[1,2,3],
                    'quaternion_xyzw':[0,1,0,0],'opening_width_mm':20}]}

    def failure_context(self):
        c=self.context();previous=remember_pick(c,{'mode':'candidate','candidate_id':'r1:a'},None)
        c.update(observation_revision=2,candidate_attempts=1,failure={
            'source':'mujoco_preflight_copy','failure_code':'IK_NOT_CONVERGED','phase':'approach_pregrasp'})
        return c,previous

    def test_policy_retries_same_pose_at_60_then_40_without_stale_candidate_ids(self):
        policy=Scripted();c=self.context();first=policy.propose(c)
        self.assertEqual(first['grasp']['mode'],'candidate')
        c.update(observation_revision=2,candidate_attempts=1,failure={
            'source':'mujoco_preflight_copy','failure_code':'IK_NOT_CONVERGED','phase':'approach_pregrasp'})
        c['grasp_candidates'][0]['candidate_id']='r2:a'
        second=policy.propose(c)
        self.assertEqual(second['grasp']['position_mm'],[1,2,3])
        self.assertIsNone(second['grasp']['parent_candidate_id'])
        self.assertEqual(second['motion_plan']['approach_distance_mm'],60.)
        self.assertEqual(policy.last_provenance,'scripted_approach_feedback_revalidation')
        c.update(observation_revision=3,candidate_attempts=2)
        self.assertEqual(policy.propose(c)['motion_plan']['approach_distance_mm'],40.)

    def test_real_failure_changed_pose_other_part_and_fixed_mode_are_not_replayed(self):
        c,previous=self.failure_context()
        for key,value in [('part_id','other'),('target_id','other'),('observation_revision',1),
                          ('current_pose',{'changed':True}),('holding_part','unseen'),
                          ('motion_plan_contract',{'mode':'fixed'})]:
            changed=deepcopy(c);changed[key]=value
            self.assertIsNone(shorter_approach(changed,previous))
        changed=deepcopy(c);changed['failure']['source']='live_simulation'
        self.assertIsNone(shorter_approach(changed,previous))
        changed=deepcopy(c);changed['failure']['phase']='pick_lift'
        self.assertIsNone(shorter_approach(changed,previous))

    def test_bound_and_previous_values_are_preserved(self):
        c,previous=self.failure_context();original=deepcopy(previous)
        result=shorter_approach(c,previous)
        self.assertEqual(previous,original)
        self.assertEqual(result[1]['lift_height_mm'],original['motion_plan']['lift_height_mm'])
        previous['motion_plan']['approach_distance_mm']=40.
        self.assertIsNone(shorter_approach(c,previous))

if __name__=='__main__':unittest.main()
