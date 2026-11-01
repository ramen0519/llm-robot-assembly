"""Offline scripted policy baseline in LangGraph; no paid model calls."""
import argparse
import csv
import json
from pathlib import Path
import mujoco

from assembly_graph import run_assembly_graph
from failure_report import write_json
from insertion_scene import build_insertion_scene
from insertion_skills import InsertionSkills
from run_insertion import INSERTION_TRACE_FIELDS
from run_pick_place import AssemblyRecorder, save_snapshot
from workcell_scene import ROOT


def execute_trial(*, mode='roles', scenario='nominal', output_root=None, snapshots=False):
    recorder = AssemblyRecorder(output_root or ROOT / 'runs/step21', skill='assembly_graph/v1',
                                validation_profile='post_socket_insertion/v1')
    skills, state = None, None
    reports = []
    status, error = 'failed', None
    try:
        model, data, config, joint = build_insertion_scene()
        write_json(recorder.run_dir / 'resolved_config.json', {
            'config': config, 'joint': joint, 'scenario': scenario, 'mode': mode,
            'policy_kind': 'scripted', 'models': None, 'api_calls': 0,
            'environment': 'simulation', 'observation_source': 'sim_ground_truth', 'simulator_version': mujoco.__version__})
        with recorder.trace_path.open('w', newline='', encoding='utf-8') as stream, (recorder.run_dir / 'graph_events.jsonl').open('w', encoding='utf-8') as events:
            writer = csv.DictWriter(stream, fieldnames=INSERTION_TRACE_FIELDS)
            writer.writeheader()
            def sample(row):
                writer.writerow(row)
                recorder.remember(skills, row)
            def stage(name):
                print(f'[{data.time:7.3f} s] {name}', flush=True)
            def failure_handler(exc):
                recorder.fail(exc, skills, 21)
                report = json.loads((recorder.run_dir / 'failure_report.json').read_text(encoding='utf-8'))
                reports.append(report)
                write_json(recorder.run_dir / f'failure_{len(reports):03d}.json', report)
                return report
            def event(row):
                events.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + '\n')
                events.flush()
            skills = InsertionSkills(model, data, config, joint, sample, stage)
            state = run_assembly_graph(skills, failure_handler=failure_handler, mode=mode, scenario=scenario, on_event=event)
            status, error = state['status'], state['error']
            if snapshots:
                save_snapshot(model, data, config, recorder.run_dir / 'final.png')
    except (Exception, KeyboardInterrupt) as exc:
        status = recorder.fail(exc, skills, 21)
        error = str(exc)
    result = {'status': status, 'error': error, 'run_dir': str(recorder.run_dir), 'graph_state': state,
              'policy_kind': 'scripted', 'policy_mode': mode, 'scenario': scenario,
              'api_calls': 0, 'input_tokens': 0, 'output_tokens': 0, 'api_cost': 0,
              'physical_product_parts': 2, 'robot_installed_parts': 1 if status == 'success' else 0,
              'simulated_seconds': float(skills.data.time) if skills else None, 'samples': recorder.samples,
              'results': skills.results if skills else [], 'plans': skills.motion.plans if skills else []}
    write_json(recorder.run_dir / 'result.json', result)
    print(f'{status}: {recorder.run_dir}', flush=True)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=['single', 'roles'], default='roles')
    parser.add_argument('--scenario', choices=['nominal', 'alignment_offset'], default='nominal')
    parser.add_argument('--snapshots', action='store_true')
    args = parser.parse_args()
    result = execute_trial(mode=args.mode, scenario=args.scenario, snapshots=args.snapshots)
    raise SystemExit(0 if result['status'] == 'success' else 1)
