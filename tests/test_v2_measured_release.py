from pathlib import Path
from types import SimpleNamespace
import sys,unittest
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from failure_report import FailureCode,FailureEvent
from assembly_v2.release import open_until_clear,release_observation


class MeasuredRelease(unittest.TestCase):
    def scene(self,frames,collision=False):
        now=[0];steps=[];holds=[]
        verifier=SimpleNamespace(base_rotation=np.eye(3),_last_contacts={'robot_contact_parts':[]})
        verifier.clearance=lambda data,pid:frames[now[0]].get('clearance',.0002)
        motion=SimpleNamespace(dt=.004,gripper_command=180.)
        def sample(**kw):
            frame=frames[now[0]];verifier._last_contacts={'robot_contact_parts':['p'] if frame.get('contact') else []}
            t=np.eye(4);t[0,3]=frame.get('error',0.)
            return {'simulation_time_s':now[0]*.004,'pad_gap_mm':frame['gap'],'supported':frame.get('supported',True),
                'part_pose':t.tolist(),'part_speed_m_s':frame.get('speed',0.),'part_angular_speed_rad_s':0.,
                'left_contact':False,'right_contact':False}
        def step(**kw):
            steps.append({**kw,'command':motion.gripper_command})
            if collision:raise FailureEvent(FailureCode.UNEXPECTED_CONTACT,'measured collision')
            now[0]=min(now[0]+1,len(frames)-1);return sample(**kw)
        def hold(seconds,**kw):holds.append((seconds,kw));return [sample(**kw)]
        motion.sample=sample;motion.step=step;motion.hold=hold
        skills=SimpleNamespace(prepared={'parts':{'p':{'target':np.eye(4).tolist()}}},active='p',data=None,
            verifier=verifier,motion=motion,source='unit_test_not_physics',path_attempts=[])
        return skills,steps,holds

    def test_stops_at_measured_separation_before_two_mm(self):
        s,steps,holds=self.scene([{'gap':10.,'contact':True},{'gap':10.2,'contact':False}])
        r=open_until_clear(s,{'held_gap_mm':10.})
        self.assertEqual(len(steps),251);self.assertEqual(r['status'],'released_supported')
        self.assertAlmostEqual(r['actual_extra_opening_mm'],.2)
        self.assertFalse(r['physical_installation_success'])
        self.assertTrue(steps[0]['allow_grasp']);self.assertTrue(all(not x['allow_grasp'] for x in steps[1:]))
        self.assertEqual(len({x['command'] for x in steps}),1)

    def test_zero_force_contact_is_not_detachment(self):
        s,_,_=self.scene([{'gap':10.,'contact':True}])
        r=release_observation(s,s.motion.sample())
        self.assertFalse(r['detached']);self.assertTrue(r['robot_contact'])

    def test_no_contact_without_support_cannot_complete(self):
        s,_,_=self.scene([{'gap':10.2,'contact':False,'supported':False}])
        with self.assertRaisesRegex(FailureEvent,'stable support'):
            open_until_clear(s,{'held_gap_mm':10.})

    def test_unseparated_at_opening_ceiling_is_failure(self):
        s,steps,_=self.scene([{'gap':12.,'contact':True}])
        with self.assertRaisesRegex(FailureEvent,'bounded opening'):
            open_until_clear(s,{'held_gap_mm':10.})
        self.assertEqual(steps,[])

    def test_physical_collision_propagates_without_retry(self):
        s,steps,_=self.scene([{'gap':10.,'contact':True}],collision=True);ctx={'held_gap_mm':10.}
        with self.assertRaisesRegex(FailureEvent,'measured collision'):open_until_clear(s,ctx)
        self.assertEqual(len(steps),1);self.assertEqual(ctx['opening_evidence']['status'],'failed')

    def test_goal_error_and_speed_are_not_hidden_by_release(self):
        for extra in ({'error':.0051},{'speed':.0011}):
            s,_,_=self.scene([{'gap':10.2,'contact':False,**extra}])
            with self.assertRaises(FailureEvent):open_until_clear(s,{'held_gap_mm':10.})

    def test_separation_must_persist_through_settling(self):
        s,_,_=self.scene([{'gap':10.,'contact':True},{'gap':10.2,'contact':False},{'gap':10.2,'contact':True}])
        with self.assertRaisesRegex(FailureEvent,'recontacted'):open_until_clear(s,{'held_gap_mm':10.})

    def test_opening_overshoot_is_not_hidden_by_stopping_the_command(self):
        s,_,_=self.scene([{'gap':10.2,'contact':False},{'gap':12.1,'contact':False}])
        with self.assertRaisesRegex(FailureEvent,'exceeded bounded opening'):open_until_clear(s,{'held_gap_mm':10.})


if __name__=='__main__':unittest.main()
