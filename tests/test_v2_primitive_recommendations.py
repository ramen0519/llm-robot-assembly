from pathlib import Path
import sys,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from assembly_v2.grasp_screen import choose_joint_opening
from assembly_v2.policy import development_candidate_rank,Scripted


class PrimitiveRecommendations(unittest.TestCase):
    def test_short_closure_when_no_roomy_opening(self):
        narrow={'requested_opening_mm':10.,'minimum_distance_m':.002}
        wide={'requested_opening_mm':18.,'minimum_distance_m':.0025}
        self.assertIs(choose_joint_opening([wide,narrow]),narrow)

    def test_existing_roomy_preference_is_preserved(self):
        narrow={'requested_opening_mm':10.,'minimum_distance_m':.002}
        roomy={'requested_opening_mm':18.,'minimum_distance_m':.004}
        self.assertIs(choose_joint_opening([narrow,roomy]),roomy)
        self.assertIsNone(choose_joint_opening([]))

    def test_development_policy_does_not_prefer_known_collision_for_small_rotation(self):
        blocked={'checks':{'nominal_source_tool':{'status':'no_jointly_clear_sampled_opening'}},
                 'motion_hint':{'goal_top_approach_deviation_rad':0.,'tcp_orientation_change_rad':0.}}
        clear={'checks':{'nominal_source_tool':{'status':'clear_sampled_nominal'},
                        'nominal_full_tool_closure':{'goal_with_first_contact':{'status':'clear_sampled_nominal'}}},
               'motion_hint':{'goal_top_approach_deviation_rad':.1,'tcp_orientation_change_rad':1.}}
        self.assertEqual(sorted([blocked,{},clear],key=development_candidate_rank),[clear,{},blocked])

    def test_first_part_injection_does_not_repeat_rejected_candidate_on_next_part(self):
        initial={'mode':'pose','frame':'part_local','position_mm':[0,0,10],
                 'quaternion_xyzw':[0,1,0,0],'opening_width_mm':20,'parent_candidate_id':None}
        policy=Scripted(first_grasp=initial)
        ctx={'part_id':'first','target_id':'goal','observation_revision':1,'allowed_skills':['pick'],
             'candidate_attempts':0,'grasp_candidates':[
                 {'candidate_id':'a','approach_family':'top','motion_hint':{}},
                 {'candidate_id':'b','approach_family':'top','motion_hint':{}}]}
        self.assertEqual(policy.propose(ctx)['grasp'],initial)
        ctx.update(part_id='next',candidate_attempts=0)
        self.assertEqual(policy.propose(ctx)['grasp']['candidate_id'],'a')
        ctx.update(candidate_attempts=1,observation_revision=2)
        self.assertEqual(policy.propose(ctx)['grasp']['candidate_id'],'b')

    def test_generated_pose_repairs_are_not_lost_in_scripted_selection(self):
        from copy import deepcopy
        alt={'mode':'pose','frame':'part_local','position_mm':[0,0,20],
             'quaternion_xyzw':[0,1,0,0],'opening_width_mm':28,'parent_candidate_id':'a',
             'geometry':{'goal_clearance_mm':2.8}}
        alt2=deepcopy(alt);alt2['position_mm']=[0,0,20.5]
        c={'candidate_id':'a','approach_family':'top','motion_hint':{},
            'checks':{'nominal_source_tool':{'status':'no_jointly_clear_sampled_opening'}},
            'geometry_pose_alternatives':{'physical_success':False,'alternatives':[alt,alt2]}}
        context={'part_id':'unseen','target_id':'goal','observation_revision':1,
                 'allowed_skills':['pick'],'candidate_attempts':0,'grasp_candidates':[c]}
        original=deepcopy(c);policy=Scripted()
        action=policy.propose(context)
        self.assertEqual(action['grasp']['mode'],'pose')
        self.assertEqual(action['grasp']['position_mm'],[0,0,20])
        self.assertNotIn('geometry',action['grasp'])
        context['candidate_attempts']=1
        self.assertEqual(policy.propose(context)['grasp']['position_mm'],[0,0,20.5])
        self.assertEqual(c,original)
        self.assertEqual(policy.last_provenance,'scripted_geometry_pose_alternative')

    def test_unchecked_or_negative_clearance_is_not_promoted_to_repaired_pose(self):
        from assembly_v2.policy import development_choices
        c={'candidate_id':'a','approach_family':'top','geometry_pose_alternatives':{
            'alternatives':[{'geometry':{}},{'geometry':{'goal_clearance_mm':-1}}]}}
        self.assertEqual(development_choices([c]),[c])

    def test_ik_failure_is_not_ranked_as_reachable_for_small_wrist_turn(self):
        a={'checks':{'nominal_goal_ik':{'status':'not_found'}},
           'motion_hint':{'goal_top_approach_deviation_rad':0.,'tcp_orientation_change_rad':0.}}
        b={'checks':{'nominal_goal_ik':{'status':'converged'}},
           'motion_hint':{'goal_top_approach_deviation_rad':.25,'tcp_orientation_change_rad':1.}}
        self.assertLess(development_candidate_rank(b),development_candidate_rank(a))

    def test_nominal_margin_is_preferred_among_equally_eligible_alternatives(self):
        def item(margin):
            return {'checks':{'nominal_full_tool_closure':{'goal_with_first_contact':
                {'status':'clear_sampled_nominal','worst_clearance_mm':margin}}}}
        self.assertLess(development_candidate_rank(item(4.)),development_candidate_rank(item(.8)))

    def test_small_feature_does_not_retract_farther_only_for_a_small_clearance_gain(self):
        def item(offset,margin):
            return {'geometry_pose_adjustment_mm':offset,'checks':{'nominal_full_tool_closure':{
                'goal_with_first_contact':{'status':'clear_sampled_nominal','worst_clearance_mm':margin}}}}
        self.assertLess(development_candidate_rank(item(2.,2.8)),development_candidate_rank(item(2.5,3.)))


if __name__=='__main__':unittest.main()
