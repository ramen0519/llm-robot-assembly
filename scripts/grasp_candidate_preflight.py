"""One observed rejection -> bounded candidate -> current-scene copy preflight."""
from copy import deepcopy
import hashlib
import math
from pathlib import Path

from export_grasp_experience import DEFAULT_OUTPUT, CHECKS, digest
from failure_report import json_safe
from generate_grasp_lesson import _write_json
from grasp_skill_candidates import validate_candidate
from grasp_skill_runtime import capture_observation, evaluate_candidate, validate_grasp, validate_observation
from grasp_preflight_trace import run_recorded_preflight


def source_state(skills):
    """Read the full integrator, shared model and executor state without stepping."""
    import mujoco
    import numpy as np
    spec = mujoco.mjtState.mjSTATE_INTEGRATION
    values = np.empty(mujoco.mj_stateSize(skills.model, spec), dtype=np.float64)
    mujoco.mj_getState(skills.model, skills.data, values, spec)
    model_bytes = np.empty(mujoco.mj_sizeModel(skills.model), dtype=np.uint8)
    mujoco.mj_saveModel(skills.model, buffer=model_bytes)
    host = json_safe({'config': skills.config, 'joint': skills.verifier.joint,
        'registry': skills.registry, 'installed': sorted(skills.installed),
        'reference': skills.motion.reference, 'held_pose': skills.motion.held_pose,
        'gripper_command': skills.motion.gripper_command, 'goal': skills.motion.goal,
        'stage': skills.motion.stage, 'results': skills.results, 'plans': skills.motion.plans,
        'transport_attempts': skills.transport_attempts, 'replan_attempts': skills.replan_attempts,
        'empty_approach_attempts': skills.empty_approach_attempts,
        'mating_motion': skills.verifier.mating_motion})
    return {'schema': 'preflight_source_state/v1', 'simulation_time_s': float(skills.data.time),
        'state_spec': int(spec), 'integration_sha256': hashlib.sha256(values.tobytes()).hexdigest(),
        'model_sha256': hashlib.sha256(model_bytes.tobytes()).hexdigest(),
        'host_sha256': digest(host), 'host': host}


def checked_trial(trial, grasp, observation):
    """Never interpret a malformed/mismatched preflight record as acceptance."""
    if (type(trial) is not dict or type(trial.get('accepted')) is not bool
            or trial.get('source') != 'mujoco_preflight_copy' or trial.get('grasp') != grasp
            or type(trial.get('observation_revision')) is not int
            or trial['observation_revision'] != observation['revision']
            or type(trial.get('live_start_s')) not in (int, float)
            or trial['live_start_s'] != observation['simulation_time_s']
            or type(trial.get('copy_end_s')) not in (int, float)
            or not math.isfinite(trial['copy_end_s']) or trial['copy_end_s'] < trial['live_start_s']):
        raise ValueError('Preflight result is not bound to the requested grasp and observation.')
    validate_grasp(trial['grasp'], observation['condition_context']['config']['part']['size_m'])
    if trial['accepted']:
        checks, depth = trial.get('checks'), trial.get('minimum_final_depth_m')
        support = {'socket_floor_support', 'declared_mating_support'} & (set(checks) if type(checks) is dict else set())
        if (type(checks) is not dict or not checks or any(v is not True for v in checks.values())
                or len(support) != 1 or set(checks) != CHECKS | support
                or trial.get('failure_code') or trial.get('phase')
                or type(depth) not in (int, float) or not math.isfinite(depth) or depth <= 0
                or abs(depth - observation['condition_context']['joint']['nominal_insertion_depth_m']) >= .001):
            raise ValueError('Accepted preflight lacks measured verifier evidence.')
    elif (not isinstance(trial.get('failure_code'), str) or not trial['failure_code']
          or not isinstance(trial.get('phase'), str) or not trial['phase']):
        raise ValueError('Rejected preflight needs a code and phase.')
    return trial


def _metric(trial):
    pad = trial.get('held_pad_geometry', {})
    value = pad.get('predicted_lowest_pad_height_relative_to_rim_mm')
    clearance = value if type(value) in (float, int) and math.isfinite(value) else None
    return {'grasp': deepcopy(trial['grasp']), 'accepted': trial['accepted'],
        'pick_completed_in_copy': trial.get('trace_summary', {}).get('pick_completed', False),
        'failure_code': trial.get('failure_code'), 'failure_phase': trial.get('phase'),
        'reported_contact_violations': deepcopy(trial.get('violations', [])),
        'predicted_pad_to_rim_clearance_mm': clearance,
        'held_pad_geometry': deepcopy(trial.get('held_pad_geometry')),
        'copy_elapsed_sim_s': trial['copy_end_s'] - trial['live_start_s'],
        'terminal_checks': deepcopy(trial.get('checks'))}


