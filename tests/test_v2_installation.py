from pathlib import Path
import sys,unittest
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from failure_report import FailureCode,FailureEvent
from joint_path_planner import check_tcp_endpoint,plan_tcp_path
from assembly_v2.artifacts import failure_summary
from assembly_v2.verification import MeshVerifier


class InstallationTests(unittest.TestCase):
    @staticmethod
    def one_candidate(*args):
        args[-1].append({'seed_index':0,'converged':True})
        yield np.zeros(6),1.

    def test_terminal_collision_keeps_exact_detector_and_contact(self):
        cause=FailureEvent(FailureCode.PLANNED_CONTACT,'palm hits case',phase='install_endpoint_check/preflight',
            violations=[{'geoms':['gripper_base','case'],'roles':['gripper','product:0'],'signed_distance_m':-.012}])
        cause.v2snapshot={'source':'planner_candidate','state':'exact.npy'};cause.candidate={'qpos':[1,2]}
        motion=SimpleNamespace(_candidate=lambda *args:(_ for _ in ()).throw(cause))
        with patch('joint_path_planner._tcp_ik_candidates',side_effect=self.one_candidate):
            with self.assertRaises(FailureEvent) as got:
                check_tcp_endpoint(motion,np.zeros(3),np.eye(3))
        self.assertEqual(got.exception.violations[0],cause.violations[0])
        self.assertIs(got.exception.v2snapshot,cause.v2snapshot)
        self.assertEqual(got.exception.phase,cause.phase)

    def test_joint_search_wrapper_preserves_failure_cause(self):
        cause=FailureEvent(FailureCode.PLANNED_CONTACT,'edge collision',violations=[{'geoms':['pad','case']}])
        cause.v2snapshot={'source':'planner_candidate'}
        with patch('joint_path_planner._tcp_ik_candidates',side_effect=self.one_candidate),patch('joint_path_planner.plan_joint_path',side_effect=cause):
            with self.assertRaises(FailureEvent) as got:plan_tcp_path(SimpleNamespace(),np.zeros(3),np.eye(3))
        self.assertIs(got.exception.v2snapshot,cause.v2snapshot)
        self.assertEqual(got.exception.violations[0]['geoms'],['pad','case'])

    def test_endpoint_uses_real_ik_without_advancing_live_state(self):
        from ur3e_with_gripper import load_robot_with_gripper
        from workcell_motion import WorkcellMotion
        from view_tcp import read_tcp_pose
        from assembly_v12.media import state
        m,d=load_robot_with_gripper();before=state(m,d).copy();calls=[]
        # Here the narrow test is endpoint IK/state purity, not collision safety.
        v=SimpleNamespace(contacts=lambda *args,**kw:calls.append(kw['planned']))
        motion=WorkcellMotion(m,d,v);p,r=read_tcp_pose(d)
        proof=check_tcp_endpoint(motion,p,r,max_time_s=5)
        self.assertFalse(proof['path_verified']);self.assertFalse(proof['physical_success'])
        self.assertEqual(calls,[True]);np.testing.assert_array_equal(before,state(m,d))

    def test_fixture_only_supports_its_own_uninstalled_part(self):
        v=MeshVerifier.__new__(MeshVerifier);v.bodies={'a':1};v.geom_part={1:'a'};v.active='a';v.base='base'
        v.fixture_owner={4:'a'};v.pads={};v.robot_geoms=set();v.gripper_geoms=set();v.table_geom=3
        v.goal_pairs=set();v.reference_mode=False;v.installed=set();v.mating=False;v.stage='supply'
        v.prepared={'completion_profile':{'execution_hard_contact_penetration_max_m':.001}}
        v.model=SimpleNamespace(geom=lambda g:SimpleNamespace(name='g'+str(g)))
        c=SimpleNamespace(geom1=1,geom2=4,dist=0.,pos=np.zeros(3),frame=np.eye(3).ravel());d=SimpleNamespace(contact=[c])
        v.contacts(d,allow_support=True,allow_grasp=False,planned=True)
        with self.assertRaises(FailureEvent):v.contacts(d,allow_support=False,allow_grasp=False,planned=True)
        v.fixture_owner[4]='other'
        with self.assertRaises(FailureEvent):v.contacts(d,allow_support=True,allow_grasp=False,planned=True)
        v.fixture_owner[4]='a';v.installed={'a'}
        with self.assertRaises(FailureEvent):v.contacts(d,allow_support=True,allow_grasp=False,planned=True)

    def test_failure_summary_does_not_turn_lift_into_installation(self):
        row={'id':'event','part_id':'3','executed':False,'preflight':{'grasp_feasible':'passed','installation_feasible_with_grasp':'failed'},
             'failure':{'failure_code':'PLANNED_CONTACT','phase':'install_endpoint_check/preflight','source':'planner_candidate',
                'violations':[{'geoms':['palm','case'],'signed_distance_m':-.012}]}}
        f=failure_summary([row])['failures'][0]
        self.assertEqual(f['grasp_feasible'],'passed');self.assertEqual(f['installation_feasible_with_grasp'],'failed')
        self.assertFalse(f['main_executed']);self.assertEqual(f['contacts'][0]['geoms'],['palm','case'])


if __name__=='__main__':unittest.main()
