"""Regression checks against falsely accepting an empty-scene gripper trial."""

import contextlib
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import mujoco

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import move_gripper


class GripperFailureTests(unittest.TestCase):
    def test_stuck_open_and_incomplete_trials_are_rejected(self):
        model, data = move_gripper.load_robot_with_gripper()
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            # Physics still runs: suppress only the commanded close motion.
            with patch.object(move_gripper, "command_at", return_value=(0.0, "stuck_open")):
                rows = move_gripper.run_cycle(model, data, trace_path=Path(directory) / "trial.csv")
            self.assertFalse(move_gripper.assess_cycle(rows))
            with self.assertRaisesRegex(ValueError, "full 13-second trial"):
                move_gripper.assess_cycle(rows[:100])

    def test_floor_collision_is_rejected(self):
        model, data = move_gripper.load_robot_with_gripper()
        # Change only this in-memory fixture; no vendored model files are edited.
        model.geom_pos[model.geom("preview_floor").id, 2] = 0.4
        # Recompute constants and reset cached fixed-geometry transforms.
        mujoco.mj_setConst(model, data)
        mujoco.mj_resetDataKeyframe(model, data, model.key("home").id)
        mujoco.mj_forward(model, data)
        self.assertTrue(any(c.dist <= 0 for c in data.contact), "Collision fixture is invalid.")
        with self.assertRaisesRegex(RuntimeError, "Unexpected contact"):
            move_gripper.measure_state(model, data, data.qpos[:6].copy(), "injected_collision")


if __name__ == "__main__":
    unittest.main()
