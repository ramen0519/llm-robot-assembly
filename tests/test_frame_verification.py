"""Assembled-pose fixtures validate geometry/support; do not count as robot execution."""
from pathlib import Path
import sys
import unittest
import mujoco
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from frame_assembly_scene import build_frame_scene
from frame_assembly_skills import FrameSkills
from move_tcp import ArmPositionController
from failure_report import FailureEvent
from workcell_scene import part_name


class FrameVerificationTests(unittest.TestCase):
    def test_completed_geometry_supports_every_component_without_welds(self):
        model, data, registry = build_frame_scene(10)
        for entry in registry.values():
            data.joint(part_name(entry['config']) + '_free').qpos[:3] = entry['joint']['target_part_center_world_m']
        mujoco.mj_forward(model, data)
        skills = FrameSkills(model, data, 'cover', registry, set(registry) - {'cover'})
        skills.verifier.mating_enabled = True
        controller, reference = ArmPositionController(model), data.qpos[:6].copy()
        rows = []
        for i in range(1500):
            controller.apply(data, reference)
            mujoco.mj_step(model, data)
            mujoco.mj_forward(model, data)
            row = skills.verifier.sample(data, reference, allow_support=True, allow_grasp=False)
            if i > 1250:
                rows.append(row)
        try:
            checks = skills.verifier.verify_insertion(rows)
        except FailureEvent as exc:
            self.fail(f'{exc.violations}; last measurement={rows[-1]}')
        self.assertTrue(all(checks.values()))
        self.assertEqual(model.neq, 3)
        # Observation-contract fixture: transient contact is not final settledness.
        # This assigned velocity is not counted as a robot assembly or recovery.
        sleeve_velocity = data.joint('workcell_part_sleeve_1_free').qvel
        old_velocity = sleeve_velocity.copy()
        sleeve_velocity[3] = .012
        skills.verifier.verify_other_parts(data, require_settled=False)
        with self.assertRaises(FailureEvent):
            skills.verifier.verify_other_parts(data)
        sleeve_velocity[3] = .11
        with self.assertRaises(FailureEvent):
            skills.verifier.verify_other_parts(data, require_settled=False)
        sleeve_velocity[:] = old_velocity
        # A displaced installed component must be rejected even if still supported.
        data.joint('workcell_part_sleeve_1_free').qpos[0] += .004
        mujoco.mj_forward(model, data)
        with self.assertRaises(FailureEvent):
            skills.verifier.verify_other_parts(data)
        with self.assertRaises(FailureEvent):
            skills.verifier.verify_other_parts(data, require_settled=False)


if __name__ == '__main__':
    unittest.main()
