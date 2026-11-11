"""Capture the first sleeve search/decision requests on known development checkpoints."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from assembly_graph import run_assembly_graph
from export_grasp_experience import digest
from frame_assembly_scene import build_frame_scene
from frame_assembly_skills import FrameSkills
from llm_runtime import ApiLedger, load_llm_config
from openai_policy import OpenAIAssemblyPolicy
from run_frame_assembly import episode_boundary, restore_episode_data
from sleeve_memory_policy import FirstGraspMemory


DEVELOPMENT_RUNS = {
    '20261111T134520_867280Z_047a16bf': ('four_post', 10, 'four_post_frame_wide'),
    '20261111T160843_513944Z_29d9f165': ('triangle_three_post', 8, 'triangle_three_post_v1'),
}
QUERY = 'square sleeve gripper pad tabletop clearance before closure'
MEMORY_INSTRUCTION = (
    ' You searched for experiences before this first proposal. In your reason identify '
    'the source part and explain how its observation informed your choice, or why you '
    'did not apply it. Select the parameters for the present geometry and observation.'
)


def _require(ok, message):
    if not ok:
        raise ValueError('Development sleeve packet: ' + message)


class _Captured(Exception):
    """The decision request is complete; no transport or action is allowed."""


class _LocalCapture(OpenAIAssemblyPolicy):
    def __init__(self, config, ledger):
        super().__init__(config, client=None, ledger=ledger, role='assembly')
        self.requests = {}

    def _call_model(self, request, prepared_event):
        _require(prepared_event not in self.requests, 'unexpected repeated request')
        self.requests[prepared_event] = request
        if prepared_event == 'request_prepared':
            raise _Captured()
        _require(prepared_event == 'sleeve_search_prepared', 'unexpected model call')
        return {'id': 'offline-fixed-search', 'status': 'completed', 'output': [
            {'type': 'function_call', 'name': 'search_sleeve_experience',
             'arguments': json.dumps({'query': QUERY})}]}


def _raise_error(error):
    raise error


def _capture(model, registry, case, start, part, task, config, record, directory):
    installed = set(start['installed'])
    data = restore_episode_data(model, start)
    _require(episode_boundary(model, data, registry, installed) == start, 'checkpoint did not restore exactly')
    skills = FrameSkills(model, data, part, registry, installed,
                         empty_approach_strategy=case['empty_approach_strategy'],
                         transport_strategy=case['transport_strategy'])
    policy = _LocalCapture(config, ApiLedger(config, directory/'offline_events.jsonl'))
    hook = FirstGraspMemory(record, directory/'search', '22U-development-dry-run') if record else None
    observed = []

    def event(row):
        _require(row['node'] in ('settle', 'observe'), 'unexpected original-scene action')
        if row['node'] == 'observe':
            observed.append(episode_boundary(model, data, registry, installed))

    try:
        run_assembly_graph(skills, mode='roles', policies={'assembly': policy, 'recovery': None},
                           failure_handler=_raise_error, on_event=event, task_id=task + '/' + part,
                           insert_result_skill='insert_sleeve/v1', propose_grasp=True, finish_on_verified_insert=True,
                           recovery_budget=case['recovery_budget'], transport_replan_budget=case['transport_replan_budget'],
                           first_grasp_memory=hook)
    except _Captured:
        pass
    else:
        raise ValueError('Graph ended without an intercepted first decision')
    _require(len(observed) == 1 and observed[0] == episode_boundary(model, data, registry, installed)
             and not skills.results and not policy.ledger.model_requests, 'state or request count changed')
    return policy.requests, observed[0]['integration_sha256']


def check_sleeve_packets(repo, folder, part, record):
    """Replay only a saved start state; prior agent replies or future outcomes never enter the graph."""
    folder = Path(folder)
    _require(folder.name in DEVELOPMENT_RUNS, 'requires a 22R/22S development run')
    layout, count, task = DEVELOPMENT_RUNS[folder.name]
    case = json.loads((folder/'resolved_config.json').read_text(encoding='utf-8'))
    _require(case.get('product_layout', 'four_post') == layout and case['product_parts_present'] == count
             and part in case['sleeve_memory']['targets'], 'unexpected scene or sleeve')
    start = json.loads((folder/'result.json').read_text(encoding='utf-8'))['part_episodes'][part]['start']
    config = load_llm_config(Path(repo)/'runtime/freezes/sim-prototype-v1.0.0/config/llm.astra-high-native-frame.toml')
    model, _, registry = build_frame_scene(count, layout=layout)
    captures = {}
    settled = {}
    with TemporaryDirectory(prefix='22u-sleeve-') as temp:
        for arm in ('A', 'B', 'C'):
            directory = Path(temp)/arm
            directory.mkdir()
            captures[arm], settled[arm] = _capture(model, registry, case, start, part, task, config,
                                                  None if arm == 'A' else record, directory)
    _require(len(set(settled.values())) == 1, 'arms started the decision in different states')
    a, b, c = (captures[arm] for arm in ('A', 'B', 'C'))
    _require(set(a) == {'request_prepared'} and set(b) == {'sleeve_search_prepared', 'request_prepared'}
             and b == c, 'search opportunity or B/C packets differ')
    base, treatment = a['request_prepared'], b['request_prepared']
    control_input, with_memory = (json.loads(req['input']) for req in (base, treatment))
    _require(control_input['experiences'] == [] and control_input['recipes'] == []
             and control_input['failure_history'] == [] and control_input['grasp_task']['previous_trials'] == []
             and control_input['part_id'] == part and control_input['observation_revision'] == 1,
             'expected a first observation without past cards')
    cards = with_memory.pop('experiences')
    _require(len(cards) == 1 and cards[0]['record_id'] == record['experience_id']
             and set(cards[0]) == {'record_id','source_part','scope','rejected','repair','original_pick','original_insert','use'},
             'B/C did not receive the frozen advisory sleeve experience')
    _require({**with_memory, 'experiences': []} == control_input, 'non-memory observation changed')
    _require({**treatment, 'input': base['input'], 'instructions': base['instructions']} == base
             and treatment['instructions'] == base['instructions'] + MEMORY_INSTRUCTION,
             'unregistered difference in prompt, tools, model or settings')
    # Use the archived provider journal only after reconstruction. The search terms
    # and resulting actions from that run never enter the graph.
    archived_requests = {}
    with (folder/'llm_events.jsonl').open(encoding='utf-8') as journal:
        for line in journal:
            event = json.loads(line)
            if event['event'] not in b or event.get('agent_role') != 'assembly':
                continue
            packet = json.loads(event['request']['input'])
            if packet.get('part_id') == part and packet.get('observation_revision') == 1:
                _require(event['event'] not in archived_requests, 'ambiguous archived request')
                archived_requests[event['event']] = event['request']
    _require(archived_requests == b, 'first search/decision do not match the actual development journal')
    return {'development_run': folder.name, 'sleeve': part, 'mode': 'offline_request_capture',
            'provider_calls': 0, 'executed_actions': 0, 'search_terms_source': 'fixed_development_query',
            'search_text': QUERY, 'settled_integration_sha256': settled['A'],
            'decision_request_sha256': {arm: digest(requests['request_prepared']) for arm, requests in captures.items()},
            'search_request_sha256': {'A': None, 'B': digest(b['sleeve_search_prepared']), 'C': digest(c['sleeve_search_prepared'])},
            'experience_ids': {'A': [], 'B': [record['experience_id']], 'C': [record['experience_id']]},
            'matches_archived_first_search_and_decision': True,
            'request_differences_A_to_B': ['input.experiences','instructions.memory_citation_suffix'],
            'identical_B_C_first_sleeve_requests': True}
