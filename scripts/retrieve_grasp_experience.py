"""Deterministic development-only retrieval of historical post-grasp evidence.

No model, simulator, motion, vector database, or automatic skill registration.
A returned card is a reference to a past event, never permission to replay it.
"""
import argparse
from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re

from export_grasp_experience import (DEFAULT_OUTPUT, MAX_BYTES, _decode, _grasp,
    _insertion_verdict, _number, _post_contexts, canonical, digest, validate_export)

QUERY_SCHEMA = 'post_grasp_query/v1'
RESULT_SCHEMA = 'post_grasp_search/v1'
MAX_FILES = 128
MAX_TOTAL_BYTES = 64 * 1024 * 1024
MAX_CHAIN = 8
CONVENTIONS = {'grasp_frame': 'part_local_geometric_center', 'grasp_position_unit': 'mm',
               'jaw_yaw_unit': 'deg', 'approach': 'top_down', 'tcp_definition': 'nominal_gripper_TCP'}


def _read(path):
    with Path(path).open('rb') as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError('Input exceeds 16 MiB.')
    return raw, _decode(raw.decode('utf-8-sig'))


def query_from_run(run_dir, part_id, *, failure_code=None, failure_phase=None):
    """Use only saved scene configuration, never the current episode's outcome."""
    run = Path(run_dir).resolve()
    raw, config = _read(run / 'resolved_config.json')
    contexts = _post_contexts(config)
    if part_id not in contexts:
        raise ValueError('Unknown or unsupported post.')
    query = {'schema': QUERY_SCHEMA, 'purpose': 'development_reference',
        'environment': config.get('environment'), 'observation_source': config.get('observation_source'),
        'source_run_id': run.name, 'source_episode_id': digest({'run_id': run.name,
            'config_sha256': hashlib.sha256(raw).hexdigest()}),
        'part_id': part_id, 'context': contexts[part_id],
        'simulator_version': config.get('simulator_version'),
        'failure_code': failure_code, 'failure_phase': failure_phase}
    _validate_query(query)
    return query


def _features(context):
    cfg, joint = context['config'], context['joint']
    part, robot = cfg['part'], cfg['robot']
    size = part['size_m']
    depth, clearance = joint['nominal_insertion_depth_m'], joint['nominal_side_clearance_m']
    if (not isinstance(part.get('id'), str) or not part['id'].startswith('post_')
            or part.get('shape') != 'box' or not isinstance(size, list) or len(size) != 3
            or not all(_number(x) and x > 0 for x in size)
            or not _number(depth) or depth <= 0 or not _number(clearance) or clearance < 0):
        raise ValueError('Unsupported post geometry or missing dimensions.')
    signature = {'robot': robot['model'], 'gripper': robot['gripper'],
                 **{k: context[k] for k in CONVENTIONS}}
    return {'signature': signature, 'size_m': size, 'depth_m': depth, 'clearance_m': clearance,
            'product': joint.get('product_candidate')}


def _validate_query(query):
    if (query.get('schema') != QUERY_SCHEMA or query.get('purpose') != 'development_reference'
            or query.get('environment') != 'simulation'
            or query.get('observation_source') != 'sim_ground_truth'
            or not isinstance(query.get('source_run_id'), str) or not query['source_run_id']
            or not re.fullmatch('[0-9a-f]{64}', query.get('source_episode_id', ''))
            or query['context']['config']['part']['id'] != query.get('part_id')):
        raise ValueError('A simulation query with current episode identity is required.')
    features = _features(query['context'])
    if features['signature'] != {'robot': 'ur3e', 'gripper': 'robotiq_2f85', **CONVENTIONS}:
        raise ValueError('Query uses an unsupported robot or coordinate convention.')
    code, phase = query.get('failure_code'), query.get('failure_phase')
    if code is not None and (not isinstance(code, str) or not re.fullmatch('[A-Z][A-Z0-9_]{0,63}', code)):
        raise ValueError('Failure code must be an uppercase detector code.')
    if phase is not None and (code is None or not isinstance(phase, str) or not 0 < len(phase) <= 128):
        raise ValueError('Failure phase requires a code and a nonempty string up to 128 characters.')
    canonical(query)
    return features


