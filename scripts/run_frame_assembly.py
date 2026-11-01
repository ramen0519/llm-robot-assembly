"""Physical frame assembly with compound sleeves, prerequisite checks and full-state verification."""
import argparse
import csv
from contextlib import nullcontext
import hashlib
import json
import math
import numpy as np
import time
import mujoco
import mujoco.viewer
from pathlib import Path
from failure_report import FailureCode, FailureEvent, write_json
from frame_assembly_scene import build_frame_scene
from frame_assembly_skills import FrameSkills
from run_insertion import INSERTION_TRACE_FIELDS
from run_pick_place import AssemblyRecorder, save_snapshot
from workcell_scene import ROOT, set_workcell_camera, part_name
from workcell_verification import rotation_error


def execute_trial(*, product_count=6, snapshots=False, output_root=None, mode='scripted', scenario='nominal', show_viewer=False, transport_strategy='direct', transfer_obstacle=False, observer=None):
    if mode not in ('scripted', 'single', 'roles') or scenario not in ('nominal', 'first_alignment_offset'):
        raise ValueError('Unknown orchestration mode/scenario')
    if mode == 'scripted' and scenario != 'nominal':
        raise ValueError('The first-alignment scenario requires a graph mode')
    if transport_strategy not in ('direct', 'direct_then_rrt') or type(transfer_obstacle) is not bool:
        raise ValueError('Invalid transport option.')
    if (transport_strategy != 'direct' or transfer_obstacle) and (type(product_count) is not int or not 2 <= product_count <= 5):
        raise ValueError('Detour experiments currently support base plus posts only (2-5 parts).')
    wall_started = time.monotonic()
    recorder = AssemblyRecorder(output_root or ROOT / 'runs/step23_frame', skill='scripted_frame_assembly/v1' if mode == 'scripted' else 'frame_graph/v1',
                                validation_profile='compound_frame_assembly/v1')
    skills, installed, results = None, set(), []
    status, error = 'failed', None
    graph_states, failures = {}, []
    transport_attempts, empty_approach_attempts, motion_preflight_failures, part_metrics = {}, {}, [], {}
    remaining_recoveries = 1 if mode != 'scripted' else 0
    from assembly_policy import ScriptedPolicy
    policies = {key: ScriptedPolicy(key) for key in (['single'] if mode == 'single' else ['assembly', 'recovery'])}
    try:
        model, data, registry = build_frame_scene(product_count, transfer_obstacle=transfer_obstacle)
        write_json(recorder.run_dir / 'resolved_config.json', {
            'registry': registry, 'environment': 'simulation', 'observation_source': 'sim_ground_truth',
            'product_parts_present': product_count, 'robot_installations_goal': product_count - 1,
            'transport_strategy': transport_strategy, 'transfer_obstacle': transfer_obstacle,
            'policy_kind': 'scripted', 'orchestration': mode, 'scenario': scenario, 'recovery_budget': remaining_recoveries, 'api_calls': 0, 'simulator_version': mujoco.__version__,
            'implementation_sha256': {name: hashlib.sha256((ROOT / 'scripts' / name).read_bytes()).hexdigest()
                for name in ('frame_assembly_scene.py', 'frame_assembly_skills.py', 'workcell_motion.py', 'run_frame_assembly.py',
                             'insertion_scene.py', 'insertion_skills.py', 'loaded_transport.py', 'joint_path_planner.py', 'empty_approach.py')}})
        viewer_context = mujoco.viewer.launch_passive(model, data) if show_viewer else nullcontext(None)
        with viewer_context as viewer, recorder.trace_path.open('w', newline='', encoding='utf-8') as stream, (recorder.run_dir / 'graph_events.jsonl').open('w', encoding='utf-8') as events:
            clock = {'last_draw': -1., 'wall': time.monotonic(), 'sim': float(data.time)}
            if viewer:
                set_workcell_camera(viewer.cam, next(iter(registry.values()))['config'])
            writer = csv.DictWriter(stream, fieldnames=['active_part_id', *INSERTION_TRACE_FIELDS])
            writer.writeheader()
            def sample(row):
                writer.writerow({'active_part_id': skills.active, **row})
                recorder.remember(skills, row)
                if transport_strategy != 'direct' or transfer_obstacle:
                    metrics = part_metrics[skills.active]
                    metrics['verified_samples'] += 1
                    for name in installed:
                        body = data.body(part_name(registry[name]['config']))
                        previous = metrics['previous_parts'][name]
                        previous['maximum_position_error_m'] = max(previous['maximum_position_error_m'],
                            float(np.linalg.norm(body.xpos - registry[name]['joint']['target_part_center_world_m'])))
                        previous['maximum_orientation_error_rad'] = max(previous['maximum_orientation_error_rad'],
                            rotation_error(body.xmat.reshape(3, 3), registry[name]['joint']['target_part_rotation_world']))
                    if row['stage'] == 'insert_transfer':
                        metrics['transport_samples'] += 1
                        metrics['maximum_transport_slip_m'] = max(metrics['maximum_transport_slip_m'], row.get('relative_slip_m', 0.))
                        z = data.body(skills.verifier.part_body).xmat.reshape(3, 3)[2, 2]
                        metrics['maximum_transport_tilt_rad'] = max(metrics['maximum_transport_tilt_rad'], math.acos(float(np.clip(z, -1., 1.))))
                if viewer:
                    if not viewer.is_running():
                        raise FailureEvent(FailureCode.CANCELLED, 'Viewer closed.')
                    if data.time - clock['last_draw'] >= 1 / 30:
                        viewer.sync()
                        time.sleep(max(0., clock['wall'] + data.time - clock['sim'] - time.monotonic()))
                        clock['last_draw'] = float(data.time)
            def stage(name):
                stream.flush()
                clock.update(wall=time.monotonic(), sim=float(data.time))
                print(f'[{data.time:8.3f} s] {skills.active}: {name}', flush=True)
            def transport_failure(exc):
                recorder.fail(exc, skills, 23)
                report = json.loads((recorder.run_dir / 'failure_report.json').read_text(encoding='utf-8'))
                report['response'] = 'stop_direct_plan; reobserve_before_proposing_alternative'
                report['active_part_id'] = skills.active
                motion_preflight_failures.append(report)
                write_json(recorder.run_dir / f'motion_preflight_{len(motion_preflight_failures):03d}.json', report)
                write_json(recorder.run_dir / 'failure_report.json', report)
            for active, entry in registry.items():
                part_metrics[active] = {'verified_samples': 0, 'transport_samples': 0,
                    'maximum_transport_slip_m': 0., 'maximum_transport_tilt_rad': 0.,
                    'previous_parts': {name: {'maximum_position_error_m': 0., 'maximum_orientation_error_rad': 0.}
                                       for name in installed}}
                skills = FrameSkills(model, data, active, registry, installed, sample, stage,
                    transport_strategy=transport_strategy, on_transport_preflight_failure=transport_failure)
                transport_attempts[active] = skills.transport_attempts
                empty_approach_attempts[active] = skills.empty_approach_attempts
                if observer:
                    observer(skills)  # Test hook; not exposed through CLI/policy actions.
                if mode == 'scripted':
                    skills.settle()
                    skills.pick(active)
                    result = skills.insert(active, entry['config']['target']['id'])
                else:
                    from assembly_graph import run_assembly_graph
                    def failure_handler(exc):
                        recorder.fail(exc, skills, 23)
                        report = json.loads((recorder.run_dir / 'failure_report.json').read_text(encoding='utf-8'))
                        failures.append(report)
                        write_json(recorder.run_dir / f'failure_{len(failures):03d}.json', report)
                        return report
                    def event(row):
                        events.write(json.dumps({'active_part_id': active, **row}, ensure_ascii=False, allow_nan=False) + '\n')
                        events.flush()
                    expected = {'post': 'insert_post/v1', 'sleeve': 'insert_sleeve/v1', 'cover': 'seat_cover/v1'}[entry['kind']]
                    state = run_assembly_graph(skills, failure_handler=failure_handler, mode=mode, policies=policies,
                        scenario='alignment_offset' if scenario == 'first_alignment_offset' and active == 'post_1' else 'nominal',
                        recovery_budget=remaining_recoveries, on_event=event,
                        task_id='four_post_frame_wide/' + active, insert_result_skill=expected)
                    graph_states[active] = state
                    remaining_recoveries -= state['recoveries']
                    if state['status'] != 'success':
                        status, error = state['status'], state['error']
                        break
                    result = skills.results[-1]
                installed.add(active)
                results.append(result)
                if snapshots:
                    save_snapshot(model, data, skills.config, recorder.run_dir / f'after_{active}.png')
            else:
                row = skills.verifier.sample(data, skills.motion.reference, allow_support=True, allow_grasp=False)
                skills.verifier.verify_insertion([row])
                skills.verifier.verify_other_parts(data)
                status = 'success'
    except (Exception, KeyboardInterrupt) as exc:
        status = recorder.fail(exc, skills, 23)
        error = str(exc)
    result = {'status': status, 'error': error, 'run_dir': str(recorder.run_dir),
              'product_parts_present': product_count, 'robot_installations_goal': product_count - 1,
              'completed_installations': sorted(installed), 'robot_installed_parts': len(installed),
              'ten_part_assembly_complete': status == 'success' and product_count == 10,
              'results': results, 'observation': skills.observe() if skills else None,
              'samples': recorder.samples, 'simulated_seconds': float(skills.data.time) if skills else None,
              'graph_states': graph_states, 'failures': failures, 'orchestration': mode,
              'transport_strategy': transport_strategy, 'transfer_obstacle': transfer_obstacle,
              'transport_attempts': transport_attempts, 'empty_approach_attempts': empty_approach_attempts,
              'motion_preflight_failures': motion_preflight_failures,
              'part_metrics': part_metrics if transport_strategy != 'direct' or transfer_obstacle else {},
              'recovery_budget_remaining': remaining_recoveries,
              'policy_calls': sum(s['policy_calls'] for s in graph_states.values()),
              'policy_kind': 'scripted', 'api_calls': 0, 'wall_elapsed_s': time.monotonic() - wall_started}
    write_json(recorder.run_dir / 'result.json', result)
    print(f'{status}: {recorder.run_dir}', flush=True)
    if error:
        print(error)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--parts', type=int, choices=range(2, 11), default=6)
    parser.add_argument('--snapshots', action='store_true')
    parser.add_argument('--transport-strategy', choices=['direct', 'direct_then_rrt'], default='direct')
    parser.add_argument('--transfer-obstacle', action='store_true', help='Fixed transfer test obstacle; base + posts only')
    parser.add_argument('--viewer', action='store_true', help='Open the passive MuJoCo viewer for this physical episode')
    parser.add_argument('--mode', choices=['scripted', 'single', 'roles'], default='scripted')
    parser.add_argument('--scenario', choices=['nominal', 'first_alignment_offset'], default='nominal')
    args = parser.parse_args()
    result = execute_trial(product_count=args.parts, snapshots=args.snapshots, mode=args.mode, scenario=args.scenario, show_viewer=args.viewer, transport_strategy=args.transport_strategy, transfer_obstacle=args.transfer_obstacle)
    raise SystemExit(0 if result['status'] == 'success' else 1)
