"""Resume only a stopped, unexecuted grasp search; preserve evidence and global budget."""
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import shutil

from frame_assembly_skills import FrameSkills
from failure_report import json_safe


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def prepare_resume(folder, model, registry, config, options, restore, boundary):
    folder = Path(folder).resolve()
    parent = json.loads((folder / 'result.json').read_text(encoding='utf-8'))
    settings = json.loads((folder / 'resolved_config.json').read_text(encoding='utf-8'))
    if (parent['status'] != 'stopped' or parent['error'] != 'Grasp proposal budget exhausted before pick.'
            or settings['policy_kind'] != 'openai_responses' or settings['orchestration'] != 'single'
            or settings['llm_config'] != config.public() or settings['registry'] != json_safe(registry)
            or any(settings[key] != value for key, value in options.items())):
        raise ValueError('Resume requires the same scene, settings and global API budget after a pre-pick stop.')
    order = list(registry)
    done = parent['execution_order']
    if done != order[:len(done)] or set(done) != set(parent['completed_installations']) or len(done) == len(order):
        raise ValueError('Completed installations must be a verified prefix of this assembly.')
    previous = None
    for name in done:
        ep = parent['part_episodes'][name]
        result = next(r for r in parent['results'] if r['part_id'] == name)
        if (parent['graph_states'][name]['status'] != 'success' or not all(result['checks'].values())
                or (previous is not None and ep['start'] != previous)):
            raise ValueError('Missing successful, continuous prefix evidence.')
        previous = ep['end']
    active = order[len(done)]
    state = parent['graph_states'][active]
    trials = state['grasp_trials']
    if (state['status'] != 'stopped' or state['completed'] or len(trials) != 3
            or state['policy_calls'] != 3 or any(t['accepted'] is not False for t in trials)):
        raise ValueError('Cannot resume an executed/uncertain command or renew an already-retried grasp search.')
    start = parent['part_episodes'][active]['start']
    if previous is not None and start != previous:
        raise ValueError('Stopped component did not start at the completed prefix boundary.')
    if parent.get('resume_checkpoint'):
        data = restore(model, parent['resume_checkpoint'])
        method = 'recorded_stop_checkpoint'
    else:
        # Legacy 21K saved completed boundaries, but not the subsequent 3-second settle.
        # This is allowed ONLY before any pick: reconstruct that known idle evolution,
        # then require exact equality of all reported observations before any API request.
        data = restore(model, start)
        probe = FrameSkills(model, data, active, registry, set(done),
            transport_strategy=options['transport_strategy'], empty_approach_strategy=options['empty_approach_strategy'])
        probe.settle()
        method = 'completed_boundary_plus_exactly_reproduced_idle_settle'
    probe = FrameSkills(model, data, active, registry, set(done),
        transport_strategy=options['transport_strategy'], empty_approach_strategy=options['empty_approach_strategy'])
    if probe.observe() != parent['observation'] or float(data.time) != parent['simulated_seconds']:
        raise ValueError('Stopped observations could not be reproduced exactly; no action may be replayed.')
    probe.verifier.verify_other_parts(data)
    with (folder / 'skill_trace.csv').open('rb') as stream:
        stream.seek(max(0, (folder / 'skill_trace.csv').stat().st_size - 8192))
        last_row = stream.read().decode('utf-8').strip().splitlines()[-1].split(',')
    if last_row[0] != active or last_row[1] != 'settle_workcell' or float(last_row[2]) != float(data.time):
        raise ValueError('Parent trace must end at the same stopped pre-pick physical state.')
    snapshot = boundary(model, data, registry, set(done))
    provenance = {'parent_run_dir': str(folder), 'parent_result_sha256': file_hash(folder / 'result.json'),
        'parent_config_sha256': file_hash(folder / 'resolved_config.json'),
        'parent_trace_sha256': file_hash(folder / 'skill_trace.csv'),
        'parent_api_log_sha256': file_hash(folder / 'llm_events.jsonl'),
        'root_run_dir': parent.get('resume', {}).get('root_run_dir', str(folder)) if parent.get('resume') else str(folder),
        'active_part': active, 'installed_at_resume': done, 'restoration_method': method,
        'restored_boundary': snapshot, 'api_calls_at_resume': parent['api_calls'],
        'additional_grasp_proposals_limit': 3, 'uncertain_robot_command_replayed': False}
    return data, parent, provenance


def carry_accounting(ledger, parent, folder):
    """The new journal owns a copied prefix; counters and original caps are never reset."""
    usage = parent['llm_usage']
    if (usage['unknown_usage_requests'] or usage['unresolved_cost_reserve_usd'] or usage['halted']
            or usage['api_calls'] != parent['api_calls'] or usage['input_count_requests'] != usage['api_calls']
            or usage['requested_model'] != ledger.config.model):
        raise ValueError('Unknown or inconsistent parent API accounting; cannot renew the budget.')
    mapping = {'http_requests': 'http_requests', 'count_requests': 'input_count_requests',
        'model_requests': 'api_calls', 'input_tokens': 'input_tokens', 'output_tokens': 'output_tokens',
        'cached_tokens': 'cached_input_tokens', 'reasoning_tokens': 'reasoning_tokens',
        'cache_write_tokens': 'cache_write_30m_tokens'}
    for attribute, key in mapping.items():
        value = usage[key]
        if type(value) is not int or value < 0:
            raise ValueError('Invalid parent request/token count.')
        setattr(ledger, attribute, value)
    if (ledger.cached_tokens + ledger.cache_write_tokens > ledger.input_tokens
            or ledger.reasoning_tokens > ledger.output_tokens
            or ledger.http_requests != ledger.count_requests + ledger.model_requests):
        raise ValueError('Inconsistent parent token/request totals.')
    ledger.known_cost = ledger.cost(ledger.input_tokens, ledger.output_tokens,
                                   cached=ledger.cached_tokens, written=ledger.cache_write_tokens)
    if ledger.known_cost != Decimal(str(usage['known_api_cost_estimate_usd'])):
        raise ValueError('Parent usage does not reconcile with the unchanged configured rates.')
    records = [json.loads(line) for line in (Path(folder) / 'llm_events.jsonl').read_text(encoding='utf-8').splitlines()]
    validated = [r for r in records if r['event'] == 'proposal_validated']
    if len(validated) != ledger.model_requests or not validated or validated[-1]['summary'] != usage:
        raise ValueError('Parent response log does not reconcile with the request count.')
    shutil.copyfile(Path(folder) / 'llm_events.jsonl', ledger.path)
    ledger.record('episode_resumed', parent_run_dir=str(Path(folder).resolve()), carried_usage=usage)
    ledger.ensure_room()


def copy_prefix_artifacts(folder, destination, installed):
    folder, destination = Path(folder), Path(destination)
    names = ['skill_trace.csv', 'graph_events.jsonl', 'before_assembly.png',
             *(f'after_{name}.png' for name in installed)]
    names += [p.name for pattern in ('motion_preflight_*.json', 'failure_*.json') for p in folder.glob(pattern)]
    for name in names:
        if (folder / name).is_file():
            shutil.copyfile(folder / name, destination / name)
