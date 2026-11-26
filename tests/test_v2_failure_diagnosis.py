from pathlib import Path
import sys, unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from assembly_v2.failure_diagnosis import diagnose_failure
from assembly_v2.packets import tool_result_packet


class FailureDiagnosis(unittest.TestCase):
    def test_timeout_after_release_does_not_claim_grasp_failed(self):
        d=diagnose_failure({'failure_code':'TimeoutError','phase':'open_after_retreat','source':'live_simulation'})
        self.assertEqual(d['failure_layer'],'resource_or_cancellation')
        self.assertEqual(d['physical_outcome_interpretation'],'unknown')
        self.assertFalse(d['automatic_motion_retry_authorized'])

    def test_ik_exhaustion_is_not_global_impossibility(self):
        d=diagnose_failure({'failure_code':'IK_NOT_CONVERGED','phase':'pick_lift','source':'planner_candidate'})
        self.assertEqual(d['physical_outcome_interpretation'],'bounded_plan_not_found')
        self.assertFalse(d['global_impossibility_proven'])

    def test_contact_facts_survive_model_packet(self):
        original={'failure_code':'UNEXPECTED_CONTACT','phase':'grasp_close','source':'live_simulation',
                  'violations':[{'pair':['right_backing','part4_collision_92'],'depth_m':.00000645}],
                  'failure_snapshot':{'state':'failures/a.npy','source':'live_simulation'}}
        annotated={**original,'diagnosis':diagnose_failure(original)}
        packet=tool_result_packet({'status':'failed','failure':annotated},'event-1')
        self.assertEqual(packet['failure']['violations'],original['violations'])
        self.assertEqual(packet['failure']['diagnosis']['failure_layer'],'execution_contact')
        self.assertFalse(packet['failure']['diagnosis']['root_cause_confirmed'])
        annotated['diagnosis']['evidence']['snapshot']['state']='changed'
        self.assertEqual(original['failure_snapshot']['state'],'failures/a.npy')

    def test_internal_exception_is_not_a_physical_infeasibility(self):
        d=diagnose_failure({'failure_code':'KeyError','phase':'insert'})
        self.assertEqual(d['physical_outcome_interpretation'],'unknown')
        self.assertEqual(d['failure_layer'],'unclassified')

if __name__=='__main__':unittest.main()
