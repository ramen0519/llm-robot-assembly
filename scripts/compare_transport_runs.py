"""Compare two recorded simulation episodes differing only in transport strategy."""
import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path

from experience_memory import CHECKS
from failure_report import TrialRecorder, write_json
from workcell_scene import ROOT

FRAME_CHECKS = (CHECKS - {'socket_floor_support'}) | {'declared_mating_support'}


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _load(folder, strategy):
    folder = Path(folder)
    config = json.loads((folder / 'resolved_config.json').read_text(encoding='utf-8'))
    result = json.loads((folder / 'result.json').read_text(encoding='utf-8'))
    if (config.get('environment') != 'simulation' or config.get('policy_kind') != 'scripted'
            or result.get('policy_kind') != 'scripted' or config.get('api_calls') != 0 or result.get('api_calls') != 0):
        raise ValueError('Only recorded offline simulation episodes are supported.')
    if config.get('transport_strategy') != strategy or result.get('transport_strategy') != strategy:
        raise ValueError('Strategy labels do not match this comparison arm.')
    sources = config.get('implementation_sha256')
    if not isinstance(sources, dict) or not sources or any(not isinstance(v, str) or len(v) != 64 for v in sources.values()):
        raise ValueError('Missing recorded implementation fingerprints.')
    registry = config.get('registry')
    if not isinstance(registry, dict) or not registry:
        raise ValueError('Missing product registry.')
    component = config.get('component')
    if component is not None:
        if component not in registry:
            raise ValueError('Probe component is absent from the registry.')
        expected_initial = sorted((set(registry) - {component}) | {'base'})
        if config.get('initial_preassembled_parts') != expected_initial or result.get('initial_preassembled_parts') != expected_initial:
            raise ValueError('Initial fixture accounting is inconsistent.')
        if result.get('whole_frame_robot_assembly') is not False:
            raise ValueError('An isolated probe is not full robot assembly.')
        goal, scope = 1, 'one_component_on_preassembled_fixture'
    else:
        goal, scope = len(registry), 'continuous_frame_assembly'
        if config.get('product_parts_present') != goal + 1 or result.get('product_parts_present') != goal + 1:
            raise ValueError('Product count is inconsistent.')
    count = result.get('robot_installed_parts')
    if (config.get('robot_installations_goal') != goal or result.get('robot_installations_goal') != goal
            or type(count) is not int or not 0 <= count <= goal):
        raise ValueError('Robot installation accounting is inconsistent.')
    status = result.get('status')
    if status not in ('success', 'failed', 'stopped', 'cancelled'):
        raise ValueError('Incomplete episode cannot be scored.')
    for field in ('simulated_seconds', 'wall_elapsed_s'):
        value = result.get(field)
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise ValueError('Missing finite recorded time.')
    if type(result.get('samples')) is not int or result['samples'] <= 0:
        raise ValueError('No physical samples recorded.')
    expected_ids = {component} if component else set(registry)
    completed = [result.get('result')] if component and count else (result.get('results', []) if not component else [])
    if len(completed) != count or any(not isinstance(item, dict) for item in completed):
        raise ValueError('Missing completed installation records.')
    ids = [item.get('part_id') for item in completed]
    if len(set(ids)) != len(ids) or not set(ids) <= expected_ids:
        raise ValueError('Completed part identities are inconsistent.')
    for item in completed:
        error = item.get('maximum_final_position_error_m')
        if (item.get('status') != 'success' or set(item.get('checks', {})) != FRAME_CHECKS
                or not all(v is True for v in item['checks'].values())
                or type(error) not in (int, float) or not math.isfinite(error) or not 0 <= error < .001):
            raise ValueError('Installation is missing measured verification.')
    if status == 'success' and (count != goal or set(ids) != expected_ids):
        raise ValueError('Success cannot omit required installations.')
    if component is None:
        if set(result.get('completed_installations', [])) != set(ids):
            raise ValueError('Completed installation lists disagree.')
        if result.get('ten_part_assembly_complete') is not (status == 'success' and goal == 9):
            raise ValueError('Whole-assembly success flag is inconsistent.')
    terminal_code = None
    if status != 'success':
        failure = json.loads((folder / 'failure_report.json').read_text(encoding='utf-8'))
        if failure.get('environment') != 'simulation' or not failure.get('failure_code'):
            raise ValueError('Missing simulation failure evidence.')
        terminal_code = failure['failure_code']
    planners = Counter()
    for field in ('transport_attempts', 'empty_approach_attempts'):
        attempts = result.get(field, [])
        groups = attempts.values() if isinstance(attempts, dict) else [attempts]
        for group in groups:
            for attempt in group:
                if attempt.get('status') == 'executed':
                    planners[attempt['planning']['planner']] += 1
    row = {'scope': scope, 'component': component, 'strategy': strategy, 'status': status,
           'assembly_success': status == 'success', 'robot_installed_parts': count, 'robot_installations_goal': goal,
           'simulated_seconds': result['simulated_seconds'], 'wall_elapsed_s': result['wall_elapsed_s'],
           'samples': result['samples'], 'policy_calls': result.get('policy_calls'), 'api_calls': 0,
           'executed_recorded_planner_counts': dict(planners), 'terminal_failure_code': terminal_code,
           'run_dir': str(folder.resolve()),
           'input_sha256': {name: hashlib.sha256((folder / name).read_bytes()).hexdigest()
                            for name in ('resolved_config.json', 'result.json')}}
    common = deepcopy(config)
    del common['transport_strategy']
    return common, row


