"""Recompute a registered development recovery and challenge it with a different real failure."""
import hashlib
import json
from pathlib import Path
import tempfile

from frame_assembly_scene import build_frame_scene
from grasp_candidate_preflight import checked_trial, source_state
from grasp_preflight_trace import RecordedFrameSkills, run_recorded_preflight
from grasp_skill_runtime import bind_rejection, capture_observation, evaluate_candidate, revision_geometry
from run_frame_assembly import restore_episode_data


def _read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def _require(ok, message):
    if not ok:
        raise ValueError('Development post audit: ' + message)


def _original(result, part, expected_grasp, method, selected):
    state = result['graph_states'][part]
    end = result['part_episodes'][part]
    rejected, accepted = state['grasp_trials']
    outbound, returned = state['agent_handoffs']
    proof = returned['proof']
    insertion, = (row for row in result['results'] if row['part_id'] == part)
    _require(rejected['accepted'] is False and accepted['accepted'] is True
             and accepted['grasp'] == expected_grasp == end['executed_approach']['proposed_grasp']
             and outbound['from'] == returned['to'] == 'assembly'
             and outbound['to'] == returned['from'] == 'recovery'
             and outbound['proof']['recovery_method'] == proof['recovery_method'] == method
             and proof['kind'] == 'verified_original_pick' and proof['held_and_lifted'] is True
             and proof['accepted_grasp'] == expected_grasp and proof['selected_id'] == selected
             and part not in end['start']['installed'] and part in end['end']['installed']
             and insertion['status'] == state['status'] == 'success' and insertion['checks']
             and all(v is True for v in insertion['checks'].values()),
             'original preflight, verified pick, handoff or installation differs for ' + part)
    return {'archived_recovery_method': method, 'first_grasp': rejected['grasp'],
            'executed_grasp': expected_grasp, 'archived_selected_skill': selected,
            'archived_original_lift_clearance_mm': 1000 * proof['part_bottom_clearance_m'],
            'archived_installation_position_error_mm': 1000 * insertion['maximum_final_position_error_m']}


def _triangle(f, library, key):
    result, config = _read(f / 'result.json'), _read(f / 'resolved_config.json')
    state = result['graph_states']['post_3']
    old = state['grasp_trials'][0]
    start = result['part_episodes']['post_3']['start']
    _require(config['product_layout'] == 'triangle_three_post' and not config.get('grasp_skill_library')
             and result['requested_sequence'][0] == 'post_3' and start['installed'] == []
             and old['accepted'] is False and old['phase'] == 'insert_lower/preflight'
             and old['failure_code'] == 'PLANNED_CONTACT', 'expected the archived triangular first failure')
    model, _, registry = build_frame_scene(8, layout='triangle_three_post')
    data = restore_episode_data(model, start)
    skills = RecordedFrameSkills(model, data, 'post_3', registry, set(),
                                 transport_strategy=config['transport_strategy'],
                                 empty_approach_strategy=config['empty_approach_strategy'])
    skills.settle()
    source = source_state(skills)
    handoff = state['agent_handoffs'][0]['proof']['source_state']
    _require(all(source[k] == handoff[k] for k in ('integration_sha256', 'host_sha256', 'model_sha256', 'simulation_time_s')),
             'restored scene differs from the original preflight boundary')
    obs = capture_observation(skills, episode_id=f.name + '-scope-replay', revision=old['observation_revision'])
    with tempfile.TemporaryDirectory(prefix='dev-triangle-post-preflight-') as directory:
        trial = run_recorded_preflight(skills, old['grasp'], observation_revision=obs['revision'], log_dir=directory)
        _require(source_state(skills) == source and all(trial.get(k) == v for k, v in old.items()),
                 'clone changed the original scene or failed to reproduce the archived rejection')
        checked_trial(trial, old['grasp'], obs)
        facts = revision_geometry(trial)
        _require(trial['trace_summary']['trace_complete'] is True and facts['pad_contacts_socket_wall']
                 and facts['pick_completed_in_copy'] and facts['predicted_pad_to_rim_clearance_mm'] is not None,
                 'rejection does not have recorded pick and pad-wall evidence')
        rejection = bind_rejection(obs, trial)
        try:
            library.resolve(key, obs, rejection)
        except ValueError as exc:
            if str(exc) != 'Registered skill outside verified conditions in this episode.':
                raise
            reason = str(exc)
        else:
            raise ValueError('Registered old skill unexpectedly resolved on the triangular geometry.')
    actual_scope = obs['condition_context']['active_part_condition_id']
    _require(actual_scope != library.read(key)['scope']['condition_id'], 'expected distinct geometry fingerprints')
    original = _original(result, 'post_3', state['grasp_trials'][1]['grasp'], 'numeric_grasp_revision', None)
    return {**original, 'product': config['product_layout'], 'part': 'post_3', 'target': skills.config['target']['id'],
            'condition_id': actual_scope, 'replayed_integration_sha256': source['integration_sha256'],
            'clone_failure_phase': trial['phase'], 'clone_failure_code': trial['failure_code'],
            'clone_contact_geoms': [v['geoms'] for v in trial['violations']],
            'clone_geometry': facts, 'original_unchanged_during_preflight': True,
            'scope_denial': reason}


