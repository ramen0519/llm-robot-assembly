"""Synthetic log-contract tests only; these records are not physical evidence."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from compare_transport_runs import FRAME_CHECKS, compare_runs


class TransportComparisonTests(unittest.TestCase):
    def pair(self, root):
        config = {'environment': 'simulation', 'policy_kind': 'scripted', 'api_calls': 0,
                  'implementation_sha256': {'source.py': 'a'*64}, 'registry': {'post_1': {}, 'sleeve_1': {}},
                  'component': 'sleeve_1', 'initial_preassembled_parts': ['base', 'post_1'],
                  'robot_installations_goal': 1, 'transfer_obstacle': True}
        common = {'policy_kind': 'scripted', 'api_calls': 0, 'initial_preassembled_parts': ['base', 'post_1'],
                  'whole_frame_robot_assembly': False, 'robot_installations_goal': 1,
                  'simulated_seconds': 10., 'wall_elapsed_s': 2., 'samples': 5000}
        completed = {'part_id': 'sleeve_1', 'status': 'success', 'checks': dict.fromkeys(FRAME_CHECKS, True),
                     'maximum_final_position_error_m': .0001}
        dirs = []
        for strategy in ('direct', 'direct_then_rrt'):
            folder = root / strategy
            folder.mkdir()
            (folder / 'resolved_config.json').write_text(json.dumps({**config, 'transport_strategy': strategy}))
            success = strategy != 'direct'
            result = {**common, 'transport_strategy': strategy, 'status': 'success' if success else 'failed',
                      'robot_installed_parts': int(success), 'result': completed if success else None,
                      'transport_attempts': [{'status': 'executed', 'planning': {'planner': 'checked_joint_interpolation'}}] if success else []}
            (folder / 'result.json').write_text(json.dumps(result))
            # A successful fallback may retain the earlier preflight failure report.
            (folder / 'failure_report.json').write_text(json.dumps({'environment': 'simulation', 'failure_code': 'PLANNED_CONTACT'}))
            dirs.append(folder)
        return dirs

    def change(self, folder, name, change):
        p = folder / name
        value = json.loads(p.read_text())
        change(value)
        p.write_text(json.dumps(value))

    def test_matching_pair_preserves_success_failure_and_planner_identity(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            direct, fallback = self.pair(root)
            summary = compare_runs(direct, fallback, output_root=root/'summary')
            a, b = summary['rows']
            self.assertFalse(a['assembly_success'])
            self.assertTrue(b['assembly_success'])
            self.assertEqual((a['robot_installed_parts'], b['robot_installed_parts']), (0, 1))
            self.assertIsNone(b['terminal_failure_code'])
            self.assertEqual(b['executed_recorded_planner_counts'], {'checked_joint_interpolation': 1})
            self.assertFalse(summary['llm_comparison'])
            self.assertFalse(summary['timing_valid_for_comparison'])
            self.assertEqual(len(b['input_sha256']['result.json']), 64)

    def test_changed_scene_or_source_cannot_be_compared(self):
        for update in (lambda x: x.update(transfer_obstacle=False),
                       lambda x: x.update(implementation_sha256={'source.py': 'b'*64}),
                       lambda x: x['registry']['post_1'].update(position=[1, 0, 0])):
            with self.subTest(update=update), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                direct, fallback = self.pair(root)
                self.change(fallback, 'resolved_config.json', update)
                with self.assertRaises(ValueError):
                    compare_runs(direct, fallback, output_root=root/'summary')
                self.assertFalse((root/'summary').exists())

    def test_unverified_success_fixture_inflation_and_real_api_records_rejected(self):
        changes = [lambda x: x['result']['checks'].update(released=False),
                   lambda x: x.update(robot_installed_parts=3),
                   lambda x: x.update(initial_preassembled_parts=[]),
                   lambda x: x.update(whole_frame_robot_assembly=True),
                   lambda x: x.update(api_calls=1), lambda x: x.update(samples=0),
                   lambda x: x.update(status='running')]
        for change in changes:
            with self.subTest(change=change), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                direct, fallback = self.pair(root)
                self.change(fallback, 'result.json', change)
                with self.assertRaises(ValueError):
                    compare_runs(direct, fallback, output_root=root/'summary')

    def test_partial_frame_installation_is_not_whole_success(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            direct, fallback = self.pair(root)
            for folder in (direct, fallback):
                def config(value):
                    value.pop('component')
                    value.pop('initial_preassembled_parts')
                    value.update(product_parts_present=3, robot_installations_goal=2)
                self.change(folder, 'resolved_config.json', config)
                def result(value):
                    item = value.pop('result')
                    value.pop('whole_frame_robot_assembly')
                    value.pop('initial_preassembled_parts')
                    value.update(status='failed', product_parts_present=3, robot_installations_goal=2,
                                 ten_part_assembly_complete=False, completed_installations=['sleeve_1'] if item else [],
                                 results=[item] if item else [])
                self.change(folder, 'result.json', result)
            summary = compare_runs(direct, fallback, output_root=root/'summary')
            self.assertTrue(all(not row['assembly_success'] for row in summary['rows']))
            self.assertEqual(summary['rows'][1]['robot_installed_parts'], 1)
            self.assertEqual(summary['rows'][1]['robot_installations_goal'], 2)


if __name__ == '__main__':
    unittest.main()
