"""One robot-installed component on a declared preassembled, still-free fixture."""
import argparse
import csv
import hashlib
import json
import math
import time
from pathlib import Path
import mujoco
import numpy as np
from failure_report import write_json
from frame_assembly_scene import build_frame_scene
from frame_assembly_skills import FrameSkills
from run_insertion import INSERTION_TRACE_FIELDS
from run_pick_place import AssemblyRecorder, save_snapshot
from workcell_scene import ROOT, part_name
from workcell_verification import rotation_error


def execute_trial(component='sleeve_1', *, cover_grip='center', snapshots=False,
                  transport_strategy='direct', transfer_obstacle=False, output_root=None, observer=None):
    if component not in ('sleeve_1', 'sleeve_2', 'sleeve_3', 'sleeve_4', 'cover'):
        raise ValueError('This probe installs one sleeve or the cover.')
    if transport_strategy not in ('direct', 'direct_then_rrt') or type(transfer_obstacle) is not bool:
        raise ValueError('Invalid transport option.')
    wall_started = time.monotonic()
    recorder = AssemblyRecorder(output_root or ROOT / 'runs/component_probes', skill='component_probe/v1',
                                validation_profile='compound_component_on_preassembled_fixture/v1')
    skills, result, initial_installed = None, None, set()
    status, error = 'failed', None
    motion_preflight_failures = []
    metrics = {'transport_samples': 0, 'maximum_transport_slip_m': 0., 'maximum_transport_tilt_rad': 0.,
               'previous_parts': {}}
    try:
        model, data, registry = build_frame_scene(10 if component == 'cover' else 5 + int(component[-1]),
                                                 cover_grip=cover_grip, transfer_obstacle=transfer_obstacle)
        initial_installed = set(registry) - {component}
        # Initial fixture configuration only, before any physical episode begins.
        # These components remain free bodies and are not robot installation successes.
        for name in initial_installed:
            entry = registry[name]
            data.joint(part_name(entry['config']) + '_free').qpos[:3] = entry['joint']['target_part_center_world_m']
        mujoco.mj_forward(model, data)
        metrics['previous_parts'] = {name: {'maximum_position_error_m': 0., 'maximum_orientation_error_rad': 0.}
                                     for name in sorted(initial_installed)}
        write_json(recorder.run_dir / 'resolved_config.json', {
            'environment': 'simulation', 'component': component, 'cover_grip': cover_grip,
            'initial_preassembled_parts': sorted(initial_installed | {'base'}),
            'robot_installations_goal': 1, 'registry': registry, 'simulator_version': mujoco.__version__,
            'transport_strategy': transport_strategy, 'transfer_obstacle': transfer_obstacle,
            'policy_kind': 'scripted', 'api_calls': 0,
            'implementation_sha256': {name: hashlib.sha256((ROOT / 'scripts' / name).read_bytes()).hexdigest()
                for name in ('frame_assembly_scene.py', 'frame_assembly_skills.py', 'workcell_motion.py',
                             'run_component_probe.py', 'insertion_scene.py', 'insertion_skills.py',
                             'loaded_transport.py', 'joint_path_planner.py', 'empty_approach.py')}})
        with recorder.trace_path.open('w', newline='', encoding='utf-8') as stream:
            writer = csv.DictWriter(stream, fieldnames=INSERTION_TRACE_FIELDS)
            writer.writeheader()
            def sample(row):
                writer.writerow(row)
                recorder.remember(skills, row)
                for name, previous in metrics['previous_parts'].items():
                    body = data.body(part_name(registry[name]['config']))
                    previous['maximum_position_error_m'] = max(previous['maximum_position_error_m'],
                        float(np.linalg.norm(body.xpos - registry[name]['joint']['target_part_center_world_m'])))
                    previous['maximum_orientation_error_rad'] = max(previous['maximum_orientation_error_rad'],
                        rotation_error(body.xmat.reshape(3, 3), registry[name]['joint']['target_part_rotation_world']))
                if row['stage'] == 'insert_transfer':
                    metrics['transport_samples'] += 1
                    metrics['maximum_transport_slip_m'] = max(metrics['maximum_transport_slip_m'], row.get('relative_slip_m', 0.))
                    z = data.body(skills.verifier.part_body).xmat.reshape(3, 3)[2, 2]
                    metrics['maximum_transport_tilt_rad'] = max(metrics['maximum_transport_tilt_rad'],
                        math.acos(float(np.clip(z, -1., 1.))))
            def stage(name):
                stream.flush()
                print(f'[{data.time:8.3f} s] {component}: {name}', flush=True)
            def transport_failure(exc):
                recorder.fail(exc, skills, 23)
                report = json.loads((recorder.run_dir / 'failure_report.json').read_text(encoding='utf-8'))
                report.update(response='stop_direct_plan; reobserve_before_proposing_alternative', active_part_id=component)
                motion_preflight_failures.append(report)
                write_json(recorder.run_dir / f'motion_preflight_{len(motion_preflight_failures):03d}.json', report)
                write_json(recorder.run_dir / 'failure_report.json', report)
            skills = FrameSkills(model, data, component, registry, initial_installed, sample, stage,
                                 transport_strategy=transport_strategy, on_transport_preflight_failure=transport_failure)
            if observer:
                observer(skills)  # Test hook after fixture initialization; not a policy action.
            skills.settle()
            skills.pick(component)
            result = skills.insert(component, registry[component]['config']['target']['id'])
            status = 'success'
    except (Exception, KeyboardInterrupt) as exc:
        status = recorder.fail(exc, skills, 23)
        error = str(exc)
    if skills and snapshots:
        save_snapshot(skills.model, skills.data, skills.config, recorder.run_dir / 'final.png')
    report = {'status': status, 'error': error, 'run_dir': str(recorder.run_dir), 'component': component,
              'initial_preassembled_parts': sorted(initial_installed | {'base'}),
              'robot_installations_goal': 1, 'robot_installed_parts': int(status == 'success'),
              'whole_frame_robot_assembly': False, 'result': result, 'samples': recorder.samples,
              'simulated_seconds': float(skills.data.time) if skills else None,
              'transport_strategy': transport_strategy, 'transfer_obstacle': transfer_obstacle,
              'transport_attempts': skills.transport_attempts if skills else [],
              'empty_approach_attempts': skills.empty_approach_attempts if skills else [],
              'motion_preflight_failures': motion_preflight_failures, 'part_metrics': metrics,
              'policy_kind': 'scripted', 'api_calls': 0, 'wall_elapsed_s': time.monotonic() - wall_started}
    write_json(recorder.run_dir / 'result.json', report)
    print(f'{status}: {recorder.run_dir}', flush=True)
    if error:
        print(error)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--component', choices=['sleeve_1', 'sleeve_2', 'sleeve_3', 'sleeve_4', 'cover'], default='sleeve_1')
    parser.add_argument('--cover-grip', choices=['center', 'edge'], default='center')
    parser.add_argument('--snapshots', action='store_true')
    parser.add_argument('--transport-strategy', choices=['direct', 'direct_then_rrt'], default='direct')
    parser.add_argument('--transfer-obstacle', action='store_true', help='Fixed test fixture, present from the start')
    args = parser.parse_args()
    result = execute_trial(args.component, cover_grip=args.cover_grip, snapshots=args.snapshots,
                           transport_strategy=args.transport_strategy, transfer_obstacle=args.transfer_obstacle)
    raise SystemExit(0 if result['status'] == 'success' else 1)
