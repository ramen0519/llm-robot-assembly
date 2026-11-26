from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import sys,unittest
import mujoco,numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from joint_path_planner import plan_joint_path,RESOLUTION_RAD
from assembly_v2.budget import Budget,DevelopmentBudget
from assembly_v2.continuation import finite_plan_execution_budget,MotionSlice
from failure_report import FailureEvent,FailureCode


class DirectPlanReuse(unittest.TestCase):
    def fixture(self):
        bodies=''.join(f'<body pos="{i*.1} 0 .5"><joint type="hinge" range="-360 360"/><geom type="sphere" size=".01"/></body>' for i in range(6))
        m=mujoco.MjModel.from_xml_string('<mujoco><worldbody>'+bodies+'</worldbody></mujoco>')
        d=mujoco.MjData(m);mujoco.mj_forward(m,d)
        return SimpleNamespace(model=m,data=d,budget=Budget(10)),[]

    def test_every_direct_knot_was_checked_without_a_second_identical_pass(self):
        motion,seen=self.fixture();motion._candidate=lambda q,*a:seen.append(q.copy())
        goal=np.zeros(6);goal[-1]=6.
        with patch('joint_path_planner.read_tcp_pose',return_value=(np.zeros(3),np.eye(3))):
            plan,meta=plan_joint_path(motion,goal)
        self.assertEqual(meta['planner'],'checked_joint_interpolation')
        self.assertEqual(meta['collision_queries'],len(plan.arm_knots)+2)
        self.assertTrue(all(np.array_equal(k,q[:6]) for k,q in zip(plan.arm_knots,seen[2:])))
        self.assertLessEqual(np.max(np.abs(np.diff(plan.arm_knots,axis=0))),RESOLUTION_RAD+1e-12)
        np.testing.assert_array_equal(motion.data.qpos,np.zeros(6))

    def test_search_returned_edge_is_still_checked_for_an_interior_obstacle(self):
        motion,seen=self.fixture()
        def check(q,*a):
            if .04<q[0]<.06:raise FailureEvent(FailureCode.PLANNED_CONTACT,'test interior obstacle')
        motion._candidate=check;goal=np.zeros(6);goal[0]=.1
        with patch('joint_path_planner.rrt_connect',return_value=[np.zeros(6),goal]):
            with self.assertRaises(FailureEvent):plan_joint_path(motion,goal)

    def test_unlimited_development_motion_is_finite_and_cancellable(self):
        cancelled=[False];parent=DevelopmentBudget(cancelled=lambda:cancelled[0])
        actual=finite_plan_execution_budget(parent,60,70,.002)
        self.assertIs(actual,parent);cancelled[0]=True
        with self.assertRaisesRegex(TimeoutError,'cancelled'):actual.check('physics_step')
        self.assertIsInstance(finite_plan_execution_budget(Budget(100),60,70,.002),MotionSlice)
        with self.assertRaises(ValueError):finite_plan_execution_budget(parent,60,float('inf'),.002)


if __name__=='__main__':unittest.main()
