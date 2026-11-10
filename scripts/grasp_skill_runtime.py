"""Host-owned observation gates and proposal validation; no robot execution."""
from copy import deepcopy
import math
import re

from export_grasp_experience import DEFAULT_OUTPUT, canonical, digest, _post_contexts
from grasp_skill_candidates import GRASP_FIELDS, validate_candidate
from grasp_skill_vm import execute_source
from retrieve_grasp_experience import CONVENTIONS, _features

OBS_SCHEMA = 'post_grasp_observation/v1'
REJECTION_SCHEMA = 'post_grasp_rejection_binding/v1'
BOOL_MEASURES = ('supported', 'holding_part', 'left_contact', 'right_contact')
NUM_MEASURES = ('upright_cosine', 'linear_speed_m_s', 'angular_speed_rad_s', 'pad_gap_mm')
CONTEXT_KEYS = {'config', 'joint', 'active_part_condition_id', *CONVENTIONS}


def _seal(body, key):
    return {key: digest(body), **deepcopy(body)}


def _sealed(value, key, schema):
    if (type(value) is not dict or value.get('schema') != schema
            or value.get(key) != digest({k: v for k, v in value.items() if k != key})):
        raise ValueError('Modified or unsupported observation record.')


def _number(value):
    return type(value) in (int, float) and abs(value) <= 1e6 and math.isfinite(value)


def _context(context):
    result = {k: deepcopy(context[k]) for k in CONTEXT_KEYS}
    features = _features(result)
    if features['signature'] != {'robot': 'ur3e', 'gripper': 'robotiq_2f85', **CONVENTIONS}:
        raise ValueError('Unsupported robot or coordinate conventions.')
    return result


def make_observation(*, episode_id, revision, condition_context, simulator_version,
                     simulation_time_s, measurements, state_fingerprint,
                     provenance='offline_fixture', installed_parts=None):
    body = {'schema': OBS_SCHEMA, 'episode_id': episode_id, 'revision': revision,
        'environment': 'simulation', 'provenance': provenance,
        'observation_source': 'sim_ground_truth' if provenance == 'mujoco_live_snapshot' else 'offline_fixture',
        'condition_context': _context(condition_context), 'simulator_version': simulator_version,
        'simulation_time_s': simulation_time_s, 'measurements': deepcopy(measurements),
        'state_fingerprint': state_fingerprint, 'installed_parts': deepcopy([] if installed_parts is None else installed_parts),
        'stability_evidence': 'instantaneous_measurement_not_settling_window'}
    value = _seal(body, 'observation_id')
    validate_observation(value)
    return value


def validate_observation(obs):
    _sealed(obs, 'observation_id', OBS_SCHEMA)
    if (obs['environment'] != 'simulation' or obs['provenance'] not in ('offline_fixture', 'mujoco_live_snapshot')
            or obs['observation_source'] != ('sim_ground_truth' if obs['provenance'] == 'mujoco_live_snapshot' else 'offline_fixture')
            or not isinstance(obs['episode_id'], str) or not 0 < len(obs['episode_id']) <= 128
            or type(obs['revision']) is not int or not 1 <= obs['revision'] <= 1000000
            or not _number(obs['simulation_time_s']) or obs['simulation_time_s'] < 0
            or not isinstance(obs['simulator_version'], str) or not 0 < len(obs['simulator_version']) <= 128
            or not isinstance(obs['state_fingerprint'], str)
            or not re.fullmatch('[0-9a-f]{64}', obs['state_fingerprint'])
            or obs['stability_evidence'] != 'instantaneous_measurement_not_settling_window'):
        raise ValueError('Invalid simulation observation metadata.')
    m = obs['measurements']
    if (type(m) is not dict or set(m) != set(BOOL_MEASURES + NUM_MEASURES)
            or any(type(m[k]) is not bool for k in BOOL_MEASURES)
            or any(not _number(m[k]) for k in NUM_MEASURES)
            or not -1 <= m['upright_cosine'] <= 1
            or any(m[k] < 0 for k in NUM_MEASURES if k != 'upright_cosine')):
        raise ValueError('Measurements need exact booleans and bounded finite numbers.')
    installed = obs['installed_parts']
    if (type(installed) is not list or len(installed) > 10
            or any(not isinstance(p, str) or not p or len(p) > 80 for p in installed)
            or installed != sorted(set(installed))):
        raise ValueError('Installed parts must be unique sorted identifiers.')
    context = _context(obs['condition_context'])
    if context != obs['condition_context']:
        raise ValueError('Unexpected active-part context fields.')
    expected = digest({'config': context['config'], 'joint': context['joint'],
                       'simulator_version': obs['simulator_version']})
    if context['active_part_condition_id'] != expected:
        raise ValueError('Active-part condition fingerprint is inconsistent.')
    canonical(obs)


