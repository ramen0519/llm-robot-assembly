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


def model_fingerprint(model):
    """Include compiled geometry and physics options when identifying a checkpoint."""
    buffer = np.empty(mujoco.mj_sizeModel(model), dtype=np.uint8)
    mujoco.mj_saveModel(model, buffer=buffer)
    return hashlib.sha256(buffer.tobytes()).hexdigest()


def restore_episode_data(model, checkpoint):
    """Restore simulation data only; this does not resume a graph or renew an API budget."""
    spec = mujoco.mjtState.mjSTATE_INTEGRATION
    if (checkpoint['simulator_version'] != mujoco.__version__
            or checkpoint['state_spec'] != int(spec)
            or checkpoint['model_sha256'] != model_fingerprint(model)):
        raise ValueError('Checkpoint simulator, model or state specification differs.')
    state = np.asarray(checkpoint['integration_state'], dtype=np.float64)
    if (state.shape != (mujoco.mj_stateSize(model, spec),) or not np.isfinite(state).all()
            or hashlib.sha256(state.tobytes()).hexdigest() != checkpoint['integration_sha256']):
        raise ValueError('Checkpoint integration state is incomplete or corrupted.')
    restored = mujoco.MjData(model)
    mujoco.mj_setState(model, restored, state, spec)
    mujoco.mj_forward(model, restored)
    actual = np.empty_like(state)
    mujoco.mj_getState(model, restored, actual, spec)
    if not np.array_equal(actual, state):
        raise ValueError('Recomputing observations changed the restored integration state.')
    return restored


def episode_boundary(model, data, registry, installed):
    """Save the complete integrator state, plus human-readable measured part poses."""
    spec = mujoco.mjtState.mjSTATE_INTEGRATION
    state = np.empty(mujoco.mj_stateSize(model, spec))
    mujoco.mj_getState(model, data, state, spec)
    parts = {name: {'position_world_m': data.body(part_name(entry['config'])).xpos.tolist(),
                    'quaternion_wxyz': data.body(part_name(entry['config'])).xquat.tolist()}
             for name, entry in registry.items()}
    return {'time_s': float(data.time), 'installed': sorted(installed), 'parts': parts,
            'integration_sha256': hashlib.sha256(state.tobytes()).hexdigest(),
            'integration_state': state.tolist(), 'state_spec': int(spec),
            'simulator_version': mujoco.__version__, 'model_sha256': model_fingerprint(model)}


