"""Actual misalignment and depth measurements; no synthetic success observations."""
from pathlib import Path
import sys
import unittest
import mujoco
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from failure_report import FailureCode, FailureEvent
from insertion_scene import build_insertion_scene, insertion_config
from insertion_skills import InsertionSkills
from move_tcp import ArmPositionController
from workcell_scene import part_name


class InsertionFailureTests(unittest.TestCase):
    def build(self):
        model, data, config, joint = build_insertion_scene()
        return InsertionSkills(model, data, config, joint)

    def test_real_offset_motion_detected_before_lowering_with_grasp_preserved(self):
        skills = self.build()
        skills.settle()
        skills.pick('post_1')
        with self.assertRaises(FailureEvent) as caught:
            skills.insert('post_1', 'socket_1', alignment_offset_m=[.006, 0.])
        self.assertEqual(caught.exception.code, FailureCode.ALIGNMENT_OUT_OF_TOLERANCE)
        self.assertEqual(skills.motion.stage, 'insert_alignment_check')
        self.assertGreater(caught.exception.violations[0]['lateral_error_m'], .005)
        self.assertNotIn('insert_lower', [p['stage'] for p in skills.motion.plans])
        row = skills.motion.sample(allow_support=False, allow_grasp=True)
        self.assertTrue(row['left_contact'] and row['right_contact'])
        with self.assertRaises(FailureEvent) as depth:
            skills.verifier.verify_pre_release([row])
        self.assertEqual(depth.exception.code, FailureCode.INSERTION_DEPTH_NOT_REACHED)
        self.assertEqual(skills.motion.gripper_command, row['gripper_command'])

    def test_fixture_impact_has_real_force_and_triggers_limit(self):
        config = insertion_config()
        config['part']['mass_kg'] = 1.
        model, data, config, joint = build_insertion_scene(config)
        skills = InsertionSkills(model, data, config, joint)
        model, data, v = skills.model, skills.data, skills.verifier
        # Independent impact fixture: a heavier post released above the socket.
        # It is not a robot assembly trial and is never reported as success.
        # Stiff contact is a test-only material perturbation, not a relaxed limit.
        for geom in v.fixture_geoms | {v.part_geom}:
            model.geom_solref[geom] = [.004, 1.]
        data.joint(part_name(skills.config) + '_free').qpos[:3] = v.target_position + [0., 0., .006]
        mujoco.mj_forward(model, data)
        v.mating_enabled = True
        reference = data.qpos[:6].copy()
        controller = ArmPositionController(model)
        detected = None
        for _ in range(1000):
            controller.apply(data, reference)
            mujoco.mj_step(model, data)
            mujoco.mj_forward(model, data)
            try:
                v.sample(data, reference, allow_support=True, allow_grasp=False)
            except FailureEvent as exc:
                detected = exc
                break
        self.assertIsNotNone(detected)
        self.assertEqual(detected.code, FailureCode.INSERTION_CONTACT_LIMIT, str(detected))
        self.assertGreater(detected.violations[0]['fixture_contact_force_n'], 8.)


if __name__ == '__main__':
    unittest.main()