def validate_grasp(grasp, size_m):
    """Domain output gate, including bool/NaN rejection; no clipping or correction."""
    if type(grasp) is not dict or set(grasp) != set(GRASP_FIELDS) or any(not _number(v) for v in grasp.values()):
        raise ValueError('Expected exactly four finite numeric grasp values, not booleans.')
    limits = [*(x * 500 for x in size_m), 180]
    if any(abs(grasp[k]) > limit for k, limit in zip(GRASP_FIELDS, limits)):
        raise ValueError('Grasp outside current part bounds or yaw range.')
    return deepcopy(grasp)


def bind_rejection(observation, trial):
    """Called by the host when a preflight returns; never by generated code."""
    validate_observation(observation)
    expected_source = 'mujoco_preflight_copy' if observation['provenance'] == 'mujoco_live_snapshot' else 'offline_fixture'
    if (type(trial) is not dict or trial.get('source') != expected_source or trial.get('accepted') is not False
            or type(trial.get('observation_revision')) is not int
            or trial['observation_revision'] != observation['revision']
            or not _number(trial.get('live_start_s')) or trial['live_start_s'] != observation['simulation_time_s']
            or not isinstance(trial.get('failure_code'), str) or not re.fullmatch('[A-Z][A-Z0-9_]{0,63}', trial['failure_code'])
            or not isinstance(trial.get('phase'), str) or not 0 < len(trial['phase']) <= 128):
        raise ValueError('Need a rejected trial bound to this observation and provenance.')
    validate_grasp(trial.get('grasp'), observation['condition_context']['config']['part']['size_m'])
    return _seal({'schema': REJECTION_SCHEMA, 'source_observation': deepcopy(observation),
                  'trial': deepcopy(trial), 'purpose': 'proposal_correction_only'}, 'rejection_id')


def _state_identity(obs):
    # A new observe call may increment revision without changing the physics.
    return {k: v for k, v in obs.items() if k not in ('observation_id', 'revision')}


def _scope_matches(candidate, obs):
    matches = []
    for case in candidate['input_packet']['lesson']['evidence_packet']['cases']:
        conditions = case['source_conditions']
        installed = conditions.get('part_episode_start_installed')
        if (installed is None or sorted(installed) != obs['installed_parts']
                or conditions.get('simulator_version') != obs['simulator_version']):
            continue
        if any(_context(c) == obs['condition_context'] for c in case['contexts']):
            matches.append(case['case_id'])
    return matches


def evaluate_candidate(candidate, observation, *, current_reader, rejection,
                       remaining_proposals, memory_dir=DEFAULT_OUTPUT,
                       max_steps=1000, timeout_s=3.0):
    """Proposal-only calculation; current_reader is a trusted host callback.

    The host serializes scene access and owns revisions, failure bindings and
    budget. Fingerprints detect changes, not authenticity of caller-supplied data.
    No return value from this module authorizes robot or preflight execution.
    """
    result = {'schema': 'post_grasp_candidate_evaluation/v1', 'status': 'rejected',
        'reason': None, 'proposal': None, 'vm': None, 'context': None,
        'candidate_id': candidate.get('candidate_id'), 'code_sha256': candidate.get('code_sha256'),
        'observation_id': observation.get('observation_id'), 'matched_case_ids': [],
        'API_calls': 0, 'robot_commands': 0, 'execution_authorized': False,
        'automatic_skill_registration': False, 'physics_validated': False,
        'evaluation_provenance': observation.get('provenance')}
    stage = 'candidate_validation'
    try:
        validate_candidate(candidate, memory_dir=memory_dir)
        stage = 'observation_validation'
        validate_observation(observation)
        stage = 'current_observation_read'
        fresh = current_reader()
        validate_observation(fresh)
        if fresh != observation:
            result['reason'] = 'stale_observation'
            return result
        if type(remaining_proposals) is not int or not 0 <= remaining_proposals <= 3:
            raise ValueError('Host proposal budget must be an integer from zero to three.')
        if remaining_proposals == 0:
            result.update(status='abstained', reason='proposal_budget_exhausted')
            return result
        context = observation['condition_context']
        size = context['config']['part']['size_m']
        if not (.02 <= size[0] <= .06 and .02 <= size[1] <= .06 and .03 <= size[2] <= .08):
            result['reason'] = 'unsupported_post_dimensions'
            return result
        matches = _scope_matches(candidate, observation)
        if not matches:
            result['reason'] = 'outside_recorded_conditions'
            return result
        result['matched_case_ids'] = matches
        m = observation['measurements']
        blocks = [('holding_part', m['holding_part'] or m['left_contact'] or m['right_contact']),
            ('part_not_supported', not m['supported']),
            ('part_not_upright', m['upright_cosine'] < math.cos(math.radians(1))),
            ('part_not_stationary', m['linear_speed_m_s'] >= .001 or m['angular_speed_rad_s'] >= .01),
            ('gripper_not_open', m['pad_gap_mm'] < 80)]
        for reason, blocked in blocks:
            if blocked:
                result.update(status='abstained', reason=reason)
                return result
        stage = 'rejection_validation'
        if rejection is None:
            result.update(status='abstained', reason='no_current_rejection')
            return result
        _sealed(rejection, 'rejection_id', REJECTION_SCHEMA)
        old = rejection['source_observation']
        if bind_rejection(old, rejection['trial']) != rejection:
            raise ValueError('Modified rejection binding.')
        if old['revision'] > observation['revision'] or _state_identity(old) != _state_identity(observation):
            result['reason'] = 'rejection_from_different_state'
            return result
        trial = rejection['trial']
        query = candidate['input_packet']['lesson']['evidence_packet']['search']['query']
        if trial['failure_code'] != query['failure_code'] or trial['phase'] != query['failure_phase']:
            result.update(status='abstained', reason='failure_context_mismatch')
            return result
        ctx = {'part_size_xyz_mm': [v * 1000 for v in size],
            'previous_grasp': deepcopy(trial['grasp']), 'failure_code': trial['failure_code'],
            'failure_phase': trial['phase'], 'holding_part': False,
            'observation_is_current': True, 'remaining_proposals': remaining_proposals}
        result['context'] = deepcopy(ctx)
        stage = 'bounded_calculation'
        vm = execute_source(candidate['source_code'], ctx, max_steps=max_steps, timeout_s=timeout_s)
        result['vm'] = vm
        stage = 'post_calculation_observation'
        after = current_reader()
        validate_observation(after)
        if after != observation:
            result['reason'] = 'observation_changed_during_calculation'
            return result
        if vm['status'] != 'returned':
            result['reason'] = 'candidate_calculation_failed'
            return result
        if vm['output'] is None:
            result.update(status='abstained', reason='candidate_returned_none')
            return result
        stage = 'output_validation'
        grasp = validate_grasp(vm['output'], size)
        result.update(status='proposed', reason='bounded_calculation_and_output_checks_passed',
            proposal={'kind': 'post_grasp_proposal', 'grasp': grasp,
                'part_id': context['config']['part']['id'], 'target_id': context['config']['target']['id'],
                'episode_id': observation['episode_id'], 'observation_revision': observation['revision'],
                'observation_id': observation['observation_id'], 'candidate_id': candidate['candidate_id'],
                'code_sha256': candidate['code_sha256'], 'rejection_id': rejection['rejection_id'],
                'requires_current_scene_preflight': True, 'execution_authorized': False})
    except Exception as error:
        result['reason'] = stage + '_failed'
        result['error'] = {'type': type(error).__name__, 'stage': stage}
    return result


