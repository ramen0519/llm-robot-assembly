from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import mujoco
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from workcell_scene import build_workcell, load_config
from move_tcp import ArmPositionController


class GravityBiasTests(unittest.TestCase):
    def test_bias_matches_full_forward_and_does_not_solve_contacts(self):
        model, live = build_workcell(load_config())
        controller = ArmPositionController(model)
        reference = mujoco.MjData(model)
        for offset in (0., .1, -.1):
            live.qpos[0] = offset
            live.qvel[:] = .02
            before_q, before_v = live.qpos.copy(), live.qvel.copy()
            reference.qpos[:] = live.qpos
            reference.qvel[:] = 0.
            mujoco.mj_forward(model, reference)
            with patch.object(mujoco, 'mj_forward', side_effect=AssertionError('Gravity-only query must not solve contacts')):
                torque = controller.gravity_torque(live)
            np.testing.assert_allclose(torque, reference.qfrc_bias[:6], atol=1e-12, rtol=1e-12)
            np.testing.assert_array_equal(live.qpos, before_q)
            np.testing.assert_array_equal(live.qvel, before_v)


if __name__ == '__main__':
    unittest.main()
