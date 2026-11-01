"""Synthetic record contracts only; not physical success evidence."""
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from benchmark_assembly import CASES, trial_metrics
from experience_memory import CHECKS


class BenchmarkMetricTests(unittest.TestCase):
    def fixture(self, success=False, recoveries=0):
        return {'status': 'success' if success else 'failed', 'run_dir': 'synthetic_contract_fixture',
                'policy_kind': 'scripted', 'api_calls': 0, 'input_tokens': 0, 'output_tokens': 0, 'api_cost': 0,
                'robot_installed_parts': int(success), 'graph_state': {
                    'status': 'success' if success else 'failed', 'recoveries': recoveries, 'policy_calls': 2,
                    'failures': [{'environment': 'simulation', 'failure_code': 'ALIGNMENT_OUT_OF_TOLERANCE'}]},
                'results': ([{'skill': 'insert_post/v1', 'status': 'success', 'checks': dict.fromkeys(CHECKS, True),
                             'maximum_final_position_error_m': .0001, 'minimum_final_depth_m': .016}] if success else [])}

    def test_expected_failure_is_never_assembly_success(self):
        metrics = trial_metrics(self.fixture(), CASES[1])
        self.assertTrue(metrics['expected_behavior_passed'])
        self.assertFalse(metrics['assembly_success'])
        self.assertFalse(metrics['recovered_to_completed_assembly'])

    def test_unverified_success_is_rejected(self):
        record = self.fixture(True, 1)
        record['results'][0]['checks']['released'] = False
        with self.assertRaises(ValueError):
            trial_metrics(record, CASES[2])

    def test_real_mode_or_api_cost_is_not_silently_scored_as_offline(self):
        record = self.fixture()
        record['api_calls'] = 1
        with self.assertRaises(ValueError):
            trial_metrics(record, CASES[1])
        record = self.fixture()
        record['graph_state']['failures'][0]['environment'] = 'real'
        with self.assertRaises(ValueError):
            trial_metrics(record, CASES[1])

    def test_cancelled_episode_does_not_satisfy_expected_failure(self):
        record = self.fixture()
        record['status'] = 'cancelled'
        self.assertFalse(trial_metrics(record, CASES[1])['expected_behavior_passed'])


if __name__ == '__main__':
    unittest.main()
