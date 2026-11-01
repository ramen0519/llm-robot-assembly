"""Bounded API-free ASAP trial using our CAD and an existing WSL installation."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path, PureWindowsPath
import subprocess
import time

import numpy as np
from experience_memory import read_json
from export_planner_input import export_bundle
from failure_report import write_json
from frame_assembly_scene import build_frame_scene
from workcell_scene import ROOT

ASAP_ROOT = '/opt/asap-research/ASAP'
ASAP_PYTHON = '/opt/asap-research/envs/asap/bin/python'
EXPECTED_COMMIT = '3727e716b57e0b508c4e9ecd9fefa6953e8f72da'


def wsl_path(path):
    p = PureWindowsPath(Path(path).resolve())
    if not p.drive or len(p.drive) != 2 or p.drive[1] != ':':
        raise ValueError('Expected an absolute local Windows drive path.')
    return '/mnt/' + p.drive[0].lower() + '/' + '/'.join(p.parts[1:])


def prepare_normalized(bundle, count=10, scale=100.):
    if type(count) is not int or not 2 <= count <= 10 or not np.isfinite(scale) or not 1 <= scale <= 1000:
        raise ValueError('Invalid product count or uniform length scale.')
    folder = Path(bundle)
    destination = folder / 'asap_normalized'
    destination.mkdir()  # Never overwrite an earlier solver input or cache.
    config = read_json(folder / 'asap_input/config.json')
    selected = {str(i): deepcopy(config[str(i)]) for i in range(count)}
    for part, info in selected.items():
        info['final_state'] = (np.asarray(info['final_state']) * scale).tolist()
        # The transformed loader treats an absent initial_state as unspecified;
        # an explicit null is passed to len() by its current implementation.
        if info.get('initial_state') is None:
            info.pop('initial_state', None)
        lines = []
        for line in (folder / 'asap_input' / (part + '.obj')).read_text(encoding='utf-8').splitlines():
            fields = line.split()
            if fields and fields[0] == 'v':
                if len(fields) != 4:
                    raise ValueError('Expected XYZ-only OBJ vertices.')
                line = 'v ' + ' '.join(format(float(x) * scale, '.17g') for x in fields[1:])
            lines.append(line)
        (destination / (part + '.obj')).write_text('\n'.join(lines) + '\n', encoding='utf-8')
    write_json(destination / 'config.json', selected)
    binding = read_json(folder / 'binding_template.json')
    binding['source_length_scale_m'] = 1. / scale
    binding['part_map'] = {key: binding['part_map'][key] for key in selected}
    manifest = {'schema': 'asap_normalized_input/v1', 'length_scale_native_per_m': scale,
                'source_length_scale_m': 1. / scale, 'count': count,
                'units_note': 'Uniform geometric normalization only; native physical constants are unchanged.',
                'binding': binding, 'input_sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in sorted(destination.iterdir())}, 'original_manifest': read_json(folder / 'planner_input_manifest.json')}
    write_json(folder / 'normalization.json', manifest)
    return destination, manifest


def assess_candidate(report, manifest, registry):
    """Convert a complete native sequence only after conservative robot-scope checks."""
    errors, assembly = [], []
    mapping = manifest['binding']['part_map']
    if report.get('schema') != 'asap_native_dfs/v1' or report.get('source_commit') != EXPECTED_COMMIT:
        errors.append('Unsupported native report schema/source revision.')
    if report.get('input_sha256') != manifest['input_sha256']:
        errors.append('Native inputs do not match the exported CAD hashes.')
    if report.get('stats', {}).get('success') is not True or report.get('remaining') != ['0']:
        errors.append('No complete verified native disassembly sequence.')
    if report.get('stats', {}).get('stop_msg') in ('exception', 'interrupt'):
        errors.append('Native backend ended with an error or interruption.')
    edges = report.get('selected_edges', [])
    remaining = sorted(mapping)
    sequence = []
    for edge in edges:
        info = edge.get('sim_info', {})
        part = info.get('part_move')
        after = [p for p in remaining if p != part]
        if (part == '0' or part not in remaining or edge.get('before') != remaining
                or edge.get('after') != after or info.get('feasible') is not True):
            errors.append('Selected native transitions are incomplete or discontinuous.')
            break
        if info.get('base_part') != '0' or info.get('parts_fix') != []:
            errors.append('Additional holding/support is outside the one-arm fixture scope: ' + str(part))
        pose = info.get('pose')
        if pose is not None and (np.asarray(pose).shape != (4, 4) or not np.allclose(pose, np.eye(4), atol=1e-9)):
            errors.append('Assembly reorientation is not supported by this executor.')
        action = np.asarray(info.get('action'), dtype=float)
        if (action.shape != (3,) or not np.all(np.isfinite(action)) or np.linalg.norm(action) < 1e-9
                or not np.allclose(action / np.linalg.norm(action), [0., 0., 1.], atol=1e-6)):
            errors.append('Removal direction is outside the current vertical insertion skill: ' + str(part))
        sequence.append(part)
        remaining = after
    if len(sequence) != len(mapping) - 1 or remaining != ['0'] or report.get('stats', {}).get('sequence') != sequence:
        errors.append('Selected edges do not cover each non-base product part exactly once.')
    installed = {'base'}
    for part in reversed(sequence):
        name = mapping[part]
        if name not in registry or not set(registry[name]['requires']).issubset(installed):
            errors.append('Reversed sequence violates robot assembly prerequisites: ' + name)
        assembly.append(name)
        installed.add(name)
    return {'schema': 'native_assembly_candidate/v1', 'status': 'candidate' if not errors else 'rejected',
            'errors': errors, 'proposed_order': assembly, 'robot_execution_validated': False,
            'requires': ['current robot observations', 'grasp/IK/path checks', 'independent MuJoCo assembly verification']}


def execute_native(*, count=3, timeout_s=120, budget=20, output_root=None):
    if type(timeout_s) is not int or not 10 <= timeout_s <= 600 or type(budget) is not int or not 1 <= budget <= 100:
        raise ValueError('Native wall time/budget must be bounded.')
    bundle = export_bundle(output_root or ROOT / 'runs/asap_native')
    source, manifest = prepare_normalized(bundle, count)
    command = ['wsl', '-d', 'ASAP-Ubuntu', '-u', 'root', '--cd', ASAP_ROOT, '--',
               'env', 'OMP_NUM_THREADS=1', 'PYTHONDONTWRITEBYTECODE=1',
               'timeout', '--signal=TERM', '--kill-after=5s', str(timeout_s + 15),
               ASAP_PYTHON, '-B', '-u', wsl_path(ROOT / 'scripts/asap_native_worker.py'),
               '--root', ASAP_ROOT, '--input', wsl_path(source), '--output', wsl_path(bundle / 'native_report.json'),
               '--budget', str(budget), '--timeout', str(timeout_s)]
    write_json(bundle / 'invocation.json', {'command': command, 'api_calls': 0,
        'worker_sha256': hashlib.sha256((ROOT / 'scripts/asap_native_worker.py').read_bytes()).hexdigest(),
        'host_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})
    started = time.monotonic()
    with (bundle / 'native_stdout.log').open('w', encoding='utf-8') as stdout, (bundle / 'native_stderr.log').open('w', encoding='utf-8') as stderr:
        completed = subprocess.run(command, stdout=stdout, stderr=stderr, check=False)
    result = {'exit_code': completed.returncode, 'api_calls': 0, 'run_dir': str(bundle),
              'wall_elapsed_s': time.monotonic() - started, 'status': 'incomplete',
              'robot_execution_validated': False}
    if completed.returncode == 0 and (bundle / 'native_report.json').is_file():
        report = read_json(bundle / 'native_report.json')
        _, _, registry = build_frame_scene(count)
        candidate = assess_candidate(report, manifest, registry)
        candidate['source_report_sha256'] = hashlib.sha256((bundle / 'native_report.json').read_bytes()).hexdigest()
        write_json(bundle / 'assembly_candidate.json', candidate)
        native_status = ('incomplete' if report['stats']['stop_msg'] in ('exception', 'interrupt')
                         else 'native_success' if report['stats']['success'] else 'native_no_solution_found')
        result.update(status=native_status,
                      robot_candidate_status=candidate['status'], native_stats=report['stats'])
    else:
        result['note'] = 'Timeout/error is unknown feasibility; inspect native logs. It is not proof of impossibility.'
    write_json(bundle / 'result.json', result)
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--parts', type=int, default=3, choices=range(2, 11))
    parser.add_argument('--timeout', type=int, default=120)
    parser.add_argument('--budget', type=int, default=20)
    args = parser.parse_args()
    result = execute_native(count=args.parts, timeout_s=args.timeout, budget=args.budget)
    raise SystemExit(0 if result['status'] == 'native_success' else 1)
