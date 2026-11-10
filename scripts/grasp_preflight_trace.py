"""Record the existing copied-scene grasp check without changing its algorithm.

Physical samples are from the disposable preflight scene, never the live scene.
Nominal path rejection snapshots remain separate from those dynamic samples.
No extra step/forward, calibration, planning, or verification call is performed.
"""
from contextlib import ExitStack
import json
import math
import os
from pathlib import Path
import time

import numpy as np

from failure_report import FailureEvent, json_safe, utc_now
from frame_assembly_skills import FrameSkills

SCHEMA = 'recorded_grasp_preflight/v1'
METRICS = ('arm_speed_rad_s', 'robot_speed_rad_s', 'tracking_error_rad',
    'part_speed_m_s', 'part_angular_speed_rad_s', 'target_position_error_m',
    'target_orientation_error_rad', 'tcp_position_error_m', 'tcp_orientation_error_rad',
    'relative_slip_m', 'relative_rotation_rad', 'penetration_m',
    'fixture_contact_force_n', 'insertion_depth_m', 'part_bottom_clearance_m', 'pad_gap_mm')


def _finite(value):
    if type(value) in (int, float) and math.isfinite(value):
        return float(value)
    return None


def _clock(skills):
    return _finite(float(skills.data.time))


def _json(value):
    return json.dumps(json_safe(value), ensure_ascii=False, allow_nan=False)


def _write(path, value):
    with Path(path).open('x', encoding='utf-8', newline='\n') as stream:
        stream.write(_json(value) + '\n')
        stream.flush()
        os.fsync(stream.fileno())


def _failure_fields(error):
    code = getattr(error, 'code', None)
    return {'error_type': type(error).__name__, 'failure_code': getattr(code, 'value', None),
            'phase': getattr(error, 'phase', None),
            'violations': json_safe(getattr(error, 'violations', [])),
            'candidate': json_safe(getattr(error, 'candidate', None)),
            'kinematics_fresh': getattr(error, 'kinematics_fresh', None)}


def _pad_geometry(trial):
    """Read box-pad projections, not signed collision distances or force estimates."""
    try:
        body = trial.data.body(trial.verifier.part_body)
        part_center = np.asarray(body.xpos)
        part_axis = np.asarray(body.xmat).reshape(3, 3)[:, 2]
        part_half_height = trial.config['part']['size_m'][2] / 2
        pads = []
        for geom_id, name in trial.verifier.pads.items():
            geom = trial.data.geom(geom_id)
            position, rotation = np.asarray(geom.xpos), np.asarray(geom.xmat).reshape(3, 3)
            size = np.asarray(trial.model.geom_size[geom_id])
            bottom_world = float(position[2] - np.abs(rotation[2]) @ size)
            bottom_local = float((position - part_center) @ part_axis + part_half_height
                                 - np.abs(rotation.T @ part_axis) @ size)
            pads.append({'geom_id': int(geom_id), 'geom_name': name,
                         'center_world_m': position.copy(), 'rotation_world': rotation.copy(),
                         'model_geom_size_m': size.copy(), 'lowest_world_z_m': bottom_world,
                         'lowest_above_part_bottom_local_z_m': bottom_local})
        if not pads:
            raise ValueError('No pad geometry.')
        low = min(p['lowest_world_z_m'] for p in pads)
        local_low = min(p['lowest_above_part_bottom_local_z_m'] for p in pads)
        rim = trial.verifier.joint.get('socket_rim_z_world_m')
        metrics = {'lowest_pad_world_z_m': low,
                   'lowest_pad_height_over_table_plane_m': low - trial.verifier.table_z,
                   'lowest_pad_height_over_rim_plane_m': None if rim is None else low - rim,
                   'lowest_pad_above_part_bottom_local_z_m': local_low}
        return {'status': 'read', 'pads': pads, 'metrics': metrics,
                'holding_part': trial.motion.held_pose is not None,
                'meaning': 'Box-pad extent projections. Plane heights are not collision clearance; '
                           'no current-scene safety or seated-pose validation is inferred.'}
    except (AttributeError, KeyError, IndexError, TypeError, ValueError) as error:
        return {'status': 'unavailable', 'pads': [], 'metrics': {},
                'error_type': type(error).__name__}


