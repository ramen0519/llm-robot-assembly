from pathlib import Path
import sys
import unittest
import mujoco
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from failure_report import FailureCode, FailureEvent
from multi_post_scene import build_multi_post_scene
from multi_post_skills import MultiPostSkills
from workcell_scene import part_name


class MultiPostTests(unittest.TestCase):
    def test_all_free_parts_exist_at_start_and_have_no_attachment(self):
        for count in (2, 3, 4):
            model, data, registry = build_multi_post_scene(count)
            self.assertEqual((model.nq, model.nv, model.neq, model.nu), (14 + count * 7, 14 + count * 6, 3, 7))
            self.assertEqual(len(registry), count)
            for entry in registry.values():
                joint = model.joint(part_name(entry['config']) + '_free')
                self.assertEqual(model.jnt_type[joint.id], mujoco.mjtJoint.mjJNT_FREE)
            skills = MultiPostSkills(model, data, 'post_1', registry, set())
            skills.settle()
            self.assertEqual(len(skills.observe()['all_parts']), count)

    def test_inactive_part_support_permission_never_allows_robot_contact(self):
        model, data, registry = build_multi_post_scene(2)
        installed = set()
        skills = MultiPostSkills(model, data, 'post_1', registry, installed)
        v = skills.verifier
        other = next(iter(v.other_geoms))
        self.assertTrue(v.contact_policy({other, v.table_geom}, allow_support=False, allow_grasp=False)[0])
        self.assertFalse(v.contact_policy({other, next(iter(v.pads))}, allow_support=True, allow_grasp=True)[0])
        self.assertFalse(v.contact_policy({other, v.floor_geom}, allow_support=True, allow_grasp=True)[0])
        installed.add('post_2')
        self.assertTrue(v.contact_policy({other, v.floor_geom}, allow_support=True, allow_grasp=True)[0])
        self.assertFalse(v.contact_policy({other, v.table_geom}, allow_support=True, allow_grasp=True)[0])

    def test_movement_of_an_inactive_part_is_detected(self):
        model, data, registry = build_multi_post_scene(2)
        skills = MultiPostSkills(model, data, 'post_1', registry, set())
        skills.settle()
        q = data.joint(part_name(registry['post_2']['config']) + '_free').qpos
        q[0] += .010  # Negative detector fixture only; not assembly execution.
        mujoco.mj_forward(model, data)
        with self.assertRaises(FailureEvent) as caught:
            skills.verifier.check_other_parts(data)
        self.assertEqual(caught.exception.code, FailureCode.PLACEMENT_NOT_CONFIRMED)
        self.assertEqual(caught.exception.violations[0]['part_id'], 'post_2')


if __name__ == '__main__':
    unittest.main()
