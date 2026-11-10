"""22K: compare a measured baseline rejection and the unchanged saved candidate."""
import argparse
import hashlib
import json
from pathlib import Path
from uuid import uuid4

from export_grasp_experience import ROOT, DEFAULT_OUTPUT, _decode
from failure_report import json_safe
from generate_grasp_lesson import _new_run_dir, _write_json
from grasp_skill_candidates import validate_candidate
from grasp_skill_runtime import capture_observation, bind_rejection
from grasp_candidate_preflight import source_state, checked_trial, preflight_candidate, compare_trials
from grasp_preflight_trace import RecordedFrameSkills, run_recorded_preflight


def _progress(event):
    print(json.dumps(event, ensure_ascii=False), flush=True)


def run_comparison(candidate_path, *, memory_dir=DEFAULT_OUTPUT,
                   output_root=ROOT / 'runs/step22k_candidate_preflight', progress=None):
    import mujoco
    from frame_assembly_scene import build_frame_scene
    folder = _new_run_dir(output_root)
    emit = progress or (lambda event: None)
    episode = 'candidate-preflight-' + uuid4().hex
    result = {'schema': 'post_grasp_candidate_preflight_run/v1', 'status': 'error',
        'run_dir': str(folder), 'episode_id': episode, 'simulator_version': mujoco.__version__,
        'API_calls': 0, 'automatic_skill_registration': False,
        'original_scene_assembly_executed': False, 'original_installed_parts': [],
        'baseline': None, 'candidate_evaluation': None, 'comparison': None}
    stage, raw, skills, before = 'load_candidate', None, None, None
    emit({'event': 'run_started', 'run_dir': str(folder)})
    try:
        candidate_path = Path(candidate_path)
        raw = candidate_path.read_bytes()
        if len(raw) > 4 * 1024 * 1024:
            raise ValueError('Candidate too large.')
        candidate = _decode(raw.decode('utf-8'))
        validate_candidate(candidate, memory_dir=memory_dir)
        _write_json(folder / 'candidate_snapshot.json', candidate)
        result.update(candidate_id=candidate['candidate_id'], code_sha256=candidate['code_sha256'],
                      source_candidate_sha256=hashlib.sha256(raw).hexdigest())
        _write_json(folder / 'implementation_hashes.json', {
            str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted((ROOT / 'scripts').rglob('*.py'))})
        stage = 'settle_original_scene'
        model, data, registry = build_frame_scene(product_count=10)
        skills = RecordedFrameSkills(model, data, 'post_1', registry, set())
        skills.preflight_trace_on_event = emit
        _write_json(folder / 'resolved_scene.json', json_safe({'registry': registry,
            'product_count': 10, 'active_part': 'post_1', 'installed_parts': [],
            'config': skills.config, 'joint': skills.verifier.joint,
            'transport_strategy': skills.transport_strategy,
            'empty_approach_strategy': skills.empty_approach_strategy}))
        start = float(data.time)
        emit({'event': 'settling_original_scene', 'simulation_time_s': start})
        settled = skills.settle()
        _write_json(folder / 'settled_observation.json', settled)
        result['original_scene_settle'] = {'start_s': start, 'end_s': float(data.time),
            'elapsed_sim_s': float(data.time) - start, 'status': 'supported_and_settled'}
        before = source_state(skills)
        _write_json(folder / 'source_before_comparison.json', before)
        obs1 = capture_observation(skills, episode_id=episode, revision=1)
        _write_json(folder / 'baseline_observation.json', obs1)
        stage = 'baseline_copy_preflight'
        grasp = {'x_mm': 0., 'y_mm': 0., 'z_mm': 0., 'jaw_yaw_deg': 0.}
        result['baseline_control'] = {'grasp': grasp,
            'origin': 'declared_experimental_control_not_a_new_LLM_proposal'}
        emit({'event': 'baseline_preflight_started', 'grasp': grasp})
        baseline = run_recorded_preflight(skills, grasp, observation_revision=1,
                                           log_dir=folder / 'baseline_preflight')
        result['baseline'] = baseline
        checked_trial(baseline, grasp, obs1)
        after_baseline = source_state(skills)
        _write_json(folder / 'source_after_baseline.json', after_baseline)
        if after_baseline != before:
            raise ValueError('Baseline copy changed the source scene.')
        if (baseline['accepted'] or baseline.get('failure_code') != 'PLANNED_CONTACT'
                or baseline.get('phase') != 'insert_lower/preflight'):
            result['status'] = 'baseline_not_applicable'
            return result
        stage = 'bind_rejection_and_reobserve'
        rejection = bind_rejection(obs1, baseline)
        _write_json(folder / 'baseline_rejection_binding.json', rejection)
        reader = lambda: capture_observation(skills, episode_id=episode, revision=2)
        obs2 = reader()
        _write_json(folder / 'candidate_observation.json', obs2)
        stage = 'candidate_calculation_and_copy_preflight'
        emit({'event': 'candidate_evaluation_started', 'baseline_failure_code': baseline['failure_code'],
              'baseline_phase': baseline['phase']})
        evaluated = preflight_candidate(skills, candidate, obs2, current_reader=reader,
            rejection=rejection, remaining_proposals=1, log_dir=folder / 'candidate', memory_dir=memory_dir)
        result['candidate_evaluation'] = evaluated
        if evaluated['trial'] is None:
            result['status'] = 'candidate_not_proposed'
            return result
        stage = 'record_comparison'
        comparison = compare_trials(baseline, evaluated['trial'])
        result['comparison'] = comparison
        _write_json(folder / 'comparison.json', comparison)
        # Negative evidence is persisted too. It is not automatically promoted to
        # a successful skill or injected into the older retrieval database.
        _write_json(folder / 'experience.json', {
            'schema': 'post_grasp_candidate_preflight_experience/v1',
            'environment': 'simulation', 'measurement_domain': 'mujoco_preflight_copy',
            'episode_id': episode, 'candidate_id': candidate['candidate_id'],
            'code_sha256': candidate['code_sha256'], 'rejection_id': rejection['rejection_id'],
            'baseline_observation_id': obs1['observation_id'],
            'candidate_observation_id': obs2['observation_id'],
            'candidate_snapshot_path': str(folder / 'candidate_snapshot.json'),
            'evaluation_path': str(folder / 'candidate/evaluation.json'),
            'baseline_trace_paths': baseline['trace_paths'],
            'candidate_trace_paths': evaluated['trial']['trace_paths'],
            'comparison': comparison, 'automatic_retrieval_import': False,
            'original_scene_assembly_success': False, 'automatic_skill_registration': False})
        result['status'] = 'comparison_recorded'
        return result
    except (Exception, KeyboardInterrupt) as error:
        result['status'] = 'error'
        result['error'] = {'type': type(error).__name__, 'stage': stage}
        code = getattr(error, 'code', None)
        if code is not None:
            result['error'].update(failure_code=code.value, phase=getattr(error, 'phase', None),
                                  violations=json_safe(getattr(error, 'violations', [])))
        return result
    finally:
        if raw is not None:
            result['source_candidate_unchanged'] = (candidate_path.is_file()
                and raw == candidate_path.read_bytes())
            if not result['source_candidate_unchanged']:
                result['status'] = 'error'
                result['error'] = {'type': 'SourceCandidateChanged', 'stage': stage}
        if before is not None:
            after = source_state(skills)
            _write_json(folder / 'source_after_comparison.json', after)
            result['original_state_unchanged_during_comparison'] = after == before
            result['original_installed_parts'] = sorted(skills.installed)
            if after != before:
                result['status'] = 'error'
                result['error'] = {'type': 'SourceStateChanged', 'stage': stage}
        _write_json(folder / 'result.json', result)
        emit({'event': 'run_finished', 'status': result['status'], 'run_dir': str(folder)})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate', type=Path, required=True)
    parser.add_argument('--memory-dir', type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--output-root', type=Path, default=ROOT / 'runs/step22k_candidate_preflight')
    args = parser.parse_args()
    result = run_comparison(args.candidate, memory_dir=args.memory_dir,
                            output_root=args.output_root, progress=_progress)
    print(json.dumps({k: result.get(k) for k in ('status', 'run_dir', 'error', 'comparison',
        'source_candidate_unchanged', 'original_state_unchanged_during_comparison')},
        ensure_ascii=False, indent=2), flush=True)
    return 0 if result['status'] == 'comparison_recorded' else 1


if __name__ == '__main__':
    raise SystemExit(main())
