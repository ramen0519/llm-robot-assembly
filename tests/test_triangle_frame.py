"""CAD, current scene and physical verifier checks for the three-post development frame."""
from pathlib import Path
import json
import sys
import tempfile
import unittest

import mujoco
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from failure_report import FailureEvent
from frame_assembly_scene import build_frame_scene
from frame_assembly_skills import FrameSkills
from frame_product import FrameDimensions, export_product, frame_hole_centers, product_parts
from move_tcp import ArmPositionController
from multi_post_scene import MULTI_DIMENSIONS
from workcell_scene import part_name


TRIANGLE = 'triangle_three_post'
POSTS = ['post_1', 'post_2', 'post_3']
SLEEVES = ['sleeve_1', 'sleeve_2', 'sleeve_3']
TRIANGLE_IDS = ['base', *POSTS, *SLEEVES, 'cover']


class TriangleFrameTests(unittest.TestCase):
    def test_layout_units_open_holes_and_cover_prerequisites(self):
        centers = frame_hole_centers(MULTI_DIMENSIONS, layout=TRIANGLE)
        self.assertEqual(centers, [(-.04, -.04), (.04, -.04), (0., .04)])
        self.assertEqual(frame_hole_centers(FrameDimensions(), TRIANGLE)[2], (0., .03))
        parts = product_parts(MULTI_DIMENSIONS, layout=TRIANGLE)
        self.assertEqual([p['id'] for p in parts], TRIANGLE_IDS)
        self.assertEqual(parts[-1]['requires'], [*SLEEVES, *POSTS])
        for index, center in enumerate(centers):
            for part in (parts[index+1], parts[index+4]):
                self.assertEqual(tuple(part['position_product_m'][:2]), center)
        # Probe a horizontal section through the working walls. The old fourth-post site is now solid.
        for plate, z in ((parts[0], .01), (parts[-1], .004)):
            def contains(x, y):
                return any(np.all(np.abs(np.array([x, y, z]) - box['center']) <= np.array(box['size']) / 2)
                           for box in plate['boxes'])
            self.assertTrue(contains(.04, .04))
            self.assertFalse(any(contains(x, y) for x, y in centers))

    def test_exported_cad_and_default_four_post_layout(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            triangle = export_product(folder / 'triangle', MULTI_DIMENSIONS, layout=TRIANGLE)
            self.assertEqual(triangle, json.loads((folder / 'triangle/manifest.json').read_text(encoding='utf-8')))
            self.assertEqual((triangle['candidate_id'], triangle['product_parts'], triangle['robot_installations']),
                             ('triangle_three_post_v1', 8, 7))
            self.assertEqual({p.stem for p in (folder / 'triangle').glob('*.obj')}, set(TRIANGLE_IDS))
            self.assertTrue(all(p['triangles'] > 0 and p['volume_m3'] > 0 for p in triangle['parts']))
            four = export_product(folder / 'default', MULTI_DIMENSIONS)
            self.assertEqual(four, export_product(folder / 'four', MULTI_DIMENSIONS, layout='four_post'))
            self.assertEqual((four['candidate_id'], four['product_parts'], four['robot_installations']),
                             ('four_post_frame_v2_center_grip', 10, 9))
            self.assertNotEqual(triangle['parts'][-1]['sha256'], four['parts'][-1]['sha256'])

    def test_scene_uses_full_product_identity_and_semantic_tray_slots(self):
        _, _, legacy = build_frame_scene(10)
        model, data, registry = build_frame_scene(8, layout=TRIANGLE)
        self.assertEqual(list(registry), TRIANGLE_IDS[1:])
        self.assertEqual((model.nq, model.nv), (63, 56))
        for name, entry in registry.items():
            self.assertEqual(entry['joint']['product_candidate'], 'triangle_three_post_v1')
            self.assertEqual(entry['joint']['eventual_product_parts'], 8)
            self.assertEqual(entry['config']['part']['spawn_xy_table_m'], legacy[name]['config']['part']['spawn_xy_table_m'])
            np.testing.assert_array_equal(entry['spawn_center_world_m'], legacy[name]['spawn_center_world_m'])
        cover = registry['cover']
        self.assertEqual(cover['support_ids'], SLEEVES)
        self.assertEqual(cover['mating_ids'], [*SLEEVES, *POSTS])
        self.assertEqual(list(cover['bore_centers_part_m']), POSTS)
        skills = FrameSkills(model, data, 'cover', registry, set())
        self.assertEqual(skills.observe()['assembly_joint']['required_support_ids'], SLEEVES)
        geometry = skills.grasp_geometry()
        self.assertEqual(geometry['required_support_count'], 3)
        self.assertEqual(geometry['bore_centers_xyz_mm']['post_3'], [0, 40, -15])
        self.assertNotIn('four', geometry['seating_surface'])
        self.assertEqual(list(build_frame_scene(3, layout=TRIANGLE, first_sleeve_cell=True)[2]), ['post_1', 'sleeve_1'])
        with self.assertRaises(ValueError):
            build_frame_scene(9, layout=TRIANGLE)
        with self.assertRaises(ValueError):
            product_parts(layout='unknown')

    def test_posed_cover_requires_each_of_three_posts_and_each_of_three_supports(self):
        # Isolate the verifier in an assembled geometry fixture. This is not a robot installation.
        model, data, registry = build_frame_scene(8, layout=TRIANGLE)
        installed = set(registry) - {'cover'}
        for entry in registry.values():
            data.joint(part_name(entry['config']) + '_free').qpos[:3] = entry['joint']['target_part_center_world_m']
        mujoco.mj_forward(model, data)
        controller = ArmPositionController(model)
        arm_pose = data.qpos[:6].copy()
        for _ in range(800):
            controller.apply(data, arm_pose)
            mujoco.mj_step(model, data)
        mujoco.mj_forward(model, data)
        skills = FrameSkills(model, data, 'cover', registry, installed)
        skills.verifier.mating_enabled = True
        row = skills.verifier.sample(data, skills.motion.reference, allow_support=True, allow_grasp=False)
        evidence = skills.insertion_evidence([row])
        self.assertTrue(evidence['checks']['all_posts_through_both_openings'])
        self.assertTrue(evidence['checks']['all_sleeves_support_cover'])
        self.assertNotIn('four_posts_through_both_openings', evidence['checks'])
        fit = evidence['cover_fit']
        self.assertEqual(set(fit['bores']), set(POSTS))
        self.assertEqual(set(fit['vertical_normal_load_n']), set(SLEEVES))
        self.assertTrue(all(force > .01 for force in fit['vertical_normal_load_n'].values()))
        for name in [*POSTS, *SLEEVES]:
            with self.subTest(removed=name):
                pose = data.joint(part_name(registry[name]['config']) + '_free').qpos
                before = pose.copy()
                if name in POSTS:
                    pose[0] += .006
                else:
                    pose[2] -= .003
                mujoco.mj_forward(model, data)
                with self.assertRaises(FailureEvent) as caught:
                    skills.verify_cover_fit()
                detail = caught.exception.violations[0]
                if name in POSTS:
                    self.assertEqual(detail['post_id'], name)
                else:
                    self.assertEqual(detail['vertical_normal_load_n'][name], 0.)
                pose[:] = before
                mujoco.mj_forward(model, data)


if __name__ == '__main__':
    unittest.main()
