"""Compound carried geometry, physical execution, and honest fixture accounting."""
from contextlib import ExitStack
from copy import deepcopy
import csv
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import mujoco
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from failure_report import FailureCode, FailureEvent
from joint_path_planner import plan_tcp_path
from run_component_probe import execute_trial
from workcell_scene import part_name


class ComponentDetourTests(unittest.TestCase):
    def test_fixture_parts_do_not_count_as_robot_installations_on_failure(self):
        def stop(skills):
            self.assertEqual(len(skills.installed), 8)
            raise FailureEvent(FailureCode.INPUT_OR_PRECONDITION_INVALID, 'Explicit test stop after fixture setup.')
        with tempfile.TemporaryDirectory() as temporary:
            result = execute_trial('cover', observer=stop, output_root=temporary)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['robot_installed_parts'], 0)
        self.assertEqual(result['robot_installations_goal'], 1)
        self.assertEqual(len(result['initial_preassembled_parts']), 9)
        self.assertFalse(result['whole_frame_robot_assembly'])
        self.assertEqual(result['simulated_seconds'], 0.)
        self.assertEqual(result['transport_attempts'], [])

    def test_actual_sleeve_detour_checks_walls_and_preserves_free_fixture(self):
        captured = {'wall_contact': False}
        with ExitStack() as stack, tempfile.TemporaryDirectory() as temporary:
            def observer(skills):
                model, data = skills.model, skills.data
                self.assertEqual(skills.installed, {'post_1', 'post_2', 'post_3', 'post_4', 'sleeve_1'})
                captured.update(q=data.qpos.copy(), v=data.qvel.copy(), fixed=model.body_pos.copy(),
                                equality=model.eq_data.copy())
                real_step, real_forward = mujoco.mj_step, mujoco.mj_forward
                def unchanged():
                    for actual, expected in ((data.qpos, captured['q']), (data.qvel, captured['v']),
                                             (model.body_pos, captured['fixed']), (model.eq_data, captured['equality'])):
                        if not np.array_equal(actual, expected):
                            raise AssertionError('Live state or fixed geometry changed outside physics.')
                    self.assertEqual(model.neq, 3)
                    self.assertFalse(np.any(model.body_gravcomp) or np.any(data.xfrc_applied) or np.any(data.qfrc_applied))
                def step(m, d, *args, **kwargs):
                    if d is data:
                        unchanged()
                    real_step(m, d, *args, **kwargs)
                    if d is data:
                        captured.update(q=d.qpos.copy(), v=d.qvel.copy())
                def forward(m, d):
                    if d is data:
                        unchanged()
                    real_forward(m, d)
                stack.enter_context(patch.object(mujoco, 'mj_step', side_effect=step))
                stack.enter_context(patch.object(mujoco, 'mj_forward', side_effect=forward))
                old_stage = skills.stage
                def stage(name):
                    old_stage(name)
                    if name != 'insert_transfer':
                        return
                    # Overlap the sleeve's side WALL with an actual installed post.
                    # This is an IK/planning candidate, never a physical command.
                    p, r = skills.motion.held_pose
                    rotation = skills.verifier.target_rotation @ r.T
                    center = data.body(part_name(skills.registry['post_2']['config'])).xpos.copy() + [.015, 0., .020]
                    target = center - rotation @ p
                    original_goal = deepcopy(skills.motion.goal)
                    before = (float(data.time), data.qpos.copy(), data.qvel.copy(), data.ctrl.copy())
                    candidate = skills.motion._candidate
                    walls = set(skills.registry['sleeve_2']['collision_geom_names'])
                    post = set(skills.registry['post_2']['collision_geom_names'])
                    dummy = model.geom(part_name(skills.config) + '_geom').id
                    self.assertEqual((model.geom_contype[dummy], model.geom_conaffinity[dummy]), (0, 0))
                    self.assertGreater(len(walls), 1)
                    def inspect(qpos, allow_support, allow_grasp):
                        try:
                            return candidate(qpos, allow_support, allow_grasp)
                        except FailureEvent as error:
                            if error.candidate:
                                for contact in error.candidate['contacts']:
                                    geoms = {g['geom_name'] for g in contact['geoms']}
                                    if geoms & walls and geoms & post:
                                        captured['wall_contact'] = True
                            raise
                    try:
                        with patch.object(skills.motion, '_candidate', side_effect=inspect), self.assertRaises(FailureEvent) as blocked:
                            plan_tcp_path(skills.motion, target, rotation, allow_support=False, allow_grasp=True)
                        self.assertEqual(blocked.exception.code, FailureCode.PLANNED_CONTACT)
                        self.assertTrue(captured['wall_contact'])
                        self.assertEqual(data.time, before[0])
                        for actual, expected in zip((data.qpos, data.qvel, data.ctrl), before[1:]):
                            np.testing.assert_array_equal(actual, expected)
                    finally:
                        skills.motion.goal = original_goal
                skills.stage = stage
            result = execute_trial('sleeve_2', transport_strategy='direct_then_rrt', transfer_obstacle=True,
                                   observer=observer, output_root=temporary)
            self.assertEqual(result['status'], 'success', result['error'])
            self.assertTrue(captured['wall_contact'])
            self.assertEqual(result['robot_installed_parts'], 1)
            self.assertEqual(len(result['initial_preassembled_parts']), 6)
            self.assertFalse(result['whole_frame_robot_assembly'])
            self.assertEqual(result['api_calls'], 0)
            self.assertTrue(all(result['result']['checks'].values()))
            attempt = result['transport_attempts'][0]
            self.assertEqual(attempt['status'], 'executed')
            self.assertEqual(attempt['planning']['planner'], 'checked_joint_interpolation')
            self.assertTrue(attempt['planning']['fallback_used'])
            for previous in result['part_metrics']['previous_parts'].values():
                self.assertLess(previous['maximum_position_error_m'], .001)
            with (Path(result['run_dir']) / 'skill_trace.csv').open(newline='', encoding='utf-8') as stream:
                times = np.array([float(r['simulation_time_s']) for r in csv.DictReader(stream)])
            self.assertTrue(np.all(np.diff(times) >= 0))
            self.assertLessEqual(float(np.diff(times).max()), .00200001)


if __name__ == '__main__':
    unittest.main()
