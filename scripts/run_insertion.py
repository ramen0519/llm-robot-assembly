"""Run one physical post/socket joint and preserve measured evidence or failure."""
import argparse
from contextlib import nullcontext
import csv
import json
from pathlib import Path
import time

import mujoco
import mujoco.viewer
from failure_report import FailureCode, FailureEvent, write_json
from insertion_scene import build_insertion_scene
from insertion_skills import InsertionSkills
from run_pick_place import AssemblyRecorder, TRACE_FIELDS, save_snapshot
from workcell_scene import ROOT, set_workcell_camera

INSERTION_TRACE_FIELDS = TRACE_FIELDS + ['fixture_supported', 'fixture_contact_force_n', 'insertion_depth_m']


def execute_trial(*, headless=True, snapshots=False, output_root=None, on_stage=None, scenario="nominal", recover=False):
    if scenario not in ("nominal", "alignment_offset"):
        raise ValueError("Unknown scenario")
    recorder = AssemblyRecorder(output_root or ROOT / 'runs/step18', skill='scripted_insert_post/v1',
                                validation_profile='post_socket_insertion/v1')
    skills, status, error = None, 'failed', None
    failures, recovery_results = [], []
    try:
        model, data, config, joint = build_insertion_scene()
        write_json(recorder.run_dir / 'resolved_config.json', {'config': config, 'joint': joint,
                   'environment': 'simulation', 'observation_source': 'sim_ground_truth',
                   'simulator_version': mujoco.__version__, 'orchestration': 'scripted',
                   'scenario': scenario, 'physical_product_parts': 2, 'robot_installed_parts_goal': 1})
        context = nullcontext(None) if headless else mujoco.viewer.launch_passive(model, data)
        with context as viewer, recorder.trace_path.open('w', newline='', encoding='utf-8') as stream:
            writer = csv.DictWriter(stream, fieldnames=INSERTION_TRACE_FIELDS, extrasaction='raise')
            writer.writeheader()
            clock = {'wall': time.monotonic(), 'sim': 0., 'last_draw': -1.}
            if viewer:
                set_workcell_camera(viewer.cam, config)
            def sample(row):
                writer.writerow(row)
                recorder.remember(skills, row)
                if viewer:
                    if not viewer.is_running():
                        raise FailureEvent(FailureCode.CANCELLED, 'Viewer closed.')
                    if data.time - clock['last_draw'] > 1 / 60:
                        viewer.sync()
                        time.sleep(max(0., clock['wall'] + data.time - clock['sim'] - time.monotonic()))
                        clock['last_draw'] = float(data.time)
            def stage(name):
                print(f'[{data.time:7.3f} s] {name}', flush=True)
                if on_stage:
                    on_stage(name, skills)
                if snapshots and name in ('insert_transfer', 'insert_release', 'insert_verify'):
                    save_snapshot(model, data, config, recorder.run_dir / f'{name}.png')
                clock.update(wall=time.monotonic(), sim=float(data.time))
            skills = InsertionSkills(model, data, config, joint, sample, stage)
            skills.settle()
            skills.pick(config['part']['id'])
            try:
                skills.insert(config['part']['id'], config['target']['id'],
                              alignment_offset_m=[.006, 0.] if scenario == 'alignment_offset' else None)
            except FailureEvent as initial_failure:
                if not recover or initial_failure.code != FailureCode.ALIGNMENT_OUT_OF_TOLERANCE:
                    raise
                # Preserve the original failed attempt before running at most one recovery.
                recorder.fail(initial_failure, skills, 20)
                report = json.loads((recorder.run_dir / 'failure_report.json').read_text(encoding='utf-8'))
                failures.append(report)
                write_json(recorder.run_dir / 'failure_001.json', report)
                from insertion_recovery import recover_alignment
                recovery_results.append(recover_alignment(skills, initial_failure))
                write_json(recorder.run_dir / 'recovery_001.json', recovery_results[-1])
                # A newly planned insertion after fresh observation and verified realignment.
                # This is not replay of a command with uncertain execution status.
                skills.insert(config['part']['id'], config['target']['id'])
            status = 'success'
            if snapshots:
                save_snapshot(model, data, config, recorder.run_dir / 'final.png')
    except (Exception, KeyboardInterrupt) as exc:
        error = str(exc)
        status = recorder.fail(exc, skills, 20 if recover else 18)
        report = json.loads((recorder.run_dir / 'failure_report.json').read_text(encoding='utf-8'))
        failures.append(report)
        write_json(recorder.run_dir / f'failure_{len(failures):03d}.json', report)
    result = {'status': status, 'error': error, 'run_dir': str(recorder.run_dir),
              'environment': 'simulation', 'validation_profile': 'post_socket_insertion/v1',
              'simulated_seconds': float(skills.data.time) if skills else None,
              'scenario': scenario, 'recovery_enabled': recover, 'recovery_budget': 1 if recover else 0,
              'failures': failures, 'recovery_results': recovery_results,
              'samples': recorder.samples, 'results': skills.results if skills else [],
              'robot_installed_parts': 1 if status == 'success' else 0,
              'plans': skills.motion.plans if skills else []}
    write_json(recorder.run_dir / 'result.json', result)
    print(f"{status}: {recorder.run_dir}", flush=True)
    if error:
        print(error, flush=True)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--headless', action='store_true')
    parser.add_argument('--snapshots', action='store_true')
    parser.add_argument('--scenario', choices=['nominal', 'alignment_offset'], default='nominal')
    parser.add_argument('--recover', action='store_true', help='Allow one measured alignment recovery')
    args = parser.parse_args()
    result = execute_trial(headless=args.headless, snapshots=args.snapshots, scenario=args.scenario, recover=args.recover)
    raise SystemExit(0 if result['status'] == 'success' else 1)