def _views(bundle):
    """Validate links and derive evidence, without trusting summary success counts."""
    validate_export(bundle)
    if (bundle.get('environment') != 'simulation' or bundle.get('observation_source') != 'sim_ground_truth'
            or bundle.get('training_eligibility') != 'unassigned_requires_dataset_manifest'):
        raise ValueError('Unsupported evidence domain or dataset status.')
    expected_episode = digest({'run_id': bundle['source_run_id'],
                              'config_sha256': bundle['source_files']['resolved_config.json']['sha256']})
    if bundle.get('source_episode_id') != expected_episode:
        raise ValueError('Episode identity mismatch.')
    records = {r['record_id']: r for r in bundle['records']}
    attempts = {a['attempt_id']: a for a in bundle['attempts']}
    if len(attempts) != len(bundle['attempts']):
        raise ValueError('Duplicate attempt identity.')
    covered, output = set(), []
    for aid, attempt in attempts.items():
        pid = attempt['part_id']
        context = bundle['contexts'][pid]
        features = _features(context)
        if context['config']['part']['id'] != pid:
            raise ValueError('Context is bound to a different part.')
        rows = [records[rid] for rid in attempt['record_ids']]
        if not rows or len({r['record_id'] for r in rows}) != len(rows):
            raise ValueError('Empty or repeated attempt evidence.')
        target = context['config']['target']['id']
        parent = attempt.get('parent_failure_record_id')
        parent_attempt = None
        if parent is not None:
            prior = records[parent]
            if (prior['part_id'] != pid or prior['target_id'] != target or prior['stage'] != 'preflight'
                    or prior['data']['status'] not in ('rejected', 'outcome_unknown')
                    or prior['source']['line'] >= rows[0]['source']['line']
                    or prior['attempt_id'] not in attempts or prior['attempt_id'] == aid):
                raise ValueError('Correction parent is not an earlier failure of the same part.')
            parent_attempt = prior['attempt_id']
        failures, facts, measured, insertion = [], {}, False, 'unknown'
        grasp = _grasp(attempt['grasp'])
        executed_grasp = 'unknown'
        preflight_status, live_pick_status = 'unknown', 'unknown'
        for row in rows:
            rid = row['record_id']
            if (rid in covered or row['attempt_id'] != aid or row['part_id'] != pid
                    or row['target_id'] != target or row['grasp'] != grasp
                    or row.get('domain') != 'mujoco_robot_assembly'
                    or row.get('evidence_kind') != 'historical_log_projection'
                    or row['source'].get('file') != 'graph_events.jsonl'
                    or type(row['source'].get('line')) is not int):
                raise ValueError('Attempt/evidence binding mismatch.')
            covered.add(rid)
            if any(records[p]['part_id'] != pid or records[p]['target_id'] != target for p in row['parent_record_ids']):
                raise ValueError('Cross-part evidence parent.')
            data, stage = row['data'], row['stage']
            if stage not in ('proposal', 'preflight', 'execution', 'verification'):
                raise ValueError('Unsupported evidence stage.')
            if stage != 'proposal' and data.get('scene') != ('copy' if stage == 'preflight' else 'live'):
                raise ValueError('Copy/live scene evidence mismatch.')
            parents = [records[p] for p in row['parent_record_ids']]
            if stage != 'proposal':
                expected_parent = {'preflight': 'proposal', 'execution': 'proposal', 'verification': 'execution'}[stage]
                if stage == 'execution' and data['action']['skill'] == 'pick':
                    expected_parent = 'preflight'
                if (len(parents) != 1 or parents[0]['stage'] != expected_parent
                        or parents[0]['attempt_id'] != aid):
                    raise ValueError('Broken proposal/check/execution/verification chain.')
                if expected_parent == 'preflight' and parents[0]['data']['status'] != 'accepted':
                    raise ValueError('Pick execution lacks accepted copy preflight.')
            if stage in ('proposal', 'execution'):
                action = data['action']
                if (action['part_id'] != pid or action['target_id'] != target
                        or action['observation_revision'] != row['observation_revision']
                        or (action['skill'] == 'pick' and _grasp(action['grasp']) != grasp)):
                    raise ValueError('Action binding mismatch.')
            if stage == 'verification' and (data.get('new_physics_validation') is not False
                    or data['action_skill'] != parents[0]['data']['action']['skill']):
                raise ValueError('Verification action or historical scope mismatch.')
            detail = None
            if stage == 'preflight':
                trial = data.get('trial')
                preflight_status = data['status']
                if preflight_status not in ('accepted', 'rejected', 'outcome_unknown'):
                    raise ValueError('Unknown preflight status.')
                if preflight_status == 'rejected':
                    live_pick_status = 'not_executed_preflight_rejected'
                if trial is None and preflight_status != 'outcome_unknown':
                    raise ValueError('Preflight decision lacks a trial.')
                if trial is not None:
                    if trial['grasp'] != grasp or trial.get('source') != 'mujoco_preflight_copy':
                        raise ValueError('Preflight grasp/source mismatch.')
                    if type(trial.get('accepted')) is not bool or data['status'] != ('accepted' if trial['accepted'] else 'rejected'):
                        raise ValueError('Preflight acceptance mismatch.')
                    if trial.get('accepted') is False and trial.get('failure_code'):
                        failures.append({'failure_code': trial['failure_code'], 'phase': trial.get('phase'),
                                         'record_id': rid, 'scope': 'copy_preflight'})
            if stage == 'execution' and data['action']['skill'] == 'pick':
                if data['node_outcome'] not in ('reported_failure', 'returned_without_reported_failure'):
                    raise ValueError('Unknown execution node outcome.')
                live_pick_status = 'outcome_unknown' if data['node_outcome'] == 'reported_failure' else 'node_returned'
                detail = data.get('recorded_executed_approach')
                if detail is not None:
                    observation = detail['observation']
                    if (_grasp(detail['proposed_grasp']) != grasp or detail['part_id'] != pid
                            or observation.get('source') != 'sim_ground_truth'
                            or observation['part']['id'] != pid or observation['target']['id'] != target
                            or not rows[0]['simulation_time_s'] <= observation['simulation_time_s'] <= row['simulation_time_s']):
                        raise ValueError('Executed grasp evidence mismatch.')
                    executed_grasp = 'recorded_match'
            if stage == 'verification':
                detail = data.get('skill_result')
                if data['action_skill'] == 'pick' and data['graph_transition_recorded'] is True:
                    live_pick_status = 'graph_verified'
                if data['action_skill'] in ('insert', 'replan_insert'):
                    insertion = 'graph_verified_without_saved_measurements' if data['graph_transition_recorded'] else 'unknown'
                    if detail is not None:
                        observation = detail['observation']
                        if (detail.get('skill') != 'insert_post/v1' or detail['part_id'] != pid
                                or observation.get('source') != 'sim_ground_truth'
                                or observation['part']['id'] != pid
                                or abs(observation['simulation_time_s'] - row['simulation_time_s']) > 1e-8):
                            raise ValueError('Insertion measurement binding mismatch.')
                        insertion = _insertion_verdict(detail, target, features['depth_m'])
                    if data['measurement_status'] == 'recorded_measured_pass':
                        if insertion != 'recorded_measured_pass' or data['graph_transition_recorded'] is not True:
                            raise ValueError('Unsubstantiated measured-success claim.')
                        measured = True
                    elif insertion == 'recorded_measured_pass':
                        raise ValueError('Measurement verdict mismatch.')
            # Stable graph facts across snapshots; optional measurement evidence may arrive later.
            core = {k: deepcopy(v) for k, v in row.items() if k not in
                    ('record_id', 'source_snapshot_id', 'measurement_refs', 'parent_record_ids')}
            core['parent_sources'] = [records[p]['source'] for p in row['parent_record_ids']]
            for key in ('recorded_executed_approach', 'executed_grasp_status', 'skill_result', 'measurement_status'):
                core['data'].pop(key, None)
            key = (row['source']['line'], stage)
            if key in facts:
                raise ValueError('Repeated event position.')
            facts[key] = {'core': core, 'detail': detail}
        if rows[0]['stage'] != 'proposal' or rows[0]['data']['action']['skill'] != 'pick':
            raise ValueError('Attempt does not begin with a pick proposal.')
        if rows[0]['parent_record_ids'] != ([parent] if parent else []):
            raise ValueError('Correction parent disagrees with proposal evidence.')
        predecessors = [a for a in attempts.values() if a['part_id'] == pid
                        and records[a['record_ids'][0]]['source']['line'] < rows[0]['source']['line']]
        previous = max(predecessors, key=lambda a: records[a['record_ids'][0]]['source']['line']) if predecessors else None
        changed = None if previous is None else {k: grasp[k] - previous['grasp'][k] for k in sorted(grasp)
                                                if grasp[k] != previous['grasp'][k]}
        if (attempt.get('changed_parameters') != changed or attempt['preflight_status'] != preflight_status
                or attempt['live_pick_status'] != live_pick_status or attempt['insertion_status'] != insertion):
            raise ValueError('Attempt summary disagrees with its event evidence.')
        output.append({'bundle': bundle, 'attempt': attempt, 'rows': rows, 'context': context,
                       'features': features, 'parent_attempt': parent_attempt, 'facts': facts,
                       'failures': failures, 'measured': measured, 'insertion': insertion,
                       'executed_grasp': executed_grasp, 'changed_parameters': changed,
                       'change_reference_attempt_id': previous['attempt_id'] if previous else None,
                       'preflight_status': preflight_status, 'live_pick_status': live_pick_status})
    if covered != set(records):
        raise ValueError('Unassigned event records.')
    return output


