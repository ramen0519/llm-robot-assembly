from copy import deepcopy
from pathlib import Path
import sys,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from failure_report import FailureEvent
from assembly_v2.failure_diagnosis import completion_error,diagnose_failure


class CompletionStop(unittest.TestCase):
    def partial(self,reason='TimeoutError: deadline:physics_step'):
        return {'schema':'completion_evidence/v1.2','complete':False,'status':'unknown',
            'samples':29,'duration_s':.056,'incomplete_reason':reason,'violations':[],
            'parts':{'2':{'checks':{'position_base_axis5mm':{'status':'passed'}}}}}

    def test_deadline_is_not_placement_failure(self):
        evidence=self.partial();saved=deepcopy(evidence);exc=completion_error(evidence)
        self.assertIsInstance(exc,TimeoutError);self.assertNotIsInstance(exc,FailureEvent)
        self.assertEqual(str(exc),'deadline:physics_step')
        self.assertEqual(exc.completion_evidence,saved);self.assertEqual(evidence,saved)

    def test_cancellation_retains_termination_kind(self):
        exc=completion_error(self.partial('TimeoutError: cancelled:physics_step'))
        self.assertIsInstance(exc,TimeoutError);self.assertTrue(str(exc).startswith('cancelled:'))

    def test_historical_wrapped_timeout_is_unknown_physical_outcome(self):
        failure={'failure_code':'PLACEMENT_NOT_CONFIRMED','phase':'completion_observation',
                 'source':'mujoco_preflight_copy','completion_evidence':self.partial()}
        saved=deepcopy(failure);d=diagnose_failure(failure)
        self.assertEqual(d['failure_layer'],'resource_or_cancellation')
        self.assertEqual(d['physical_outcome_interpretation'],'unknown')
        self.assertEqual(d['detector_fact']['failure_code'],'PLACEMENT_NOT_CONFIRMED')
        self.assertFalse(d['automatic_motion_retry_authorized']);self.assertEqual(failure,saved)

    def test_measurement_error_is_not_physical_infeasibility(self):
        e=self.partial('ValueError: missing sensor field');exc=completion_error(e)
        self.assertIsInstance(exc,RuntimeError);self.assertNotIsInstance(exc,FailureEvent)
        d=diagnose_failure({'failure_code':'PLACEMENT_NOT_CONFIRMED','completion_evidence':e})
        self.assertEqual(d['failure_layer'],'observation_incomplete')
        self.assertEqual(d['physical_outcome_interpretation'],'unknown')

    def test_timeout_preserves_preceding_measured_violation(self):
        e=self.partial();e['violations']=[{'part_id':'2','check':'origin_linear_speed','time_s':1.}]
        e['status']='failed'
        exc=completion_error(e);self.assertIsInstance(exc,TimeoutError)
        d=diagnose_failure({'failure_code':'TimeoutError','completion_evidence':exc.completion_evidence})
        self.assertTrue(d['measured_completion_violation'])
        self.assertEqual(d['physical_outcome_interpretation'],'measured_violation')

    def test_completed_failed_window_remains_physical_failure(self):
        e=self.partial();e.update(complete=True,status='failed',samples=501,duration_s=1.)
        e.pop('incomplete_reason');e['parts']['2']['checks']['position_base_axis5mm']['status']='failed'
        self.assertIsInstance(completion_error(e),FailureEvent)

    def test_contact_during_settling_remains_physical_failure(self):
        e=self.partial('FailureEvent: unexpected contact');e['status']='failed'
        e['execution_failure']={'code':'UNEXPECTED_CONTACT'}
        self.assertIsInstance(completion_error(e),FailureEvent)

    def test_structured_interruption_survives_diagnostic_view(self):
        e=self.partial();e.pop('incomplete_reason')
        e['interruption']={'exception_type':'TimeoutError','message':'deadline:physics_step'}
        exc=completion_error(e);self.assertIsInstance(exc,TimeoutError)
        d=diagnose_failure({'failure_code':'TimeoutError','completion_evidence':e})
        self.assertEqual(d['completion_interruption'],e['interruption'])


if __name__=='__main__':unittest.main()
