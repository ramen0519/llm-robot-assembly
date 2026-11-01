"""Two real installations, retained parts, and bounded empty-gripper planning."""
from contextlib import ExitStack
import csv
from copy import deepcopy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import mujoco
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from assembly_skills import DOWNWARD_TCP
from empty_approach import plan_empty_approach
from failure_report import FailureCode, FailureEvent
from frame_assembly_scene import build_frame_scene
from frame_assembly_skills import FrameSkills
from joint_path_planner import plan_tcp_path
from run_frame_assembly import execute_trial
from workcell_scene import part_name


class FrameDetourTests(unittest.TestCase):
    def test_empty_approach_boundaries_and_changed_state(self):
        model, data, registry = build_frame_scene(3, transfer_obstacle=True)
        skills = FrameSkills(model, data, 'post_1', registry, set())
        skills.settle()
        before = (float(data.time), data.qpos.copy(), data.qvel.copy(), data.ctrl.copy())
        goal = np.array(registry['post_1']['joint']['transport_test_obstacle']['center_world_m']) - [0., 0., .020]
        for stage in ('approach_lower', 'insert_lower', 'insert_transfer'):
            skills.stage(stage)
            with self.assertRaises(FailureEvent) as caught:
                plan_empty_approach(skills, goal, DOWNWARD_TCP)
            self.assertEqual(caught.exception.code, FailureCode.INPUT_OR_PRECONDITION_INVALID)
        skills.stage('approach_hover')
        skills.motion.held_pose = object()  # Invalid occupancy marker; refusal must precede any IK.
        with self.assertRaises(FailureEvent):
            plan_empty_approach(skills, goal, DOWNWARD_TCP)
        skills.motion.held_pose = None
        callbacks = []
        def change_control(error):
            callbacks.append(error.code)
            data.ctrl[0] += .001
        with self.assertRaises(FailureEvent) as stale:
            plan_empty_approach(skills, goal, DOWNWARD_TCP, on_preflight_failure=change_control)
        self.assertEqual(callbacks, [FailureCode.PLANNED_CONTACT])
        self.assertEqual(stale.exception.code, FailureCode.INPUT_OR_PRECONDITION_INVALID)
        data.ctrl[:] = before[3]
        with self.assertRaises(FailureEvent) as budget:
            plan_empty_approach(skills, goal, DOWNWARD_TCP, max_time_s=1e-9)
        self.assertEqual(budget.exception.code, FailureCode.MOTION_PATH_NOT_FOUND)
        self.assertEqual(data.time, before[0])
        np.testing.assert_array_equal(data.qpos, before[1])
        np.testing.assert_array_equal(data.qvel, before[2])
        np.testing.assert_array_equal(data.ctrl, before[3])

    def test_full_frame_compound_detours_still_rejected_before_execution(self):
        for count in (6, 10):
            with self.assertRaises(ValueError):
                execute_trial(product_count=count, transport_strategy='direct_then_rrt')
            with self.assertRaises(ValueError):
                execute_trial(product_count=count, transfer_obstacle=True)
        # Geometry/skills can be used by the separately labelled one-component probe.
        model, data, registry = build_frame_scene(6, transfer_obstacle=True)
        skills = FrameSkills(model, data, 'sleeve_1', registry, set(), transport_strategy='direct_then_rrt')
        self.assertEqual(skills.active, 'sleeve_1')
        self.assertEqual(data.time, 0.)

    def test_two_post_graph_preserves_first_part_and_checks_its_collision_geometry(self):
        holder, calls = {}, []
        with ExitStack() as stack, tempfile.TemporaryDirectory() as temporary:
            def observe_plan(motion, *args, **kwargs):
                calls.append((motion.verifier.active, motion.stage, sorted(motion.verifier.installed)))
                self.assertIn(motion.stage, ('approach_hover', 'insert_transfer'))
                self.assertFalse(motion.verifier.mating_enabled)
                self.assertFalse(motion.verifier.mating_motion)
                return plan_tcp_path(motion, *args, **kwargs)
            stack.enter_context(patch('empty_approach.plan_tcp_path', side_effect=observe_plan))
            stack.enter_context(patch('loaded_transport.plan_tcp_path', side_effect=observe_plan))
            def observer(skills):
                model, data = skills.model, skills.data
                if not holder:
                    holder.update(data=data, model=model, last_q=data.qpos.copy(), last_v=data.qvel.copy(),
                                  fixed=model.body_pos.copy(), equality=model.eq_data.copy(), installed_collision=False)
                    real_step, real_forward = mujoco.mj_step, mujoco.mj_forward
                    def unchanged():
                        for value, expected in ((data.qpos, holder['last_q']), (data.qvel, holder['last_v']),
                                                (model.body_pos, holder['fixed']), (model.eq_data, holder['equality'])):
                            if not np.array_equal(value, expected):
                                raise AssertionError('Live state or fixed geometry changed outside physics.')
                        self.assertEqual(model.neq, 3)
                        self.assertFalse(np.any(model.body_gravcomp) or np.any(data.xfrc_applied) or np.any(data.qfrc_applied))
                    def step(m, d, *args, **kwargs):
                        if d is data:
                            unchanged()
                        real_step(m, d, *args, **kwargs)
                        if d is data:
                            holder.update(last_q=d.qpos.copy(), last_v=d.qvel.copy())
                    def forward(m, d):
                        if d is data:
                            unchanged()
                        real_forward(m, d)
                    stack.enter_context(patch.object(mujoco, 'mj_step', side_effect=step))
                    stack.enter_context(patch.object(mujoco, 'mj_forward', side_effect=forward))
                self.assertIs(data, holder['data'])
                self.assertIs(model, holder['model'])
                self.assertEqual((model.nq, model.nv), (28, 26))
                if skills.active != 'post_2':
                    return
                self.assertEqual(skills.installed, {'post_1'})
                original_stage = skills.stage
                def stage(name):
                    original_stage(name)
                    if name != 'insert_transfer':
                        return
                    # Deliberately request an endpoint where the carried post overlaps
                    # the real first installation. This is planning-only, never executed.
                    held_p, held_r = skills.motion.held_pose
                    rotation = skills.verifier.target_rotation @ held_r.T
                    center = np.array(skills.registry['post_1']['joint']['target_part_center_world_m']) + [0., 0., .045]
                    target = center - rotation @ held_p
                    old_goal = deepcopy(skills.motion.goal)
                    before = (float(data.time), data.qpos.copy(), data.qvel.copy(), data.ctrl.copy())
                    candidate = skills.motion._candidate
                    def inspect(qpos, allow_support, allow_grasp):
                        try:
                            return candidate(qpos, allow_support, allow_grasp)
                        except FailureEvent as error:
                            if error.candidate:
                                first = set(skills.registry['post_1']['collision_geom_names'])
                                second = set(skills.registry['post_2']['collision_geom_names'])
                                for contact in error.candidate['contacts']:
                                    geoms = {g['geom_name'] for g in contact['geoms']}
                                    if geoms & first and geoms & second:
                                        holder['installed_collision'] = True
                            raise
                    try:
                        with patch.object(skills.motion, '_candidate', side_effect=inspect), self.assertRaises(FailureEvent) as blocked:
                            plan_tcp_path(skills.motion, target, rotation, allow_support=False, allow_grasp=True)
                        self.assertEqual(blocked.exception.code, FailureCode.PLANNED_CONTACT)
                        self.assertTrue(holder['installed_collision'])
                        self.assertEqual(data.time, before[0])
                        np.testing.assert_array_equal(data.qpos, before[1])
                        np.testing.assert_array_equal(data.qvel, before[2])
                        np.testing.assert_array_equal(data.ctrl, before[3])
                    finally:
                        skills.motion.goal = old_goal
                skills.stage = stage
            result = execute_trial(product_count=3, mode='single', transport_strategy='direct_then_rrt',
                                   transfer_obstacle=True, observer=observer, output_root=temporary)
            self.assertEqual(result['status'], 'success', result['error'])
            self.assertEqual(result['completed_installations'], ['post_1', 'post_2'])
            self.assertEqual(result['robot_installed_parts'], 2)
            self.assertFalse(result['ten_part_assembly_complete'])
            self.assertEqual(result['api_calls'], 0)
            self.assertTrue(holder['installed_collision'])
            self.assertTrue(any(active == 'post_2' and installed == ['post_1'] for active, _, installed in calls))
            self.assertTrue(all(s['status'] == 'success' for s in result['graph_states'].values()))
            for active in ('post_1', 'post_2'):
                self.assertEqual(result['transport_attempts'][active][0]['status'], 'executed')
                self.assertEqual(result['empty_approach_attempts'][active][0]['status'], 'executed')
            previous = result['part_metrics']['post_2']['previous_parts']['post_1']
            self.assertLess(previous['maximum_position_error_m'], .001)
            self.assertLess(previous['maximum_orientation_error_rad'], np.deg2rad(1.))
            with (Path(result['run_dir'])/'skill_trace.csv').open(newline='', encoding='utf-8') as stream:
                rows = list(csv.DictReader(stream))
            times = np.array([float(r['simulation_time_s']) for r in rows])
            self.assertTrue(np.all(np.diff(times) >= 0))
            self.assertLessEqual(float(np.diff(times).max()), .00200001)
            self.assertEqual({r['active_part_id'] for r in rows}, {'post_1', 'post_2'})

    def test_failure_after_first_installation_is_not_full_success(self):
        def observer(skills):
            if skills.active == 'post_2':
                raise FailureEvent(FailureCode.INPUT_OR_PRECONDITION_INVALID, 'Explicit test stop before second installation.')
        with tempfile.TemporaryDirectory() as temporary:
            result = execute_trial(product_count=3, mode='single', observer=observer, output_root=temporary)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['robot_installed_parts'], 1)
        self.assertEqual(result['robot_installations_goal'], 2)
        self.assertEqual(result['completed_installations'], ['post_1'])
        self.assertFalse(result['ten_part_assembly_complete'])


if __name__ == '__main__':
    unittest.main()
