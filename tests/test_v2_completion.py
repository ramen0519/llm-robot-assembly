"""Adversarial boundaries of the approved, shared completion contract."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest
import numpy as np
from scipy.spatial.transform import Rotation
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from assembly_v2.completion import load_profile,sample_checks,checks_pass,ObservationWindow,valid_evidence
from assembly_v2.transforms import errors
from assembly_v2.budget import Budget,ControlBudget

class Completion(unittest.TestCase):
    def setUp(self):
        self.profile=load_profile()
        self.operation={'kind':'generic_goal_placement','not_applicable_reason':'Object rests on a planar support; no defined insertion.'}
        self.measure={'position_error_base_xyz_m':[.004]*3,'orientation_error_rad':0.,'linear_speed_m_s':0.,
            'angular_speed_rad_s':0.,'hard_penetration_m':.0005,'gripper_clearance_m':.005,
            'released':True,'retreat_complete':True,'robot_product_noncontact':True}
    def test_componentwise_boundary_and_rotated_base(self):
        self.assertTrue(checks_pass(sample_checks(self.measure,self.operation,self.profile)))
        m={**self.measure,'position_error_base_xyz_m':[.0051,0,0]}
        self.assertFalse(checks_pass(sample_checks(m,self.operation,self.profile)))
        m['position_error_base_xyz_m']=[.005]*3
        self.assertTrue(checks_pass(sample_checks(m,self.operation,self.profile)))
        base=Rotation.from_euler('z',45,degrees=True).as_matrix()
        goal=np.eye(4);current=np.eye(4);current[:3,3]=base@np.array([.0051,0,0])
        xyz,_=errors(goal,current,base);self.assertAlmostEqual(xyz[0],.0051)
    def test_known_insertion_cannot_skip_depth_or_prealignment(self):
        op={'kind':'linear_insertion','geometry_evidence':{'surface':'verified'}}
        m={**self.measure,'insertion_depth_error_m':.004,'prealignment_perpendicular_error_m':0.,'prealignment_orientation_error_rad':0.}
        self.assertFalse(checks_pass(sample_checks(m,op,self.profile)))
        m['insertion_depth_error_m']=.0009;self.assertTrue(checks_pass(sample_checks(m,op,self.profile)))
        del m['prealignment_perpendicular_error_m'];self.assertFalse(checks_pass(sample_checks(m,op,self.profile)))
    def window(self):
        return ObservationWindow(['x'],self.profile,check_id='actual',source='live_simulation',model_sha256='model',asset_hash='mesh',operations={'x':self.operation})
    def test_full_window_and_middle_failure_not_hidden(self):
        w=self.window()
        for i in range(501):w.add(i*.002,{'x':self.measure})
        self.assertTrue(valid_evidence(w.finish(),required_parts=['x']))
        w=self.window()
        for i in range(501):
            m=deepcopy(self.measure)
            if i==250:m['robot_product_noncontact']=False
            w.add(i*.002,{'x':m})
        self.assertFalse(valid_evidence(w.finish()))
        self.assertEqual(len(w.finish()['violations']),1)
    def test_missing_window_and_holding_rejected(self):
        w=self.window()
        for i in range(500):w.add(i*.002,{'x':self.measure})
        self.assertFalse(valid_evidence(w.finish()))
        m={**self.measure,'released':False,'gripper_clearance_m':0.}
        self.assertFalse(checks_pass(sample_checks(m,self.operation,self.profile)))
    def test_budget_before_new_work_and_independent_control_limit(self):
        clock=[0.];b=Budget(1,clock=lambda:clock[0]);clock[0]=1.
        with self.assertRaises(TimeoutError):b.check('next physics step')
        c=ControlBudget(2)
        for _ in range(12):c.begin()
        with self.assertRaises(RuntimeError):c.begin()

if __name__=='__main__':unittest.main()
