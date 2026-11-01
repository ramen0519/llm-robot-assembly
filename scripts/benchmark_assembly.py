"""Run a declared offline evaluation matrix using real MuJoCo skill episodes."""
import argparse
import csv
import hashlib
from pathlib import Path
from experience_memory import CHECKS, condition_fingerprint, read_json
from failure_report import TrialRecorder, write_json
from run_assembly_graph import execute_trial
from workcell_scene import ROOT

CASES = (
    {'id': 'nominal', 'scenario': 'nominal', 'recovery_budget': 1, 'expected_status': 'success', 'expected_recoveries': 0},
    {'id': 'alignment_without_recovery', 'scenario': 'alignment_offset', 'recovery_budget': 0, 'expected_status': 'failed', 'expected_recoveries': 0},
    {'id': 'alignment_with_recovery', 'scenario': 'alignment_offset', 'recovery_budget': 1, 'expected_status': 'success', 'expected_recoveries': 1},
)


def trial_metrics(result, case):
    if result.get('status') not in ('success', 'failed', 'stopped', 'cancelled'):
        raise ValueError('Incomplete outcome cannot be scored.')
    if (result.get('policy_kind') != 'scripted' or result.get('api_calls') != 0
            or any(result.get(k) != 0 for k in ('input_tokens', 'output_tokens', 'api_cost'))):
        raise ValueError('This offline matrix has no paid-model accounting contract.')
    graph = result.get('graph_state')
    if not isinstance(graph, dict):
        raise ValueError('Missing recorded graph state.')
    reports = graph.get('failures', [])
    if not isinstance(reports, list) or any(r.get('environment') != 'simulation' for r in reports):
        raise ValueError('Expected simulation failure evidence.')
    success = result['status'] == 'success'
    insertions = [r for r in result.get('results', []) if r.get('skill') == 'insert_post/v1' and r.get('status') == 'success']
    if success:
        if (result.get('robot_installed_parts') != 1 or len(insertions) != 1
                or set(insertions[0].get('checks', {})) != CHECKS
                or not all(v is True for v in insertions[0]['checks'].values())
                or not 0 <= insertions[0].get('maximum_final_position_error_m', -1) < .001
                or not .015 < insertions[0].get('minimum_final_depth_m', -1) < .017
                or graph.get('status') != 'success'):
            raise ValueError('Success without measured assembly evidence.')
    elif result.get('robot_installed_parts') != 0:
        raise ValueError('Failed first-joint episode cannot count a completed installation.')
    recoveries = graph.get('recoveries')
    if type(recoveries) is not int or not 0 <= recoveries <= case['recovery_budget']:
        raise ValueError('Invalid recovery count.')
    alignment_failures = sum(r.get('failure_code') == 'ALIGNMENT_OUT_OF_TOLERANCE' for r in reports)
    expected_failures = 0 if case['scenario'] == 'nominal' else 1
    expectation = (result['status'] == case['expected_status'] and recoveries == case['expected_recoveries']
                   and alignment_failures == expected_failures and len(reports) == expected_failures)
    return {'status': result['status'], 'assembly_success': success, 'expected_behavior_passed': expectation,
            'robot_installed_parts': result['robot_installed_parts'],
            'failure_count': len(reports), 'alignment_failure_count': alignment_failures,
            'recovery_attempts_succeeded': recoveries,
            'recovered_to_completed_assembly': success and recoveries > 0,
            'simulated_seconds': result.get('simulated_seconds'), 'wall_elapsed_s': result.get('wall_elapsed_s'),
            'policy_calls': graph.get('policy_calls'), 'api_calls': 0, 'input_tokens': 0, 'output_tokens': 0,
            'api_cost': 0., 'max_final_position_error_mm': insertions[0]['maximum_final_position_error_m'] * 1000 if success else None,
            'run_dir': result['run_dir']}


