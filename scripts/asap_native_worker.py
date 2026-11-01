"""Python 3.8 worker for an existing ASAP installation; no API or robot execution."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import shutil
import sys
import tempfile
import time


def validate_prerequisites(requires, parts):
    if not isinstance(requires, dict) or set(requires) != set(parts):
        raise ValueError('Prerequisites must cover exactly the native input parts.')
    for part, dependencies in requires.items():
        if (not isinstance(dependencies, list) or len(dependencies) != len(set(dependencies))
                or part in dependencies or not set(dependencies) <= set(parts)):
            raise ValueError('Invalid prerequisite IDs.')
    installed = set()
    while len(installed) < len(parts):
        ready = {p for p in parts if p not in installed and set(requires[p]) <= installed}
        if not ready:
            raise ValueError('Cyclic robot prerequisites.')
        installed.update(ready)


def removable_candidates(ordered, assembled, requires):
    return [p for p in ordered if p in assembled and p != '0'
            and not any(p in requires[q] for q in assembled if q != p)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True)
    parser.add_argument('--input', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--budget', type=int, default=20)
    parser.add_argument('--timeout', type=float, default=120)
    parser.add_argument('--robot-prerequisites')
    args = parser.parse_args()
    # Native SDF serialization makes many small writes. Keep generated caches on
    # the Linux filesystem instead of the Windows/WSL mount; copy inputs verbatim.
    with tempfile.TemporaryDirectory(prefix='llm-assembly-asap-') as local:
        run(args, Path(local))


def run(args, local):
    root, source, destination = (Path(x).resolve() for x in (args.root, args.input, args.output))
    print('native_stage: import_backend', flush=True)
    sys.path.insert(0, str(root))
    from plan_sequence.generator import generators
    from plan_sequence.planner import planners
    from plan_sequence.planner.base import NumpyEncoder
    inputs = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
              for p in sorted(source.iterdir()) if p.suffix == '.obj' or p.name == 'config.json'}
    for name in inputs:
        shutil.copyfile(source / name, local / name)
    if inputs != {name: hashlib.sha256((local / name).read_bytes()).hexdigest() for name in inputs}:
        raise ValueError('Native staging changed input bytes.')
    revision = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip()
    started = time.monotonic()
    print('native_stage: initialize_sdf_and_mass', flush=True)
    generator = generators['heur-out'](str(root / 'assets'), str(local), base_part='0', save_sdf=True)
    constraints = None
    if args.robot_prerequisites:
        constraint_bytes = Path(args.robot_prerequisites).read_bytes()
        requires = json.loads(constraint_bytes)
        validate_prerequisites(requires, [p.stem for p in local.glob('*.obj')])
        constraints = {'source': 'developer_declared_robot_prerequisites', 'requires': requires,
                       'sha256': hashlib.sha256(constraint_bytes).hexdigest()}
        original_generator = generator
        class ConstrainedGenerator:
            def __getattr__(self, name):
                return getattr(original_generator, name)
            def generate_candidate_part(self, assembled):
                return iter(removable_candidates(list(original_generator.generate_candidate_part(assembled)), assembled, requires))
        generator = ConstrainedGenerator()
    planner = planners['dfs'](generator, num_proc=1, save_sdf=True)
    planner.seed(3)
    print('native_stage: search', flush=True)
    tree = planner.plan(budget=args.budget, max_grippers=1, max_poses=1, pose_reuse=0,
                        early_term=True, timeout=args.timeout, plan_grasp=False, plan_arm=False,
                        debug=1, render=False)
    stats = planner.get_stats(tree)
    stats.update(total_n_eval=planner.n_eval, stop_msg=planner.stop_msg)
    edges = [{'before': list(a), 'after': list(b), 'n_eval': edge['n_eval'], 'sim_info': edge['sim_info']}
             for a, b, edge in tree.edges(data=True)]
    remaining, selected = list(planner.parts), []
    for part in stats['sequence'] or []:
        following = [p for p in remaining if p != part]
        selected.append({'before': remaining, 'after': following,
                         'sim_info': tree.edges[tuple(remaining), tuple(following)]['sim_info']})
        remaining = following
    report = {'schema': 'asap_native_dfs/v1', 'source_commit': revision, 'input_sha256': inputs,
              'backend': 'ASAP/RedMax', 'policy_kind': 'heur-out/dfs', 'api_calls': 0,
              'candidate_constraints': constraints,
              'stats': stats, 'selected_edges': selected, 'explored_edges': edges,
              'remaining': remaining, 'wall_elapsed_s': time.monotonic() - started,
              'input_staging': 'byte-identical Linux temporary directory; generated SDF caches excluded from source input',
              'settings': {'base_part': '0', 'max_grippers': 1, 'seed': 3, 'budget': args.budget,
                           'timeout_s': args.timeout, 'plan_grasp': False, 'plan_arm': False},
              'robot_execution_validated': False,
              'limitations': ['Normalized native solver units are not SI dynamics equivalence.',
                             'Native leaf expansion can add checks outside total_n_eval.',
                             'No robot, gripper, insertion trajectory, or assembly execution validation.']}
    destination.write_text(json.dumps(report, cls=NumpyEncoder, indent=2, allow_nan=False), encoding='utf-8')
    print(json.dumps({'success': stats['success'], 'sequence': stats['sequence'], 'output': str(destination)}), flush=True)


if __name__ == '__main__':
    main()
