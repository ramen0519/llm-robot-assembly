"""Reproduce the saved Luna grasp rejection, then test one Astra revision in MuJoCo."""
import argparse
import hashlib
import json
import math
from pathlib import Path
from uuid import uuid4

from export_grasp_experience import ROOT, DEFAULT_OUTPUT, CHECKS, _decode
from failure_report import json_safe
from generate_grasp_lesson import _new_run_dir, _write_json
from grasp_candidate_preflight import checked_trial, compare_trials, preflight_candidate, source_state
from grasp_preflight_trace import RecordedFrameSkills, run_recorded_preflight
from grasp_skill_candidates import validate_candidate
from grasp_skill_runtime import bind_rejection, capture_observation, validate_grasp, validate_observation

DEFAULT_RUNS = ROOT / 'runs/step22l_grasp_revision_trials'
REPRODUCTION_GRASP = {'x_mm': 0., 'y_mm': 0., 'z_mm': 1., 'jaw_yaw_deg': 0.}


def _read(path):
    with Path(path).open('rb') as stream:
        raw = stream.read(8 * 1024 * 1024 + 1)
    if len(raw) > 8 * 1024 * 1024:
        raise ValueError('Input artifact exceeds the size limit.')
    return _decode(raw.decode('utf-8')), hashlib.sha256(raw).hexdigest()


def load_parent_control(source_run, parent):
    """Recover the actual submitted +1 grasp and its historical execution lineage."""
    root = Path(source_run).resolve()
    required = ('candidate_snapshot.json', 'candidate/submitted_grasp.json', 'candidate/evaluation.json',
                'candidate/preflight/terminal.json', 'candidate_observation.json',
                'baseline_rejection_binding.json', 'result.json', 'experience.json')
    loaded = {name: _read(root / name) for name in required}
    artifacts = {k: value for k, (value, _) in loaded.items()}
    submitted = artifacts['candidate/submitted_grasp.json']
    evaluated = artifacts['candidate/evaluation.json']
    terminal = artifacts['candidate/preflight/terminal.json']
    obs = artifacts['candidate_observation.json']
    old = artifacts['result.json']
    experience = artifacts['experience.json']
    rejection = artifacts['baseline_rejection_binding.json']
    validate_observation(obs)
    if (artifacts['candidate_snapshot.json'] != parent or old['status'] != 'comparison_recorded'
            or old.get('source_candidate_unchanged') is not True
            or old.get('original_state_unchanged_during_comparison') is not True
            or old.get('original_scene_assembly_executed') is not False
            or obs['provenance'] != 'mujoco_live_snapshot'
            or evaluated['status'] != 'proposed' or evaluated['proposal'] != submitted
            or submitted['candidate_id'] != parent['candidate_id']
            or submitted['code_sha256'] != parent['code_sha256']
            or submitted['observation_id'] != obs['observation_id']
            or submitted['observation_revision'] != obs['revision']
            or submitted['episode_id'] != obs['episode_id'] or submitted['part_id'] != 'post_1'
            or submitted['target_id'] != obs['condition_context']['config']['target']['id']
            or submitted['rejection_id'] != rejection['rejection_id']
            or bind_rejection(rejection['source_observation'], rejection['trial']) != rejection
            or submitted['grasp'] != REPRODUCTION_GRASP
            or experience['candidate_id'] != parent['candidate_id']
            or experience['candidate_observation_id'] != obs['observation_id']
            or experience['comparison'] != old['comparison']
            or old['candidate_evaluation']['evaluation'] != evaluated
            or terminal['status'] != 'returned'):
        raise ValueError('Historical control does not match the submitted Luna proposal.')
    historical = old['candidate_evaluation']['trial']
    checked_trial(historical, submitted['grasp'], obs)
    if (any(historical.get(k) != v for k, v in terminal['outcome'].items())
            or historical['trace_summary'] != terminal['trace_summary']
            or historical['accepted'] is not False or historical['failure_code'] != 'PLANNED_CONTACT'
            or historical['phase'] != 'insert_lower/preflight'):
        raise ValueError('The historical +1 control must be a measured insertion-path rejection.')
    return {'historical_run': str(root), 'historical_episode_id': obs['episode_id'],
            'historical_observation': obs, 'submitted_grasp': submitted,
            'failure_code': historical['failure_code'], 'phase': historical['phase'],
            'historical_held_pad_geometry': historical.get('held_pad_geometry'),
            'artifact_sha256': {k: h for k, (_, h) in loaded.items()}}


