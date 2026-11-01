"""Contract-only fixtures: these synthetic records are not physical assembly evidence."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from experience_memory import (CHECKS, condition_fingerprint, experience_from_run, read_json,
                               retrieve_experiences, save_experience)


class ExperienceTests(unittest.TestCase):
    def fixture(self, root, name='run', success=True):
        run = Path(root) / name
        run.mkdir()
        resolved = {'environment': 'simulation', 'observation_source': 'sim_ground_truth',
                    'simulator_version': 'contract-test-only', 'config': {'test_fixture': True},
                    'joint': {'product_candidate': 'four_post_frame_v1'}, 'policy_kind': 'scripted'}
        result = {'status': 'success' if success else 'failed', 'robot_installed_parts': int(success),
                  'results': ([{'status': 'success', 'skill': 'recover_alignment/v1'},
                               {'status': 'success', 'skill': 'insert_post/v1', 'checks': dict.fromkeys(CHECKS, True),
                                'maximum_final_position_error_m': .0002, 'minimum_final_depth_m': .016}] if success else []),
                  'failures': [{'environment': 'simulation', 'failure_code': 'ALIGNMENT_OUT_OF_TOLERANCE'}]}
        for name, value in [('resolved_config.json', resolved), ('result.json', result)]:
            (run / name).write_text(json.dumps(value), encoding='utf-8')
        return run, resolved, result

    def test_roundtrip_dedup_and_successful_recovery_first_with_negative_example(self):
        with tempfile.TemporaryDirectory() as root:
            memory = Path(root) / 'memory'
            fail, _, _ = self.fixture(root, 'failure', False)
            run, resolved, _ = self.fixture(root, 'recovered', True)
            save_experience(fail, memory)
            first, created = save_experience(run, memory)
            again, second_created = save_experience(run, memory)
            self.assertTrue(created)
            self.assertFalse(second_created)
            self.assertEqual(first, again)
            found = retrieve_experiences(condition_fingerprint(resolved), failure_code='ALIGNMENT_OUT_OF_TOLERANCE', memory_dir=memory)
            self.assertEqual([r['outcome'] for r in found], ['success', 'failed'])
            self.assertFalse(found[0]['automatic_skill_registration'])
            self.assertEqual(found[0]['successful_recovery_skills'], ['recover_alignment/v1'])

    def test_changed_conditions_or_failure_code_do_not_match(self):
        with tempfile.TemporaryDirectory() as root:
            run, resolved, _ = self.fixture(root)
            memory = Path(root) / 'memory'
            save_experience(run, memory)
            altered = copy.deepcopy(resolved)
            altered['config']['friction'] = .2
            self.assertEqual(retrieve_experiences(condition_fingerprint(altered), memory_dir=memory), [])
            self.assertEqual(retrieve_experiences(condition_fingerprint(resolved), failure_code='PART_LOST', memory_dir=memory), [])

    def test_false_success_and_real_evidence_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            run, resolved, result = self.fixture(root)
            result['results'][-1]['checks']['socket_floor_support'] = False
            (run / 'result.json').write_text(json.dumps(result))
            with self.assertRaises(ValueError):
                experience_from_run(run)
            resolved['environment'] = 'real'
            with self.assertRaises(ValueError):
                condition_fingerprint(resolved)

    def test_modified_record_is_not_used_as_advice(self):
        with tempfile.TemporaryDirectory() as root:
            run, resolved, _ = self.fixture(root)
            memory = Path(root) / 'memory'
            record, _ = save_experience(run, memory)
            record['successful_recovery_skills'] = ['arbitrary_code']
            (memory / (record['record_id'] + '.json')).write_text(json.dumps(record))
            with self.assertRaises(ValueError):
                retrieve_experiences(condition_fingerprint(resolved), memory_dir=memory)

    def test_duplicate_keys_and_nonfinite_json_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'bad.json'
            for text in ('{"a":1,"a":2}', '{"x":NaN}'):
                path.write_text(text)
                with self.assertRaises(ValueError):
                    read_json(path)


if __name__ == '__main__':
    unittest.main()
