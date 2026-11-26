from pathlib import Path
import sys,unittest,math
from types import SimpleNamespace
from copy import deepcopy
import numpy as np
from scipy.spatial.transform import Rotation
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from assembly_v2.verification import MeshVerifier
from assembly_v2.scene import body_name
from continue_offline_install import validate_resume_request

class SameFrameGoals(unittest.TestCase):
    def test_batch_matches_scalar_at_limits_and_recomputes_changed_state(self):
        v=MeshVerifier.__new__(MeshVerifier);v.base='0';v.active='1';v.base_rotation=np.eye(3);v.mating=False
        v.prepared={'parts':{pid:{'target':np.eye(4).tolist(),'operation':{'kind':'generic_goal_placement'}} for pid in ('0','1','2')}}
        bodies={body_name(pid,'0'):SimpleNamespace(xpos=np.zeros(3),xmat=np.eye(3).ravel()) for pid in ('0','1','2')}
        d=SimpleNamespace(body=lambda name:bodies[name])
        for mating in (False,True):
            v.mating=mating
            for dx in (0.,.003999999,.004000001,.005,.00500001,.01499999,.01500001):
                for angle in (0,math.radians(3)-1e-9,math.radians(3)+1e-9):
                    bodies[body_name('1','0')].xpos[:]=[dx,0,0]
                    bodies[body_name('2','0')].xmat[:]=Rotation.from_rotvec([angle,0,0]).as_matrix().ravel()
                    for kind in ('generic_goal_placement','linear_insertion'):
                        v.prepared['parts']['1']['operation']={'kind':kind,'axis_world':[0,0,1]}
                        scalar={pid:bool(v.near_goal(d,pid)) for pid in ('0','1','2')}
                        self.assertEqual(scalar,v.near_goal_many(d,['0','1','2']))

class RetreatContinuation(unittest.TestCase):
    def records(self):
        c={'state_spec':'mjSTATE_INTEGRATION','source':'live_simulation','phase':'retreat_limited_open',
           'part_id':'7','controller':{'held_pose':None,'slip_reference':None}}
        prior={'failure':{'phase':'retreat_limited_open','failure_code':'TimeoutError'},
               'path_checks':[{'phase':'confined_release'},{'phase':'retreat_limited_open','source':'live_simulation','target_tcp':np.eye(4).tolist()}]}
        p={'parts':{'7':{'operation':{'kind':'generic_goal_placement'}}}}
        return c,prior,p
    def test_requires_release_empty_tool_and_checked_target(self):
        c,prior,p=self.records();self.assertIsNone(validate_resume_request(c,prior,p,'retreating'))
        for field in ('held_pose','slip_reference'):
            changed=deepcopy(c);changed['controller'][field]=[[0,0,0]]
            with self.assertRaises(ValueError):validate_resume_request(changed,prior,p,'retreating')
        for paths in ([],prior['path_checks'][:1],prior['path_checks'][1:]):
            with self.assertRaises(ValueError):validate_resume_request(c,{**prior,'path_checks':paths},p,'retreating')
    def test_contact_failure_and_missing_insertion_evidence_rejected(self):
        c,prior,p=self.records();prior['failure']['failure_code']='UNEXPECTED_CONTACT'
        with self.assertRaises(ValueError):validate_resume_request(c,prior,p,'retreating')
        prior['failure']['failure_code']='TimeoutError';p['parts']['7']['operation']['kind']='linear_insertion'
        with self.assertRaises(ValueError):validate_resume_request(c,prior,p,'retreating')

if __name__=='__main__':unittest.main()