def _contacts(trial):
    distances = [float(c.dist) for c in trial.data.contact]
    finite = [value for value in distances if math.isfinite(value)]
    return {'contact_count': len(distances),
            'minimum_contact_signed_distance_m': min(finite) if finite else None,
            'nonfinite_distance_count': len(distances) - len(finite),
            'force_measurement': 'not_computed_by_trace'}


def _accumulate(target, values):
    for key, value in values.items():
        number = _finite(value)
        if number is None:
            continue
        entry = target.setdefault(key, {'minimum': number, 'maximum': number, 'samples': 0})
        entry['minimum'], entry['maximum'] = min(entry['minimum'], number), max(entry['maximum'], number)
        entry['samples'] += 1


class _Trace:
    def __init__(self, folder, skills, grasp, revision):
        self.folder, self.skills, self.revision = Path(folder).resolve(), skills, revision
        self.trial, self.original_callbacks = None, None
        self.stages, self.current, self.failures = [], None, []
        self.recording_errors = []
        self.sample_count, self.metrics = 0, {}
        self.started_wall = time.monotonic()
        self.on_event = getattr(skills, 'preflight_trace_on_event', None)
        self.paths = {name: str(self.folder / filename) for name, filename in {
            'started': 'started.json', 'samples': 'samples.jsonl', 'stages': 'stages.jsonl',
            'pad_geometry': 'pad_geometry.jsonl', 'failure_events': 'failure_events.jsonl',
            'trial_results': 'trial_results.json', 'motion_plans': 'motion_plans.json',
            'check_failure': 'check_failure.json', 'summary': 'trace_summary.json',
            'terminal': 'terminal.json'}.items()}
        self.folder.mkdir(parents=True, exist_ok=True)
        # This marker is exclusive and also makes interrupted directories non-reusable.
        _write(self.paths['started'], {'schema': SCHEMA, 'event': 'started', 'timestamp_utc': utc_now(),
            'observation_revision': revision, 'grasp': grasp, 'active_part': skills.active,
            'live_start_s': _clock(skills), 'measurement_domain': 'mujoco_preflight_copy',
            'live_execution_authorized': False})
        self.streams = ExitStack()
        try:
            self.files = {name: self.streams.enter_context(Path(self.paths[name]).open(
                'x', encoding='utf-8', newline='\n'))
                for name in ('samples', 'stages', 'pad_geometry', 'failure_events')}
        except BaseException:
            self.streams.close()
            raise

    def append(self, name, value, *, flush=False):
        stream = self.files[name]
        try:
            stream.write(_json(value) + '\n')
            if flush:
                stream.flush()
        except (Exception, KeyboardInterrupt) as error:
            self.recording_errors.append({'operation': 'write_' + name,
                                          'error_type': type(error).__name__})
            raise

    def geometry(self, reason):
        if self.trial is None:
            return
        self.append('pad_geometry', {'reason': reason, 'stage': self.trial.motion.stage,
            'copy_current_time_s': _clock(self.trial),
            'source': 'copy_current_geometry_read_not_a_failure_candidate',
            'validated_physical_sample': False, 'geometry': _pad_geometry(self.trial)}, flush=True)

    def stage(self, name):
        now, wall = _clock(self.trial), time.monotonic() - self.started_wall
        if self.current is not None:
            self.current.update(copy_stage_end_s=now, wall_end_s=wall)
        self.current = {'index': len(self.stages), 'name': str(name),
            'copy_stage_start_s': now, 'copy_stage_end_s': None,
            'wall_start_s': wall, 'wall_end_s': None, 'sample_count': 0, 'physical_metrics': {}}
        self.stages.append(self.current)
        event = {'event': 'stage_started', 'stage_index': self.current['index'],
                 'stage': str(name), 'copy_current_time_s': now, 'wall_elapsed_s': wall}
        self.append('stages', event, flush=True)
        self.files['samples'].flush()
        self.geometry('stage_boundary')
        if self.on_event is not None:
            self.on_event(dict(event))

    def sample(self, row):
        # WorkcellMotion invokes this only AFTER its current-state verifiers return.
        # Initial/hold samples may share a time: count is not a simulation step count.
        values = json_safe(row)
        name = values.get('stage', self.trial.motion.stage)
        if self.current is None or self.current['name'] != name:
            self.stage(name)
        geometry, contacts = _pad_geometry(self.trial), _contacts(self.trial)
        self.sample_count += 1
        self.current['sample_count'] += 1
        metrics = {key: values.get(key) for key in METRICS}
        metrics.update(geometry['metrics'])
        metrics['minimum_contact_signed_distance_m'] = contacts['minimum_contact_signed_distance_m']
        _accumulate(self.metrics, metrics)
        _accumulate(self.current['physical_metrics'], metrics)
        self.append('samples', {'index': self.sample_count, 'stage_index': self.current['index'],
            'source': 'mujoco_preflight_copy_dynamic_measurement', 'values': values,
            'pad_metrics': geometry['metrics'], 'pad_geometry_status': geometry['status'],
            'contacts': contacts}, flush=self.sample_count % 100 == 0)

    def detector_failure(self, error):
        facts = _failure_fields(error)
        candidate = facts['candidate']
        event = {'event': 'motion_plan_detector_failure',
            'stage': self.trial.motion.stage, 'copy_current_time_s_at_detection': _clock(self.trial),
            'candidate_source': candidate.get('source') if isinstance(candidate, dict) else None,
            'physical_contact_force_from_candidate': None, **facts}
        self.append('failure_events', event, flush=True)
        self.failures.append(event)

    def attach(self, trial):
        if self.trial is not None:
            raise RuntimeError('One check must create at most one instrumented trial.')
        self.trial = trial
        previous_stage, previous_sample, previous_plan = trial.on_stage, trial.motion.on_sample, trial.motion.plan
        self.original_callbacks = previous_stage, previous_sample, previous_plan

        def stage(name):
            self.stage(name)
            if previous_stage is not None:
                previous_stage(name)

        def sample(row):
            self.sample(row)
            if previous_sample is not None:
                previous_sample(row)

        def plan(*args, **kwargs):
            try:
                return previous_plan(*args, **kwargs)
            except FailureEvent as error:
                # Preserve the original nominal/IK candidate before check_grasp discards it.
                error_count = len(self.recording_errors)
                try:
                    self.detector_failure(error)
                except (Exception, KeyboardInterrupt) as recording_error:
                    if len(self.recording_errors) == error_count:
                        self.recording_errors.append({'operation': 'motion_plan_detector_failure',
                                                      'error_type': type(recording_error).__name__})
                    error.add_note('Trace write failed: ' + type(recording_error).__name__)
                raise

        trial.on_stage, trial.motion.on_sample, trial.motion.plan = stage, sample, plan
        self.geometry('trial_created')

    def finish(self, outcome=None, error=None):
        trial = self.trial
        if self.current is not None:
            self.current.update(copy_stage_end_s=_clock(trial),
                                wall_end_s=time.monotonic() - self.started_wall)
        self.geometry('terminal_current_copy_state_not_failure_candidate')
        results = [] if trial is None else json_safe(trial.results)
        plans = [] if trial is None else json_safe(trial.motion.plans)
        _write(self.paths['trial_results'], {'source': 'completed_skills_in_preflight_copy', 'results': results})
        _write(self.paths['motion_plans'], {'source': 'preflight_copy_motion_executor_entry', 'plans': plans,
            'meaning': 'Entries are appended at executor entry, not completion. checked_samples are '
                       'nominal geometric checks, not executed physics or force measurements.'})
        failure = None
        if error is not None:
            failure = {'source': 'raised_check_exception', **_failure_fields(error)}
        elif outcome is not None and not outcome.get('accepted'):
            failure = {'source': 'returned_check_rejection', 'outcome': json_safe(outcome)}
        _write(self.paths['check_failure'], failure)
        summary = {'schema': SCHEMA, 'measurement_domain': 'mujoco_preflight_copy',
            'trace_complete': not self.recording_errors, 'recording_errors': list(self.recording_errors),
            'observation_revision': self.revision, 'trial_created': trial is not None,
            'sample_count': self.sample_count, 'physical_metrics': self.metrics, 'stages': self.stages,
            'copy_end_s': None if trial is None else _clock(trial),
            'wall_elapsed_s': time.monotonic() - self.started_wall,
            'completed_skill_result_count': len(results), 'motion_executor_entries': len(plans),
            'completed_skills': [{'skill': item.get('skill'), 'status': item.get('status')}
                                 for item in results],
            'pick_completed': any(item.get('skill') == 'pick/v1' and item.get('status') == 'success'
                                  for item in results),
            'captured_plan_detector_failures': len(self.failures),
            'held_pad_geometry': None if outcome is None else outcome.get('held_pad_geometry'),
            'accepted': None if outcome is None else outcome.get('accepted'),
            'limitations': ['No live-scene execution is recorded here.',
                'Samples appear only after existing verifiers return; a failed physical sample may be absent.',
                'Sample count includes repeated observations at the same simulation time.',
                'Nominal/IK candidate snapshots are separate from dynamic measurements.',
                'Pad plane heights/projections are not collision clearances or verified seated poses.',
                'First forbidden-contact signed distance is not an improvement-rate metric.']}
        _write(self.paths['summary'], summary)
        terminal = {'schema': SCHEMA, 'event': 'terminal', 'timestamp_utc': utc_now(),
                    'status': 'raised' if error is not None else 'returned',
                    'outcome': outcome, 'error': None if error is None else _failure_fields(error),
                    'trace_summary': summary, 'trace_paths': self.paths}
        _write(self.paths['terminal'], terminal)
        return json_safe(summary)

    def close(self):
        if self.trial is not None and self.original_callbacks is not None:
            stage, sample, plan = self.original_callbacks
            self.trial.on_stage, self.trial.motion.on_sample, self.trial.motion.plan = stage, sample, plan
        self.streams.close()


