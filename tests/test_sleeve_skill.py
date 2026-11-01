"""Physical sleeve installation from an explicitly preassembled-post initial fixture."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import mujoco
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from frame_assembly_scene import build_frame_scene
from frame_assembly_skills import FrameSkills
from workcell_scene import part_name


class SleeveSkillTests(unittest.TestCase):
    def test_robot_seats_cover_on_free_preassembled_frame(self):
        model, data, registry = build_frame_scene(10)
        preassembled = set(registry) - {'cover'}
        for name in preassembled:
            entry = registry[name]
            data.joint(part_name(entry['config']) + '_free').qpos[:3] = entry['joint']['target_part_center_world_m']
        mujoco.mj_forward(model, data)
        skills = FrameSkills(model, data, 'cover', registry, preassembled)
        skills.settle()
        skills.pick('cover')
        result = skills.insert('cover', 'mate_cover')
        self.assertEqual(result['skill'], 'seat_cover/v1')
        self.assertTrue(all(result['checks'].values()))
        skills.verifier.verify_other_parts(data)
        self.assertEqual(model.neq, 3)

    def test_robot_installs_one_sleeve_on_free_preassembled_posts(self):
        model, data, registry = build_frame_scene(6)
        preassembled = {f'post_{i}' for i in range(1, 5)}
        # Setup only: these four posts are not counted as robot-installed in this test.
        for name in preassembled:
            entry = registry[name]
            data.joint(part_name(entry['config']) + '_free').qpos[:3] = entry['joint']['target_part_center_world_m']
        mujoco.mj_forward(model, data)
        skills = FrameSkills(model, data, 'sleeve_1', registry, preassembled)
        last_q, last_v = data.qpos.copy(), data.qvel.copy()
        step = mujoco.mj_step
        def guarded(m, d, *args, **kwargs):
            nonlocal last_q, last_v
            if d is data:
                np.testing.assert_array_equal(d.qpos, last_q)
                np.testing.assert_array_equal(d.qvel, last_v)
            step(m, d, *args, **kwargs)
            if d is data:
                last_q, last_v = d.qpos.copy(), d.qvel.copy()
        with patch.object(mujoco, 'mj_step', side_effect=guarded):
            skills.settle()
            skills.pick('sleeve_1')
            result = skills.insert('sleeve_1', 'mate_sleeve_1')
        self.assertEqual(result['skill'], 'insert_sleeve/v1')
        self.assertTrue(all(result['checks'].values()))
        skills.verifier.verify_other_parts(data)
        self.assertEqual(model.neq, 3)


if __name__ == '__main__':
    unittest.main()