def _dominates(new, old):
    if (new['parent_attempt'] != old['parent_attempt']
            or canonical(new['context']['config']) != canonical(old['context']['config'])
            or canonical(new['context']['joint']) != canonical(old['context']['joint'])):
        return False
    for key, prior in old['facts'].items():
        current = new['facts'].get(key)
        if current is None or current['core'] != prior['core']:
            return False
        if prior['detail'] is not None and current['detail'] != prior['detail']:
            return False
    return True


def _ratio(a, b):
    return 1. if a == b == 0 else min(a, b) / max(a, b)


def _compact_attempt(view):
    attempt, bundle = view['attempt'], view['bundle']
    checks = []
    for row in view['rows']:
        data = row['data']
        evidence = {'record_id': row['record_id'], 'stage': row['stage'], 'source': row['source'],
                    'simulation_time_s': row['simulation_time_s'], 'observation_revision': row['observation_revision']}
        if row['stage'] == 'preflight':
            trial = data.get('trial') or {}
            violations = trial.get('violations') or []
            evidence.update(status=data['status'], failure_code=trial.get('failure_code'), phase=trial.get('phase'),
                held_pad_geometry=trial.get('held_pad_geometry'), contacts=deepcopy(violations[:4]),
                contact_count=len(violations), contacts_truncated=len(violations) > 4)
        elif row['stage'] == 'execution':
            evidence.update(action=data['action']['skill'], node_outcome=data['node_outcome'],
                            command_submission_receipt=None)
        elif row['stage'] == 'verification':
            result = data.get('skill_result') or {}
            evidence.update(action=data['action_skill'], graph_transition_recorded=data['graph_transition_recorded'],
                measurement_status=data['measurement_status'], measurement_refs=row['measurement_refs'],
                maximum_final_position_error_m=result.get('maximum_final_position_error_m'),
                minimum_final_depth_m=result.get('minimum_final_depth_m'))
        checks.append(evidence)
    return {'attempt_id': attempt['attempt_id'], 'parent_attempt_id': view['parent_attempt'],
            'parent_failure_record_id': attempt.get('parent_failure_record_id'),
            'export_id': bundle['export_id'], 'source_snapshot_id': bundle['source_snapshot_id'],
            'grasp': attempt['grasp'], 'changed_parameters': view['changed_parameters'],
            'change_reference_attempt_id': view['change_reference_attempt_id'],
            'preflight_status': view['preflight_status'], 'live_pick_status': view['live_pick_status'],
            'insertion_status': view['insertion'], 'executed_grasp_status': view['executed_grasp'],
            'recorded_measured_insertion': view['measured'], 'evidence': checks}