def execute_matrix(*, repetitions=1, modes=('single', 'roles'), output_root=None):
    if type(repetitions) is not int or not 1 <= repetitions <= 10:
        raise ValueError('Repetitions must be 1..10.')
    if not modes or len(set(modes)) != len(modes) or not set(modes) <= {'single', 'roles'}:
        raise ValueError('Expected distinct offline policy modes.')
    folder = TrialRecorder(output_root or ROOT / 'runs/step25_baseline').run_dir
    manifest = {'schema_version': 1, 'environment': 'simulation', 'split': 'development',
                'policy_kind': 'scripted', 'models': None, 'memory_enabled': False,
                'case_definitions': CASES, 'modes': modes, 'repetitions': repetitions,
                'timing_valid_for_comparison': False, 'llm_performance_comparison': False,
                'note': 'Deterministic plumbing baseline; repeated identical conditions are not an unseen-task benchmark.',
                'implementation_sha256': {name: hashlib.sha256((ROOT / 'scripts' / name).read_bytes()).hexdigest()
                    for name in ('benchmark_assembly.py', 'assembly_graph.py', 'assembly_policy.py', 'run_assembly_graph.py',
                                 'insertion_skills.py', 'insertion_recovery.py', 'workcell_motion.py', 'move_tcp.py')}}
    write_json(folder / 'suite_manifest.json', manifest)
    rows, fingerprints = [], set()
    for repetition in range(1, repetitions + 1):
        for case in CASES:
            for mode in modes:
                result = execute_trial(mode=mode, scenario=case['scenario'], recovery_budget=case['recovery_budget'],
                                       output_root=folder / 'episodes')
                fingerprint = condition_fingerprint(read_json(Path(result['run_dir']) / 'resolved_config.json'))
                fingerprints.add(fingerprint)
                row = {'case_id': case['id'], 'mode': mode, 'repetition': repetition, 'condition_fingerprint': fingerprint,
                       **trial_metrics(result, case)}
                rows.append(row)
                # Retain partial progress if a later trial is interrupted.
                write_json(folder / 'progress.json', {'completed_episodes': len(rows), 'rows': rows})
                if result['status'] == 'cancelled':
                    raise KeyboardInterrupt('Evaluation interrupted; completed episodes remain recorded.')
    if len(fingerprints) != 1:
        raise ValueError('Physical task/solver conditions differ across the requested comparison.')
    summary = {'schema_version': 1, 'status': 'passed' if all(r['expected_behavior_passed'] for r in rows) else 'failed',
               'policy_kind': 'scripted', 'llm_comparison': False, 'episodes': len(rows),
               'assembly_successes': sum(r['assembly_success'] for r in rows),
               'expected_behavior_passes': sum(r['expected_behavior_passed'] for r in rows),
               'rows': rows, 'run_dir': str(folder)}
    write_json(folder / 'summary.json', summary)
    with (folder / 'metrics.csv').open('w', newline='', encoding='utf-8-sig') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    lines = ['# 규칙 정책 개발 평가', '',
             '실제 MuJoCo 실행이다. LLM 모델 비교가 아니며 API 호출·토큰·비용은 모두 0이다.', '',
             '정상 성공, 복구 없이 중단, 복구 후 성공을 구분한다. 예상대로 실패한 시험도 조립 성공률에는 실패로 센다.', '',
             '| 조건 | 정책 구성 | 조립 성공 | 예상 동작 | 복구 성공 | sim 초 | 정책 호출 |',
             '|---|---|---|---|---:|---:|---:|']
    for r in rows:
        lines.append(f"| {r['case_id']} | {r['mode']} | {r['assembly_success']} | {r['expected_behavior_passed']} | {r['recovery_attempts_succeeded']} | {r['simulated_seconds']:.3f} | {r['policy_calls']} |")
    lines += ['', '동일한 규칙을 역할별 객체로 나눈 비교다. 멀티에이전트 LLM의 우위·비용 절감을 입증하지 않는다.',
              '개발 중 실행 시간은 기록용이다. 다른 시뮬레이션과의 CPU 경합이 있어 wall time 성능 비교에는 사용하지 않는다.',
              '반복 수를 늘려도 같은 결정론적 장면의 재현 검사이며 새로운 작업에 대한 일반화 시험이 되지 않는다.', '']
    (folder / 'REPORT.md').write_text('\n'.join(lines), encoding='utf-8')
    print(f"{summary['status']}: {folder}", flush=True)
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repetitions', type=int, default=1)
    parser.add_argument('--modes', nargs='+', choices=['single', 'roles'], default=['single', 'roles'])
    args = parser.parse_args()
    result = execute_matrix(repetitions=args.repetitions, modes=tuple(args.modes))
    raise SystemExit(0 if result['status'] == 'passed' else 1)