def check_revision_source(revision, control):
    evidence = revision['evidence']
    expected = {'source_result_sha256': control['artifact_sha256']['result.json'],
                'source_run_name': Path(control['historical_run']).name,
                'source_episode': control['historical_episode_id']}
    if any(evidence.get(key) != value for key, value in expected.items()):
        raise ValueError('Revision evidence belongs to a different historical run or result.')
    return expected


def _same_source(skills, before, path):
    after = source_state(skills)
    _write_json(path, after)
    if after != before:
        raise ValueError('The original scene or executor changed during a copy-only operation.')


def _reproduce(skills, observation, control, folder):
    old = control['historical_observation']
    if any(observation[k] != old[k] for k in ('condition_context', 'installed_parts', 'simulator_version')):
        raise ValueError('Historical and current post conditions do not match.')
    grasp = validate_grasp(control['submitted_grasp']['grasp'],
                           observation['condition_context']['config']['part']['size_m'])
    trial = run_recorded_preflight(skills, grasp, observation_revision=observation['revision'],
                                   log_dir=folder / 'reproduced_parent_preflight')
    checked_trial(trial, grasp, observation)
    if trial['trace_summary'].get('trace_complete') is not True:
        raise ValueError('Current parent trace is incomplete.')
    return trial


def _execute_original(skills, observation, grasp, trial, folder, result):
    """Submit exactly the accepted grasp to the still-current original simulation."""
    if capture_observation(skills, episode_id=observation['episode_id'], revision=observation['revision']) != observation:
        raise ValueError('Original scene changed after approval of the grasp.')
    checked_trial(trial, grasp, observation)
    if trial['accepted'] is not True or trial['trace_summary'].get('trace_complete') is not True:
        raise ValueError('Original execution requires a completed, accepted current-scene preflight.')
    part, target = skills.active, skills.config['target']['id']
    if (part != 'post_1' or skills.registry[part]['kind'] != 'post' or part in skills.installed
            or skills.motion.held_pose is not None or skills.results):
        raise ValueError('Unexpected original execution state.')
    evidence = {'environment': 'simulation', 'part_id': part, 'target_id': target,
                'grasp': grasp, 'observation_id': observation['observation_id'],
                'observation_revision': observation['revision'], 'preflight_trace': trial['trace_paths'],
                'start_time_s': float(skills.data.time)}
    _write_json(folder / 'original_pick_intent.json', evidence)
    result['original_execution_started'] = True
    result['original_stage'] = 'pick'
    pick = skills.pick(part, grasp=grasp)
    _write_json(folder / 'original_pick_result.json', json_safe(pick))
    if (pick.get('skill') != 'pick/v1' or pick.get('status') != 'success'
            or pick.get('part_id') != part or skills.motion.held_pose is None
            or [r.get('skill') for r in skills.results] != ['approach_component/v1', 'pick/v1']
            or skills.results[0].get('proposed_grasp') != grasp):
        raise ValueError('Pick returned without confirming the requested part.')
    result['original_stage'] = 'insert'
    _write_json(folder / 'original_insert_intent.json', {'part_id': part, 'target_id': target,
                'simulation_time_s': float(skills.data.time)})
    insertion = skills.insert(part, target)
    _write_json(folder / 'original_insert_result.json', json_safe(insertion))
    if (insertion.get('skill') != 'insert_post/v1' or insertion.get('status') != 'success'
            or insertion.get('part_id') != part or insertion.get('target_id') != target
            or skills.motion.held_pose is not None
            or [r.get('skill') for r in skills.results] != ['approach_component/v1', 'pick/v1', 'insert_post/v1']):
        raise ValueError('Insertion returned without confirming the requested joint and release.')
    result['original_stage'] = 'independent_final_measurement'
    initial_state = source_state(skills)
    row = skills.verifier.sample(skills.data, skills.motion.reference, allow_support=True, allow_grasp=False)
    checks = skills.insertion_evidence([row])['checks']
    skills.verifier.verify_other_parts(skills.data)
    post_state = source_state(skills)
    verification = {'measurement_domain': 'original_simulation', 'row': json_safe(row),
        'checks': json_safe(checks), 'installed_part': part, 'target_id': target,
        'other_parts_preserved': True, 'read_only_measurements': initial_state == post_state,
        'simulation_time_s': float(skills.data.time),
        'insertion_window_measured_depth_m': insertion.get('minimum_final_depth_m'),
        'insertion_window_max_position_error_m': insertion.get('maximum_final_position_error_m')}
    _write_json(folder / 'original_final_verification.json', verification)
    if (set(checks) != CHECKS | {'declared_mating_support'} or any(v is not True for v in checks.values())
            or set(insertion.get('checks', {})) != set(checks)
            or any(v is not True for v in insertion['checks'].values()) or initial_state != post_state):
        raise ValueError('Independent final measurement failed.')
    depth = insertion.get('minimum_final_depth_m')
    if (type(depth) not in (int, float) or not math.isfinite(depth)
            or abs(depth - skills.verifier.joint['nominal_insertion_depth_m']) >= .001):
        raise ValueError('Original insertion depth is outside the verified window.')
    skills.installed.add(part)
    result.update(original_stage='verified', original_scene_assembly_success=True,
                  original_final_verification=verification)


