"""LangGraph assembly with an offline baseline or explicitly configured single LLM policy."""
import argparse
import csv
import json
import time
from pathlib import Path
import mujoco

from assembly_graph import run_assembly_graph
from failure_report import write_json
from insertion_scene import build_insertion_scene
from insertion_skills import InsertionSkills
from run_insertion import INSERTION_TRACE_FIELDS
from run_pick_place import AssemblyRecorder, save_snapshot
from workcell_scene import ROOT


def execute_trial(*, mode='roles', scenario='nominal', output_root=None, snapshots=False, use_memory=False, recovery_budget=1, recipe=None, llm_config_path=None):
    if mode not in ('single', 'roles') or scenario not in ('nominal', 'alignment_offset'):
        raise ValueError('Unknown policy mode/scenario.')
    if type(recovery_budget) is not int or recovery_budget not in (0, 1):
        raise ValueError('Recovery budget must be 0 or 1.')
    if llm_config_path is not None and (mode != 'single' or recipe is not None):
        raise ValueError('First live LLM integration supports single policy only, without recipe replay.')
    policy_kind = 'openai_responses' if llm_config_path is not None else ('recipe_replay' if recipe else 'scripted')
    wall_started = time.monotonic()
    recorder = AssemblyRecorder(output_root or ROOT / 'runs/step21', skill='assembly_graph/v1',
                                validation_profile='post_socket_insertion/v1')
    skills, state = None, None
    reports = []
    status, error = 'failed', None
    meter, client, live_policy, llm_config = None, None, None, None
    try:
        if llm_config_path is not None:
            from llm_runtime import load_llm_config, create_openai_client, ApiLedger
            from openai_policy import OpenAIAssemblyPolicy
            llm_config = load_llm_config(llm_config_path).validate(live=True)
            client = create_openai_client(llm_config)
            meter = ApiLedger(llm_config, recorder.run_dir / 'llm_events.jsonl')
            live_policy = OpenAIAssemblyPolicy(llm_config, client, meter)
        model, data, config, joint = build_insertion_scene()
        write_json(recorder.run_dir / 'resolved_config.json', {
            'config': config, 'joint': joint, 'scenario': scenario, 'mode': mode,
            'recovery_budget': recovery_budget, 'memory_enabled': use_memory,
            'policy_kind': policy_kind, 'models': {'single': llm_config.model} if llm_config else None,
            'llm_config': llm_config.public() if llm_config else None, 'api_calls_at_start': 0,
            'environment': 'simulation', 'observation_source': 'sim_ground_truth', 'simulator_version': mujoco.__version__})
        from experience_memory import condition_fingerprint, read_json
        fingerprint = condition_fingerprint(read_json(recorder.run_dir / 'resolved_config.json'))
        policies = {'single': live_policy} if live_policy else None
        if recipe is not None:
            from skill_recipes import RecipeReplayPolicy, validate_candidate
            validate_candidate(recipe)
            if recipe['condition_fingerprint'] != fingerprint:
                raise ValueError('Candidate condition mismatch; no physical execution.')
            keys = ['single'] if mode == 'single' else ['assembly', 'recovery']
            policies = {key: RecipeReplayPolicy(recipe, key) for key in keys}
            write_json(recorder.run_dir / 'recipe_candidate.json', recipe)
        lookup = None
        if use_memory:
            from experience_memory import condition_fingerprint, read_json, retrieve_experiences
            fingerprint = condition_fingerprint(read_json(recorder.run_dir / 'resolved_config.json'))
            def lookup(failure):
                records = retrieve_experiences(fingerprint, failure_code=failure['failure_code'] if failure else None)
                fields = ('record_id', 'outcome', 'failure_codes', 'verified_skill_results', 'successful_recovery_skills')
                return [{key: record[key] for key in fields} for record in records]
        with recorder.trace_path.open('w', newline='', encoding='utf-8') as stream, (recorder.run_dir / 'graph_events.jsonl').open('w', encoding='utf-8') as events:
            writer = csv.DictWriter(stream, fieldnames=INSERTION_TRACE_FIELDS)
            writer.writeheader()
            def sample(row):
                writer.writerow(row)
                recorder.remember(skills, row)
            def stage(name):
                stream.flush()
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
            state = run_assembly_graph(skills, failure_handler=failure_handler, mode=mode, scenario=scenario, on_event=event, memory_lookup=lookup, recovery_budget=recovery_budget, policies=policies, condition_fingerprint=fingerprint)
            status, error = state['status'], state['error']
            if snapshots:
                save_snapshot(model, data, config, recorder.run_dir / 'final.png')
    except (Exception, KeyboardInterrupt) as exc:
        status = recorder.fail(exc, skills, 21)
        error = str(exc)
    finally:
        if client is not None:
            client.close()
    usage = meter.summary() if meter is not None else {'api_calls': 0, 'input_tokens': 0, 'output_tokens': 0, 'api_cost': 0}
    result = {'status': status, 'error': error, 'run_dir': str(recorder.run_dir), 'graph_state': state,
              'policy_kind': policy_kind, 'policy_mode': mode,
              'recipe_candidate_id': recipe['candidate_id'] if recipe else None, 'scenario': scenario, 'memory_enabled': use_memory,
              'api_calls': usage['api_calls'], 'input_tokens': usage['input_tokens'],
              'output_tokens': usage['output_tokens'], 'api_cost': usage['api_cost'],
              'llm_usage': usage if llm_config_path is not None else None,
              'wall_elapsed_s': time.monotonic() - wall_started, 'recovery_budget': recovery_budget,
              'physical_product_parts': 2 if skills else 0, 'product_parts_goal': 2, 'robot_installed_parts': 1 if status == 'success' else 0,
              'simulated_seconds': float(skills.data.time) if skills else None, 'samples': recorder.samples,
              'results': skills.results if skills else [], 'plans': skills.motion.plans if skills else []}
    write_json(recorder.run_dir / 'result.json', result)
    print(f'{status}: {recorder.run_dir}', flush=True)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--llm-config', type=Path, help='Explicit live single-policy config; omitted means scripted/offline')
    parser.add_argument('--mode', choices=['single', 'roles'], default='roles')
    parser.add_argument('--scenario', choices=['nominal', 'alignment_offset'], default='nominal')
    parser.add_argument('--snapshots', action='store_true')
    parser.add_argument('--recovery-budget', type=int, choices=[0, 1], default=1)
    parser.add_argument('--memory', action='store_true', help='Retrieve matching development experiences')
    args = parser.parse_args()
    result = execute_trial(mode=args.mode, scenario=args.scenario, snapshots=args.snapshots, use_memory=args.memory, recovery_budget=args.recovery_budget, llm_config_path=args.llm_config)
    raise SystemExit(0 if result['status'] == 'success' else 1)
