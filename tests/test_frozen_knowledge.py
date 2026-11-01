from copy import deepcopy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from experience_memory import digest
from frozen_knowledge import FrozenKnowledge, validate_bundle
from failure_report import write_json
from run_assembly_graph import execute_trial
from skill_recipes import implementation_fingerprint


def bundle_fixture():
    record = {'schema_version': 1, 'environment': 'simulation', 'split': 'development',
        'automatic_skill_registration': False, 'condition_fingerprint': 'a' * 64, 'outcome': 'success',
        'failure_codes': ['ALIGNMENT_OUT_OF_TOLERANCE'], 'verified_skill_results': ['recover_alignment/v1', 'insert_post/v1'],
        'successful_recovery_skills': ['recover_alignment/v1']}
    record = {'record_id': digest(record), **record}
    payload = {'schema': 'frozen_development_knowledge/v1', 'source_split': 'development',
        'implementation_fingerprint': implementation_fingerprint(), 'condition_fingerprint': 'a' * 64,
        'update_during_episode': False, 'experiences': [record], 'recipes': []}
    return {'bundle_id': digest(payload), **payload}


class FrozenKnowledgeTests(unittest.TestCase):
    def test_loaded_copy_is_frozen_and_returned_summaries_cannot_mutate_it(self):
        bundle = bundle_fixture()
        knowledge = FrozenKnowledge(bundle, 'a' * 64, 'memory')
        bundle['experiences'].clear()
        result = knowledge.lookup(None)
        result[0]['failure_codes'].clear()
        self.assertEqual(len(knowledge.lookup({'failure_code': 'ALIGNMENT_OUT_OF_TOLERANCE'})), 1)
        self.assertEqual(knowledge.lookup({'failure_code': 'PLANNED_CONTACT'}), [])
        self.assertEqual(knowledge.recipe_summaries(), [])

    def test_rejects_changed_code_conditions_records_and_evaluation_split(self):
        for change in ('implementation_fingerprint', 'condition_fingerprint', 'source_split'):
            bundle = bundle_fixture()
            bundle[change] = 'evaluation' if change == 'source_split' else 'b' * 64
            bundle['bundle_id'] = digest({k: v for k, v in bundle.items() if k != 'bundle_id'})
            with self.assertRaises(ValueError):
                FrozenKnowledge(bundle, 'a' * 64, 'memory')
        bundle = bundle_fixture()
        bundle['experiences'][0]['outcome'] = 'failed'
        bundle['bundle_id'] = digest({k: v for k, v in bundle.items() if k != 'bundle_id'})
        with self.assertRaises(ValueError):
            FrozenKnowledge(bundle, 'a' * 64, 'memory')

    def test_recipe_mode_requires_current_physical_validation(self):
        with self.assertRaises(ValueError):
            FrozenKnowledge(bundle_fixture(), 'a' * 64, 'recipes')

    def test_mismatched_bundle_stops_runner_before_motor_execution(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'bundle.json'
            write_json(path, bundle_fixture())
            with patch('insertion_skills.InsertionSkills.settle', side_effect=AssertionError('Motor should not run')) as settle:
                result = execute_trial(mode='single', knowledge_bundle=path, knowledge_mode='memory', output_root=Path(temp) / 'runs')
            self.assertEqual(result['status'], 'failed')
            settle.assert_not_called()
            self.assertEqual(result['api_calls'], 0)
            self.assertEqual(result['robot_installed_parts'], 0)


if __name__ == '__main__':
    unittest.main()