def run_revision_trial(revision_path, *, parent_candidate, source_run, memory_dir=DEFAULT_OUTPUT,
                       output_root=DEFAULT_RUNS, execute_original=False, progress=None):
    """Two copy trials on one original scene. A zero-argument provider may return a revision path."""
    import mujoco
    from frame_assembly_scene import build_frame_scene

    folder = _new_run_dir(output_root)
    emit = progress or (lambda event: None)
    episode = 'grasp-revision-' + uuid4().hex
    result = {'schema': 'post_grasp_revision_trial/v1', 'status': 'error', 'run_dir': str(folder),
        'environment': 'simulation', 'simulator_version': mujoco.__version__, 'episode_id': episode,
        'API_calls': 0, 'api_accounting_scope': 'simulation_trial_revision_generation_metered_separately',
        'automatic_skill_registration': False, 'new_copy_preflights_returned': 0,
        'original_execution_requested': execute_original, 'original_execution_started': False,
        'original_stage': None, 'original_scene_assembly_success': False,
        'original_state_unchanged_through_preflights': None, 'original_installed_parts': [],
        'comparison': None, 'error': None}
    tracked = {}
    skills = before = None
    stage = 'validate_historical_control'

    def read_revision(path):
        from grasp_revision_generation import validate_revision
        value, fingerprint = _read(path)
        validate_revision(value, parent)
        if value['parent_candidate_id'] != parent['candidate_id']:
            raise ValueError('Revision references a different parent.')
        result['revision_source_binding'] = check_revision_source(value, control)
        tracked[Path(path).resolve()] = fingerprint
        _write_json(folder / 'revision_snapshot.json', value)
        result.update(revision_id=value['revision_id'], code_sha256=value['code_sha256'],
                      revision_file_sha256=fingerprint)
        return value

    try:
        if type(execute_original) is not bool:
            raise ValueError('Original-scene execution option must be boolean.')
        parent_candidate = Path(parent_candidate).resolve()
        parent, parent_sha = _read(parent_candidate)
        validate_candidate(parent, memory_dir=memory_dir)
        tracked[parent_candidate] = parent_sha
        control = load_parent_control(source_run, parent)
        for name, expected_sha in control['artifact_sha256'].items():
            tracked[Path(source_run).resolve() / name] = expected_sha
        _write_json(folder / 'historical_parent_control.json', control)
        _write_json(folder / 'parent_candidate_snapshot.json', parent)
        result.update(parent_candidate_id=parent['candidate_id'], parent_code_sha256=parent['code_sha256'],
                      parent_candidate_file_sha256=parent_sha, historical_run=str(Path(source_run).resolve()))
        revision = None
        if not callable(revision_path):
            stage = 'validate_revision'
            revision = read_revision(revision_path)
        _write_json(folder / 'implementation_hashes.json', {
            str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted((ROOT / 'scripts').rglob('*.py'))})
        stage = 'settle_original_scene'
        emit({'event': 'settling_original_scene', 'run_dir': str(folder)})
        model, data, registry = build_frame_scene(product_count=10)
        skills = RecordedFrameSkills(model, data, 'post_1', registry, set())
        skills.preflight_trace_on_event = emit
        _write_json(folder / 'settled_observation.json', json_safe(skills.settle()))
        before = source_state(skills)
        _write_json(folder / 'source_before_trials.json', before)
        initial = capture_observation(skills, episode_id=episode, revision=1)
        _write_json(folder / 'initial_observation.json', initial)
        stage = 'reproduce_parent_rejection'
        obs2 = capture_observation(skills, episode_id=episode, revision=2)
        _write_json(folder / 'parent_control_observation.json', obs2)
        emit({'event': 'reproducing_saved_luna_grasp', 'grasp': control['submitted_grasp']['grasp']})
        trial = _reproduce(skills, obs2, control, folder)
        result['new_copy_preflights_returned'] += 1
        _write_json(folder / 'reproduced_parent_trial.json', trial)
        _same_source(skills, before, folder / 'source_after_reproduction.json')
        if (trial['accepted'] or trial.get('failure_code') != control['failure_code']
                or trial.get('phase') != control['phase'] or trial['trace_summary'].get('pick_completed') is not True):
            result['status'] = 'parent_failure_not_reproduced'
            return result
        stage = 'bind_current_rejection'
        rejection = bind_rejection(obs2, trial)
        _write_json(folder / 'reproduced_rejection_binding.json', rejection)
        result['current_rejection_id'] = rejection['rejection_id']
        if revision is None:
            stage = 'resolve_revision_after_reproduction'
            revision = read_revision(revision_path())
        stage = 'revision_preflight'
        reader = lambda: capture_observation(skills, episode_id=episode, revision=3)
        obs3 = reader()
        _write_json(folder / 'revision_observation.json', obs3)
        emit({'event': 'revision_evaluation_started', 'revision_id': revision['revision_id'],
              'rejection_id': rejection['rejection_id']})
        tested = preflight_candidate(skills, parent, obs3, current_reader=reader, rejection=rejection,
            remaining_proposals=1, log_dir=folder / 'revision', memory_dir=memory_dir, revision=revision)
        _same_source(skills, before, folder / 'source_after_revision_preflight.json')
        result['original_state_unchanged_through_preflights'] = True
        result['revision_evaluation_status'] = tested['status']
        evaluation = tested['evaluation']
        revised = tested['trial']
        if revised is None:
            result.update(status='revision_not_proposed', proposal_reason=evaluation.get('reason'))
            return result
        result['new_copy_preflights_returned'] += 1
        proposal = evaluation['proposal']
        if (proposal['candidate_id'] != revision['revision_id'] or proposal['code_sha256'] != revision['code_sha256']
                or proposal['rejection_id'] != rejection['rejection_id'] or proposal['observation_id'] != obs3['observation_id']
                or tested['original_state_unchanged'] is not True or revised['trace_summary'].get('trace_complete') is not True):
            raise ValueError('Revision evaluation is not bound to the current origin and complete trace.')
        checked_trial(revised, proposal['grasp'], obs3)
        result['comparison'] = compare_trials(trial, revised)
        _write_json(folder / 'comparison.json', result['comparison'])
        if not revised['accepted']:
            result['status'] = 'revision_preflight_rejected'
            return result
        result['status'] = 'revision_preflight_accepted'
        if execute_original:
            stage = 'original_simulation_execution'
            if any(_read(path)[1] != expected for path, expected in tracked.items()):
                raise ValueError('An input artifact changed before original execution.')
            _same_source(skills, before, folder / 'source_before_original_execution.json')
            emit({'event': 'accepted_revision_original_simulation_started', 'grasp': proposal['grasp']})
            _execute_original(skills, obs3, proposal['grasp'], revised, folder, result)
            result['status'] = 'original_simulation_verified'
        return result
    except (Exception, KeyboardInterrupt) as error:
        result['status'] = 'error'
        result['error'] = {'stage': stage, 'type': type(error).__name__}
        code = getattr(error, 'code', None)
        if code is not None:
            result['error'].update(failure_code=code.value, phase=getattr(error, 'phase', None),
                                   violations=json_safe(getattr(error, 'violations', [])))
        return result
    finally:
        audit_errors = []
        try:
            result['input_artifacts_unchanged'] = all(_read(p)[1] == h for p, h in tracked.items()) if tracked else None
            if result['input_artifacts_unchanged'] is False:
                raise ValueError('Input artifact changed.')
        except (Exception, KeyboardInterrupt) as audit_error:
            result['input_artifacts_unchanged'] = False
            audit_errors.append({'stage': 'input_artifacts', 'type': type(audit_error).__name__})
        try:
            if before is not None:
                final_state = source_state(skills)
                _write_json(folder / 'source_final.json', final_state)
                result['original_installed_parts'] = sorted(skills.installed)
                if not result['original_execution_started']:
                    result['original_state_unchanged_through_preflights'] = final_state == before
                    if final_state != before:
                        raise ValueError('Unexpected original simulation mutation.')
        except (Exception, KeyboardInterrupt) as audit_error:
            audit_errors.append({'stage': 'original_scene', 'type': type(audit_error).__name__})
        if audit_errors:
            result['status'] = 'error'
            result['final_audit_errors'] = audit_errors
        _write_json(folder / 'result.json', result)
        emit({'event': 'run_finished', 'status': result['status'], 'run_dir': str(folder)})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--revision', type=Path, required=True)
    parser.add_argument('--parent-candidate', type=Path, required=True)
    parser.add_argument('--source-run', type=Path, required=True, help='The source 22K comparison directory.')
    parser.add_argument('--memory-dir', type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--output-root', type=Path, default=DEFAULT_RUNS)
    parser.add_argument('--execute-original', action='store_true',
                        help='Also execute the accepted grasp once in the original MuJoCo simulation.')
    args = parser.parse_args(argv)
    result = run_revision_trial(args.revision, parent_candidate=args.parent_candidate,
        source_run=args.source_run, memory_dir=args.memory_dir, output_root=args.output_root,
        execute_original=args.execute_original,
        progress=lambda event: print(json.dumps(event, ensure_ascii=False), flush=True))
    print(json.dumps({k: result.get(k) for k in ('status', 'run_dir', 'error', 'comparison',
            'original_state_unchanged_through_preflights', 'original_scene_assembly_success')},
        ensure_ascii=False, indent=2, allow_nan=False), flush=True)
    return 0 if result['status'] in ('revision_preflight_accepted', 'revision_preflight_rejected',
                                      'original_simulation_verified') else 1


if __name__ == '__main__':
    raise SystemExit(main())
