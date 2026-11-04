"""Cover geometry and independent four-post checks using real MuJoCo state."""
from pathlib import Path
import sys
import unittest

import mujoco
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from failure_report import FailureCode, FailureEvent
from frame_assembly_scene import build_frame_scene
from frame_assembly_skills import FrameSkills
from move_tcp import ArmPositionController
from workcell_scene import part_name


def cover_cell(*, assembled=False, tab_variant='center'):
    model, data, registry = build_frame_scene(10, cover_grip=tab_variant)
    installed = set(registry) - {'cover'}
    for name in registry if assembled else installed:
        entry = registry[name]
        data.joint(part_name(entry['config']) + '_free').qpos[:3] = entry['joint']['target_part_center_world_m']
    mujoco.mj_forward(model, data)
    return FrameSkills(model, data, 'cover', registry, installed)


class CoverGraspTests(unittest.TestCase):
    def test_policy_geometry_matches_actual_tab_and_bores_without_preset_grasp(self):
        for variant, y in [('center', 0), ('edge', -65)]:
            skills = cover_cell(tab_variant=variant)
            packet = skills.grasp_geometry()
            np.testing.assert_allclose(packet['tab_center_xyz_mm'], [0, y, 4])
            np.testing.assert_allclose(packet['tab_size_xyz_mm'], [30, 25, 30])
            np.testing.assert_allclose(packet['part_size_xyz_mm'], [160, 160, 38])
            self.assertEqual(packet['bore_centers_xyz_mm']['post_4'], [40, 40, -15])
            profile = packet['unloaded_closure_profile_mm']
            self.assertEqual(len(profile), 8)
            self.assertGreater(profile[0]['jaw_gap'], profile[-1]['jaw_gap'])
            near_tab_width = min(profile, key=lambda row: abs(row['jaw_gap'] - 30))
            self.assertGreater(near_tab_width['pad_reach_below_tcp'], 16.)
            np.testing.assert_allclose(packet['plate_solid_z_range_mm'], [-19, -11])
            np.testing.assert_allclose(packet['tab_solid_z_range_mm'], [-11, 19])
            self.assertNotIn('grasp_offset_part_m', packet)
            self.assertNotIn('socket_insertion_depth_mm', packet)

    def test_four_holes_and_four_supports_are_independent(self):
        # Posed validation fixture. No robot installation is credited for these synthetic counterexamples.
        s = cover_cell(assembled=True)
        controller = ArmPositionController(s.model)
        joints = s.data.qpos[:6].copy()
        for _ in range(800):
            controller.apply(s.data, joints)
            mujoco.mj_step(s.model, s.data)
        mujoco.mj_forward(s.model, s.data)
        fit = s.verify_cover_fit()
        self.assertEqual(set(fit['bores']), {f'post_{i}' for i in range(1, 5)})
        self.assertTrue(all(f > .01 for f in fit['vertical_normal_load_n'].values()))
        # One bad post must fail even when all other post/hole pairs match.
        for name, coord, delta in [('post_2', 0, .004), ('post_4', 2, -.010)]:
            pose = s.data.joint(part_name(s.registry[name]['config']) + '_free').qpos
            saved = pose.copy()
            pose[coord] += delta
            mujoco.mj_forward(s.model, s.data)
            with self.assertRaises(FailureEvent) as caught:
                s.verify_cover_fit()
            self.assertEqual(caught.exception.violations[0]['post_id'], name)
            pose[:] = saved
        # Lower just one sleeve. The four openings still surround their posts.
        s.data.joint(part_name(s.registry['sleeve_3']['config']) + '_free').qpos[2] -= .003
        mujoco.mj_forward(s.model, s.data)
        with self.assertRaises(FailureEvent) as caught:
            s.verify_cover_fit()
        self.assertEqual(caught.exception.violations[0]['vertical_normal_load_n']['sleeve_3'], 0.)

    def test_low_grasp_hits_plate_in_trial_without_moving_original_cover(self):
        s = cover_cell()
        s.settle()
        before = (s.data.qpos.copy(), s.data.qvel.copy(), s.data.time)
        trial = s.check_grasp(dict(x_mm=0, y_mm=0, z_mm=-10, jaw_yaw_deg=0))
        self.assertFalse(trial['accepted'])
        self.assertIn(trial['failure_code'], [FailureCode.PLANNED_CONTACT.value, FailureCode.UNEXPECTED_CONTACT.value])
        self.assertTrue(any('workcell_part_cover_wall' in geom for v in trial['violations'] for geom in v['geoms']))
        feedback = trial['approach_geometry']
        self.assertLess(feedback['predicted_open_pad_bottom_minus_plate_top_mm'], 0)
        self.assertEqual(feedback['cover_contacts'][0]['feature'], 'solid part of flat plate')
        np.testing.assert_array_equal(before[0], s.data.qpos)
        np.testing.assert_array_equal(before[1], s.data.qvel)
        self.assertEqual(before[2], s.data.time)
        self.assertEqual(s.results, [])
