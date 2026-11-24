from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import mujoco
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from failure_report import FailureCode, FailureEvent
from workcell_scene import build_workcell, load_config
from assembly_skills import AssemblySkills
from simulation_warnings import checked_physics


class SimulationWarningsTests(unittest.TestCase):
    def test_native_warning_stops_before_a_valid_sample_and_restores_callback(self):
        config = load_config()
        model, data = build_workcell(config)
        skills = AssemblySkills(model, data, config)
        old_callback = mujoco.get_mju_user_warning()
        real_step = mujoco.mj_step
        def step_with_diagnostic(m, d):
            real_step(m, d)
            mujoco.get_mju_user_warning()('synthetic diagnostic to test stop/report propagation')
        with patch.object(mujoco, 'mj_step', side_effect=step_with_diagnostic), patch.object(skills.motion, 'sample') as sample:
            with self.assertRaises(FailureEvent) as raised:
                skills.motion.step(allow_support=True, allow_grasp=False)
            sample.assert_not_called()
        self.assertEqual(raised.exception.code, FailureCode.SIMULATION_INVALID)
        self.assertEqual(raised.exception.violations[0]['warning_count'], 1)
        self.assertFalse(raised.exception.kinematics_fresh)
        self.assertIs(mujoco.get_mju_user_warning(), old_callback)

    def test_callback_restored_on_unrelated_error(self):
        old_callback = mujoco.get_mju_user_warning()
        with self.assertRaisesRegex(ValueError, 'test error'):
            with checked_physics('test'):
                raise ValueError('test error')
        self.assertIs(mujoco.get_mju_user_warning(), old_callback)


if __name__ == '__main__':
    unittest.main()
