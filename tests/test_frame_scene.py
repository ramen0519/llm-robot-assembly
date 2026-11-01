from pathlib import Path
import sys
import unittest
import mujoco
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from frame_assembly_scene import build_frame_scene
from frame_assembly_skills import FrameSkills
from failure_report import FailureCode, FailureEvent
from workcell_scene import part_name
from move_tcp import ArmPositionController


class FrameSceneTests(unittest.TestCase):
    def test_compound_sleeves_and_cover_have_open_collision_holes(self):
        model, data, registry = build_frame_scene(10)
        self.assertEqual((model.nq, model.nv, model.neq), (77, 68, 3))
        self.assertEqual(len(registry), 9)
        for name in ('sleeve_1', 'cover'):
            entry = registry[name]
            dummy = model.geom(part_name(entry['config']) + '_geom').id
            self.assertEqual((model.geom_contype[dummy], model.geom_conaffinity[dummy]), (0, 0))
            self.assertGreater(len(entry['collision_geom_names']), 1)
        skills = FrameSkills(model, data, 'post_1', registry, set())
        skills.settle()
        self.assertEqual(len(skills.observe()['all_parts']), 9)

    def test_sleeve_cannot_be_picked_before_corresponding_post_is_installed(self):
        model, data, registry = build_frame_scene(6)
        skills = FrameSkills(model, data, 'sleeve_1', registry, set())
        with self.assertRaises(FailureEvent) as caught:
            skills.approach('sleeve_1')
        self.assertEqual(caught.exception.code, FailureCode.INPUT_OR_PRECONDITION_INVALID)
        self.assertEqual(data.time, 0.)

    def test_other_part_contact_with_robot_is_not_mating(self):
        model, data, registry = build_frame_scene(6)
        skills = FrameSkills(model, data, 'post_1', registry, set())
        self.assertFalse(skills.verifier.mating_relation('sleeve_1', next(iter(skills.verifier.pads))))
        self.assertTrue(skills.verifier.mating_relation('sleeve_1', model.geom('workcell_part_post_1_geom').id))
        self.assertFalse(skills.verifier.support_relation('sleeve_1', model.geom('workcell_part_post_1_geom').id))

    def test_sleeve_hole_physically_passes_over_fixed_probe_and_filled_hole_does_not(self):
        # Isolated hole geometry test with a fixed measuring post, not a robot assembly episode.
        from frame_product import product_parts
        from multi_post_scene import MULTI_DIMENSIONS
        sleeve = next(p for p in product_parts(MULTI_DIMENSIONS) if p['id'] == 'sleeve_1')
        heights = []
        for filled in (False, True):
            spec = mujoco.MjSpec()
            spec.option.timestep = .002
            spec.worldbody.add_geom(type=mujoco.mjtGeom.mjGEOM_PLANE, size=[1., 1., .1])
            spec.worldbody.add_geom(type=mujoco.mjtGeom.mjGEOM_BOX, pos=[0., 0., .030], size=[.010, .010, .030])
            body = spec.worldbody.add_body(name='probe_sleeve', pos=[0., 0., .080])
            body.add_freejoint()
            boxes = ([{'center': [0., 0., .015], 'size': [.036, .036, .030]}]
                     if filled else sleeve['boxes'])
            for box in boxes:
                body.add_geom(type=mujoco.mjtGeom.mjGEOM_BOX,
                              pos=np.array(box['center']) - [0., 0., .015], size=np.array(box['size']) / 2,
                              mass=.040 / len(boxes))
            model = spec.compile()
            data = mujoco.MjData(model)
            for _ in range(1500):
                mujoco.mj_step(model, data)
            self.assertFalse(any(w.number for w in data.warning))
            heights.append(float(data.body('probe_sleeve').xpos[2]))
        self.assertAlmostEqual(heights[0], .015, delta=.001)
        self.assertAlmostEqual(heights[1], .075, delta=.001)


if __name__ == '__main__':
    unittest.main()
