"""22J calculation probes: controlled inputs and a fresh, unmoved MuJoCo scene."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from uuid import uuid4

from export_grasp_experience import ROOT, DEFAULT_OUTPUT, _decode, digest
from generate_grasp_lesson import _new_run_dir, _write_json
from grasp_skill_candidates import validate_candidate
from grasp_skill_runtime import (make_observation, bind_rejection, evaluate_candidate,
                                capture_observation)


def controlled_inputs(candidate, *, memory_dir=DEFAULT_OUTPUT):
    """Authored state/trials, actual candidate source. No physical-success claim."""
    evidence = candidate['input_packet']['lesson']['evidence_packet']
    case = next(c for c in evidence['cases'] if c['source_conditions']['part_episode_start_installed'] == [])
    ctx, conditions = case['contexts'][0], case['source_conditions']
    nominal = make_observation(episode_id='controlled-' + uuid4().hex, revision=1,
        condition_context=ctx, simulator_version=conditions['simulator_version'], simulation_time_s=0.,
        measurements={'supported': True, 'holding_part': False, 'left_contact': False,
            'right_contact': False, 'upright_cosine': 1., 'linear_speed_m_s': 0.,
            'angular_speed_rad_s': 0., 'pad_gap_mm': 85.},
        state_fingerprint=digest({'controlled_input': True, 'case_id': case['case_id']}))
    query = evidence['search']['query']
    def bound(obs, z=0.):
        return bind_rejection(obs, {'source': 'offline_fixture', 'accepted': False,
            'observation_revision': obs['revision'], 'live_start_s': obs['simulation_time_s'],
            'failure_code': query['failure_code'], 'phase': query['failure_phase'],
            'grasp': {'x_mm': 0., 'y_mm': 0., 'z_mm': z, 'jaw_yaw_deg': 0.}})
    def changed(obs, **updates):
        value = deepcopy(obs)
        value.update(updates)
        value['observation_id'] = digest({k: v for k, v in value.items() if k != 'observation_id'})
        return value
    rows = []
    def run(name, obs, rejection, *, budget=2, reader=None):
        result = evaluate_candidate(candidate, obs, current_reader=reader or (lambda: deepcopy(obs)),
            rejection=rejection, remaining_proposals=budget, memory_dir=memory_dir)
        rows.append({'name': name, 'input_kind': 'authored_offline_fixture',
                     'observation': obs, 'rejection': rejection, 'result': result})
    rejection = bound(nominal)
    run('nominal_calculation', nominal, rejection)
    run('candidate_abstains_at_upper_z', nominal,
        bound(nominal, ctx['config']['part']['size_m'][2] * 500))
    holding = changed(nominal, measurements={**nominal['measurements'], 'holding_part': True})
    run('host_blocks_holding', holding, bound(holding))
    run('host_blocks_stale_revision', nominal, rejection,
        reader=lambda: changed(nominal, revision=2))
    run('host_blocks_budget', nominal, rejection, budget=0)
    count = [0]
    def moving_reader():
        count[0] += 1
        return deepcopy(nominal) if count[0] == 1 else changed(nominal, state_fingerprint=digest('changed'))
    run('host_blocks_change_during_calculation', nominal, rejection, reader=moving_reader)
    # A re-observe revision increase alone is valid when the entire physics state is unchanged.
    newer = changed(nominal, revision=2)
    run('same_state_new_revision', newer, rejection)
    expected = [('proposed', 'bounded_calculation_and_output_checks_passed'),
        ('abstained', 'candidate_returned_none'), ('abstained', 'holding_part'),
        ('rejected', 'stale_observation'), ('abstained', 'proposal_budget_exhausted'),
        ('rejected', 'observation_changed_during_calculation'),
        ('proposed', 'bounded_calculation_and_output_checks_passed')]
    for row, (status, reason) in zip(rows, expected):
        row['expected_contract_matched'] = row['result']['status'] == status and row['result']['reason'] == reason
    return rows


def fresh_scene(candidate, *, memory_dir=DEFAULT_OUTPUT):
    import mujoco
    from failure_report import json_safe
    from frame_assembly_scene import build_frame_scene
    from frame_assembly_skills import FrameSkills
    model, data, registry = build_frame_scene(product_count=10)
    skills = FrameSkills(model, data, 'post_1', registry, set())
    episode = 'fresh-scene-' + uuid4().hex
    def data_id():
        return digest(json_safe({'time': float(data.time), 'qpos': data.qpos,
                                 'qvel': data.qvel, 'ctrl': data.ctrl, 'act': data.act}))
    before = data_id()
    reader = lambda: capture_observation(skills, episode_id=episode, revision=1)
    observation = reader()
    result = evaluate_candidate(candidate, observation, current_reader=reader, rejection=None,
                                 remaining_proposals=2, memory_dir=memory_dir)
    return {'name': 'fresh_scene_observation_only', 'simulator_version': mujoco.__version__,
        'observation': observation, 'result': result, 'simulation_steps': 0,
        'robot_commands': 0, 'physics_state_unchanged': before == data_id(),
        'expected_contract_matched': result['status'] == 'abstained'
            and result['reason'] == 'part_not_supported' and before == data_id(),
        'note': 'No current preflight failure exists. The initial part is unsupported; no settle/pick/preflight was run.'}


def run_probe(candidate_path, *, probe='all', memory_dir=DEFAULT_OUTPUT,
              output_root=ROOT / 'runs/step22j_skill_runtime'):
    log_dir = _new_run_dir(output_root)
    result = {'schema': 'post_grasp_runtime_probe/v1', 'status': 'failed', 'run_dir': str(log_dir),
        'API_calls': 0, 'robot_commands': 0, 'simulation_steps': 0,
        'execution_authorized': False, 'automatic_skill_registration': False,
        'assembly_success_measured': False, 'controlled_cases': [], 'fresh_scene': None}
    try:
        raw = Path(candidate_path).read_bytes()
        if len(raw) > 4 * 1024 * 1024:
            raise ValueError('Candidate too large.')
        candidate = _decode(raw.decode('utf-8'))
        validate_candidate(candidate, memory_dir=memory_dir)
        _write_json(log_dir / 'candidate_snapshot.json', candidate)
        result.update(candidate_id=candidate['candidate_id'], code_sha256=candidate['code_sha256'],
                      source_candidate_sha256=hashlib.sha256(raw).hexdigest())
        if probe in ('all', 'controlled'):
            result['controlled_cases'] = controlled_inputs(candidate, memory_dir=memory_dir)
        if probe in ('all', 'fresh-scene'):
            result['fresh_scene'] = fresh_scene(candidate, memory_dir=memory_dir)
        checks = [r['expected_contract_matched'] for r in result['controlled_cases']]
        if result['fresh_scene'] is not None:
            checks.append(result['fresh_scene']['expected_contract_matched'])
        result['source_candidate_unchanged'] = raw == Path(candidate_path).read_bytes()
        result['status'] = 'passed' if checks and all(checks) and result['source_candidate_unchanged'] else 'failed'
    except Exception as error:
        result['error'] = {'type': type(error).__name__}
    _write_json(log_dir / 'result.json', result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate', type=Path, required=True)
    parser.add_argument('--probe', choices=('all', 'controlled', 'fresh-scene'), default='all')
    parser.add_argument('--memory-dir', type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = run_probe(args.candidate, probe=args.probe, memory_dir=args.memory_dir)
    print(json.dumps({k: v for k, v in result.items() if k not in ('controlled_cases', 'fresh_scene')}, indent=2))
    for row in result['controlled_cases'] + ([result['fresh_scene']] if result['fresh_scene'] else []):
        print(json.dumps({'case': row['name'], 'status': row['result']['status'],
            'reason': row['result']['reason'], 'proposal': row['result']['proposal'],
            'passed': row['expected_contract_matched']}, ensure_ascii=False))
    return 0 if result['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
