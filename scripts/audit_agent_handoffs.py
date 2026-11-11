"""Independently correlate role ownership, measured handoffs and provider turns in a fresh full-frame run."""
from collections import Counter
from decimal import Decimal
import hashlib
from itertools import groupby
import json
from pathlib import Path
import re


def _json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def _rows(path):
    with path.open(encoding='utf-8') as stream:
        return [json.loads(line) for line in stream]


def _require(condition, message):
    if not condition:
        raise ValueError('Role audit: ' + message)


def _digest(value):
    raw = json.dumps(value, ensure_ascii=False, separators=(',', ':'), sort_keys=True, allow_nan=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def _turns(folder, result, config):
    """Group the serial journal and recompute each agent's totals from the provider usages."""
    turns, seen = [], set()
    totals = {role: Counter() for role in ('assembly', 'recovery')}
    llm = config['llm_config']
    for event in _rows(folder / 'llm_events.jsonl'):
        if event['event'] in ('request_prepared', 'recovery_search_prepared', 'sleeve_search_prepared'):
            turns.append([])
        _require(turns and event.get('agent_role') in totals, 'untagged or unowned journal event')
        turns[-1].append(event)
    for turn in turns:
        names = [e['event'] for e in turn]
        normal = names[0] == 'request_prepared'
        expected = ['request_prepared', 'input_count_started', 'input_count_completed', 'model_request_started',
                    'model_response_received', 'proposal_validated'] if normal else [
                    'recovery_search_prepared', 'input_count_started', 'model_request_started', 'model_response_received']
        memory_search = names[0] == 'sleeve_search_prepared'
        if memory_search:
            expected = ['sleeve_search_prepared','input_count_started','input_count_completed','model_request_started',
                        'model_response_received','sleeve_search_validated']
        role = turn[0]['agent_role']
        _require(names == expected and all(e['agent_role'] == role for e in turn), 'incomplete or cross-role model turn')
        events = {e['event']: e for e in turn}
        start, response = events['model_request_started'], events['model_response_received']
        response_id = response['response_id']
        _require(response_id and response_id not in seen and response['response_status'] == 'completed',
                 'missing, reused or incomplete provider response')
        seen.add(response_id)
        _require(start['model'] == llm['model'] and turn[0]['request_hash'] == turn[1]['request_hash'],
                 'configured model or counted request differs')
        if normal or memory_search:
            digest = hashlib.sha256(json.dumps(turn[0]['request'], sort_keys=True).encode()).hexdigest()
            _require(response_id == turn[-1]['response_id'] and
                     start['request_hash'] == digest == turn[0]['request_hash'], 'proposal is not bound to provider turn')
            if memory_search:
                _require(role == 'assembly', 'first-grasp retrieval outside the assembly agent')
        else:
            _require(role == 'recovery', 'catalogue operation outside recovery agent')
        usage = response['usage']
        inputs, outputs = usage['input_tokens'], usage['output_tokens']
        cached = usage['input_tokens_details'].get('cached_tokens', 0)
        writes = usage['input_tokens_details'].get('cache_write_tokens', 0)
        reasoning = usage['output_tokens_details'].get('reasoning_tokens', 0)
        _require(all(type(v) is int and v >= 0 for v in (inputs, outputs, cached, writes, reasoning))
                 and inputs + outputs == usage['total_tokens'] and cached + writes <= inputs and reasoning <= outputs,
                 'invalid token accounting')
        dollars = ((inputs - cached - writes) * Decimal(llm['input_usd_per_million'])
                   + cached * Decimal(llm['cached_input_usd_per_million'] or llm['input_usd_per_million'])
                   + writes * Decimal(llm['cache_write_30m_usd_per_million'] or '0')
                   + outputs * Decimal(llm['output_usd_per_million'])) / Decimal(1_000_000)
        totals[role].update(api_calls=1, input_tokens=inputs, output_tokens=outputs, reasoning_tokens=reasoning,
                            known_api_cost_estimate_usd=dollars)
    for role, observed in [*totals.items(), ('episode', totals['assembly'] + totals['recovery'])]:
        reported = result['llm_usage'] if role == 'episode' else result['agent_usage'][role]
        _require(reported['unknown_usage_requests'] == 0 and observed['api_calls'] > 0,
                 'missing completed real turns for ' + role)
        for key in ('api_calls', 'input_tokens', 'output_tokens', 'reasoning_tokens', 'known_api_cost_estimate_usd'):
            _require(abs(Decimal(str(reported[key])) - observed[key]) <= Decimal('0.000000000001'),
                     f'{role} {key} disagrees with provider responses')
    _require(set(result['agent_usage']) == set(totals) and len(turns) == result['api_calls'],
             'unknown agent, omitted or duplicated charge')
    return turns


def _selector(folder, handoff, receipt, turns):
    base = folder / 'grasp_recovery' / handoff['part_id']
    query, selector = _json(base / 'selection_query.json'), _json(base / 'selector/result.json')
    _require(query['handoff'] == handoff and query['part_id'] == handoff['part_id'] and
             query['observation_revision'] == handoff['observation_revision'] and
             selector['transport'] == 'openai_responses' and selector['status'] == receipt['selection_status'] == 'selected'
             and selector['selected_id'] == receipt['selected_id'] and
             selector['API_calls'] == len(selector['events']) == receipt['API_calls'] and
             receipt == _json(base / 'decision_receipt.json'), 'unbound skill-selection receipt')
    history = []
    for i, operation in enumerate(selector['events'], 1):
        turn = next(turns)
        request, raw = [_json(base / f'selector/{kind}_{i}.json') for kind in ('request', 'response')]
        request_base = {k: v for k, v in request.items() if k not in ('max_output_tokens', 'store', 'service_tier')}
        packet = json.loads(request['input'])
        response = turn[-1]
        _require(turn[0]['event'] == 'recovery_search_prepared' and turn[0]['round'] == i
                 and turn[0]['request_hash'] == _digest(request_base) and turn[-2]['request_hash'] == _digest(request)
                 and response['agent_role'] == 'recovery' and response['response_id'] == raw['id']
                 and response['model'] == raw['model'] and response['usage'] == raw['usage']
                 and response['response_status'] == raw['status'] == 'completed' and packet['current'] == query
                 and packet['tool_history'] == history, 'selector request, role, history or response disagrees')
        calls = [item for item in raw['output'] if item['type'] == 'function_call']
        _require(len(calls) == 1 and calls[0]['name'] == operation['tool'] and
                 json.loads(calls[0]['arguments']) == operation['arguments'] and
                 _json(base / f'selector/operation_{i}.json') == operation, 'selector operation differs from response')
        history.append(operation)
    _require(history and history[-1]['tool'] == 'select_skill'
             and history[-1]['arguments']['document_id'] == receipt['selected_id'], 'wrong final selection')
    original = _json(base / 'trial_01/source_after.json')
    _require(all(original[k] == v for k, v in handoff['proof']['source_state'].items()), 'changed original scene at transfer')


def audit_first_grasp_search(folder, part, revision, turn, next_request, memory_id):
    from sleeve_grasp_memory import retrieve_experience
    receipt = _json(folder/'memory_search'/part/'retrieval.json')
    origin = _json(folder/'sleeve_memory_source.json')
    _require(turn[0]['event'] == 'sleeve_search_prepared' and origin['experience_id'] == memory_id,
             'expected the selected sleeve experience and a distinct search call')
    query = json.loads(turn[0]['request']['input'])
    verified = turn[-1]
    _require(verified['event'] == 'sleeve_search_validated' and verified['agent_role'] == 'assembly'
             and receipt['search_text'] == verified['query_text'] == json.loads(verified['tool_call']['arguments'])['query'].strip()
             and verified['tool_call']['name'] == 'search_sleeve_experience'
             and verified['response_id'] == receipt['response_id']
             and receipt['initial_query']['episode_id'] == receipt['episode_id'] == folder.name
             and query['part_id'] == receipt['part_id'] == part == next_request['part_id'] == receipt['initial_query']['part_id']
             and receipt['observation_revision'] == revision == query['observation_revision'] == next_request['observation_revision']
             and query['geometry'] == next_request['grasp_task']['geometry'] and next_request['grasp_task']['previous_trials'] == [],
             'sleeve search not bound to this first decision')
    hit = retrieve_experience(origin, receipt['initial_query'], search_text=receipt['search_text'])
    cards = [hit] if hit else []
    _require(receipt['cards'] == next_request['experiences'] == cards and
             receipt['experience_ids'] == [c['record_id'] for c in cards] and receipt['status'] == ('hit' if hit else 'miss'),
             'retrieved experience differs from the assembly prompt')


def _graph_part(folder, part, state, phase, rows, turns, fixed, memory_id=None):
    owner, handoffs, checked = 'assembly', [], []
    verified_pick = False
    authorized = None
    executed = []
    for index, row in enumerate(rows):
        node, rev, action = row['node'], row['observation_revision'], row['action']
        previous = rows[index - 1] if index else None
        if 'handoff' in row:
            h = row['handoff']
            proof = h['proof']
            _require(node == 'observe' and h['part_id'] == part and h['from'] == owner
                     and h['observation_revision'] == rev and previous and rev == previous['observation_revision'] + 1,
                     'handoff lacks fresh observation or owner')
            stamp = proof['source_state']
            _require(stamp['simulation_time_s'] == row['simulation_time_s'] and all(
                re.fullmatch('[0-9a-f]{64}', stamp[k]) for k in ('integration_sha256', 'model_sha256', 'host_sha256')),
                'missing physical-state fingerprint')
            if owner == 'assembly':
                _require(not handoffs and h['to'] == 'recovery' and previous['node'] == 'check_grasp'
                         and checked[-1]['accepted'] is False and proof['kind'] == 'rejected_preflight'
                         and proof['accepted'] is False and checked[-1]['grasp'] == proof['rejected_grasp']
                         and checked[-1]['failure_code'] == proof['failure_code'] and checked[-1]['phase'] == proof['phase']
                         and checked[-1]['observation_revision'] == proof['rejected_revision'], 'unproved initial rejection')
                _require(proof['recovery_method'] == ('verified_skill_search' if fixed else 'numeric_grasp_revision'),
                         'unexpected recovery method')
            else:
                _require(len(handoffs) == 1 and h['to'] == 'assembly' and verified_pick and
                         previous['node'] == 'verify' and previous['completed'] == ['pick'] and
                         previous['simulation_time_s'] == row['simulation_time_s'] and proof['held_and_lifted'] is True
                         and proof['kind'] == 'verified_original_pick' and proof['part_bottom_clearance_m'] >= .05
                         and proof['source_state']['model_sha256'] == handoffs[0]['proof']['source_state']['model_sha256']
                         and proof['recovery_method'] == handoffs[0]['proof']['recovery_method']
                         and proof['preflight_revision'] == checked[-1]['observation_revision']
                         and proof['accepted_grasp'] == checked[-1]['grasp'] == phase['executed_approach']['proposed_grasp']
                         and proof['selected_id'] == (state['grasp_recovery_receipt']['selected_id'] if fixed else None),
                         'handoff before original verified pick or with a different grasp')
            owner = h['to']
            handoffs.append(h)
        _require(row['active_role'] == owner, f'{part} {node} ran outside its owner')
        if node == 'check_grasp':
            trial = row['grasp_trial']
            saved = state['grasp_trials'][len(checked)]
            _require(action == authorized and trial['grasp'] == saved['grasp'] == action['grasp'] and trial['accepted'] is saved['accepted']
                     and trial['observation_revision'] == saved['observation_revision'] == rev, 'substituted preflight')
            checked.append(trial)
        elif node == 'decide':
            authorized = action
            if fixed and not checked:
                _require(owner == 'assembly' and action['part_id'] == part and action['skill'] == 'pick'
                         and action['grasp'] == {'x_mm': 0, 'y_mm': 0, 'z_mm': 1, 'jaw_yaw_deg': 0},
                         'unbilled fixture differs from declared development trigger')
                continue
            search_turn = next(turns) if memory_id and not checked else None
            turn = next(turns)
            _require(turn[0]['event'] == 'request_prepared' and turn[0]['agent_role'] == owner and
                     turn[-1]['proposal'] == action, 'validated proposal differs from executing graph decision')
            request = turn[0]['request']
            packet = json.loads(request['input'])
            if search_turn is not None:
                audit_first_grasp_search(folder, part, rev, search_turn, packet, memory_id)
                _require(row['experience_ids'] == [c['record_id'] for c in packet['experiences']],
                         'graph experience IDs differ from memory-assisted decision')
            _require(packet['part_id'] == part and packet['active_role'] == owner
                     and packet['observation_revision'] == rev == action['observation_revision']
                     and action['skill'] in packet['allowed_skills'] and f'You are the {owner} agent.' in request['instructions'],
                     'provider context differs from owner or fresh observation')
            handoff = handoffs[-1] if handoffs and handoffs[-1]['observation_revision'] == rev else None
            _require(packet.get('handoff') == handoff, 'provider missed or received an unrelated transfer')
        elif node == 'recover_grasp':
            _require(fixed and owner == 'recovery' and len(handoffs) == 1 and
                     row['grasp_recovery_receipt'] == state['grasp_recovery_receipt'] and action['part_id'] == part
                     and action['observation_revision'] == rev == row['grasp_recovery_receipt']['observation_revision']
                     and action['grasp'] == row['grasp_recovery_receipt']['proposed_grasp'], 'unowned selector execution')
            _selector(folder, handoffs[0], row['grasp_recovery_receipt'], turns)
            authorized = action
        elif node in ('execute', 'verify'):
            _require(action == authorized and action['skill'] in ('pick', 'insert') and
                     action['part_id'] == part and action['observation_revision'] == rev, 'wrong execution binding')
            if node == 'execute':
                executed.append(action['skill'])
            if action['skill'] == 'pick':
                _require(checked and checked[-1]['accepted'] is True and checked[-1]['grasp'] == action['grasp']
                         and checked[-1]['observation_revision'] == rev, 'unapproved original pick')
                if node == 'verify':
                    _require(previous['node'] == 'execute' and previous['action'] == action
                             and row['completed'] == ['pick'], 'missing original pick verification')
                    verified_pick = True
            elif action['skill'] == 'insert':
                _require(verified_pick and owner == 'assembly', 'insert before verified ownership')
    _require(handoffs == state['agent_handoffs'] and len(handoffs) in (0, 2) and (not fixed or len(handoffs) == 2)
             and executed == ['pick', 'insert'] and len(checked) == len(state['grasp_trials']) and rows[-1]['node'] == 'verify'
             and rows[-1]['action']['skill'] == 'insert' and rows[-1]['completed'] == ['pick', 'insert']
             and rows[-1]['completion_source'] == 'physics_verifier', 'incomplete role episode for ' + part)
    return len(handoffs)


def audit_agent_handoffs(folder, result, config):
    """Raise on conflicting evidence. Requires the fixed-interference, two-role, full development episode."""
    folder = Path(folder)
    _require(config.get('agent_roles') == ['assembly', 'recovery'] and config.get('budget_scope') == 'shared_episode'
             and config.get('fixed_first_grasp_trigger') is True and result['orchestration'] == 'roles'
             and result['status'] == 'success' and result['robot_installed_parts'] == 9
             and result['ten_part_assembly_complete'] is True and result['resume'] is None,
             'expected fresh full-frame roles run with the declared first-post interference')
    ledger = _turns(folder, result, config)
    turns = iter(ledger)
    blocks = [(part, list(rows)) for part, rows in groupby(_rows(folder / 'graph_events.jsonl'),
                                                           key=lambda row: row['active_part_id'])]
    _require([p for p, _ in blocks] == result['execution_order'] == config['sequence'] and blocks[0][0] == 'post_1',
             'graph events out of physical installation order')
    try:
        memory = config.get('sleeve_memory', {})
        counts = {p: _graph_part(folder, p, result['graph_states'][p], result['part_episodes'][p], rows, turns, p == 'post_1',
                                 memory['experience_id'] if p in memory.get('targets', []) else None)
                  for p, rows in blocks}
    except StopIteration as exc:
        raise ValueError('Role audit: required graph decision lacks provider evidence.') from exc
    _require(next(turns, None) is None, 'provider calls with no corresponding graph decision')
    return {'handoffs_by_part': counts, 'provider_turns': len(ledger), 'agent_usage': result['agent_usage']}
