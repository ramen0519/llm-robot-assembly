"""Physical free-space robot motion around an explicit static test obstacle."""
import argparse
import csv
import math
import mujoco
import numpy as np
from failure_report import FailureCode, FailureEvent, write_json
from assembly_skills import AssemblySkills
from joint_path_planner import plan_joint_path
from run_pick_place import AssemblyRecorder, TRACE_FIELDS, save_snapshot
from view_tcp import read_tcp_pose
from workcell_scene import ROOT, load_config, build_workcell_spec, compile_workcell


def build_probe():
    config = load_config()
    spec = build_workcell_spec(config)
    obstacle = spec.worldbody.add_body(name='detour_obstacle', mocap=True, pos=[2, 2, 2])
    obstacle.add_geom(name='detour_obstacle_geom', type=mujoco.mjtGeom.mjGEOM_SPHERE,
                      size=[.012, 0., 0.], margin=.001, rgba=[.9, .15, .12, 1.])
    model, data = compile_workcell(spec)
    return model, data, config


def prepare_obstacle(skills):
    """Test-scene construction only. Does not modify robot or product qpos/qvel."""
    skills.settle()
    motion, model, data = skills.motion, skills.model, skills.data
    p, r = read_tcp_pose(data)
    target = p + [0., .10, 0.]
    empty_scene_plan = motion.plan(target, r, allow_support=True, allow_grasp=False)
    scratch = mujoco.MjData(model)
    scratch.qpos[:] = data.qpos
    scratch.qpos[:6] = empty_scene_plan.arm_knots[len(empty_scene_plan.arm_knots) // 2]
    mujoco.mj_kinematics(model, scratch)
    obstacle_pos = scratch.geom('gripper_left_pad1').xpos.copy()
    data.mocap_pos[0] = obstacle_pos
    mujoco.mj_forward(model, data)
    motion.sample(allow_support=True, allow_grasp=False)
    return empty_scene_plan, target, r, obstacle_pos


def execute_trial(*, seed=3, snapshots=False, output_root=None):
    recorder = AssemblyRecorder(output_root or ROOT / 'runs/motion_detour', skill='free_space_joint_detour/v1',
                                validation_profile='empty_gripper_static_obstacle/v1')
    status, error, skills, metadata, rows = 'failed', None, None, None, []
    direct_rejected = stale_rejected = False
    try:
        model, data, config = build_probe()
        with recorder.trace_path.open('w', newline='', encoding='utf-8') as stream:
            writer = csv.DictWriter(stream, fieldnames=TRACE_FIELDS)
            writer.writeheader()
            def sample(row):
                writer.writerow(row)
                recorder.remember(skills, row)
            skills = AssemblySkills(model, data, config, sample)
            clear_plan, target, rotation, obstacle_pos = prepare_obstacle(skills)
            write_json(recorder.run_dir / 'resolved_config.json', {
                'environment': 'simulation', 'task_kind': 'motion_probe', 'config': config, 'seed': seed,
                'obstacle_center_world_m': obstacle_pos, 'obstacle_radius_m': .012, 'collision_margin_m': .001,
                'goal_arm_rad': clear_plan.arm_knots[-1], 'target_tcp_position_m': target,
                'scene_construction': 'Place a static test obstacle after finding goal IK in a parked-obstacle calculation; no product or robot state reset.'})
            # Obstacle changes at the same simulated time invalidate the earlier plan.
            before = float(data.time)
            try:
                skills.motion.execute(clear_plan, allow_support=True, allow_grasp=False)
            except FailureEvent as exc:
                stale_rejected = exc.code == FailureCode.INPUT_OR_PRECONDITION_INVALID and data.time == before
            if not stale_rejected:
                raise ValueError('Moved obstacle did not invalidate the old plan before execution.')
            skills.stage('direct_preflight')
            try:
                skills.motion.plan(target, rotation, allow_support=True, allow_grasp=False)
            except FailureEvent as exc:
                if exc.code != FailureCode.PLANNED_CONTACT:
                    raise
                direct_rejected = True
                recorder.fail(exc, skills, 12)
            if not direct_rejected:
                raise ValueError('Probe failed to block the direct path.')
            if snapshots:
                save_snapshot(model, data, config, recorder.run_dir / 'before.png')
            skills.stage('joint_detour')
            initial_q, initial_v, initial_time = data.qpos.copy(), data.qvel.copy(), float(data.time)
            plan, metadata = plan_joint_path(skills.motion, clear_plan.arm_knots[-1], seed=seed,
                                             max_time_s=20., search_margin_rad=.5)
            if not np.array_equal(initial_q, data.qpos) or not np.array_equal(initial_v, data.qvel) or data.time != initial_time:
                raise ValueError('Planner changed live robot/product physics state.')
            write_json(recorder.run_dir / 'planned_joint_path.json', {'metadata': metadata, 'arm_knots_rad': plan.arm_knots})
            rows = skills.motion.execute(plan, allow_support=True, allow_grasp=False)
            status = 'success'
    except (Exception, KeyboardInterrupt) as exc:
        status = recorder.fail(exc, skills, 12)
        error = str(exc)
    if snapshots and skills:
        save_snapshot(skills.model, skills.data, skills.config, recorder.run_dir / 'final.png')
    result = {'status': status, 'error': error, 'run_dir': str(recorder.run_dir), 'task_kind': 'motion_probe',
              'robot_installed_parts': 0, 'assembly_success': False, 'api_calls': 0,
              'direct_cartesian_path_rejected': direct_rejected, 'stale_obstacle_plan_rejected': stale_rejected,
              'planner': metadata, 'simulated_seconds': float(skills.data.time) if skills else None,
              'samples': recorder.samples, 'maximum_final_tcp_error_m': max((r['tcp_position_error_m'] for r in rows), default=None),
              'maximum_final_tcp_angle_rad': max((r['tcp_orientation_error_rad'] for r in rows), default=None)}
    write_json(recorder.run_dir / 'result.json', result)
    print(status + ': ' + str(recorder.run_dir), flush=True)
    if error:
        print(error, flush=True)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seed', type=int, default=3)
    parser.add_argument('--snapshots', action='store_true')
    args = parser.parse_args()
    result = execute_trial(seed=args.seed, snapshots=args.snapshots)
    raise SystemExit(0 if result['status'] == 'success' else 1)
