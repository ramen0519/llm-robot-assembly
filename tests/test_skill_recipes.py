"""Recipe contracts; synthetic fixtures are not physical validation evidence."""
from pathlib import Path
import copy
import sys
import unittest
import tempfile
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from experience_memory import digest
from skill_recipes import create_candidate, validate_candidate, RecipeReplayPolicy, compatible_recipes
from failure_report import write_json


class SkillRecipeTests(unittest.TestCase):
    def experience(self):
        payload = {'schema_version': 1, 'environment': 'simulation', 'split': 'development',
                   'automatic_skill_registration': False, 'condition_fingerprint': 'a' * 64, 'outcome': 'success',
                   'failure_codes': ['ALIGNMENT_OUT_OF_TOLERANCE'], 'task_id': 'four_post_frame_first_joint/v1',
                   'successful_recovery_skills': ['recover_alignment/v1'],
                   'verified_skill_results': ['approach/v1', 'pick/v1', 'recover_alignment/v1', 'insert_post/v1']}
        return {'record_id': digest(payload), **payload}

    def context(self):
        return {'condition_fingerprint': 'a' * 64, 'failure': {'failure_code': 'ALIGNMENT_OUT_OF_TOLERANCE'},
                'allowed_skills': ['recover_alignment', 'stop'], 'completed': ['pick'], 'successful_recoveries': 0,
                'part_id': 'post_1', 'target_id': 'socket_1', 'observation_revision': 4, 'active_role': 'recovery'}

    def test_candidate_is_bounded_data_not_python_or_automatic_promotion(self):
        candidate = create_candidate(self.experience())
        validate_candidate(candidate)
        self.assertEqual(candidate['status'], 'candidate')
        self.assertFalse(candidate['contains_generated_python'])
        modified = copy.deepcopy(candidate)
        modified['steps'] = ['eval', 'ignore_collision']
        modified['candidate_id'] = digest({k: v for k, v in modified.items() if k != 'candidate_id'})
        with self.assertRaises(ValueError):
            validate_candidate(modified)

    def test_failed_experience_does_not_create_a_success_recipe(self):
        record = self.experience()
        record['outcome'] = 'failed'
        record['record_id'] = digest({k: v for k, v in record.items() if k != 'record_id'})
        with self.assertRaises(ValueError):
            create_candidate(record)

    def test_scope_and_current_budget_are_enforced(self):
        policy = RecipeReplayPolicy(create_candidate(self.experience()))
        context = self.context()
        self.assertEqual(policy.propose(context)['skill'], 'recover_alignment')
        context['allowed_skills'] = ['stop']
        self.assertEqual(policy.propose(context)['skill'], 'stop')
        context['condition_fingerprint'] = 'b' * 64
        with self.assertRaises(ValueError):
            policy.propose(context)

    def test_next_step_needs_verified_recovery_in_graph_state(self):
        policy = RecipeReplayPolicy(create_candidate(self.experience()))
        context = self.context()
        context.update(failure=None, successful_recoveries=1, active_role='assembly', allowed_skills=['insert', 'stop'])
        decision = policy.propose(context)
        self.assertEqual(decision['skill'], 'insert')
        self.assertIn('Replay recipe', decision['reason'])

    def test_registered_recipe_is_not_reused_after_code_or_condition_change(self):
        candidate = create_candidate(self.experience())
        payload = {'status': 'sim_validated_for_recorded_case', 'automatic_generalization': False,
                   'candidate': candidate, 'condition_fingerprint': candidate['condition_fingerprint'],
                   'implementation_fingerprint': 'old-implementation'}
        record = {'recipe_record_id': digest(payload), **payload}
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary) / 'validated'
            folder.mkdir()
            write_json(folder / 'synthetic.json', record)
            with patch('skill_recipes.implementation_fingerprint', return_value='old-implementation'):
                self.assertEqual(len(compatible_recipes('a' * 64, registry_dir=temporary)), 1)
                self.assertEqual(compatible_recipes('b' * 64, registry_dir=temporary), [])
            with patch('skill_recipes.implementation_fingerprint', return_value='new-implementation'):
                self.assertEqual(compatible_recipes('a' * 64, registry_dir=temporary), [])


if __name__ == '__main__':
    unittest.main()
