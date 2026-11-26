"""A cached pose decision must never cross a contact traversal boundary."""
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from assembly_v2.verification import MeshVerifier
from failure_report import FailureEvent


class ContactCacheTests(unittest.TestCase):
    def setup_verifier(self):
        v=MeshVerifier.__new__(MeshVerifier)
        v.bodies={'a':10,'b':11};v.geom_part={1:'a',2:'b'};v.active='a';v.base='b'
        v.pads={};v.robot_geoms=set();v.gripper_geoms=set();v.table_geom=3
        v.goal_pairs={frozenset(('a','b'))};v.reference_mode=True;v.installed={'a'};v.mating=False
        v.stage='test';v.prepared={'completion_profile':{'execution_hard_contact_penetration_max_m':.001}}
        v.model=SimpleNamespace(geom=lambda g:SimpleNamespace(name='g'+str(g)))
        calls=[]
        def near(data,pid):calls.append(pid);return data.near[pid]
        v.near_goal=near
        c=SimpleNamespace(geom1=1,geom2=2,dist=0.,pos=np.zeros(3),frame=np.eye(3).ravel())
        data=SimpleNamespace(time=2.,near={'a':True,'b':True},contact=[c]*12)
        return v,data,calls

    def test_repeated_contacts_share_pose_checks_only_within_call(self):
        v,d,calls=self.setup_verifier()
        v.contacts(d,allow_support=True,allow_grasp=False,planned=True)
        self.assertEqual(calls,['a','b']);self.assertEqual(len(v.last_contacts['rows']),12)
        # A planning candidate may mutate qpos while keeping simulation time.
        d.near['b']=False
        with self.assertRaises(FailureEvent) as raised:
            v.contacts(d,allow_support=True,allow_grasp=False,planned=True)
        self.assertEqual(raised.exception.violations[0]['rejection_reason'],'part_outside_goal_tolerance')
        self.assertEqual(calls,['a','b','a','b'])

    def test_another_copy_at_same_time_gets_new_pose_checks(self):
        v,d,calls=self.setup_verifier();v.contacts(d,allow_support=True,allow_grasp=False,planned=True)
        other=SimpleNamespace(time=d.time,near={'a':False,'b':True},contact=d.contact)
        with self.assertRaises(FailureEvent):v.contacts(other,allow_support=True,allow_grasp=False,planned=True)
        self.assertEqual(calls,['a','b','a','b'])

    def test_delayed_evidence_keeps_original_frame_after_state_changes(self):
        v,d,_=self.setup_verifier()
        v.contacts(d,allow_support=True,allow_grasp=False,planned=True)
        self.assertNotIn('rows',v._last_contacts)
        d.contact[0].pos[:]=1.;d.contact[0].dist=-.002;d.near['b']=False
        before=v.last_contacts
        self.assertEqual(before['rows'][0]['position_world_m'],[0.,0.,0.])
        self.assertEqual(before['rows'][0]['signed_distance_m'],0.)
        self.assertEqual(before['rows'][0]['near_goal'],{'a':True,'b':True})
        with self.assertRaises(FailureEvent):v.contacts(d,allow_support=True,allow_grasp=False,planned=True)
        self.assertEqual(before['rows'][0]['position_world_m'],[0.,0.,0.])

    def test_hard_penetration_rejection_materializes_full_evidence(self):
        v,d,_=self.setup_verifier();d.contact[0].dist=-.0011
        with self.assertRaises(FailureEvent) as error:
            v.contacts(d,allow_support=True,allow_grasp=False,planned=True)
        rows=error.exception.violations[0]['contacts']
        self.assertEqual(len(rows),12)
        self.assertEqual(rows[-1]['signed_distance_m'],-.0011)


if __name__=='__main__':unittest.main()