def compare_runs(direct_dir, fallback_dir, *, output_root=None):
    direct_config, direct = _load(direct_dir, 'direct')
    fallback_config, fallback = _load(fallback_dir, 'direct_then_rrt')
    if direct_config != fallback_config:
        raise ValueError('Comparison requires identical recorded conditions and source fingerprints except transport_strategy.')
    summary = {'schema_version': 1, 'environment': 'simulation', 'split': 'development',
               'condition_sha256': _hash(direct_config), 'policy_kind': 'scripted', 'llm_comparison': False,
               'timing_valid_for_comparison': False, 'generalization_benchmark': False,
               'comparison_variable': 'transport_strategy', 'rows': [direct, fallback]}
    folder = TrialRecorder(output_root or ROOT / 'runs/step25_transport').run_dir
    write_json(folder / 'summary.json', summary)
    lines = ['# 동일 조건의 운반 전략 비교', '',
             '기록된 실제 시뮬레이션 결과 두 개를 비교한다. 새 로봇 명령·시뮬레이션·API 요청을 실행하지 않는다.', '',
             f"조건 지문: `{summary['condition_sha256']}`. 입력 파일 지문은 summary.json에 남겼다.", '',
             '| 전략 | 전체 결과 | 로봇 설치 / 목표 | sim 초 | 종료 실패 코드 |',
             '|---|---|---:|---:|---|']
    for row in summary['rows']:
        lines.append(f"| {row['strategy']} | {row['status']} | {row['robot_installed_parts']} / {row['robot_installations_goal']} | {row['simulated_seconds']:.3f} | {row['terminal_failure_code'] or '없음'} |")
    lines += ['', '부품·배치·준비물·시뮬레이터 버전·기록된 코드 지문·정책 조건은 같고 운반 전략만 다르다.',
              '개별 부품 시험의 초기 사전 조립 부품은 로봇 설치 횟수에서 제외한다. 전체 실패와 부분 설치 수를 구분한다.',
              '성공 전 사전 충돌 보고서가 있어도 전체 결과가 성공이면 종료 실패로 집계하지 않는다.',
              '실패 실행은 일찍 끝날 수 있으므로 완료 시간 우열로 해석하지 않는다. 동시 실행으로 wall time도 성능 비교에 사용하지 않는다.',
              '기본 direct 경로의 모든 내부 계획 통계는 기록되지 않았으므로 누락된 수를 0회 계산으로 해석하지 않는다.',
              '고정된 개발 사례 한 쌍이다. 일반 성공률, LLM 판단 효과, 스킬 학습 효과 또는 비용 절감의 근거는 아니다.', '']
    (folder / 'REPORT.md').write_text('\n'.join(lines), encoding='utf-8')
    print(f'comparison: {folder}', flush=True)
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--direct-run', type=Path, required=True)
    parser.add_argument('--fallback-run', type=Path, required=True)
    args = parser.parse_args()
    compare_runs(args.direct_run, args.fallback_run)
