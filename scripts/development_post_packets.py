"""Audit model-visible recovery information for the frozen four-post development episode."""
import json
from pathlib import Path


def _read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def _require(condition, message):
    if not condition:
        raise ValueError('Development post input check: ' + message)


class _RequestCaptured(Exception):
    """Stop the development graph at the model transport boundary."""


def check_post_packets(repo, four_folder):
    from assembly_graph import run_assembly_graph
    from audit_agent_handoffs import _selector, _turns
    from export_grasp_experience import digest
    from failure_report import json_safe
    from frame_assembly_scene import build_frame_scene
    from frame_assembly_skills import FrameSkills
    from graph_grasp_recovery import DevelopmentFirstGrasp
    from grasp_candidate_preflight import source_state
    from llm_runtime import load_llm_config
    from openai_policy import build_request
    from run_frame_assembly import episode_boundary, restore_episode_data

    repo, folder = Path(repo).resolve(), Path(four_folder).resolve()
    settings, result = _read(folder/'resolved_config.json'), _read(folder/'result.json')
    _require(settings.get('fixed_first_grasp_trigger') is True and settings['sequence'][0] == 'post_1'
             and settings['product_parts_present'] == 10 and result['status'] == 'success',
             'expected the original development interference episode')
    base = folder/'grasp_recovery'/'post_1'
    selector = _read(base/'selector'/'result.json')
    query = _read(base/'selection_query.json')
    receipt = _read(base/'decision_receipt.json')
    turns = [t for t in _turns(folder, result, settings) if t[0]['event'] == 'recovery_search_prepared']
    _require(len(turns) == selector['API_calls'], 'extra or missing selector turns')
    _selector(folder, query['handoff'], receipt, iter(turns))
    operations = selector['events']
    _require([op['tool'] for op in operations] == ['search_library','read_documents','select_skill'],
             'unexpected sequence of historical selector tools')
    read_cards = operations[1]['results']
    _require({c['kind'] for c in read_cards} == {'skill','memory'}, 'missing historical skill/memory texts')
    final_request = _read(base/'selector'/'request_3.json')
    selector_input = json.loads(final_request['input'])
    _require(selector_input['tool_history'][1]['results'] == read_cards,
             'actual provider request did not contain the read cards')
    saved_trial = _read(base/'trial_01'/'trial.json')

    model, _, registry = build_frame_scene(10, transfer_obstacle=settings['transfer_obstacle'])
    boundary = result['part_episodes']['post_1']['start']
    data = restore_episode_data(model, boundary)
    _require(episode_boundary(model, data, registry, set()) == boundary, 'initial simulator state mismatch')
    skills = FrameSkills(model, data, 'post_1', registry, set(), transport_strategy=settings['transport_strategy'],
                         empty_approach_strategy=settings['empty_approach_strategy'])
    config = load_llm_config(repo/'config'/'llm.astra-high-native-frame.toml')
    _require(config.model == settings['llm_config']['model'] and
             config.reasoning_effort == settings['llm_config']['reasoning_effort'], 'model profile mismatch')
    captured = {}

    class CapturePostRequest:
        def propose(self, context):
            _require(context['active_role'] == 'recovery', 'unexpected additional assembly decision')
            request = build_request(config, context, role='recovery')
            captured.update(request)
            raise _RequestCaptured

    capture = CapturePostRequest()

    def check_event(row):
        if row['node'] == 'check_grasp':
            _require(row['grasp_trial']['accepted'] is False, 'unexpected acceptance; stopped before original motion')
        _require(row['node'] != 'execute', 'original execution is outside this probe')

    def fail(exc):
        raise exc

    try:
        # On post_1 the v1 frame runner never attaches its sleeve memory hook.
        # With the code library disabled its recovery goes to the numeric proposal.
        run_assembly_graph(skills, failure_handler=fail, mode='roles',
                           policies={'assembly': DevelopmentFirstGrasp(capture), 'recovery': capture},
                           on_event=check_event, propose_grasp=True, finish_on_verified_insert=True,
                           task_id='four_post_frame_wide/post_1')
    except _RequestCaptured:
        pass
    _require(bool(captured), 'no recovery request reached the capture boundary')
    packet = json.loads(captured['input'])
    history = packet['grasp_task']['previous_trials']
    _require(packet['part_id'] == query['part_id'] == 'post_1' and
             packet['observation_revision'] == query['observation_revision'] and
             len(history) == 1 and packet['experiences'] == [] and packet['recipes'] == [],
             'frozen no-library post branch delivered unexpected inputs')
    proof = packet['handoff']['proof']
    source = source_state(skills)
    archived_state = query['handoff']['proof']['source_state']
    _require(proof['source_state'] == archived_state and
             all(source[k] == v for k,v in archived_state.items()) and not skills.results,
             'four-post preflight source scene or host changed')
    _require(proof['recovery_method'] == 'numeric_grasp_revision' and
             query['handoff']['proof']['recovery_method'] == 'verified_skill_search', 'unexpected recovery routes')
    # Provenance fields added to recorded checks are not physical detector differences.
    core = {k:v for k,v in saved_trial.items() if k not in ('trace_summary','trace_paths')}
    _require(history[0] == core and core['accepted'] is False and
             core['phase'] == 'insert_lower/preflight' and core['failure_code'] == 'PLANNED_CONTACT',
             'measured failure or rejection changed in the no-library replay')
    known_ids = [card['document_id'] for card in read_cards]
    _require(all(doc not in json.dumps(captured) for doc in known_ids) and
             all(tool['name'] == 'propose_assembly_action' for tool in captured['tools']),
             'registered post card or selector capability leaked into the numeric request')
    return json_safe({
        'development_episode': folder.name,
        'source_state': archived_state, 'same_preflight_rejection': True,
        'part_id': packet['part_id'], 'target_id': packet['target_id'],
        'rejected_grasp': core['grasp'], 'detector': core['failure_code'], 'phase': core['phase'],
        'contact_pairs': [v['geoms'] for v in core['violations'] if 'geoms' in v],
        'lowest_pad_relative_to_rim_mm': core['held_pad_geometry']['predicted_lowest_pad_height_relative_to_rim_mm'],
        'frozen_no_library_post': {
            'evidence': 'fresh_no_library_graph_and_preflight; request_built_locally_before_model_call',
            'provider_calls': 0, 'role': packet['active_role'], 'request_sha256': digest(captured),
            'past_card_ids': [], 'local_failure_count': len(history), 'tool': captured['tools'][0]['name'],
            'original_pick_executed': False, 'stop_at': 'before_recovery_proposal'},
        'archived_library_post': {
            'evidence': 'archived_provider_requests_responses_and_tool_operations',
            'archived_provider_calls': len(turns), 'new_provider_calls': 0, 'role': 'recovery',
            'tools_used': [op['tool'] for op in operations], 'read_card_ids': known_ids,
            'selected_id': receipt['selected_id'], 'response_ids': [turn[-1]['response_id'] for turn in turns],
            'final_request_sha256': digest(final_request)},
        'b_document_ids': [card['record_id'] for card in packet['experiences']],
        'c_read_document_ids': known_ids,
        'same_historical_text_visible_in_v1': False,
        'recorded_only_failure_keys': sorted(set(saved_trial) - set(history[0])),
        'scope': 'development diagnostics for existing routing; no evaluation arm, model comparison or assembly outcome'
    })