def capture_observation(skills, *, episode_id, revision):
    """Read a continuing simulator executor; no mj_step/reset/robot commands.

    Caller must preserve the live executor's holding history and serialize access.
    A fresh skills object on an arbitrary restored state is not holding evidence.
    """
    import mujoco
    import numpy as np
    from failure_report import json_safe
    from workcell_verification import WorkcellVerifier
    from workcell_scene import part_name

    cfg, joint = json_safe(skills.config), json_safe(skills.verifier.joint)
    pid = cfg['part']['id']
    context = _post_contexts({'registry': {pid: {'kind': 'post', 'config': cfg, 'joint': joint}},
                              'simulator_version': mujoco.__version__})[pid]
    # Box geometry matches the active post; compound sleeves/covers are excluded.
    if not np.allclose(skills.model.geom(part_name(cfg) + '_geom').size * 2,
                       cfg['part']['size_m'], atol=1e-10, rtol=0):
        raise ValueError('Configured and modeled post size differ.')
    row = WorkcellVerifier.sample(skills.verifier, skills.data, skills.motion.reference,
                                  allow_support=True, allow_grasp=True)
    raw = skills.observe()
    rotation = skills.data.body(part_name(cfg)).xmat.reshape(3, 3)
    measures = {'supported': bool(row['supported']), 'holding_part': skills.motion.held_pose is not None,
        'left_contact': bool(row['left_contact']), 'right_contact': bool(row['right_contact']),
        'upright_cosine': float(np.clip(rotation[2, 2], -1., 1.)),
        'linear_speed_m_s': row['part_speed_m_s'], 'angular_speed_rad_s': row['part_angular_speed_rad_s'],
        'pad_gap_mm': row['pad_gap_mm']}
    # Full scene vectors catch same-time position/control changes, not just active TCP.
    state = json_safe({'observation': raw, 'qpos': skills.data.qpos, 'qvel': skills.data.qvel,
        'act': skills.data.act, 'ctrl': skills.data.ctrl, 'held_pose': skills.motion.held_pose,
        'reference': skills.motion.reference, 'gripper_command': skills.motion.gripper_command,
        'results': skills.results, 'measurements': measures})
    return make_observation(episode_id=episode_id, revision=revision, condition_context=context,
        simulator_version=mujoco.__version__, simulation_time_s=float(skills.data.time),
        measurements=measures, state_fingerprint=digest(state), provenance='mujoco_live_snapshot',
        installed_parts=sorted(getattr(skills, 'installed', [])))