def retrieve_experiences(query, *, memory_dir=DEFAULT_OUTPUT, limit=3):
    """Return at most three failure/correction families, one per source episode."""
    qf = _validate_query(query)
    if type(limit) is not int or not 1 <= limit <= 3:
        raise ValueError('limit must be 1..3.')
    folder = Path(memory_dir)
    if not folder.is_dir():
        raise ValueError('Memory directory does not exist.')
    paths = sorted(folder.glob('*.json'))
    if len(paths) > MAX_FILES:
        raise ValueError('Too many files; use a bounded development collection.')
    groups, skipped, total = defaultdict(list), [], 0

    def skip(reason, **details):
        skipped.append({'reason': reason, **details})

    for path in paths:
        total += path.stat().st_size
        if total > MAX_TOTAL_BYTES:
            raise ValueError('Memory collection exceeds 64 MiB.')
        try:
            _, bundle = _read(path)
            views = _views(bundle)
            if (bundle['source_episode_id'] == query['source_episode_id']
                    or bundle['source_run_id'] == query['source_run_id']):
                skip('current_episode_excluded', file=path.name)
                continue
            for view in views:
                if view['features']['signature'] != qf['signature']:
                    skip('incompatible_robot_or_convention', attempt_id=view['attempt']['attempt_id'])
                    continue
                groups[view['attempt']['attempt_id']].append(view)
        except (ValueError, KeyError, TypeError, IndexError, AttributeError, UnicodeError) as exc:
            skip('invalid_export', file=path.name, detail=str(exc)[:160])
    chosen = {}
    for aid, versions in groups.items():
        winners = [v for v in versions if all(_dominates(v, other) for other in versions)]
        if not winners:
            skip('conflicting_attempt_versions', attempt_id=aid)
            continue
        chosen[aid] = min(winners, key=lambda v: v['bundle']['export_id'])
    # A conflicting/missing parent cannot be bypassed by returning its successful child.
    while True:
        orphans = [aid for aid, v in chosen.items() if v['parent_attempt'] and v['parent_attempt'] not in chosen]
        if not orphans:
            break
        for aid in orphans:
            chosen.pop(aid)
            skip('missing_or_conflicting_parent', attempt_id=aid)
    families = defaultdict(list)
    for aid, view in chosen.items():
        root, seen = aid, set()
        while chosen[root]['parent_attempt']:
            if root in seen:
                raise ValueError('Cyclic correction chain.')
            seen.add(root)
            root = chosen[root]['parent_attempt']
        families[root].append(view)
    candidates = []
    for root, family in families.items():
        family.sort(key=lambda v: v['rows'][0]['source']['line'])
        if len(family) > MAX_CHAIN or any(len(v['rows']) > 32 for v in family):
            skip('evidence_chain_over_limit', attempt_id=root)
            continue
        first, context = family[0], family[0]['context']
        bundle, features = first['bundle'], first['features']
        failures = [f for v in family for f in v['failures']]
        matching = [f for f in failures if f['failure_code'] == query['failure_code']]
        if query['failure_code'] is not None and not matching:
            skip('no_matching_failure_code', attempt_id=root)
            continue
        size_similarity = min(_ratio(a, b) for a, b in zip(features['size_m'], qf['size_m']))
        joint_similarity = min(_ratio(features[k], qf[k]) for k in ('depth_m', 'clearance_m'))
        exact = (context['config'] == query['context']['config'] and context['joint'] == query['context']['joint']
                 and bundle.get('simulator_version') == query['simulator_version'])
        phase_match = (query['failure_phase'] is not None and
                       any(f['phase'] == query['failure_phase'] for f in matching))
        measured_correction = any(v['measured'] and v['parent_attempt'] is not None for v in family)
        rank = (int(exact), int(phase_match), round(size_similarity, 8), round(joint_similarity, 8),
                int(measured_correction), int(features['product'] == qf['product']))
        card = {'card_id': digest({'episode': bundle['source_episode_id'], 'root_attempt': root}),
            'source_run_id': bundle['source_run_id'], 'source_episode_id': bundle['source_episode_id'],
            'source_part_id': first['attempt']['part_id'], 'source_target_id': context['config']['target']['id'],
            'source_episode_statuses': sorted({v['bundle'].get('source_episode_status') for v in family}, key=str),
            'match': {'class': 'same_active_part_config' if exact else 'related_post_conditions',
                      'failure_code': query['failure_code'], 'matching_failure_records': matching,
                      'phase_exact': phase_match, 'dimension_similarity': round(size_similarity, 8),
                      'joint_similarity': round(joint_similarity, 8),
                      'measured_correction_available': measured_correction,
                      'same_product_profile': features['product'] == qf['product'],
                      'rank_features': list(rank)},
            'source_conditions': {'size_m': features['size_m'], 'nominal_depth_m': features['depth_m'],
                'nominal_side_clearance_m': features['clearance_m'], 'product_profile': features['product'],
                'spawn_xy_table_m': context['config']['part'].get('spawn_xy_table_m'),
                'target_xy_table_m': context['config']['target'].get('xy_table_m'),
                'part_episode_start_installed': context.get('part_episode_start_installed'),
                'simulator_version': bundle.get('simulator_version'),
                'implementation_provenance': 'recorded' if bundle.get('source_implementation_sha256') else 'unknown'},
            'attempts': [_compact_attempt(v) for v in family],
            'training_eligibility': 'unassigned_requires_dataset_manifest',
            'execution_authorized': False, 'requires_current_scene_validation': True}
        candidates.append((rank, card))
    candidates.sort(key=lambda item: (tuple(-v for v in item[0]), item[1]['card_id']))
    selected, episodes = [], set()
    for _, card in candidates:
        if card['source_episode_id'] in episodes:
            skip('one_card_per_episode', card_id=card['card_id'])
        elif len(selected) < limit:
            selected.append(card)
            episodes.add(card['source_episode_id'])
        else:
            skip('result_limit', card_id=card['card_id'])
    return {'schema': RESULT_SCHEMA, 'purpose': 'development_reference', 'query': deepcopy(query),
            'cards': selected, 'summary': {'files_scanned': len(paths), 'unique_attempts': len(chosen),
                'matching_families': len(candidates), 'returned_cards': len(selected),
                'excluded': dict(sorted(Counter(x['reason'] for x in skipped).items()))},
            'diagnostics': skipped[:20], 'diagnostics_truncated': len(skipped) > 20,
            'ranking': ['same_active_part_config', 'failure_phase', 'dimension_similarity',
                        'joint_similarity', 'measured_correction', 'same_product_profile', 'card_id'],
            'limitations': ['Historical references only; no replay or current-scene approval.',
                'Same active-part configuration does not prove the surrounding scene or TCP calibration matches.',
                'Failure-code matching currently uses attempt-bound copy-preflight reports only.',
                'No evaluation split is assigned; this search is not an independent benchmark.',
                'No model selection, lesson generation, or skill registration is performed.'],
            'API_calls': 0, 'new_physics_runs': 0, 'execution_authorized': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', type=Path, required=True, help='Read only this scene configuration; exclude its episode.')
    parser.add_argument('--part', required=True)
    parser.add_argument('--failure-code')
    parser.add_argument('--failure-phase')
    parser.add_argument('--memory-dir', type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--limit', type=int, default=3)
    args = parser.parse_args()
    query = query_from_run(args.run_dir, args.part, failure_code=args.failure_code, failure_phase=args.failure_phase)
    print(json.dumps(retrieve_experiences(query, memory_dir=args.memory_dir, limit=args.limit),
                     ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
