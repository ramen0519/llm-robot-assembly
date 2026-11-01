"""Physical frame assembly with compound sleeves, prerequisite checks and full-state verification."""
import argparse
import csv
import hashlib
import json
import time
import mujoco
from pathlib import Path
from failure_report import write_json
from frame_assembly_scene import build_frame_scene
from frame_assembly_skills import FrameSkills
from run_insertion import INSERTION_TRACE_FIELDS
from run_pick_place import AssemblyRecorder, save_snapshot
from workcell_scene import ROOT


def execute_trial(*, product_count=6, snapshots=False, output_root=None, mode='scripted', scenario='nominal'):
    if mode not in ('scripted', 'single', 'roles') or scenario not in ('nominal', 'first_alignment_offset'):
        raise ValueError('Unknown orchestration mode/scenario')
    if mode == 'scripted' and scenario != 'nominal':
        raise ValueError('The first-alignment scenario requires a graph mode')
    wall_started = time.monotonic()
    recorder = AssemblyRecorder(output_root or ROOT / 'runs/step23_frame', skill='scripted_frame_assembly/v1' if mode == 'scripted' else 'frame_graph/v1',
                                validation_profile='compound_frame_assembly/v1')
    skills, installed, results = None, set(), []
    status, error = 'failed', None
    graph_states, failures = {}, []
    remaining_recoveries = 1 if mode != 'scripted' else 0
    from assembly_policy import ScriptedPolicy
    policies = {key: ScriptedPolicy(key) for key in (['single'] if mode == 'single' else ['assembly', 'recovery'])}
    try:
        model, data, registry = build_frame_scene(product_count)
        write_json(recorder.run_dir / 'resolved_config.json', {
            'registry': registry, 'environment': 'simulation', 'observation_source': 'sim_ground_truth',
            'product_parts_present': product_count, 'robot_installations_goal': product_count - 1,
            'policy_kind': 'scripted', 'orchestration': mode, 'scenario': scenario, 'recovery_budget': remaining_recoveries, 'api_calls': 0, 'simulator_version': mujoco.__version__,
            'implementation_sha256': {name: hashlib.sha256((ROOT / 'scripts' / name).read_bytes()).hexdigest()
                for name in ('frame_assembly_scene.py', 'frame_assembly_skills.py', 'workcell_motion.py', 'run_frame_assembly.py')}})
        with recorder.trace_path.open('w', newline='', encoding='utf-8') as stream, (recorder.run_dir / 'graph_events.jsonl').open('w', encoding='utf-8') as events:
            writer = csv.DictWriter(stream, fieldnames=['active_part_id', *INSERTION_TRACE_FIELDS])
            writer.writeheader()
            def sample(row):
                writer.writerow({'active_part_id': skills.active, **row})
                recorder.remember(skills, row)
            def stage(name):
                stream.flush()
                print(f'[{data.time:8.3f} s] {skills.active}: {name}', flush=True)
            for active, entry in registry.items():
                skills = FrameSkills(model, data, active, registry, installed, sample, stage)
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
    parser.add_argument('--mode', choices=['scripted', 'single', 'roles'], default='scripted')
    parser.add_argument('--scenario', choices=['nominal', 'first_alignment_offset'], default='nominal')
    args = parser.parse_args()
    result = execute_trial(product_count=args.parts, snapshots=args.snapshots, mode=args.mode, scenario=args.scenario)
    raise SystemExit(0 if result['status'] == 'success' else 1)
