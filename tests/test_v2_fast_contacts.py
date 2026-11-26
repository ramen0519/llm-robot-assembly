"""Native MuJoCo checks: vectorization must preserve forces and first failures."""
from pathlib import Path
import sys,unittest
from types import MethodType
from copy import deepcopy
import numpy as np,mujoco
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from assembly_v2.verification import MeshVerifier
from assembly_v12.contracts import digest
from failure_report import FailureEvent


class FastContactsTests(unittest.TestCase):
    def scene(self):
        model=mujoco.MjModel.from_xml_string('''<mujoco><option timestep="0.002" cone="elliptic"/>
        <worldbody><geom name="table" type="plane" size="1 1 .1"/>
        <body name="a" pos="0 0 .01"><freejoint/><geom name="a_geom" type="box" size=".02 .02 .01" mass=".1"/></body>
        <body name="b" pos="0 0 .03"><freejoint/><geom name="b_geom" type="box" size=".015 .015 .01" mass=".1"/></body>
        </worldbody></mujoco>''')
        data=mujoco.MjData(model)
        for _ in range(20):mujoco.mj_step(model,data)
        mujoco.mj_forward(model,data)
        v=MeshVerifier.__new__(MeshVerifier)
        v.model=model;v.bodies={'a':1,'b':2};v.geom_part={1:'a',2:'b'}
        v.active='b';v.base='a';v.fixture_owner={};v.robot_geoms=set();v.gripper_geoms=set();v.pads={}
        v.table_geom=0;v.goal_pairs={frozenset(('a','b'))};v.reference_mode=True;v.mating=False
        v.installed={'a','b'};v.stage='native_test';v.max_hard_history=0.;v.hard_violation=None
        v.prepared={'completion_profile':{'execution_hard_contact_penetration_max_m':.001}}
        v.near_goal=MethodType(lambda self,data,pid:True,v)
        return v,data

    def test_native_force_sums_and_lazy_records_match_scalar_over_frames(self):
        v,d=self.scene()
        for _ in range(12):
            scalar=v._scalar_contacts(d,allow_support=True,allow_grasp=True);old=deepcopy(v.last_contacts)
            vector=v.contacts(d,allow_support=True,allow_grasp=True);new=deepcopy(v.last_contacts)
            self.assertEqual(digest([scalar,old]),digest([vector,new]))
            mujoco.mj_step(v.model,d);mujoco.mj_forward(v.model,d)

    def test_same_time_changed_pose_predicate_rejects_same_first_contact(self):
        v,d=self.scene();v.contacts(d,allow_support=True,allow_grasp=False)
        v.near_goal=MethodType(lambda self,data,pid:pid=='a',v)
        evidence=[]
        for method in (v._scalar_contacts,v.contacts):
            with self.assertRaises(FailureEvent) as error:method(d,allow_support=True,allow_grasp=False,planned=True)
            evidence.append((error.exception.code.value,error.exception.violations))
        self.assertEqual(digest(evidence[0]),digest(evidence[1]))

    def test_lazy_contact_evidence_owns_original_contact_arrays(self):
        v,d=self.scene();v._scalar_contacts(d,allow_support=True,allow_grasp=False)
        expected=deepcopy(v.last_contacts)
        v.contacts(d,allow_support=True,allow_grasp=False)
        d.contact.pos[:]=10.;d.contact.dist[:]=-.1
        self.assertEqual(digest(expected),digest(v.last_contacts))

if __name__=='__main__':unittest.main()