class RecordedFrameSkills(FrameSkills):
    """Same constructor/robot behavior; record only when run_recorded_preflight is active."""
    def _grasp_trial_copy(self):
        trial = super()._grasp_trial_copy()
        recorder = getattr(self, '_preflight_trace_session', None)
        if recorder is not None:
            recorder.attach(trial)
        return trial


def run_recorded_preflight(skills, grasp, *, observation_revision, log_dir):
    """Call the original check exactly once and retain its outcome/exception semantics."""
    if not isinstance(skills, RecordedFrameSkills):
        raise TypeError('RecordedFrameSkills is required to observe the trial copy.')
    if type(observation_revision) is not int or observation_revision < 0:
        raise ValueError('A nonnegative integer observation revision is required.')
    if getattr(skills, '_preflight_trace_session', None) is not None:
        raise RuntimeError('A preflight trace is already active on these skills.')
    trace = _Trace(log_dir, skills, grasp, observation_revision)
    skills._preflight_trace_session = trace
    raised_error = None
    try:
        outcome = skills.check_grasp(grasp)
    except BaseException as error:
        raised_error = error
        try:
            trace.finish(error=error)
        except (Exception, KeyboardInterrupt) as recording_error:
            error.add_note('Trace finalization failed: ' + type(recording_error).__name__)
        raise
    else:
        summary = trace.finish(outcome=outcome)
        if not summary['trace_complete']:
            raised_error = RuntimeError('Preflight trace is incomplete; see terminal recording_errors.')
            raise raised_error
        return {**outcome, 'observation_revision': observation_revision,
                'trace_summary': summary, 'trace_paths': dict(trace.paths)}
    finally:
        skills._preflight_trace_session = None
        try:
            trace.close()
        except BaseException as close_error:
            if raised_error is None:
                raise
            raised_error.add_note('Trace close failed: ' + type(close_error).__name__)
