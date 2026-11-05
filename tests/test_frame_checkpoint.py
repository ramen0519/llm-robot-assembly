"""Physical state round-trip and rejection of damaged/incompatible checkpoints."""
import copy
import json
from pathlib import Path
import sys
import unittest

import mujoco
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from run_frame_assembly import episode_boundary, restore_episode_data


class FrameCheckpointTests(unittest.TestCase):
    def setUp(self):
        self.model = mujoco.MjModel.from_xml_string('''<mujoco><worldbody>
          <geom type="plane" size="1 1 .1"/>
          <body pos="0 0 .15"><joint name="slide" type="slide" axis="0 0 1"/>
            <geom type="sphere" size=".05" mass=".2"/></body>
          </worldbody><actuator><motor joint="slide"/></actuator></mujoco>''')
        self.data = mujoco.MjData(self.model)
        self.data.ctrl[0] = .2
        for _ in range(120):
            mujoco.mj_step(self.model, self.data)
        mujoco.mj_forward(self.model, self.data)
        self.saved = json.loads(json.dumps(episode_boundary(self.model, self.data, {}, set())))

    def test_round_trip_preserves_state_and_next_physics_step(self):
        restored = restore_episode_data(self.model, self.saved)
        self.assertEqual(episode_boundary(self.model, restored, {}, set()), self.saved)
        mujoco.mj_step(self.model, self.data)
        mujoco.mj_step(self.model, restored)
        np.testing.assert_array_equal(restored.qpos, self.data.qpos)
        np.testing.assert_array_equal(restored.qvel, self.data.qvel)

    def test_rejects_corrupted_state_and_different_physics(self):
        for key, value in (('integration_state', [0.]), ('integration_sha256', 'wrong'),
                           ('state_spec', -1), ('simulator_version', 'other')):
            damaged = copy.deepcopy(self.saved)
            damaged[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                restore_episode_data(self.model, damaged)
        self.model.opt.gravity[2] = -5
        with self.assertRaises(ValueError):
            restore_episode_data(self.model, self.saved)


if __name__ == '__main__':
    unittest.main()
