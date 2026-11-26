from pathlib import Path
from copy import deepcopy
import sys,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from continue_offline_install import validate_resume_request


class ResumeContract(unittest.TestCase):
    def setUp(self):
        self.c={'state_spec':'mjSTATE_INTEGRATION','phase':'open_after_retreat','part_id':'5',
                'controller':{'held_pose':None,'slip_reference':None}}
        self.prior={'failure':{'phase':'open_after_retreat','failure_code':'TimeoutError'},
                    'path_checks':[{'phase':'retreat_limited_open'}]}
        self.p={'parts':{'5':{'operation':{'kind':'generic_goal_placement'}}}}
    def test_authentic_timeout_is_only_eligible_not_success(self):
        self.assertIsNone(validate_resume_request(self.c,self.prior,self.p,'released'))
    def test_physical_failure_is_not_replayed(self):
        self.prior['failure']['failure_code']='UNEXPECTED_CONTACT'
        with self.assertRaises(ValueError):validate_resume_request(self.c,self.prior,self.p,'released')
    def test_missing_retreat_or_occupied_gripper_rejected(self):
        for change in ('retreat','held'):
            c=deepcopy(self.c);prior=deepcopy(self.prior)
            if change=='retreat':prior['path_checks']=[]
            else:c['controller']['held_pose']=[[0,0,0]]
            with self.assertRaises(ValueError):validate_resume_request(c,prior,self.p,'released')
    def test_insertion_alignment_evidence_is_not_invented(self):
        self.p['parts']['5']['operation']['kind']='linear_insertion'
        with self.assertRaises(ValueError):validate_resume_request(self.c,self.prior,self.p,'released')
    def test_lift_phase_name_alone_does_not_prove_lift(self):
        c={**self.c,'phase':'pick_lift','state':'stopped.npy'}
        with self.assertRaises(ValueError):validate_resume_request(c,self.prior,self.p,'lifted')

    def transport_records(self):
        checkpoint={'state_spec':'mjSTATE_INTEGRATION','source':'live_simulation',
                    'phase':'transport_preinstall','part_id':'7',
                    'controller':{'held_pose':[[0,0,0]],'slip_reference':[[0,0,0]]}}
        prior={'failure':{'phase':'transport_preinstall','failure_code':'TimeoutError'},
               'path_checks':[{'phase':'transport_preinstall','source':'live_simulation','target_tcp':[[1]]}]}
        return checkpoint,prior
    def test_transport_timeout_eligible_but_not_success(self):
        c,prior=self.transport_records()
        self.assertIsNone(validate_resume_request(c,prior,self.p,'transporting'))
    def test_transport_physical_failure_or_wrong_stage_rejected(self):
        for code,phase in [('UNEXPECTED_CONTACT','transport_preinstall'),('TimeoutError','insert_goal')]:
            c,prior=self.transport_records();prior['failure'].update(failure_code=code,phase=phase)
            with self.assertRaises(ValueError):validate_resume_request(c,prior,self.p,'transporting')
    def test_transport_missing_original_slip_or_live_trace_rejected(self):
        for missing in ('slip_reference','held_pose','trace','source'):
            c,prior=self.transport_records()
            if missing=='trace':prior['path_checks']=[]
            elif missing=='source':c['source']='planner_candidate'
            else:c['controller'][missing]=None
            with self.assertRaises(ValueError):validate_resume_request(c,prior,self.p,'transporting')

if __name__=='__main__':unittest.main()
