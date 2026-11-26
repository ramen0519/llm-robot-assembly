from pathlib import Path
from types import SimpleNamespace
import sys,unittest
import numpy as np,mujoco
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from assembly_v12.media import state
from assembly_v2.budget import Budget
from assembly_v2.grasp_screen import GoalToolScreen


class FullToolClosure(unittest.TestCase):
    def fixture(self):
        def dummy(i):
            return f'<body name="dummy{i}" pos="1 0 0"><joint type="hinge"/><geom size=".001" contype="0" conaffinity="0" mass=".001"/></body>'
        xml='<mujoco><worldbody><site name="gripper_pinch"/>'+''.join(dummy(i) for i in range(6))
        for side,axis in [('left','0 1 0'),('right','0 -1 0')]:
            xml+=f'<body name="{side}"><joint type="slide" axis="{axis}"/><geom name="{side}_pad" type="box" size=".002 .001 .003" mass=".01"/></body>'
        xml+=''.join(dummy(i) for i in range(6,12))
        xml+='<body name="workcell_part_p"><geom name="wide_body" type="box" size=".004 .009 .002"/><geom name="small_tip" type="box" pos="0 0 .006" size=".003 .0025 .002"/></body>'
        for y in (-.0125,.0125):
            xml+=f'<geom type="box" pos=".1 {y} 0" size=".01 .001 .02"/>'
        xml+='</worldbody></mujoco>'
        model=mujoco.MjModel.from_xml_string(xml);data=mujoco.MjData(model)
        mujoco.mj_forward(model,data)
        ids={model.geom(n).id for n in ('left_pad','right_pad')}
        part={model.geom(n).id:'p' for n in ('wide_body','small_tip')}
        cal=[{'gap_mm':gap,'qpos':[gap/2000+.001]*2+[0.]*6} for gap in (28.,24.,20.,18.,16.,8.,6.,4.)]
        goal=np.eye(4);goal[0,3]=.1
        p={'base_id':'base','parts':{'p':{'target':goal.tolist()}},
           'profile':{'resources':{'candidate_seconds':10},'grasp_pad_center_calibration':{'samples':cal}}}
        skills=SimpleNamespace(model=model,data=data,prepared=p,active='p',budget=Budget(10),
                 verifier=SimpleNamespace(gripper_geoms=ids,robot_geoms=ids,geom_part=part,pads=ids))
        candidate={'position_mm':[0.,0.,0.],'quaternion_xyzw':[0.,0.,0.,1.],
                   'opening_width_mm':28.,'geometry':{'contact_spacing_m':.005}}
        return skills,candidate

    def test_wide_body_contact_overrides_small_feature_width(self):
        skills,c=self.fixture();before=state(skills.model,skills.data)
        screen=GoalToolScreen(skills)
        self.assertEqual(screen(c)['status'],'clear_nominal')
        result=screen.closure_preview(c)
        self.assertEqual(result['status'],'first_contact_bracketed')
        self.assertGreater(result['contact_gap_bracket_mm'][0],5.)
        self.assertTrue(result['surface_width_underestimates_first_contact'])
        self.assertEqual(result['goal_with_first_contact']['status'],'blocked_sampled_nominal')
        self.assertFalse(result['physical_success'])
        np.testing.assert_array_equal(before,state(skills.model,skills.data))

    def test_raised_contact_can_avoid_bulk_without_claiming_success(self):
        skills,c=self.fixture();c['position_mm'][2]=6.
        result=GoalToolScreen(skills).closure_preview(c)
        self.assertEqual(result['status'],'first_contact_bracketed')
        self.assertLessEqual(result['contact_gap_bracket_mm'][0],5.)
        self.assertEqual(result['goal_with_first_contact']['status'],'clear_sampled_nominal')
        self.assertEqual(result['bilateral_grasp'],'not_proved')

    def test_missing_calibration_and_time_limit_remain_not_run(self):
        skills,c=self.fixture();screen=GoalToolScreen(skills);screen.cal=[]
        self.assertEqual(screen.closure_preview(c)['status'],'not_run')
        screen=GoalToolScreen(skills);screen.started-=11
        self.assertEqual(screen.closure_preview(c)['reason'],'candidate_screen_time_limit')

    def test_repair_proposals_preserve_original_and_require_physics(self):
        from copy import deepcopy
        skills,c=self.fixture();c['candidate_id']='p:r1:g1';c['position_mm'][2]=4.;c['quaternion_xyzw']=[1.,0.,0.,0.]
        original=deepcopy(c);screen=GoalToolScreen(skills)
        result=screen.closure_repairs(c,screen.closure_preview(c))
        self.assertEqual(c,original)
        self.assertEqual(result['status'],'alternatives_found')
        self.assertFalse(result['physical_success'])
        for alternative in result['alternatives']:
            self.assertEqual(alternative['parent_candidate_id'],c['candidate_id'])
            self.assertLessEqual(alternative['retraction_mm'],2.5)
            self.assertIn('physical_closure_lift',alternative['requires'])

if __name__=='__main__':unittest.main()