def check_post_scope(repo, four_folder, triangle_folder, library):
    """Use stored 22R/22S decisions, the frozen SkillLibrary and local development provenance."""
    repo, four, triangle = map(Path, (repo, four_folder, triangle_folder))
    folder = four / 'grasp_recovery/post_1'
    receipt = _read(folder / 'decision_receipt.json')
    obs = _read(folder / 'trial_02/observation.json')
    rejection = _read(folder / 'trial_01/rejection.json')
    key = receipt['selected_id']
    _require(receipt['library_snapshot_id'] == library.snapshot_id
             and receipt['observation_id'] == obs['observation_id']
             and receipt['rejection_id'] == rejection['rejection_id']
             and _read(folder / 'trial_01/trial.json') == rejection['trial'],
             'registered episode identity or bound rejection differs')
    parent, revision = library.resolve(key, obs, rejection)
    memory = repo / 'runtime/memory/simulation/grasp_v2'
    lesson = parent['input_packet']['lesson_source']
    evaluated = evaluate_candidate(parent, obs, current_reader=lambda: obs, rejection=rejection,
                                   remaining_proposals=1, revision=revision, memory_dir=memory)
    archived = _read(folder / 'evaluation.json')
    _require(evaluated['status'] == 'proposed' and evaluated['proposal'] == archived['proposal']
             and evaluated['proposal']['code_sha256'] == receipt['code_sha256']
             and evaluated['proposal']['grasp'] == receipt['proposed_grasp']
             and evaluated['API_calls'] == evaluated['robot_commands'] == 0
             and not evaluated['execution_authorized'], 'frozen code did not reproduce its bounded proposal')
    saved = _read(four / 'result.json')
    _require(saved['graph_states']['post_1']['grasp_recovery_receipt'] == receipt,
             'stored selector receipt differs from the graph state')
    original = _original(saved, 'post_1', evaluated['proposal']['grasp'], 'verified_skill_search', key)
    _require(saved['graph_states']['post_1']['grasp_trials'] == [rejection['trial'], _read(folder / 'trial_02/trial.json')],
             'original graph and recorded preflight trials differ')
    return {'registered_case_recomputed': True, 'triangle_resolved': False, 'triangle_denial_verified': True,
            'scope_probe_model_calls': 0, 'scope_probe_live_robot_commands': 0, 'triangle_clone_preflights': 1,
            'registered_case': {**original, 'part': 'post_1', 'target': 'mate_post_1',
                                'condition_id': obs['condition_context']['active_part_condition_id'],
                                'skill_code_sha256': receipt['code_sha256'], 'vm_grasp': evaluated['proposal']['grasp'],
                                'registration_library_snapshot_id': library.snapshot_id,
                                'provenance_lesson': {'filename': Path(lesson['path']).name, 'sha256': lesson['sha256']},
                                'provenance_grasp_v2_sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                                              for p in sorted(memory.glob('*.json'))}},
            'triangle_case': _triangle(triangle, library, key)}
