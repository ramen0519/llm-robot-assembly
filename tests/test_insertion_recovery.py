from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import mujoco
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from failure_report import FailureCode, FailureEvent
from insertion_scene import build_insertion_scene
from insertion_skills import InsertionSkills
from insertion_recovery import recover_alignment


class RecoveryTests(unittest.TestCase):
    def build(self):
        m, d, c, j = build_insertion_scene()
        return InsertionSkills(m, d, c, j)

    def test_reobserve_realign_then_insert_same_episode_without_state_overwrite(self):
        skills = self.build()
        skills.settle()
        skills.pick('post_1')
        with self.assertRaises(FailureEvent) as caught:
            skills.insert('post_1', 'socket_1', alignment_offset_m=[.006, 0.])
        time_before = float(skills.data.time)
        last_q, last_v = skills.data.qpos.copy(), skills.data.qvel.copy()
        real_step = mujoco.mj_step
        def guarded_step(m, d, *args, **kwargs):
            nonlocal last_q, last_v
            if d is skills.data:
                np.testing.assert_array_equal(d.qpos, last_q)
                np.testing.assert_array_equal(d.qvel, last_v)
            real_step(m, d, *args, **kwargs)
            if d is skills.data:
                last_q, last_v = d.qpos.copy(), d.qvel.copy()
        with patch.object(mujoco, 'mj_step', side_effect=guarded_step):
            recovery = recover_alignment(skills, caught.exception)
            self.assertFalse(recovery['assembly_complete'])
            self.assertGreater(recovery['before']['simulation_time_s'], 0.)
            self.assertGreater(recovery['elapsed_simulation_s'], 0.)
            self.assertLess(recovery['lateral_error_after_m'], .001)
            self.assertEqual(recovery['before']['simulation_time_s'], time_before)
            result = skills.insert('post_1', 'socket_1')
        self.assertTrue(all(result['checks'].values()))
        self.assertGreater(skills.data.time, time_before)

    def test_unsupported_or_unheld_failure_never_advances_physics(self):
        skills = self.build()
        for code in (FailureCode.PART_LOST, FailureCode.ALIGNMENT_OUT_OF_TOLERANCE, FailureCode.SIMULATION_INVALID):
            before = skills.data.time
            with self.assertRaises(FailureEvent):
                recover_alignment(skills, FailureEvent(code, 'negative precondition test'))
            self.assertEqual(skills.data.time, before)

    def test_lost_current_grasp_cannot_be_recovered_using_an_old_failure(self):
        skills = self.build()
        skills.settle()
        skills.pick('post_1')
        with self.assertRaises(FailureEvent) as caught:
            skills.insert('post_1', 'socket_1', alignment_offset_m=[.006, 0.])
        # The true held transform is corrupted in the negative test only; live qpos is untouched.
        p, r = skills.motion.held_pose
        skills.motion.held_pose = (p + [.010, 0., 0.], r)
        before = skills.data.time
        with self.assertRaises(FailureEvent) as rejected:
            recover_alignment(skills, caught.exception)
        self.assertEqual(rejected.exception.code, FailureCode.PART_LOST)
        self.assertEqual(skills.data.time, before)


if __name__ == '__main__':
    unittest.main()
