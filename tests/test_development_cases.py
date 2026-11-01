from pathlib import Path
import sys
import unittest
import mujoco
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from development_cases import SCENE_CASES, build_case
from experience_memory import condition_fingerprint
from failure_report import json_safe
from workcell_scene import part_name


class DevelopmentCaseTests(unittest.TestCase):
    def test_declared_offsets_exist_at_initialization_and_do_not_move_target(self):
        _, reference, c, j = build_case()
        start = reference.body(part_name(c)).xpos.copy()
        for case in ('tray_x_plus_10mm', 'tray_y_plus_10mm'):
            _, data, config, joint = build_case(case)
            np.testing.assert_allclose(data.body(part_name(config)).xpos - start,
                                       [*SCENE_CASES[case]['tray_delta_xy_m'], 0.], atol=1e-12)
            np.testing.assert_allclose(joint['target_part_center_world_m'], j['target_part_center_world_m'])
            self.assertEqual(data.time, 0.)

    def test_changed_conditions_do_not_match_nominal_memory(self):
        fingerprints = []
        for case in SCENE_CASES:
            _, _, config, joint = build_case(case, transfer_obstacle=case.startswith('obstacle'))
            fingerprints.append(condition_fingerprint(json_safe({'config': config, 'joint': joint, 'simulator_version': mujoco.__version__,
                'environment': 'simulation', 'observation_source': 'sim_ground_truth'})))
        self.assertEqual(len(set(fingerprints)), len(SCENE_CASES))

    def test_moved_obstacle_is_real_geometry_and_missing_obstacle_is_rejected(self):
        with self.assertRaises(ValueError):
            build_case('obstacle_x_plus_10mm')
        model, data, config, joint = build_case('obstacle_x_plus_10mm', transfer_obstacle=True)
        obstacle = joint['transport_test_obstacle']
        np.testing.assert_allclose(data.body(obstacle['name']).xpos, obstacle['center_world_m'])
        self.assertTrue(model.geom(obstacle['name'] + '_geom').contype[0])


if __name__ == '__main__':
    unittest.main()