def compare_trials(baseline, modified):
    a, b = _metric(baseline), _metric(modified)
    ca, cb = a['predicted_pad_to_rim_clearance_mm'], b['predicted_pad_to_rim_clearance_mm']
    delta = None if ca is None or cb is None else cb - ca
    return {'schema': 'post_grasp_preflight_comparison/v1', 'baseline': a, 'modified': b,
        'parameter_delta': {k: modified['grasp'][k] - baseline['grasp'][k] for k in baseline['grasp']},
        'same_source_start_time': baseline['live_start_s'] == modified['live_start_s'],
        'outcome': 'resolved_in_copy_preflight' if modified['accepted'] else 'candidate_still_rejected',
        'same_failure_code_and_phase': (a['failure_code'], a['failure_phase']) == (b['failure_code'], b['failure_phase']),
        'predicted_clearance_delta_mm': delta,
        'clearance_interpretation': 'pick_pose_projection_to_nominal_seating_not_measured_seated_clearance',
        'first_contact_distance_interpretation': 'different_first_detected_path_samples_not_an_improvement_percentage',
        'executed_collision_force_comparison': None,
        'actual_original_scene_assembly_success': False}


def preflight_candidate(skills, candidate, observation, *, current_reader, rejection,
                        remaining_proposals, log_dir, memory_dir=DEFAULT_OUTPUT):
    """Only a fresh, bound VM proposal reaches the existing simulator checker."""
    folder = Path(log_dir)
    folder.mkdir(parents=True, exist_ok=False)
    result = {'status': 'not_run', 'evaluation': None, 'trial': None,
              'original_state_unchanged': None, 'original_scene_assembly_executed': False,
              'automatic_skill_registration': False, 'API_calls': 0}
    before = source_state(skills)
    _write_json(folder / 'source_before.json', before)
    try:
        # Real adapter only. Unit fixtures label their tests as synthetic; this
        # boundary never accepts the offline observation mode from the 22J probe.
        validate_observation(observation)
        if observation['provenance'] != 'mujoco_live_snapshot':
            raise ValueError('Physical preflight requires a live simulation observation.')
        def executor_observation():
            return capture_observation(skills, episode_id=observation['episode_id'],
                                       revision=observation['revision'])
        if executor_observation() != observation:
            raise ValueError('Observation belongs to a different executor scene.')
        validate_candidate(candidate, memory_dir=memory_dir)
        evaluation = evaluate_candidate(candidate, observation, current_reader=current_reader,
            rejection=rejection, remaining_proposals=remaining_proposals, memory_dir=memory_dir)
        result['evaluation'] = evaluation
        _write_json(folder / 'evaluation.json', evaluation)
        if evaluation['status'] != 'proposed':
            result['status'] = 'proposal_not_available'
            return result
        proposal = evaluation['proposal']
        context = observation['condition_context']['config']
        expected = {'candidate_id': candidate['candidate_id'], 'code_sha256': candidate['code_sha256'],
            'observation_id': observation['observation_id'], 'observation_revision': observation['revision'],
            'episode_id': observation['episode_id'], 'part_id': context['part']['id'],
            'target_id': context['target']['id'], 'rejection_id': rejection['rejection_id'],
            'kind': 'post_grasp_proposal', 'requires_current_scene_preflight': True,
            'execution_authorized': False}
        if any(type(proposal.get(k)) is not type(v) or proposal.get(k) != v for k, v in expected.items()):
            raise ValueError('Proposal binding changed before preflight.')
        grasp = validate_grasp(proposal['grasp'], context['part']['size_m'])
        if (current_reader() != observation or executor_observation() != observation
                or source_state(skills) != before):
            raise ValueError('Source scene changed before candidate preflight.')
        _write_json(folder / 'submitted_grasp.json', {'grasp': grasp, **expected})
        trial = run_recorded_preflight(skills, grasp, observation_revision=observation['revision'],
                                       log_dir=folder / 'preflight')
        result['trial'] = trial
        checked_trial(trial, grasp, observation)
        result['status'] = 'preflight_accepted' if trial['accepted'] else 'preflight_rejected'
        return result
    except (Exception, KeyboardInterrupt) as error:
        result['status'] = 'error'
        result['error'] = {'type': type(error).__name__}
        raise
    finally:
        after = source_state(skills)
        result['original_state_unchanged'] = after == before
        _write_json(folder / 'source_after.json', after)
        if not result['original_state_unchanged']:
            result['status'] = 'error'
            result['error'] = {'type': 'SourceStateChanged'}
        _write_json(folder / 'result.json', result)
        if not result['original_state_unchanged']:
            raise ValueError('Copy preflight changed the original scene or executor.')
