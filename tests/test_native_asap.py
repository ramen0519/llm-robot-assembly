import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from run_asap_native import EXPECTED_COMMIT, assess_candidate, prepare_normalized
from asap_native_worker import removable_candidates, validate_prerequisites


class NativeAsapTests(unittest.TestCase):
    def test_declared_dependencies_filter_removal_candidates_without_claiming_physics(self):
        requires = {'0': [], '1': ['0'], '2': ['1'], '3': ['1', '2']}
        validate_prerequisites(requires, list(requires))
        self.assertEqual(removable_candidates(['1', '2', '3'], ['0', '1', '2', '3'], requires), ['3'])
        self.assertEqual(removable_candidates(['1', '2'], ['0', '1', '2'], requires), ['2'])
        with self.assertRaises(ValueError):
            validate_prerequisites({'0': [], '1': ['2'], '2': ['1']}, ['0', '1', '2'])
        with self.assertRaises(ValueError):
            validate_prerequisites({'0': [], '1': ['missing']}, ['0', '1'])

    def test_uniform_normalization_preserves_mesh_and_pose_in_metres(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            (folder / 'asap_input').mkdir()
            for i in range(2):
                (folder / 'asap_input' / f'{i}.obj').write_text('v 0.01 -0.02 0.03\nf 1 1 1\n', encoding='utf-8')
            config = {str(i): {'final_state': [.01, .02, .03], 'initial_state': None} for i in range(2)}
            (folder / 'asap_input/config.json').write_text(json.dumps(config))
            (folder / 'binding_template.json').write_text(json.dumps({'part_map': {'0': 'base', '1': 'post_1'}}))
            (folder / 'planner_input_manifest.json').write_text('{}')
            before = (folder / 'asap_input/1.obj').read_bytes()
            output, manifest = prepare_normalized(folder, 2)
            vertices = np.array([float(x) for x in (output / '1.obj').read_text().splitlines()[0].split()[1:]])
            pose = np.array(json.loads((output / 'config.json').read_text())['1']['final_state'])
            self.assertNotIn('initial_state', json.loads((output / 'config.json').read_text())['1'])
            np.testing.assert_allclose((vertices + pose) * manifest['source_length_scale_m'], [.02, 0., .06])
            self.assertEqual(before, (folder / 'asap_input/1.obj').read_bytes())
            self.assertEqual(manifest['input_sha256']['1.obj'], hashlib.sha256((output / '1.obj').read_bytes()).hexdigest())
            with self.assertRaises(FileExistsError):
                prepare_normalized(folder, 2)

    def fixtures(self):
        manifest = {'input_sha256': {'0.obj': 'test-hash'}, 'binding': {'part_map': {'0': 'base', '1': 'post_1', '2': 'sleeve_1'}}}
        registry = {'post_1': {'requires': ['base']}, 'sleeve_1': {'requires': ['post_1']}}
        edges = []
        remaining = ['0', '1', '2']
        for part in ['2', '1']:
            after = [p for p in remaining if p != part]
            edges.append({'before': remaining, 'after': after, 'sim_info': {'part_move': part, 'feasible': True,
                'base_part': '0', 'parts_fix': [], 'pose': None, 'action': [0., 0., 1.]}})
            remaining = after
        report = {'schema': 'asap_native_dfs/v1', 'source_commit': EXPECTED_COMMIT, 'input_sha256': manifest['input_sha256'],
                  'stats': {'success': True, 'sequence': ['2', '1']}, 'remaining': ['0'], 'selected_edges': edges}
        return report, manifest, registry

    def test_complete_native_sequence_is_only_a_candidate(self):
        result = assess_candidate(*self.fixtures())
        self.assertEqual(result['status'], 'candidate')
        self.assertEqual(result['proposed_order'], ['post_1', 'sleeve_1'])
        self.assertFalse(result['robot_execution_validated'])

    def test_refuses_extra_hands_sideways_motion_reorientation_and_incomplete_evidence(self):
        for field, value in [('parts_fix', ['1']), ('action', [1., 0., 0.]), ('pose', (np.eye(4) * 2).tolist()), ('feasible', False)]:
            with self.subTest(field=field):
                report, manifest, registry = self.fixtures()
                report['selected_edges'][0]['sim_info'][field] = value
                self.assertEqual(assess_candidate(report, manifest, registry)['status'], 'rejected')
        report, manifest, registry = self.fixtures()
        report['selected_edges'].pop()
        self.assertEqual(assess_candidate(report, manifest, registry)['status'], 'rejected')

    def test_refuses_hash_change_and_unsatisfied_assembly_prerequisite(self):
        report, manifest, registry = self.fixtures()
        report['input_sha256'] = {}
        self.assertEqual(assess_candidate(report, manifest, registry)['status'], 'rejected')
        report, manifest, registry = self.fixtures()
        registry['post_1']['requires'] = ['sleeve_1']
        self.assertEqual(assess_candidate(report, manifest, registry)['status'], 'rejected')
        report, manifest, registry = self.fixtures()
        report['candidate_constraints'] = {'source': 'changed'}
        self.assertEqual(assess_candidate(report, manifest, registry)['status'], 'rejected')


if __name__ == '__main__':
    unittest.main()
