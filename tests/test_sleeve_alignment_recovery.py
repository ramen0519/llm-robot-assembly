from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from assembly_graph import run_assembly_graph
from assembly_policy import ScriptedPolicy
from failure_report import FailureCode, FailureEvent, json_safe
from openai_policy import compact_context
from run_component_probe import execute_trial
from test_sleeve_grasp import sleeve_cell


class SleeveAlignmentRecoveryTests(unittest.TestCase):
    def test_offset_requires_graph_and_cannot_silently_run_scripted_nominal(self):
        with self.assertRaises(ValueError):
            execute_trial(scenario='alignment_offset')

    def test_stop_after_actual_offset_preserves_grasp_and_does_not_lower(self):
        skills = sleeve_cell()
        stages = []
        skills.on_stage = stages.append
        packets = []

        class StopOnFailure(ScriptedPolicy):
            def propose(self, context):
                action = super().propose(context)
                if 'grasp_task' in context:
                    action['grasp'] = dict(x_mm=0, y_mm=0, z_mm=8, jaw_yaw_deg=0)
                if context['failure']:
                    packets.append(compact_context(context))
                    action.update(skill='stop', reason='Exercise the terminal stop after a measured misalignment.')
                return action

        def report(exc):
            self.assertIsInstance(exc, FailureEvent)
            self.assertEqual(exc.code, FailureCode.ALIGNMENT_OUT_OF_TOLERANCE)
            return {'failure_code': exc.code.value, 'phase': skills.motion.stage, 'message': str(exc),
                    'simulation_time_s': float(skills.data.time), 'violations': json_safe(exc.violations), 'status': 'failed'}

        state = run_assembly_graph(skills, mode='single', policies={'single': StopOnFailure()}, propose_grasp=True,
                                   task_id='first_sleeve_on_installed_post/v1', insert_result_skill='insert_sleeve/v1',
                                   scenario='alignment_offset', failure_handler=report)
        self.assertEqual(state['status'], 'stopped')
        self.assertEqual(state['completed'], ['pick'])
        self.assertEqual(state['recoveries'], 0)
        self.assertEqual(len(packets), 1)
        packet = packets[0]
        measured = packet['alignment_observation']
        self.assertEqual(packet['allowed_skills'], ['recover_alignment', 'stop'])
        self.assertAlmostEqual(measured['actual_minus_target_xy_m'][0], .006, delta=.0005)
        self.assertGreater(measured['rim_clearance_m'], .020)
        self.assertTrue(measured['left_contact'] and measured['right_contact'])
        self.assertFalse(measured['supported'])
        self.assertEqual(measured['simulation_time_s'], packet['observation']['simulation_time_s'])
        self.assertEqual(measured['simulation_time_s'], skills.data.time)
        self.assertAlmostEqual(measured['lateral_error_m'], packet['failure']['violations'][0]['lateral_error_m'])
        self.assertNotIn('insert_lower', stages)
        self.assertNotIn('insert_release', stages)
        self.assertIsNotNone(skills.motion.held_pose)
        self.assertTrue(state['grasp_trials'][-1]['accepted'])
