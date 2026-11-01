from pathlib import Path
import sys
import unittest
import mujoco
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from failure_report import capture_observation, json_safe
from insertion_scene import build_insertion_scene
from insertion_skills import InsertionSkills


class RecordingSnapshotTests(unittest.TestCase):
    def test_deferred_json_snapshot_is_identical_and_detached_from_live_state(self):
        model, data, _, _ = build_insertion_scene()
        expected = capture_observation(model, data)
        raw = capture_observation(model, data, serialize=False)
        self.assertEqual(json_safe(raw), expected)
        data.qpos[0] += .1
        data.ctrl[:] = 2.
        mujoco.mj_forward(model, data)
        self.assertEqual(json_safe(raw), expected)
        self.assertIsInstance(raw['qpos'], np.ndarray)

    def test_raw_workcell_observation_matches_json_and_copies_config(self):
        model, data, config, joint = build_insertion_scene()
        skills = InsertionSkills(model, data, config, joint)
        expected = skills.observe()
        raw = skills.observe(serialize=False)
        self.assertEqual(json_safe(raw), expected)
        config['table']['top_center_world_m'][0] = 99.
        joint['fixed_product_parts'].append('mutation_test')
        data.qpos[0] += .1
        mujoco.mj_forward(model, data)
        self.assertEqual(json_safe(raw), expected)


if __name__ == '__main__':
    unittest.main()