def execute_trial(*, product_count=6, snapshots=False, output_root=None, mode='scripted', scenario='nominal', show_viewer=False,
                  transport_strategy='direct', transfer_obstacle=False, observer=None,
                  transport_replan_budget=0, empty_approach_strategy=None, policies=None, planner_candidate=None,
                  post_sleeve_llm_config=None, llm_config=None):
    if mode not in ('scripted', 'single', 'roles') or scenario not in ('nominal', 'first_alignment_offset'):
        raise ValueError('Unknown orchestration mode/scenario')
    if mode == 'scripted' and scenario != 'nominal':
        raise ValueError('The first-alignment scenario requires a graph mode')
    if transport_strategy not in ('direct', 'direct_then_rrt') or type(transfer_obstacle) is not bool:
        raise ValueError('Invalid transport option.')
    if type(product_count) is not int or not 2 <= product_count <= 10:
        raise ValueError('Product count must include base plus one to nine free components.')
    if type(transport_replan_budget) is not int or not 0 <= transport_replan_budget <= 9:
        raise ValueError('The episode permits at most nine transport replans, with at most one per installation.')
    if transport_replan_budget and (mode == 'scripted' or transport_strategy != 'direct'):
        raise ValueError('Policy replanning requires graph mode and direct transport.')
    if empty_approach_strategy not in (None, 'direct', 'direct_then_rrt'):
        raise ValueError('Unknown empty-gripper approach strategy.')
    live_pair = post_sleeve_llm_config is not None
    live = live_pair or llm_config is not None
    if llm_config is not None and (live_pair or mode != 'single' or policies is not None or planner_candidate is not None):
        raise ValueError('The live frame requires single mode and one configuration with the built-in prerequisite order.')
    if live_pair and (mode != 'single' or product_count != 3 or policies is not None or planner_candidate is not None):
        raise ValueError('The live post/sleeve cell requires 3 parts, single policy and its built-in prerequisite order.')
    if policies is not None and (mode == 'scripted' or not isinstance(policies, dict)
            or any(getattr(p, 'kind', None) != 'scripted' for p in policies.values())):
        raise ValueError('Injected policies must be labelled scripted and use graph mode; live API accounting is not enabled here.')
    wall_started = time.monotonic()
    recorder = AssemblyRecorder(output_root or ROOT / 'runs/step23_frame', skill='scripted_frame_assembly/v1' if mode == 'scripted' else 'frame_graph/v1',
                                validation_profile='compound_frame_assembly/v1')
    skills, installed, results = None, set(), []
    status, error = 'failed', None
    graph_states, failures = {}, []
    transport_attempts, empty_approach_attempts, motion_preflight_failures, part_metrics = {}, {}, [], {}
    policy_replan_attempts = {}
    planner_provenance = None
    client, ledger = None, None
    part_episodes, final_verification = {}, None
    previous_end = None
    track_metrics = (live or transport_strategy != 'direct' or transfer_obstacle or transport_replan_budget
                     or empty_approach_strategy == 'direct_then_rrt')
    remaining_replans = transport_replan_budget
    remaining_recoveries = 1 if mode != 'scripted' else 0
    from assembly_policy import ScriptedPolicy
    policies = policies if policies is not None else {key: ScriptedPolicy(key) for key in (['single'] if mode == 'single' else ['assembly', 'recovery'])}
    try:
        if live:
            from llm_runtime import ApiLedger, create_openai_client, load_llm_config
            from openai_policy import OpenAIAssemblyPolicy
            config = load_llm_config(post_sleeve_llm_config if live_pair else llm_config).validate(live=True)
            client = create_openai_client(config)
            ledger = ApiLedger(config, recorder.run_dir / 'llm_events.jsonl')
            policies = {'single': OpenAIAssemblyPolicy(config, client, ledger)}
        model, data, registry = build_frame_scene(product_count, transfer_obstacle=transfer_obstacle,
                                                 first_sleeve_cell=live_pair)
        if planner_candidate is not None:
            from planner_candidate_input import load_robot_candidate
            order, planner_provenance = load_robot_candidate(planner_candidate, registry)
            registry = {name: registry[name] for name in order}
        write_json(recorder.run_dir / 'resolved_config.json', {
            'registry': registry, 'environment': 'simulation', 'observation_source': 'sim_ground_truth',
            'planner_provenance': planner_provenance,
            'product_parts_present': product_count, 'robot_installations_goal': product_count - 1,
            'transport_strategy': transport_strategy, 'transfer_obstacle': transfer_obstacle,
            'empty_approach_strategy': empty_approach_strategy,
            'transport_replan_budget': transport_replan_budget, 'transport_replan_budget_scope': 'episode',
            'policy_kind': 'openai_responses' if live else 'scripted', 'orchestration': mode, 'scenario': scenario,
            'recovery_budget': remaining_recoveries, 'simulator_version': mujoco.__version__,
            **({'llm_config': config.public(), 'api_calls_at_start': 0, 'initial_installed': [],
                'sequence': list(registry)} if live else {'api_calls': 0}),
            'implementation_sha256': {name: hashlib.sha256((ROOT / 'scripts' / name).read_bytes()).hexdigest()
                for name in ('frame_assembly_scene.py', 'frame_assembly_skills.py', 'workcell_motion.py', 'run_frame_assembly.py',
                             'insertion_scene.py', 'insertion_skills.py', 'loaded_transport.py', 'joint_path_planner.py', 'empty_approach.py',
                             'assembly_graph.py', 'assembly_policy.py', 'rejected_insert.py', 'planner_candidate_input.py',
                             'assembly_skills.py', 'post_grasp.py', 'openai_policy.py', 'llm_runtime.py', 'insertion_recovery.py', 'frame_product.py')}})
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
                if track_metrics:
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
                    transport_strategy=transport_strategy, on_transport_preflight_failure=transport_failure,
                    empty_approach_strategy=empty_approach_strategy)
                transport_attempts[active] = skills.transport_attempts
                policy_replan_attempts[active] = skills.replan_attempts
                empty_approach_attempts[active] = skills.empty_approach_attempts
                if live:
                    start = episode_boundary(model, data, registry, installed)
                    if previous_end is not None and start != previous_end:
                        raise FailureEvent(FailureCode.INPUT_OR_PRECONDITION_INVALID, 'Scene changed between component installations.')
                    part_episodes[active] = {'start': start, 'api_calls_at_start': ledger.model_requests}
                    if not installed and snapshots:
                        save_snapshot(model, data, skills.config, recorder.run_dir / 'before_assembly.png')
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
                        report['active_part_id'] = active
                        failures.append(report)
                        write_json(recorder.run_dir / f'failure_{len(failures):03d}.json', report)
                        return report
                    replans_at_part_start = remaining_replans
                    def event(row):
                        nonlocal remaining_replans
                        # Debit on the execute event, including rejected/failed attempts.
                        # A later logging/graph failure cannot make a spent attempt look unused.
                        remaining_replans = replans_at_part_start - row['transport_replans']
                        events.write(json.dumps({'active_part_id': active, **row,
                            'task_transport_replans': transport_replan_budget - remaining_replans,
                            'task_transport_replan_budget_remaining': remaining_replans}, ensure_ascii=False, allow_nan=False) + '\n')
                        events.flush()
                    expected = {'post': 'insert_post/v1', 'sleeve': 'insert_sleeve/v1', 'cover': 'seat_cover/v1'}[entry['kind']]
                    state = run_assembly_graph(skills, failure_handler=failure_handler, mode=mode, policies=policies,
                        scenario='alignment_offset' if scenario == 'first_alignment_offset' and active == 'post_1' else 'nominal',
                        recovery_budget=remaining_recoveries, on_event=event,
                        transport_replan_budget=min(1, replans_at_part_start),
                        task_id='four_post_frame_wide/' + active, insert_result_skill=expected, propose_grasp=live)
                    graph_states[active] = state
                    remaining_recoveries -= state['recoveries']
                    if state['status'] != 'success':
                        status, error = state['status'], state['error']
                        break
                    result = skills.results[-1]
                installed.add(active)
                results.append(result)
                if live:
                    previous_end = episode_boundary(model, data, registry, installed)
                    part_episodes[active].update(end=previous_end, executed_approach=skills.results[0],
                                                 api_calls_at_end=ledger.model_requests)
                    write_json(recorder.run_dir / 'progress.json', {
                        'status': 'running', 'initial_installed': [], 'completed_installations': sorted(installed),
                        'next_parts': [name for name in registry if name not in installed],
                        'part_episodes': part_episodes, 'results': results, 'graph_states': graph_states,
                        'recovery_budget_remaining': remaining_recoveries,
                        'transport_replan_budget_remaining': remaining_replans, 'llm_usage': ledger.summary()})
                if snapshots:
                    save_snapshot(model, data, skills.config, recorder.run_dir / f'after_{active}.png')
            else:
                row = skills.verifier.sample(data, skills.motion.reference, allow_support=True, allow_grasp=False)
                terminal = skills.insertion_evidence([row])
                skills.verifier.verify_other_parts(data)
                if live:
                    before = episode_boundary(model, data, registry, installed)
                    sleeve_fits = {}
                    parts = {}
                    for name in sorted(installed):
                        entry = registry[name]
                        body = data.body(part_name(entry['config']))
                        parts[name] = {
                            'position_error_m': float(np.linalg.norm(body.xpos - entry['joint']['target_part_center_world_m'])),
                            'orientation_error_rad': rotation_error(body.xmat.reshape(3, 3), entry['joint']['target_part_rotation_world'])}
                        if entry['kind'] == 'sleeve':
                            check = FrameSkills(model, data, name, registry, installed)
                            sleeve_fits[name] = check.verify_sleeve_fit()
                    if before != episode_boundary(model, data, registry, installed):
                        raise FailureEvent(FailureCode.INPUT_OR_PRECONDITION_INVALID, 'Final inspection changed the physics state.')
                    final_verification = {'terminal': terminal, 'sleeve_fits': sleeve_fits, 'parts': parts,
                                          'all_installed_parts_preserved': True, 'boundary': before}
                status = 'success'
    except (Exception, KeyboardInterrupt) as exc:
        status = recorder.fail(exc, skills, 23)
        error = str(exc)
    finally:
        if client is not None:
            client.close()
    result = {'status': status, 'error': error, 'run_dir': str(recorder.run_dir),
              'planner_provenance': planner_provenance, 'execution_order': [r['part_id'] for r in results],
              'product_parts_present': product_count, 'robot_installations_goal': product_count - 1,
              'completed_installations': sorted(installed), 'robot_installed_parts': len(installed),
              'ten_part_assembly_complete': status == 'success' and product_count == 10,
              'results': results, 'observation': skills.observe() if skills else None,
              'samples': recorder.samples, 'simulated_seconds': float(skills.data.time) if skills else None,
              'graph_states': graph_states, 'failures': failures, 'orchestration': mode,
              'transport_strategy': transport_strategy, 'transfer_obstacle': transfer_obstacle,
              'empty_approach_strategy': transport_strategy if empty_approach_strategy is None else empty_approach_strategy,
              'transport_replan_budget': transport_replan_budget,
              'transport_replans': transport_replan_budget - remaining_replans,
              'transport_replan_budget_remaining': remaining_replans,
              'policy_replan_attempts': policy_replan_attempts,
              'transport_attempts': transport_attempts, 'empty_approach_attempts': empty_approach_attempts,
              'motion_preflight_failures': motion_preflight_failures,
              'part_metrics': part_metrics if track_metrics else {},
              'recovery_budget_remaining': remaining_recoveries,
              'policy_calls': sum(s['policy_calls'] for s in graph_states.values()),
              'policy_kind': 'openai_responses' if live else 'scripted', 'api_calls': ledger.model_requests if ledger else 0,
              **({'part_episodes': part_episodes, 'final_verification': final_verification,
                  'llm_usage': ledger.summary() if ledger else None} if live else {}),
              'wall_elapsed_s': time.monotonic() - wall_started}
    write_json(recorder.run_dir / 'result.json', result)
    print(f'{status}: {recorder.run_dir}', flush=True)
    if error:
        print(error)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--parts', type=int, choices=range(2, 11), default=6)
    parser.add_argument('--llm-config', type=Path, help='Live single-policy numeric grasps for all requested frame components')
    parser.add_argument('--post-sleeve-llm-config', type=Path, help='Live post then sleeve in one initially empty fixture (--parts 3 --mode single)')
    parser.add_argument('--snapshots', action='store_true')
    parser.add_argument('--transport-strategy', choices=['direct', 'direct_then_rrt'], default='direct')
    parser.add_argument('--transfer-obstacle', action='store_true', help='Fixed transfer test obstacle, present from episode start')
    parser.add_argument('--empty-approach-strategy', choices=['direct', 'direct_then_rrt'], help='Defaults to the transport strategy; only open-gripper hover uses this option')
    parser.add_argument('--transport-replan-budget', type=int, choices=range(10), default=0, help='Global episode budget; graph mode and at most one replan per installation')
    parser.add_argument('--viewer', action='store_true', help='Open the passive MuJoCo viewer for this physical episode')
    parser.add_argument('--mode', choices=['scripted', 'single', 'roles'], default='scripted')
    parser.add_argument('--scenario', choices=['nominal', 'first_alignment_offset'], default='nominal')
    parser.add_argument('--planner-candidate', type=Path, help='Native candidate whose CAD, frames and evidence are rechecked before motion')
    args = parser.parse_args()
    result = execute_trial(product_count=args.parts, snapshots=args.snapshots, mode=args.mode, scenario=args.scenario,
        show_viewer=args.viewer, transport_strategy=args.transport_strategy, transfer_obstacle=args.transfer_obstacle,
        empty_approach_strategy=args.empty_approach_strategy, transport_replan_budget=args.transport_replan_budget,
        planner_candidate=args.planner_candidate, post_sleeve_llm_config=args.post_sleeve_llm_config,
        llm_config=args.llm_config)
    raise SystemExit(0 if result['status'] == 'success' else 1)
