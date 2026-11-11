"""Select a stored recovery skill from a fresh MuJoCo rejection and verify its reuse."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from uuid import uuid4

from export_grasp_experience import ROOT, DEFAULT_OUTPUT
from failure_report import json_safe
from generate_grasp_lesson import _new_run_dir, _write_json
from grasp_candidate_preflight import checked_trial, compare_trials, preflight_candidate, source_state
from grasp_preflight_trace import RecordedFrameSkills, run_recorded_preflight
from grasp_skill_runtime import bind_rejection, capture_observation, revision_geometry, validate_observation
from run_grasp_revision_trial import _execute_original, _same_source

DEFAULT_RUNS = ROOT / 'runs/step22m_skill_reuse'
DEVELOPMENT_GRASP = {'x_mm': 0., 'y_mm': 0., 'z_mm': 1., 'jaw_yaw_deg': 0.}


def build_reuse_query(goal, observation, rejection, *, part_kind):
    """Expose the present goal, bounded measurements and detector report to the selector."""
    validate_observation(observation)
    previous, trial = rejection['source_observation'], rejection['trial']
    if bind_rejection(previous, trial) != rejection:
        raise ValueError('Invalid rejection binding.')
    if (observation['provenance'] != 'mujoco_live_snapshot'
            or previous['revision'] >= observation['revision']
            or any(previous[k] != observation[k] for k in ('episode_id', 'state_fingerprint',
                'condition_context', 'installed_parts', 'simulation_time_s', 'measurements'))):
        raise ValueError('Rejection does not belong to the current simulated state.')
    context = observation['condition_context']
    config = context['config']
    pairs = []
    for violation in trial.get('violations', []):
        pair = violation.get('geoms') if isinstance(violation, dict) else None
        if (type(pair) is list and len(pair) == 2 and all(type(x) is str for x in pair)
                and pair not in pairs):
            pairs.append(list(pair))
    return {'schema': 'post_grasp_reuse_query/v1', 'goal': goal,
        'episode_id': observation['episode_id'], 'observation_id': observation['observation_id'],
        'observation_revision': observation['revision'], 'rejection_id': rejection['rejection_id'],
        'environment': 'simulation', 'simulator_version': observation['simulator_version'],
        'part_id': config['part']['id'], 'part_kind': part_kind, 'part_shape': config['part']['shape'],
        'part_size_xyz_mm': [size * 1000 for size in config['part']['size_m']],
        'target_id': config['target']['id'], 'robot': config['robot']['model'],
        'gripper': config['robot']['gripper'], 'active_part_condition_id': context['active_part_condition_id'],
        'installed_parts': list(observation['installed_parts']),
        'current_measurements': deepcopy(observation['measurements']),
        'failure_code': trial['failure_code'], 'failure_phase': trial['phase'],
        'rejected_grasp': deepcopy(trial['grasp']), 'contact_geom_pairs': pairs[:8],
        'contact_geom_pairs_total': len(pairs), 'detector_source': trial['source'],
        'contact_evidence_domain': ('nominal_path_candidate' if trial['failure_code'] == 'PLANNED_CONTACT'
                                    else 'preflight_copy_detector'),
        'geometry': revision_geometry(trial), 'measured_seated_pad_clearance_mm': None,
        'geometry_note': 'The predicted pad height relative to the socket rim uses the held pose '
                         'projected to nominal seating. The failed trial did not verify final seating.'}


def run_skill_reuse(*, registry_dir, config, selector=None, part='post_1', memory_dir=DEFAULT_OUTPUT,
                    output_root=DEFAULT_RUNS, progress=None):
    """Run one goal with a frozen local library and one fresh agent selection opportunity."""
    import mujoco
    from frame_assembly_scene import build_frame_scene
    from grasp_skill_library import SkillLibrary
    if selector is None:
        from grasp_skill_selector import select_skill
        selector = select_skill

    folder = _new_run_dir(output_root)
    emit = progress or (lambda event: None)
    goal = 'Install ' + part + ' in its designated target.'
    episode = 'skill-reuse-' + uuid4().hex
    result = {'schema': 'post_grasp_skill_reuse/v1', 'status': 'error', 'run_dir': str(folder),
        'episode_id': episode, 'goal': goal, 'environment': 'simulation',
        'evaluation_scope': 'new_episode_same_development_part_and_failure_trigger',
        'simulator_version': mujoco.__version__, 'API_calls': 0, 'selection_invoked': False,
        'selector_status': None, 'selected_id': None, 'selector_ledger': None, 'selector_transport': None,
        'new_copy_preflights_returned': 0, 'original_execution_started': False,
        'original_stage': None, 'original_scene_assembly_success': False,
        'original_state_unchanged_through_preflights': None, 'original_installed_parts': [],
        'automatic_skill_registration': False, 'error': None}
    skills = before = None
    stage = 'freeze_library'
    try:
        if part != 'post_1':
            raise ValueError('This development episode supports post_1.')
        library = SkillLibrary(registry_dir)
        snapshot = {'snapshot_id': library.snapshot_id, 'document_count': library.document_count,
                    'skill_count': library.skill_count}
        result['library'] = snapshot
        _write_json(folder / 'frozen_library.json', snapshot)
        _write_json(folder / 'selector_config_public.json', config.public())
        _write_json(folder / 'implementation_hashes.json', {
            str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted((ROOT / 'scripts').rglob('*.py'))})
        _write_json(folder / 'goal.json', {'goal': goal, 'part_id': part, 'episode_id': episode})
        stage = 'settle_original_scene'
        emit({'event': 'settling_original_scene', 'run_dir': str(folder), 'goal': goal})
        model, data, registry = build_frame_scene(product_count=10)
        skills = RecordedFrameSkills(model, data, part, registry, set())
        skills.preflight_trace_on_event = emit
        _write_json(folder / 'settled_observation.json', json_safe(skills.settle()))
        before = source_state(skills)
        _write_json(folder / 'source_before_trials.json', before)
        stage = 'development_trigger_preflight'
        first = capture_observation(skills, episode_id=episode, revision=1)
        _write_json(folder / 'trigger_observation.json', first)
        grasp = dict(DEVELOPMENT_GRASP)
        _write_json(folder / 'development_trigger.json', {'grasp': grasp,
            'source': 'declared_fixed_first_grasp_development_case', 'episode_id': episode,
            'observation_id': first['observation_id'], 'observation_revision': first['revision']})
        emit({'event': 'development_trigger_started', 'grasp': grasp})
        trigger = run_recorded_preflight(skills, grasp, observation_revision=first['revision'],
                                         log_dir=folder / 'trigger_preflight')
        result['new_copy_preflights_returned'] += 1
        checked_trial(trigger, grasp, first)
        if trigger['trace_summary'].get('trace_complete') is not True:
            raise ValueError('The trigger trace is incomplete.')
        _write_json(folder / 'trigger_outcome.json', trigger)
        _same_source(skills, before, folder / 'source_after_trigger.json')
        if trigger['accepted']:
            result['status'] = 'trigger_accepted_no_recovery_needed'
            return result
        stage = 'prepare_selection'
        rejection = bind_rejection(first, trigger)
        _write_json(folder / 'trigger_rejection_binding.json', rejection)
        reader = lambda: capture_observation(skills, episode_id=episode, revision=2)
        current = reader()
        _write_json(folder / 'selection_observation.json', current)
        query = build_reuse_query(goal, current, rejection, part_kind=registry[part]['kind'])
        _write_json(folder / 'selection_query.json', query)
        result.update(failure_code=trigger['failure_code'], failure_phase=trigger['phase'],
                      rejection_id=rejection['rejection_id'])
        stage = 'select_skill'
        emit({'event': 'agent_selection_started', 'failure_code': trigger['failure_code'],
              'phase': trigger['phase'], 'library_snapshot_id': library.snapshot_id})
        result.update(selection_invoked=True, API_calls=None)
        decision = selector(deepcopy(query), library, config=config, log_dir=folder / 'selector')
        _write_json(folder / 'selection_receipt.json', json_safe(decision))
        ledger = decision.get('ledger')
        if type(decision.get('API_calls')) is int:
            result['API_calls'] = decision['API_calls']
        elif type(ledger) is dict and type(ledger.get('api_calls')) is int:
            result['API_calls'] = ledger['api_calls']
        result.update(selector_status=decision['status'], selected_id=decision.get('selected_id'),
                      selector_ledger=ledger, selector_transport=decision.get('transport', 'unspecified_callback'))
        _same_source(skills, before, folder / 'source_after_selection.json')
        if (reader() != current or library.snapshot_id != snapshot['snapshot_id']
                or decision.get('snapshot_id', library.snapshot_id) != library.snapshot_id):
            raise ValueError('Scene or frozen library changed during selection.')
        if decision['status'] == 'abstained' and decision.get('selected_id') is None:
            result['status'] = 'selector_abstained'
            return result
        if decision['status'] == 'error':
            result['status'] = 'selector_error'
            return result
        if decision['status'] != 'selected' or type(decision.get('selected_id')) is not str or not decision['selected_id']:
            raise ValueError('Selector returned an invalid decision.')
        stage = 'resolve_selected_document'
        parent, revision = library.resolve(decision['selected_id'], current, rejection)
        resolution = {'selected_id': decision['selected_id'], 'library_snapshot_id': library.snapshot_id,
            'parent_candidate_id': parent['candidate_id'], 'revision_id': revision['revision_id'],
            'code_sha256': revision['code_sha256'], 'episode_id': episode,
            'observation_id': current['observation_id'], 'rejection_id': rejection['rejection_id']}
        _write_json(folder / 'resolved_skill.json', resolution)
        result['resolved_skill'] = resolution
        stage = 'selected_skill_preflight'
        emit({'event': 'selected_skill_preflight_started', 'selected_id': decision['selected_id']})
        tested = preflight_candidate(skills, parent, current, current_reader=reader,
            rejection=rejection, remaining_proposals=1, log_dir=folder / 'selected_skill',
            memory_dir=memory_dir, revision=revision)
        _same_source(skills, before, folder / 'source_after_selected_preflight.json')
        result['original_state_unchanged_through_preflights'] = True
        evaluated, selected = tested['evaluation'], tested['trial']
        if selected is None:
            result.update(status='selected_skill_not_proposed', proposal_reason=evaluated.get('reason'))
            return result
        result['new_copy_preflights_returned'] += 1
        proposal = evaluated['proposal']
        if (proposal['candidate_id'] != revision['revision_id'] or proposal['code_sha256'] != revision['code_sha256']
                or proposal['rejection_id'] != rejection['rejection_id'] or proposal['observation_id'] != current['observation_id']
                or tested['original_state_unchanged'] is not True or selected['trace_summary'].get('trace_complete') is not True):
            raise ValueError('Preflight result does not match the selected skill and current observation.')
        checked_trial(selected, proposal['grasp'], current)
        result['proposed_grasp'] = proposal['grasp']
        _write_json(folder / 'preflight_comparison.json', compare_trials(trigger, selected))
        if not selected['accepted']:
            result['status'] = 'selected_skill_preflight_rejected'
            return result
        stage = 'original_simulation_execution'
        emit({'event': 'original_simulation_started', 'part': part, 'grasp': proposal['grasp']})
        _same_source(skills, before, folder / 'source_before_original_execution.json')
        _execute_original(skills, current, proposal['grasp'], selected, folder, result)
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
        try:
            if before is not None:
                final_state = source_state(skills)
                _write_json(folder / 'source_final.json', final_state)
                result['original_installed_parts'] = sorted(skills.installed)
                if not result['original_execution_started']:
                    result['original_state_unchanged_through_preflights'] = final_state == before
                    if final_state != before:
                        raise ValueError('Copy or selection changed the original simulation state.')
        except (Exception, KeyboardInterrupt) as audit_error:
            result['status'] = 'error'
            result['final_audit_error'] = {'type': type(audit_error).__name__}
        _write_json(folder / 'result.json', result)
        emit({'event': 'run_finished', 'status': result['status'], 'run_dir': str(folder)})


def main(argv=None):
    from grasp_skill_library import DEFAULT_LIBRARY
    from llm_runtime import load_llm_config
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--part', choices=['post_1'], default='post_1')
    parser.add_argument('--library', '--registry-dir', dest='registry_dir', type=Path, default=DEFAULT_LIBRARY)
    parser.add_argument('--llm-config', type=Path, required=True)
    parser.add_argument('--memory-dir', type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--output-root', type=Path, default=DEFAULT_RUNS)
    args = parser.parse_args(argv)
    result = run_skill_reuse(registry_dir=args.registry_dir, config=load_llm_config(args.llm_config),
        part=args.part, memory_dir=args.memory_dir, output_root=args.output_root,
        progress=lambda event: print(json.dumps(event, ensure_ascii=False), flush=True))
    print(json.dumps({k: result.get(k) for k in ('status', 'run_dir', 'selected_id', 'API_calls',
        'proposed_grasp', 'original_scene_assembly_success', 'error')}, ensure_ascii=False, indent=2), flush=True)
    return 0 if result['status'] in ('original_simulation_verified', 'selector_abstained',
        'trigger_accepted_no_recovery_needed', 'selected_skill_not_proposed', 'selected_skill_preflight_rejected') else 1


if __name__ == '__main__':
    raise SystemExit(main())
