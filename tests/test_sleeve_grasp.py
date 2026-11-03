from pathlib import Path
import sys
import unittest

import mujoco
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from assembly_graph import run_assembly_graph
from assembly_policy import ScriptedPolicy
from failure_report import FailureEvent
from frame_assembly_scene import build_frame_scene
from frame_assembly_skills import FrameSkills
from workcell_scene import part_name


def sleeve_cell():
    model, data, registry = build_frame_scene(3, first_sleeve_cell=True)
    post = registry['post_1']
    data.joint(part_name(post['config']) + '_free').qpos[:3] = post['joint']['target_part_center_world_m']
    mujoco.mj_forward(model, data)
    return FrameSkills(model, data, 'sleeve_1', registry, {'post_1'})


class SleeveGraspTests(unittest.TestCase):
    def test_sleeve_input_and_copy_keep_the_real_bore_and_preinstalled_post(self):
        skills = sleeve_cell()
        info = skills.grasp_geometry()
        self.assertEqual(set(skills.registry), {'post_1', 'sleeve_1'})
        self.assertIn('center of sleeve', info['frame'])
        self.assertEqual(info['part_size_xyz_mm'], [36., 36., 30.])
        self.assertEqual(info['bore_width_mm'], 24.)
        self.assertNotIn('grasp_offset_part_m', str(info))
        trial = skills._grasp_trial_copy()
        self.assertIsInstance(trial, FrameSkills)
        self.assertEqual(trial.installed, {'post_1'})
        self.assertIsNot(trial.installed, skills.installed)
        self.assertFalse(np.shares_memory(trial.data.qpos, skills.data.qpos))
        self.assertEqual(len(trial.registry['sleeve_1']['collision_geom_names']), 4)

    def test_bore_measurement_rejects_lateral_offset_and_short_engagement(self):
        skills = sleeve_cell()
        entry = skills.registry['sleeve_1']
        # Synthetic poses test only the independent check; no assembly is credited.
        qpos = skills.data.joint(part_name(entry['config']) + '_free').qpos
        qpos[:3] = entry['joint']['target_part_center_world_m']
        mujoco.mj_forward(skills.model, skills.data)
        fit = skills.verify_sleeve_fit()
        self.assertAlmostEqual(fit['minimum_bore_side_clearance_mm'], 2.)
        for shift in ([.004, 0., 0.], [0., 0., .020]):
            qpos[:3] = np.asarray(entry['joint']['target_part_center_world_m']) + shift
            mujoco.mj_forward(skills.model, skills.data)
            with self.assertRaises(FailureEvent):
                skills.verify_sleeve_fit()

    def test_rejected_grasp_then_real_insertion_preserves_post(self):
        skills = sleeve_cell()
        test = self

        class Probe(ScriptedPolicy):
            def propose(self, context):
                action = super().propose(context)
                if 'grasp_task' in context:
                    trials = context['grasp_task']['previous_trials']
                    state = [skills.data.qpos, skills.data.qvel, skills.data.ctrl, skills.motion.reference]
                    if not trials:
                        self.before = [v.copy() for v in state]
                    else:
                        for previous, current in zip(self.before, state):
                            np.testing.assert_array_equal(previous, current)
                        test.assertAlmostEqual(skills.data.time, 3.)
                        test.assertEqual(skills.results, [])
                        test.assertEqual(skills.installed, {'post_1'})
                    action['grasp'] = dict(x_mm=0, y_mm=0, z_mm=8 if trials else -13, jaw_yaw_deg=0)
                return action

        state = run_assembly_graph(skills, mode='single', policies={'single': Probe()}, propose_grasp=True,
                                   insert_result_skill='insert_sleeve/v1', task_id='first_sleeve_on_installed_post/v1',
                                   failure_handler=lambda exc: self.fail(str(exc)))
        self.assertEqual(state['status'], 'success', state['error'])
        rejected, accepted = state['grasp_trials']
        self.assertEqual(rejected['failure_code'], 'PLANNED_CONTACT')
        self.assertIn('workcell_tabletop', rejected['violations'][0]['geoms'])
        self.assertTrue(accepted['accepted'])
        self.assertEqual(accepted['grasp'], skills.results[0]['proposed_grasp'])
        self.assertTrue(all(skills.results[-1]['checks'].values()))
        self.assertTrue(state['verification']['checks']['post_through_both_sleeve_openings'])
        self.assertEqual(state['verification']['sleeve_fit'], skills.results[-1]['sleeve_fit'])
        self.assertGreater(skills.results[-1]['sleeve_fit']['minimum_bore_side_clearance_mm'], 0)
        skills.verifier.verify_other_parts(skills.data)
