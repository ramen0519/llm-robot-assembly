from pathlib import Path
import sys
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from assembly_graph import run_assembly_graph
from assembly_policy import ScriptedPolicy, validate_proposal
from assembly_skills import DOWNWARD_TCP
from development_cases import build_case
from failure_report import FailureEvent
from insertion_skills import InsertionSkills
from post_grasp import bounds_issue, numeric_grasp, world_grasp
from workcell_scene import yaw_rotation


def pose(z, yaw=0):
    return dict(x_mm=0, y_mm=0, z_mm=z, jaw_yaw_deg=yaw)


class PostGraspTests(unittest.TestCase):
    def test_part_frame_and_physical_jaw_axis(self):
        r = yaw_rotation(90)
        center = np.array([.3, .1, .06])
        grasp = dict(x_mm=3, y_mm=2, z_mm=9, jaw_yaw_deg=30)
        p, rotation = world_grasp(center, r, grasp, DOWNWARD_TCP)
        np.testing.assert_allclose(p, [.298, .103, .069])
        np.testing.assert_allclose(-rotation[:, 1], yaw_rotation(120)[:, 0], atol=1e-12)
        np.testing.assert_allclose(rotation[:, 2], [0, 0, -1])

    def test_contract(self):
        for bad in [None, pose(float('nan')), pose(True), dict(z_mm=0)]:
            self.assertFalse(numeric_grasp(bad))
        for candidate in [pose(31), pose(0, 181)]:
            self.assertIsNotNone(bounds_issue(candidate, [.02, .02, .06]))
        ctx = dict(allowed_skills=['pick', 'stop'], part_id='p', target_id='s',
                   observation_revision=1, grasp_task={})
        action = dict(skill='pick', part_id='p', target_id='s', observation_revision=1, reason='test')
        with self.assertRaises(FailureEvent):
            validate_proposal(action, ctx)
        self.assertEqual(validate_proposal(dict(action, skill='stop', grasp=None), ctx)['skill'], 'stop')

    def test_rejected_sweep_leaves_live_state_untouched_then_reobserve_and_assemble(self):
        skills = InsertionSkills(*build_case())
        events = []

        class Probe(ScriptedPolicy):
            def propose(self, context):
                action = super().propose(context)
                if 'grasp_task' in context:
                    trials = context['grasp_task']['previous_trials']
                    if not trials:
                        self.before = (skills.data.qpos.copy(), skills.data.qvel.copy(), skills.data.ctrl.copy(),
                                       skills.motion.reference.copy(), skills.data.time)
                    else:
                        for before, after in zip(self.before, (skills.data.qpos, skills.data.qvel, skills.data.ctrl,
                                                               skills.motion.reference, skills.data.time)):
                            np.testing.assert_array_equal(before, after)
                        assert not skills.results and not skills.motion.plans and skills.motion.held_pose is None
                    action['grasp'] = pose(8 if trials else 0)
                return action

        def fail(exc):
            raise AssertionError(str(exc)) from exc

        state = run_assembly_graph(skills, mode='single', policies={'single': Probe()}, propose_grasp=True,
                                   failure_handler=fail, on_event=events.append)
        self.assertEqual(state['status'], 'success', state['error'])
        first, second = state['grasp_trials']
        self.assertEqual((first['failure_code'], first['phase']), ('PLANNED_CONTACT', 'insert_lower/preflight'))
        self.assertIn('gripper_right_pad1', first['violations'][0]['geoms'])
        self.assertIn('held_pad_geometry', first)
        self.assertTrue(second['accepted'])
        self.assertGreater(second['observation_revision'], first['observation_revision'])
        self.assertEqual(skills.results[0]['proposed_grasp'], second['grasp'])
        self.assertEqual([e['action']['skill'] for e in events if e['node'] == 'execute'], ['pick', 'insert', 'finish'])
        self.assertTrue(all(state['verification']['checks'].values()))

    def test_out_of_bounds_stops_without_motion_after_budget(self):
        skills = InsertionSkills(*build_case())

        class Outside(ScriptedPolicy):
            def propose(self, context):
                return dict(super().propose(context), grasp=pose(500))

        state = run_assembly_graph(skills, mode='single', policies={'single': Outside()}, propose_grasp=True,
                                   failure_handler=lambda exc: self.fail(str(exc)))
        self.assertEqual(state['status'], 'stopped')
        self.assertEqual(len(state['grasp_trials']), 3)
        self.assertEqual(skills.results, [])
        self.assertAlmostEqual(skills.data.time, 3.)
