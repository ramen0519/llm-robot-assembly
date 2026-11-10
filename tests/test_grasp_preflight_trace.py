"""Trace instrumentation contracts with synthetic callbacks; no dynamics or API."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
for candidate in (HERE.parent / 'scripts', HERE):
    if candidate.is_dir():
        sys.path.insert(0, str(candidate))

import grasp_preflight_trace as trace
from failure_report import FailureCode, FailureEvent
from frame_assembly_skills import FrameSkills


class FixtureSkills(trace.RecordedFrameSkills):
    """A detector/control-flow fixture; constructing it creates no MuJoCo scene."""
    def __init__(self, mode='reject', failure=None):
        self.active, self.data = 'post_1', SimpleNamespace(time=0.)
        self.mode, self.failure, self.calls, self.caught = mode, failure, 0, None
        self.after_failure = None

    def check_grasp(self, grasp):
        self.calls += 1
        outcome = {'grasp': dict(grasp), 'accepted': False, 'source': 'mujoco_preflight_copy',
                   'live_start_s': 0.}
        if self.mode == 'bounds':
            return {**outcome, 'phase': 'grasp_bounds',
                    'failure_code': 'INPUT_OR_PRECONDITION_INVALID'}
        trial = self._grasp_trial_copy()
        trial.motion.stage = 'insert_lower'
        trial.on_stage('insert_lower')
        row = {'stage': 'insert_lower', 'simulation_time_s': 2.,
               'penetration_m': .0002, 'fixture_contact_force_n': .3}
        self.original_row = deepcopy(row)
        trial.motion.on_sample(row)
        self.returned_row = row
        if self.mode == 'fatal':
            raise self.failure
        try:
            trial.motion.plan([0, 0, 0])
        except FailureEvent as error:
            self.caught = error
            if self.after_failure is not None:
                self.after_failure(error)
            return {**outcome, 'copy_end_s': 2., 'phase': error.phase,
                    'failure_code': error.code.value, 'violations': error.violations}
        raise AssertionError('Fixture planner must reject.')


class GraspPreflightTraceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.callbacks = []
        self.on_sample = lambda row: self.callbacks.append(('sample', deepcopy(row)))
        self.on_stage = lambda stage: self.callbacks.append(('stage', stage))
        self.rejection = FailureEvent(FailureCode.PLANNED_CONTACT, 'Synthetic rejection.',
            phase='insert_lower/preflight', violations=[{'signed_distance_m': -.001}],
            candidate={'source': 'nominal_path_candidate', 'simulation_time_s': None, 'qpos': [999.]})

        def plan(*args, **kwargs):
            raise self.rejection

        self.plan = plan
        self.trial = SimpleNamespace(data=SimpleNamespace(time=2., contact=[SimpleNamespace(dist=-.0002)]),
            on_stage=self.on_stage, results=[{'skill': 'pick/v1', 'status': 'success'}],
            motion=SimpleNamespace(stage='initialise', on_sample=self.on_sample, plan=self.plan,
                plans=[{'stage': 'pick', 'checked_samples': 10}], held_pose=None))
        self.factory = self.enterContext(patch.object(FrameSkills, '_grasp_trial_copy', return_value=self.trial))
        self.enterContext(patch.object(trace, '_pad_geometry', return_value={
            'status': 'read', 'pads': [], 'metrics': {'lowest_pad_height_over_rim_plane_m': .003},
            'holding_part': True}))
        self.grasp = {'x_mm': 0., 'y_mm': 0., 'z_mm': 0., 'jaw_yaw_deg': 0.}

    @staticmethod
    def read(path):
        return json.loads(Path(path).read_text(encoding='utf-8'))

    def run_trace(self, skills, name='trial'):
        return trace.run_recorded_preflight(skills, self.grasp,
                                           observation_revision=7, log_dir=self.root / name)

    def assert_restored(self):
        self.assertIs(self.trial.on_stage, self.on_stage)
        self.assertIs(self.trial.motion.on_sample, self.on_sample)
        self.assertIs(self.trial.motion.plan, self.plan)

    def test_one_check_preserves_callbacks_rows_and_completed_skill_evidence(self):
        skills = FixtureSkills()
        outcome = self.run_trace(skills)
        self.assertEqual(skills.calls, 1)
        self.factory.assert_called_once()
        self.assertEqual(skills.returned_row, skills.original_row)
        self.assertEqual(self.callbacks, [('stage', 'insert_lower'), ('sample', skills.original_row)])
        self.assert_restored()
        self.assertIsNone(skills._preflight_trace_session)
        self.assertEqual(outcome['observation_revision'], 7)
        summary = outcome['trace_summary']
        self.assertTrue(summary['trace_complete'])
        self.assertEqual(summary['sample_count'], 1)
        self.assertTrue(summary['pick_completed'])
        self.assertEqual(summary['completed_skills'], [{'skill': 'pick/v1', 'status': 'success'}])
        self.assertEqual(summary['physical_metrics']['penetration_m']['maximum'], .0002)
        self.assertTrue(Path(outcome['trace_paths']['started']).is_file())
        self.assertEqual(self.read(outcome['trace_paths']['terminal'])['status'], 'returned')

    def test_planned_failure_retains_original_snapshot_separate_from_physical_metrics(self):
        skills = FixtureSkills()
        skills.after_failure = lambda error: error.candidate['qpos'].__setitem__(0, 111.)
        outcome = self.run_trace(skills)
        self.assertIs(skills.caught, self.rejection)
        event = self.read(outcome['trace_paths']['failure_events'])
        self.assertEqual(event['candidate']['qpos'], [999.])
        self.assertEqual(self.rejection.candidate['qpos'], [111.])
        self.assertEqual(event['candidate_source'], 'nominal_path_candidate')
        self.assertIsNone(event['candidate']['simulation_time_s'])
        self.assertIsNone(event['physical_contact_force_from_candidate'])
        summary = outcome['trace_summary']
        self.assertEqual(summary['captured_plan_detector_failures'], 1)
        self.assertEqual(summary['physical_metrics']['fixture_contact_force_n']['maximum'], .3)
        self.assertEqual(summary['physical_metrics']['minimum_contact_signed_distance_m']['minimum'], -.0002)

    def test_fatal_exception_identity_and_terminal_survive_trace_close_failure(self):
        fatal = FailureEvent(FailureCode.MODEL_OR_CONTROL_INVALID, 'Synthetic fatal detector.')
        skills = FixtureSkills('fatal', fatal)
        original_close = trace._Trace.close

        def broken_close(recorder):
            original_close(recorder)
            raise OSError('Synthetic close failure.')

        with patch.object(trace._Trace, 'close', broken_close):
            with self.assertRaises(FailureEvent) as caught:
                self.run_trace(skills)
        self.assertIs(caught.exception, fatal)
        terminal = self.read(self.root / 'trial/terminal.json')
        self.assertEqual(terminal['status'], 'raised')
        self.assertEqual(terminal['error']['failure_code'], 'MODEL_OR_CONTROL_INVALID')
        self.assert_restored()
        self.assertIsNone(skills._preflight_trace_session)

    def test_existing_log_directory_cannot_trigger_a_second_check(self):
        skills = FixtureSkills()
        self.run_trace(skills)
        with self.assertRaises(FileExistsError):
            self.run_trace(skills)
        self.assertEqual(skills.calls, 1)
        self.factory.assert_called_once()

    def test_bounds_rejection_records_no_trial_and_no_physical_samples(self):
        outcome = self.run_trace(FixtureSkills('bounds'))
        self.factory.assert_not_called()
        self.assertFalse(outcome['trace_summary']['trial_created'])
        self.assertEqual(outcome['trace_summary']['sample_count'], 0)
        self.assertEqual(outcome['trace_summary']['physical_metrics'], {})
        self.assertEqual(Path(outcome['trace_paths']['samples']).read_text(encoding='utf-8'), '')
        self.assertEqual(self.read(outcome['trace_paths']['terminal'])['outcome']['phase'], 'grasp_bounds')

    def test_lost_detector_record_makes_trace_incomplete_and_prevents_completed_return(self):
        skills = FixtureSkills()
        original_append = trace._Trace.append

        def fail_detector_write(recorder, name, value, **kwargs):
            if name == 'failure_events':
                raise OSError('Synthetic detector journal failure.')
            return original_append(recorder, name, value, **kwargs)

        with patch.object(trace._Trace, 'append', fail_detector_write):
            with self.assertRaisesRegex(RuntimeError, 'trace is incomplete'):
                self.run_trace(skills)
        self.assertIs(skills.caught, self.rejection)
        terminal = self.read(self.root / 'trial/terminal.json')
        summary = terminal['trace_summary']
        self.assertFalse(summary['trace_complete'])
        self.assertEqual(summary['captured_plan_detector_failures'], 0)
        self.assertEqual(summary['recording_errors'], [
            {'operation': 'motion_plan_detector_failure', 'error_type': 'OSError'}])
        self.assert_restored()


if __name__ == '__main__':
    unittest.main()
