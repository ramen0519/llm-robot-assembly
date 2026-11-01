"""Paired declared development cases with real physics, no memory updates or LLM."""
import argparse
from pathlib import Path
from experience_memory import CHECKS, condition_fingerprint, read_json
from failure_report import TrialRecorder, write_json
from run_assembly_graph import execute_trial
from skill_recipes import implementation_fingerprint
from workcell_scene import ROOT

CASES = (
    {'id': 'tray_x_plus_10mm', 'scene_case': 'tray_x_plus_10mm', 'scenario': 'nominal', 'transfer_obstacle': False, 'transport_replan_budget': 0, 'expected_status': 'success'},
    {'id': 'tray_y_plus_10mm', 'scene_case': 'tray_y_plus_10mm', 'scenario': 'nominal', 'transfer_obstacle': False, 'transport_replan_budget': 0, 'expected_status': 'success'},
    {'id': 'moved_obstacle_replan', 'scene_case': 'obstacle_x_plus_10mm', 'scenario': 'alignment_offset', 'transfer_obstacle': True, 'transport_replan_budget': 1, 'expected_status': 'success'},
    {'id': 'moved_obstacle_stop', 'scene_case': 'obstacle_x_plus_10mm', 'scenario': 'alignment_offset', 'transfer_obstacle': True, 'transport_replan_budget': 0, 'expected_status': 'failed'},
)


def score(result):
    if result['policy_kind'] != 'scripted' or result['api_calls'] != 0 or not result.get('graph_state'):
        raise ValueError('Expected complete offline physics evidence.')
    success = result['status'] == 'success'
    inserts = [r for r in result['results'] if r.get('skill') == 'insert_post/v1' and r.get('status') == 'success']
    if success and (len(inserts) != 1 or result['robot_installed_parts'] != 1
                    or set(inserts[0]['checks']) != CHECKS or not all(x is True for x in inserts[0]['checks'].values())):
        raise ValueError('No measured insertion success evidence.')
    graph = result['graph_state']
    return {'status': result['status'], 'assembly_success': success, 'simulated_seconds': result['simulated_seconds'],
            'policy_calls': graph['policy_calls'], 'replans': graph['transport_replans'], 'recoveries': graph['recoveries'],
            'failure_codes': [f['failure_code'] for f in graph['failures']], 'api_calls': 0,
            'run_dir': result['run_dir']}


def execute_matrix(*, modes=('single', 'roles'), output_root=None):
    if not modes or len(set(modes)) != len(modes) or not set(modes) <= {'single', 'roles'}:
        raise ValueError('Expected distinct scripted modes.')
    folder = TrialRecorder(output_root or ROOT / 'runs/step25_variations').run_dir
    implementation = implementation_fingerprint()
    write_json(folder / 'suite_manifest.json', {'cases': CASES, 'modes': modes, 'split': 'development',
        'implementation_fingerprint': implementation, 'memory_enabled': False, 'llm_comparison': False,
        'timing_valid_for_comparison': False, 'note': 'Declared before execution; failures count as assembly failures.'})
    rows = []
    for case in CASES:
        fingerprints = set()
        for mode in modes:
            result = execute_trial(mode=mode, output_root=folder / 'episodes',
                                   **{k: v for k, v in case.items() if k not in ('id', 'expected_status')})
            fingerprint = condition_fingerprint(read_json(Path(result['run_dir']) / 'resolved_config.json'))
            fingerprints.add(fingerprint)
            rows.append({'case_id': case['id'], 'mode': mode, 'condition_fingerprint': fingerprint,
                         'expected_behavior_passed': result['status'] == case['expected_status'], **score(result)})
            write_json(folder / 'progress.json', {'rows': rows})
        if len(fingerprints) != 1:
            raise ValueError('Paired modes did not receive identical physics conditions.')
    unchanged = implementation == implementation_fingerprint()
    summary = {'run_dir': str(folder), 'episodes': len(rows), 'rows': rows,
               'assembly_successes': sum(r['assembly_success'] for r in rows),
               'implementation_unchanged': unchanged, 'llm_comparison': False,
               'status': 'passed' if unchanged and all(r['expected_behavior_passed'] for r in rows) else 'failed'}
    write_json(folder / 'summary.json', summary)
    print(summary['status'] + ': ' + str(folder), flush=True)
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--modes', nargs='+', choices=['single', 'roles'], default=['single', 'roles'])
    args = parser.parse_args()
    result = execute_matrix(modes=tuple(args.modes))
    raise SystemExit(0 if result['status'] == 'passed' else 1)
