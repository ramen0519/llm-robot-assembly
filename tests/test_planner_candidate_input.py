from pathlib import Path
import hashlib
import json
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from export_planner_input import export_bundle
from failure_report import write_json
from frame_assembly_scene import build_frame_scene
from planner_candidate_input import load_robot_candidate
from run_asap_native import EXPECTED_COMMIT, assess_candidate, prepare_normalized
from run_frame_assembly import execute_trial


class CandidateInputTests(unittest.TestCase):
    def fixture(self, root):
        # Synthetic contract evidence only; never counted as physical validation.
        folder = export_bundle(root)
        _, manifest = prepare_normalized(folder, 2)
        _, _, registry = build_frame_scene(2)
        report = {'schema': 'asap_native_dfs/v1', 'source_commit': EXPECTED_COMMIT,
            'input_sha256': manifest['input_sha256'], 'remaining': ['0'],
            'stats': {'success': True, 'sequence': ['1'], 'stop_msg': 'solution found'},
            'selected_edges': [{'before': ['0', '1'], 'after': ['0'], 'sim_info': {
                'feasible': True, 'part_move': '1', 'base_part': '0', 'parts_fix': [], 'pose': None, 'action': [0., 0., 1.]}}]}
        write_json(folder / 'native_report.json', report)
        candidate = assess_candidate(report, manifest, registry)
        candidate['source_report_sha256'] = hashlib.sha256((folder / 'native_report.json').read_bytes()).hexdigest()
        write_json(folder / 'assembly_candidate.json', candidate)
        return folder, registry

    def test_valid_contract_only_selects_order_and_does_not_claim_robot_success(self):
        with tempfile.TemporaryDirectory() as temp:
            folder, registry = self.fixture(temp)
            order, provenance = load_robot_candidate(folder / 'assembly_candidate.json', registry)
            self.assertEqual(order, ['post_1'])
            self.assertTrue(provenance['candidate_is_not_robot_success'])

    def test_stale_mesh_or_changed_target_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            folder, registry = self.fixture(temp)
            registry['post_1']['joint']['target_part_center_world_m'][0] += .01
            with self.assertRaisesRegex(ValueError, 'frames'):
                load_robot_candidate(folder / 'assembly_candidate.json', registry)
            _, _, registry = build_frame_scene(2)
            with (folder / 'asap_normalized/1.obj').open('a') as stream:
                stream.write('\n# changed')
            with self.assertRaisesRegex(ValueError, 'changed'):
                load_robot_candidate(folder / 'assembly_candidate.json', registry)

    def test_scope_mismatch_stops_before_any_motor_samples(self):
        with tempfile.TemporaryDirectory() as temp:
            folder, _ = self.fixture(Path(temp) / 'input')
            result = execute_trial(product_count=3, mode='single', planner_candidate=folder / 'assembly_candidate.json',
                                   output_root=Path(temp) / 'runs')
            self.assertEqual(result['status'], 'failed')
            self.assertEqual(result['samples'], 0)
            self.assertEqual(result['robot_installed_parts'], 0)
            self.assertEqual(result['api_calls'], 0)


if __name__ == '__main__':
    unittest.main()
