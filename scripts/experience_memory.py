"""Closed JSON experience records, exact-condition retrieval, no skill auto-promotion."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import uuid

from failure_report import FailureCode
from workcell_scene import ROOT

DEFAULT_MEMORY = ROOT / 'runtime/memory/simulation'
CHECKS = {'socket_floor_support', 'released', 'target_position_under_1mm', 'target_orientation_under_1deg',
          'insertion_depth_reached', 'linear_speed_under_1mm_s', 'angular_speed_under_0_01rad_s', 'penetration_under_1mm'}
KNOWN_SKILLS = {'approach/v1', 'pick/v1', 'insert_post/v1', 'recover_alignment/v1'}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                                     allow_nan=False).encode('utf-8')).hexdigest()


def read_json(path):
    path = Path(path)
    if path.stat().st_size > 4 * 1024 * 1024:
        raise ValueError('Experience input exceeds 4 MiB.')
    def pairs(items):
        obj = {}
        for k, v in items:
            if k in obj:
                raise ValueError('Duplicate JSON key.')
            obj[k] = v
        return obj
    def constant(_):
        raise ValueError('Nonfinite JSON number.')
    return json.loads(path.read_text(encoding='utf-8-sig'), object_pairs_hook=pairs, parse_constant=constant)


def condition_fingerprint(resolved):
    if (resolved.get('environment') != 'simulation' or resolved.get('observation_source') != 'sim_ground_truth'
            or not isinstance(resolved.get('config'), dict) or not isinstance(resolved.get('joint'), dict)
            or resolved['joint'].get('product_candidate') != 'four_post_frame_v1'
            or not isinstance(resolved.get('simulator_version'), str)):
        raise ValueError('Only recorded MuJoCo first-joint simulation conditions are supported.')
    return digest({key: resolved[key] for key in ('config', 'joint', 'simulator_version', 'environment', 'observation_source')})


def experience_from_run(run_dir):
    run = Path(run_dir).resolve()
    result, resolved = read_json(run / 'result.json'), read_json(run / 'resolved_config.json')
    fingerprint = condition_fingerprint(resolved)
    if result.get('status') not in ('success', 'failed', 'cancelled', 'stopped'):
        raise ValueError('Incomplete or unknown episode outcome.')
    if not isinstance(result.get('results'), list):
        raise ValueError('Missing skill evidence.')
    successful = [r for r in result['results'] if isinstance(r, dict) and r.get('status') == 'success']
    if result['status'] == 'success':
        insertions = [r for r in successful if r.get('skill') == 'insert_post/v1']
        if (result.get('robot_installed_parts') != 1 or len(insertions) != 1
                or set(insertions[0].get('checks', {})) != CHECKS
                or not all(v is True for v in insertions[0]['checks'].values())
                or not 0 <= insertions[0].get('maximum_final_position_error_m', -1) < .001
                or not .015 < insertions[0].get('minimum_final_depth_m', -1) < .017):
            raise ValueError('Success claim lacks the required measured insertion evidence.')
    reports = result.get('graph_state', {}).get('failures', []) if isinstance(result.get('graph_state'), dict) else result.get('failures', [])
    source_hashes = {name: hashlib.sha256((run / name).read_bytes()).hexdigest() for name in ('result.json', 'resolved_config.json')}
    if not reports and (run / 'failure_report.json').is_file():
        reports = [read_json(run / 'failure_report.json')]
        source_hashes['failure_report.json'] = hashlib.sha256((run / 'failure_report.json').read_bytes()).hexdigest()
    if not isinstance(reports, list):
        raise ValueError('Failure history must be a list.')
    codes = []
    for report in reports:
        if not isinstance(report, dict) or report.get('environment') != 'simulation':
            raise ValueError('Keep real and simulation failure evidence separate.')
        codes.append(FailureCode(report['failure_code']).value)
    skills = [r['skill'] for r in successful if r.get('skill') in KNOWN_SKILLS]
    payload = {'schema_version': 1, 'evidence_kind': 'recorded_simulation_result',
               'task_id': 'four_post_frame_first_joint/v1', 'environment': 'simulation', 'split': 'development',
               'condition_fingerprint': fingerprint, 'outcome': result['status'],
               'failure_codes': codes, 'verified_skill_results': skills,
               'successful_recovery_skills': [s for s in skills if s.startswith('recover_')],
               'source_run_dir': str(run), 'source_hashes': source_hashes,
               'policy_kind': resolved.get('policy_kind', 'scripted'),
               'source_implementation_revision': resolved.get('implementation_revision'),
               'automatic_skill_registration': False}
    return {'record_id': digest(payload), **payload}


def validate_record(record):
    if (not isinstance(record, dict) or record.get('schema_version') != 1
            or record.get('environment') != 'simulation' or record.get('split') != 'development'
            or record.get('automatic_skill_registration') is not False
            or not re.fullmatch('[0-9a-f]{64}', record.get('record_id', ''))
            or record['record_id'] != digest({k: v for k, v in record.items() if k != 'record_id'})):
        raise ValueError('Invalid or modified experience record.')


def save_experience(run_dir, memory_dir=DEFAULT_MEMORY):
    record = experience_from_run(run_dir)
    folder = Path(memory_dir)
    folder.mkdir(parents=True, exist_ok=True)
    destination = folder / (record['record_id'] + '.json')
    if destination.exists():
        existing = read_json(destination)
        validate_record(existing)
        if existing != record:
            raise ValueError('Immutable record conflict.')
        return record, False
    temporary = folder / ('.' + uuid.uuid4().hex + '.tmp')
    temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    temporary.replace(destination)
    return record, True


def retrieve_experiences(fingerprint, *, failure_code=None, memory_dir=DEFAULT_MEMORY, limit=3):
    if not re.fullmatch('[0-9a-f]{64}', fingerprint) or type(limit) is not int or not 1 <= limit <= 10:
        raise ValueError('Invalid search fingerprint/limit.')
    if failure_code is not None:
        failure_code = FailureCode(failure_code).value
    matches = []
    for path in sorted(Path(memory_dir).glob('*.json')):
        record = read_json(path)
        validate_record(record)
        if record['condition_fingerprint'] == fingerprint and (failure_code is None or failure_code in record['failure_codes']):
            matches.append(record)
    # Show successful recoveries first but retain failures as negative examples.
    matches.sort(key=lambda r: (not bool(r['successful_recovery_skills']), r['outcome'] != 'success', r['record_id']))
    return matches[:limit]


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['save', 'search'])
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--memory-dir', type=Path, default=DEFAULT_MEMORY)
    parser.add_argument('--failure-code')
    args = parser.parse_args()
    if args.operation == 'save':
        record, created = save_experience(args.run_dir, args.memory_dir)
        print(json.dumps({'record_id': record['record_id'], 'created': created, 'memory_dir': str(args.memory_dir)}))
    else:
        query = condition_fingerprint(read_json(args.run_dir / 'resolved_config.json'))
        results = retrieve_experiences(query, failure_code=args.failure_code, memory_dir=args.memory_dir)
        print(json.dumps(results, ensure_ascii=False, indent=2))
