"""Export saved post-grasp evidence. No simulator, model, or motion imports.

Events describe nodes AFTER they return. They are not command-submission receipts.
Copy preflight, live execution, recorded verification, and episode completion stay
separate. This reader projects historical evidence; it never revalidates physics.
"""
import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / 'runtime/memory/simulation/grasp_v2'
SCHEMA = 'assembly_grasp_experience/v2'
FIELDS = {'x_mm', 'y_mm', 'z_mm', 'jaw_yaw_deg'}
PHYSICAL = {'pick', 'insert', 'recover_alignment', 'replan_insert'}
CHECKS = {'released', 'target_position_under_1mm', 'target_orientation_under_1deg',
          'insertion_depth_reached', 'linear_speed_under_1mm_s',
          'angular_speed_under_0_01rad_s', 'penetration_under_1mm'}
MAX_BYTES = 16 * 1024 * 1024


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()


def _number(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _decode(raw):
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError('Duplicate JSON key: ' + key)
            value[key] = item
        return value
    def nonfinite(value):
        raise ValueError('Nonfinite JSON: ' + value)
    value = json.loads(raw, object_pairs_hook=pairs, parse_constant=nonfinite)
    canonical(value)  # Reject overflowing exponents such as 1e999 as well.
    return value


def _grasp(value):
    if type(value) is not dict or set(value) != FIELDS or not all(_number(v) for v in value.values()):
        raise ValueError('Expected four finite grasp numbers in part-frame mm/degrees.')
    return deepcopy(value)


def _ref(filename, pointer=None, line=None):
    return {'file': filename, 'pointer': pointer, 'line': line}


def _post_contexts(config):
    registry = config.get('registry')
    if registry is None:
        cfg, joint = config.get('config', {}), config.get('joint', {})
        part = cfg.get('part', {})
        if config.get('grasp_mode') != 'proposed' or joint.get('product_candidate') != 'four_post_frame_v1':
            raise ValueError('Only saved proposed-grasp post scenes are supported.')
        registry = {part.get('id'): {'kind': 'post', 'config': cfg, 'joint': joint}}
    contexts = {}
    for pid, entry in registry.items():
        if entry.get('kind') != 'post':
            continue
        cfg, joint = entry['config'], entry['joint']
        if (type(pid) is not str or not pid.startswith('post_') or '/' in pid
                or cfg['part']['id'] != pid or cfg['part']['shape'] != 'box'
                or cfg['robot'].get('model') != 'ur3e'
                or cfg['robot'].get('gripper') != 'robotiq_2f85'):
            raise ValueError('Unsupported post/robot binding.')
        contexts[pid] = {'config': deepcopy(cfg), 'joint': deepcopy(joint),
                         'active_part_condition_id': digest({'config': cfg, 'joint': joint,
                            'simulator_version': config.get('simulator_version')}),
                         'grasp_frame': 'part_local_geometric_center',
                         'grasp_position_unit': 'mm', 'jaw_yaw_unit': 'deg',
                         'approach': 'top_down', 'tcp_definition': 'nominal_gripper_TCP'}
    return contexts


def _details(result, pid, action, time_s):
    """Bind durable live skill results by part, target (caller), skill and time."""
    expected = {'pick': 'pick/v1', 'insert': 'insert_post/v1', 'replan_insert': 'insert_post/v1',
                'recover_alignment': 'recover_alignment/v1'}.get(action)
    if expected is None:
        return None, []
    candidates = [(r, _ref('result.json', '/results/' + str(i)))
                  for i, r in enumerate(result.get('results', []))]
    state = result.get('graph_states', {}).get(pid) or result.get('graph_state') or {}
    prefix = '/graph_states/' + pid if 'graph_states' in result else '/graph_state'
    candidates.append((state.get('last_result'), _ref('result.json', prefix + '/last_result')))
    found, refs = None, []
    for item, ref in candidates:
        if not isinstance(item, dict) or item.get('part_id') != pid or item.get('skill') != expected:
            continue
        observed = item.get('observation', {})
        recorded_time = observed.get('simulation_time_s')
        if not _number(recorded_time) or abs(recorded_time - time_s) > 1e-8:
            continue
        if observed.get('source') != 'sim_ground_truth' or observed.get('part', {}).get('id') != pid:
            raise ValueError('Live result observation has a different part/source.')
        if found is not None and canonical(found) != canonical(item):
            raise ValueError('Conflicting live results for the same action/time.')
        found = item
        refs.append(ref)
    return deepcopy(found), refs


def _insertion_verdict(detail, target, nominal_depth):
    if detail is None:
        return 'unknown'
    if detail.get('target_id') != target:
        raise ValueError('Insertion target mismatch.')
    checks = detail.get('checks', {})
    support = {'socket_floor_support', 'declared_mating_support'} & set(checks)
    complete = (len(support) == 1 and set(checks) == CHECKS | support
                and all(value is True for value in checks.values()))
    measured = (detail.get('status') == 'success' and complete
                and _number(detail.get('maximum_final_position_error_m'))
                and 0 <= detail['maximum_final_position_error_m'] < .001
                and _number(detail.get('minimum_final_depth_m'))
                and _number(nominal_depth) and nominal_depth > 0
                and abs(detail['minimum_final_depth_m'] - nominal_depth) < .001)
    if detail.get('status') == 'success' and not measured:
        raise ValueError('Claimed post insertion success lacks the recorded eight checks/measurements.')
    return 'recorded_measured_pass' if measured else 'recorded_non_success'


def _executed_approach(result, pid, action, start_s, end_s):
    """Corroborate the grasp passed to the executor, not just the graph proposal."""
    candidates = [(r, _ref('result.json', '/results/' + str(i)))
                  for i, r in enumerate(result.get('results', []))]
    episode = result.get('part_episodes', {}).get(pid, {})
    candidates.append((episode.get('executed_approach'),
                       _ref('result.json', '/part_episodes/' + pid + '/executed_approach')))
    found, refs = None, []
    for item, ref in candidates:
        if not isinstance(item, dict) or item.get('part_id') != pid or item.get('skill') not in (
                'approach/v1', 'approach_component/v1'):
            continue
        observed = item.get('observation', {})
        time_s = observed.get('simulation_time_s')
        if not _number(time_s) or not start_s <= time_s <= end_s:
            continue
        if (observed.get('source') != 'sim_ground_truth' or observed.get('part', {}).get('id') != pid
                or observed.get('target', {}).get('id') != action['target_id']
                or _grasp(item.get('proposed_grasp')) != action['grasp']):
            raise ValueError('Executed approach does not match the proposed part/target/grasp.')
        if found is not None and canonical(found) != canonical(item):
            raise ValueError('Ambiguous executed approach in the pick time interval.')
        found = item
        refs.append(ref)
    return deepcopy(found), refs


def build_export(run_dir, part_ids=None):
    """Read a consistent log snapshot; never fill absent observations with guesses."""
    run = Path(run_dir).resolve()
    raw = {}
    for name in ('resolved_config.json', 'graph_events.jsonl', 'result.json'):
        path = run / name
        if name == 'result.json' and not path.exists():
            continue
        with path.open('rb') as stream:
            content = stream.read(MAX_BYTES + 1)
        if len(content) > MAX_BYTES:
            raise ValueError('Input exceeds 16 MiB: ' + name)
        raw[name] = content
    config = _decode(raw['resolved_config.json'].decode('utf-8-sig'))
    result = _decode(raw['result.json'].decode('utf-8-sig')) if 'result.json' in raw else {}
    if (config.get('environment') != 'simulation'
            or config.get('observation_source') != 'sim_ground_truth'):
        raise ValueError('Only MuJoCo simulation ground-truth logs are supported.')
    if config.get('resume') or result.get('resume'):
        raise ValueError('Resumed segments require a parent-chain adapter; not supported in this increment.')
    contexts = _post_contexts(config)
    selected = list(contexts) if part_ids is None else list(part_ids)
    if not selected or len(set(selected)) != len(selected) or set(selected) - set(contexts):
        raise ValueError('Select unique supported post IDs from this scene.')
    contexts = {pid: contexts[pid] for pid in selected}
    for pid, context in contexts.items():
        context['scene_config_ref'] = _ref('resolved_config.json', '')
        context['scene_initial_installed'] = deepcopy(config.get('initial_installed'))
        context['part_episode_start_installed'] = deepcopy(
            result.get('part_episodes', {}).get(pid, {}).get('start', {}).get('installed'))
    files = {name: {'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)}
             for name, data in sorted(raw.items())}
    snapshot_id = digest({'run_id': run.name, 'files': files})
    # Episode/attempt identity survives result arrival and appended graph events.
    episode_id = digest({'run_id': run.name, 'config_sha256': files['resolved_config.json']['sha256']})
    events = []
    for line, text in enumerate(raw['graph_events.jsonl'].decode('utf-8-sig').splitlines(), 1):
        if not text.strip():
            continue
        row = _decode(text)
        if not isinstance(row, dict):
            raise ValueError('Graph event must be an object.')
        events.append((line, row))
    records, attempts, diagnostics = [], [], []
    states = {pid: {'attempt': None, 'proposal': None, 'observe_ref': None,
                    'last_rejected': None, 'revision': -1, 'time': -1.,
                    'failure_count': 0} for pid in contexts}

    def emit(pid, stage, event, line, payload, parents=(), detail_refs=()):
        st = states[pid]
        attempt = st['attempt']
        body = {'schema_version': 2, 'domain': 'mujoco_robot_assembly',
                'evidence_kind': 'historical_log_projection', 'source_snapshot_id': snapshot_id,
                'part_id': pid, 'target_id': contexts[pid]['config']['target']['id'],
                'attempt_id': attempt['attempt_id'] if attempt else None,
                'stage': stage, 'observation_revision': event['observation_revision'],
                'simulation_time_s': event['simulation_time_s'],
                'source': _ref('graph_events.jsonl', line=line),
                'measurement_refs': list(detail_refs), 'parent_record_ids': list(parents),
                'before_observation_ref': st['observe_ref'],
                'grasp': deepcopy(attempt['grasp']) if attempt else None,
                'data': deepcopy(payload)}
        rec = {'record_id': digest(body), **body}
        records.append(rec)
        if attempt:
            attempt['record_ids'].append(rec['record_id'])
        return rec['record_id']

    for line, event in events:
        action = event.get('action')
        pid = event.get('active_part_id') or (action or {}).get('part_id')
        if pid is None and len(contexts) == 1 and 'registry' not in config:
            pid = selected[0]
        if pid not in states:
            continue
        st = states[pid]
        revision, simtime = event.get('observation_revision'), event.get('simulation_time_s')
        if type(revision) is not int or revision < st['revision'] or not _number(simtime) or simtime < st['time']:
            raise ValueError('Nonmonotonic or invalid observation/time in post events.')
        st['revision'], st['time'] = revision, simtime
        node = event.get('node')
        previous_failures = st['failure_count']
        st['failure_count'] = event.get('failure_count', previous_failures)
        if type(st['failure_count']) is not int or st['failure_count'] < previous_failures:
            raise ValueError('Invalid or decreasing failure count.')
        if node == 'observe':
            st['observe_ref'] = _ref('graph_events.jsonl', line=line)
        if node not in ('decide', 'check_grasp', 'execute', 'verify'):
            continue
        if node == 'decide' and event.get('status') != 'running':
            diagnostics.append({'code': 'decision_failed_no_new_proposal',
                                'part_id': pid, 'source': _ref('graph_events.jsonl', line=line)})
            continue
        if not isinstance(action, dict) or action.get('skill') not in PHYSICAL:
            continue  # finish/stop are episode controls, not robot motion.
        if (action.get('part_id') != pid or action.get('target_id') != contexts[pid]['config']['target']['id']
                or action.get('observation_revision') != revision):
            raise ValueError('Action has a different part/target or stale revision.')
        skill = action['skill']
        if node == 'decide':
            if skill == 'pick':
                grasp = _grasp(action.get('grasp'))
                attempt = {'attempt_id': digest({'episode': episode_id, 'part': pid,
                                                'line': line, 'proposal_event': event}),
                           'part_id': pid, 'grasp': grasp, 'parent_failure_record_id': st['last_rejected'],
                           'changed_parameters': None if st['attempt'] is None else
                               {k: grasp[k] - st['attempt']['grasp'][k] for k in sorted(FIELDS)
                                if grasp[k] != st['attempt']['grasp'][k]},
                           'preflight_status': 'unknown', 'live_pick_status': 'unknown',
                           'insertion_status': 'unknown', 'record_ids': []}
                attempts.append(attempt)
                st['attempt'] = attempt
            if st['attempt'] is None:
                raise ValueError('Motion proposal without a preceding proposed grasp.')
            parents = [st['last_rejected']] if skill == 'pick' and st['last_rejected'] else []
            rid = emit(pid, 'proposal', event, line, {'action': action,
                'attribution': 'graph_recorded_proposal', 'provider_response_link': None}, parents)
            st['proposal'] = {'action': deepcopy(action), 'record_id': rid, 'preflight_id': None,
                              'execute_id': None, 'verify_id': None, 'start_s': simtime}
            continue
        proposal, attempt = st['proposal'], st['attempt']
        if proposal is None or proposal['action'] != action:
            raise ValueError('Event lacks a matching validated proposal.')
        if node == 'check_grasp':
            trial = event.get('grasp_trial')
            if (trial is None and skill == 'pick' and not proposal['preflight_id']
                    and (st['failure_count'] > previous_failures or event.get('status') in ('failed', 'stopped'))):
                attempt['preflight_status'] = 'outcome_unknown'
                rid = emit(pid, 'preflight', event, line,
                    {'scene': 'copy', 'status': 'outcome_unknown', 'trial': None,
                     'reason': 'check_node_failed_without_trial', 'failure_count': st['failure_count']},
                    [proposal['record_id']])
                proposal['preflight_id'] = rid
                st['last_rejected'] = rid
                continue
            if (skill != 'pick' or proposal['preflight_id'] or not isinstance(trial, dict)
                    or trial.get('grasp') != action['grasp'] or trial.get('observation_revision') != revision
                    or type(trial.get('accepted')) is not bool
                    or trial.get('source') != 'mujoco_preflight_copy'):
                raise ValueError('Preflight is inconsistent with the proposed grasp.')
            if not _number(trial.get('live_start_s')) or abs(trial['live_start_s'] - simtime) > 1e-8:
                raise ValueError('Copy preflight changed the recorded live time.')
            attempt['preflight_status'] = 'accepted' if trial['accepted'] else 'rejected'
            if not trial['accepted']:
                attempt['live_pick_status'] = 'not_executed_preflight_rejected'
            rid = emit(pid, 'preflight', event, line,
                       {'scene': 'copy', 'status': attempt['preflight_status'], 'trial': trial},
                       [proposal['record_id']])
            proposal['preflight_id'] = rid
            if not trial['accepted']:
                st['last_rejected'] = rid
        elif node == 'execute':
            if proposal['execute_id'] or (skill == 'pick' and attempt['preflight_status'] != 'accepted'):
                raise ValueError('Duplicate execution or pick without accepted preflight.')
            reported_failure = (st['failure_count'] > previous_failures or event.get('status') in ('failed', 'stopped'))
            payload = {'scene': 'live', 'action': action, 'execution_node_observed': True,
                       'node_outcome': 'reported_failure' if reported_failure else 'returned_without_reported_failure',
                       'command_submission_receipt': None, 'command_submission_time_s': None,
                       'failure_count': st['failure_count'], 'episode_status_at_event': event.get('status')}
            detail_refs = []
            if skill == 'pick':
                approach, detail_refs = _executed_approach(result, pid, action, proposal['start_s'], simtime)
                payload['recorded_executed_approach'] = approach
                payload['executed_grasp_status'] = 'recorded_match' if approach else 'unknown'
            proposal['execute_id'] = emit(pid, 'execution', event, line, payload,
                [proposal['preflight_id'] or proposal['record_id']], detail_refs)
            if skill == 'pick':
                attempt['live_pick_status'] = 'outcome_unknown' if reported_failure else 'node_returned'
        elif node == 'verify':
            if not proposal['execute_id'] or proposal['verify_id']:
                raise ValueError('Verification without execution or duplicate verification.')
            detail, refs = _details(result, pid, skill, simtime)
            completion_skill = 'insert' if skill == 'replan_insert' else skill
            completed = completion_skill in event.get('completed', [])
            verdict = _insertion_verdict(detail, action['target_id'],
                contexts[pid]['joint'].get('nominal_insertion_depth_m')) if completion_skill == 'insert' else 'unknown'
            if verdict == 'recorded_measured_pass' and not completed:
                raise ValueError('Measured pass disagrees with recorded graph transition.')
            payload = {'scene': 'live', 'action_skill': skill, 'graph_transition_recorded': completed,
                       'measurement_status': verdict, 'skill_result': detail,
                       'new_physics_validation': False}
            proposal['verify_id'] = emit(pid, 'verification', event, line, payload,
                                         [proposal['execute_id']], refs)
            if skill == 'pick' and completed:
                attempt['live_pick_status'] = 'graph_verified'
            if completion_skill == 'insert':
                attempt['insertion_status'] = (verdict if verdict != 'unknown' else
                    'graph_verified_without_saved_measurements' if completed else 'unknown')

    if not attempts:
        raise ValueError('No proposed post grasps in selected logs.')
    summaries = []
    for pid in contexts:
        state = result.get('graph_states', {}).get(pid) or result.get('graph_state') or {}
        summaries.append({'part_id': pid, 'reported_graph_status': state.get('status'),
                          'reported_completed': state.get('completed'),
                          'reported_episode_installed': result.get('part_episodes', {}).get(pid, {}).get('end', {}).get('installed'),
                          'reported_installation_committed': (pid in result['completed_installations']
                              if isinstance(result.get('completed_installations'), list) else None),
                          'recorded_measured_insertions': sum(a['part_id'] == pid and
                             a['insertion_status'] == 'recorded_measured_pass' for a in attempts),
                          'source_failure_reports': deepcopy(state.get('failures', []))})
    body = {'schema': SCHEMA, 'source_directory': str(run), 'source_run_id': run.name,
            'source_episode_id': episode_id, 'source_snapshot_id': snapshot_id, 'source_files': files,
            'environment': 'simulation', 'observation_source': 'sim_ground_truth',
            'simulator_version': config.get('simulator_version'),
            'source_implementation_sha256': config.get('implementation_sha256'),
            'source_policy_kind': config.get('policy_kind'),
            'source_model': config.get('models') or config.get('llm_config', {}).get('model'),
            'source_episode_status': result.get('status'), 'source_episode_error': result.get('error'),
            'source_claimed_robot_installed_parts': result.get('robot_installed_parts'),
            'selected_parts': selected, 'contexts': contexts, 'records': records, 'attempts': attempts,
            'part_summaries': summaries, 'diagnostics': diagnostics,
            'summary': {'records': len(records), 'attempts': len(attempts),
                        'stages': dict(Counter(r['stage'] for r in records)),
                        'preflight_accepted': sum(a['preflight_status'] == 'accepted' for a in attempts),
                        'preflight_rejected': sum(a['preflight_status'] == 'rejected' for a in attempts),
                        'recorded_measured_insertions': sum(a['insertion_status'] == 'recorded_measured_pass' for a in attempts)},
            'limitations': ['Historical log projection; no new physical validation or skill registration.',
                'Command submission receipts and absent snapshots remain unknown.',
                'LLM/API trace, interrupted JSON tails and resumed parent chains are not imported.',
                'active_part_condition_id excludes the surrounding scene; it is not a compatibility certificate.',
                'Only upright post scenes are supported; sleeves and covers require separate adapters.'],
            'automatic_skill_registration': False, 'new_physics_validation': False,
            'training_eligibility': 'unassigned_requires_dataset_manifest', 'format_version': 1}
    for name, content in raw.items():
        if (run / name).read_bytes() != content:
            raise ValueError('Source logs changed during export; retry after the run has stopped.')
    if 'result.json' not in raw and (run / 'result.json').exists():
        raise ValueError('Run completed while exporting; read a new consistent snapshot.')
    bundle = {'export_id': digest(body), **body}
    validate_export(bundle)
    return bundle


def validate_export(bundle):
    if (bundle.get('schema') != SCHEMA or bundle.get('export_id') != digest(
            {k: v for k, v in bundle.items() if k != 'export_id'})
            or bundle.get('automatic_skill_registration') is not False
            or bundle.get('new_physics_validation') is not False):
        raise ValueError('Invalid or modified experience export.')
    seen = set()
    for rec in bundle['records']:
        if (rec.get('record_id') != digest({k: v for k, v in rec.items() if k != 'record_id'})
                or rec['record_id'] in seen or rec['part_id'] not in bundle['contexts']
                or rec['source_snapshot_id'] != bundle['source_snapshot_id']
                or set(rec['parent_record_ids']) - seen):
            raise ValueError('Modified record or broken evidence chain.')
        seen.add(rec['record_id'])
    if any(set(a['record_ids']) - seen for a in bundle['attempts']):
        raise ValueError('Unknown attempt records.')


def save_export(bundle, output_dir=DEFAULT_OUTPUT):
    validate_export(bundle)
    folder = Path(output_dir).resolve()
    source = Path(bundle['source_directory']).resolve()
    if folder == source or source in folder.parents:
        raise ValueError('Output must be outside the source run.')
    # Keep the v1 memory reader (which scans direct *.json) untouched.
    if folder == (ROOT / 'runtime/memory/simulation').resolve():
        raise ValueError('Use the separate grasp_v2 folder, not the legacy memory root.')
    folder.mkdir(parents=True, exist_ok=True)
    destination = folder / (bundle['export_id'] + '.json')
    payload = (json.dumps(bundle, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8')
    if destination.exists():
        existing = _decode(destination.read_text(encoding='utf-8'))
        validate_export(existing)
        if existing != bundle:
            raise ValueError('Immutable export conflict.')
        return destination, False
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=folder, prefix='.export-', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, destination)  # Atomic publish; never replace another writer's file.
        except FileExistsError:
            existing = _decode(destination.read_text(encoding='utf-8'))
            if existing != bundle:
                raise ValueError('Concurrent immutable export conflict.')
            return destination, False
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return destination, True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--part', action='append', dest='parts', help='Post ID; repeat or omit for all posts in the log.')
    parser.add_argument('--output-dir', type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    bundle = build_export(args.run_dir, args.parts)
    path, created = save_export(bundle, args.output_dir)
    print(json.dumps({'path': str(path), 'created': created, **bundle['summary'],
                      'source_episode_status': bundle['source_episode_status'],
                      'API_calls': 0, 'new_physics_runs': 0}, ensure_ascii=False))


if __name__ == '__main__':
    main()
