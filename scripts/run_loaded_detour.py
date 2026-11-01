"""Physically pick, route a carried part around a static obstacle, then place it."""
import argparse
import csv
import hashlib
import json
import math
import time

import mujoco
import numpy as np

from assembly_skills import AssemblySkills, LIFT_M
from failure_report import FailureCode, FailureEvent, write_json
from loaded_transport import plan_loaded_transfer
from run_pick_place import AssemblyRecorder, TRACE_FIELDS, save_snapshot
from view_tcp import read_tcp_pose
from workcell_scene import ROOT, build_workcell_spec, compile_workcell, load_config

OBSTACLE_RADIUS_M = .010
OBSTACLE_MARGIN_M = .006  # Test clearance, not a guarantee for arbitrary grasp/rotation uncertainty.


def build_probe():
    config = load_config()
    spec = build_workcell_spec(config)
    obstacle = spec.worldbody.add_body(name='loaded_obstacle', mocap=True, pos=[2., 2., 2.])
    obstacle.add_geom(name='loaded_obstacle_geom', type=mujoco.mjtGeom.mjGEOM_SPHERE,
                      size=[OBSTACLE_RADIUS_M, 0., 0.], margin=OBSTACLE_MARGIN_M, rgba=[.9, .15, .12, 1.])
    model, data = compile_workcell(spec)
    return model, data, config


def construct_blocked_transfer(skills):
    """Test setup after a real pick: place ONLY the fixed test obstacle, never a product."""
    model, data, motion = skills.model, skills.data, skills.motion
    if motion.held_pose is None:
        raise ValueError('Physically pick before constructing this transport probe.')
    held_p, held_r = motion.held_pose
    rotation = skills.verifier.target_rotation @ held_r.T
    target = skills.verifier.target_position - rotation @ held_p + [0., 0., LIFT_M]
    clear = motion.plan(target, rotation, allow_support=False, allow_grasp=True)
    scratch = mujoco.MjData(model)
    scratch.qpos[:] = data.qpos
    scratch.qpos[:6] = clear.arm_knots[len(clear.arm_knots) // 2]
    mujoco.mj_kinematics(model, scratch)
    tcp_p, tcp_r = read_tcp_pose(scratch)
    part_p, part_r = tcp_p + tcp_r @ held_p, tcp_r @ held_r
    address = model.jnt_qposadr[model.body(skills.verifier.part_body).jntadr[0]]
    scratch.qpos[address:address+3] = part_p
    mujoco.mju_mat2Quat(scratch.qpos[address+3:address+7], part_r.ravel())
    bottom = part_p[2] - np.abs(part_r[2]) @ skills.verifier.half_size
    obstacle = part_p.copy()
    obstacle[2] = bottom - .006
    # This is a documented test-fixture change. The obstacle stays fixed thereafter.
    data.mocap_pos[0] = obstacle
    mujoco.mj_forward(model, data)
    motion.sample(allow_support=False, allow_grasp=True)
    scratch.mocap_pos[:] = data.mocap_pos
    scratch.mocap_quat[:] = data.mocap_quat
    mujoco.mj_fwdPosition(model, scratch)
    pairs = [[model.geom(int(c.geom1)).name, model.geom(int(c.geom2)).name] for c in scratch.contact]
    obstacle_pairs = [pair for pair in pairs if 'loaded_obstacle_geom' in pair]
    part_geom_name = model.geom(skills.verifier.part_geom).name
    if not obstacle_pairs or any(part_geom_name not in pair for pair in obstacle_pairs):
        raise ValueError('Probe must block only the carried part at the diagnostic midpoint.')
    # Separate diagnostic calculation: remove only the candidate part from the
    # scratch copy. Never execute this incomplete robot-only path.
    scratch.qpos[address:address+3] = [2., 2., 2.]
    mujoco.mj_fwdPosition(model, scratch)
    skills.verifier.contacts(scratch, allow_support=False, allow_grasp=True, planned=True)
    return target, rotation, {'obstacle_center_world_m': obstacle, 'radius_m': OBSTACLE_RADIUS_M,
                              'margin_m': OBSTACLE_MARGIN_M, 'blocked_pairs_at_midpoint': obstacle_pairs,
                              'robot_only_midpoint_clear': True,
                              'construction': 'After physical pick, place a static test obstacle at the calculated transfer midpoint; robot/product state unchanged.'}


def execute_trial(*, snapshots=False, seed=3, strategy='direct_then_rrt', output_root=None, observer=None):
    started = time.monotonic()
    recorder = AssemblyRecorder(output_root or ROOT/'runs/loaded_detour', skill='loaded_transport_probe/v1',
                                validation_profile='held_box_static_obstacle/v1')
    skills, placement, planning, setup = None, None, None, None
    status, error = 'failed', None
    blocked_reports = []
    measurements = {'transport_samples': 0, 'maximum_transport_slip_m': 0.,
                    'maximum_transport_rotation_slip_rad': 0., 'maximum_transport_part_tilt_rad': 0.}
    try:
        model, data, config = build_probe()
        with recorder.trace_path.open('w', newline='', encoding='utf-8') as stream:
            writer = csv.DictWriter(stream, fieldnames=TRACE_FIELDS)
            writer.writeheader()
            def sample(row):
                writer.writerow(row)
                recorder.remember(skills, row)
                if row['stage'] == 'transport_transfer':
                    measurements['transport_samples'] += 1
                    measurements['maximum_transport_slip_m'] = max(measurements['maximum_transport_slip_m'], row.get('relative_slip_m', 0.))
                    measurements['maximum_transport_rotation_slip_rad'] = max(measurements['maximum_transport_rotation_slip_rad'], row.get('relative_rotation_rad', 0.))
                    z = data.body(skills.verifier.part_body).xmat.reshape(3, 3)[2, 2]
                    measurements['maximum_transport_part_tilt_rad'] = max(measurements['maximum_transport_part_tilt_rad'], math.acos(float(np.clip(z, -1, 1))))
            def stage(name):
                stream.flush()
                print(f'[{data.time:8.3f}s] {name}', flush=True)
            skills = AssemblySkills(model, data, config, sample, stage)
            if observer:
                observer(skills)  # Test hook; no live CLI injection.
            skills.settle()
            skills.pick(config['part']['id'])
            target, rotation, setup = construct_blocked_transfer(skills)
            write_json(recorder.run_dir/'resolved_config.json', {
                'environment':'simulation', 'observation_source':'sim_ground_truth', 'config':config,
                'seed':seed, 'strategy':strategy, 'setup':setup,
                'target_tcp_position_m':target, 'target_tcp_rotation':rotation,
                'implementation_sha256':{name:hashlib.sha256((ROOT/'scripts'/name).read_bytes()).hexdigest()
                    for name in ('loaded_transport.py','run_loaded_detour.py','joint_path_planner.py','workcell_motion.py')}})
            if snapshots:
                save_snapshot(model, data, config, recorder.run_dir/'held_before.png')
            skills.stage('transport_transfer')
            def blocked(exc):
                recorder.fail(exc, skills, 12)
                # Preserve the actual detector output, even after later success.
                report=json.loads((recorder.run_dir/'failure_report.json').read_text(encoding='utf-8'))
                report['response'] = 'stop_direct_plan; reobserve_before_proposing_alternative'
                write_json(recorder.run_dir/'failure_report.json', report)
                blocked_reports.append(report)
                write_json(recorder.run_dir/f'preflight_failure_{len(blocked_reports):03d}.json',report)
            plan, planning = plan_loaded_transfer(skills, target, rotation, strategy=strategy, seed=seed,
                                                  on_preflight_failure=blocked)
            write_json(recorder.run_dir/'planned_path.json', {'metadata':planning, 'joint_knots_rad':plan.arm_knots})
            rows = skills.motion.execute(plan, allow_support=False, allow_grasp=True)
            if any(row['supported'] or row['part_bottom_clearance_m'] < .05 for row in rows):
                raise FailureEvent(FailureCode.GRASP_NOT_CONFIRMED, 'Transport did not end with a verified lifted part.')
            if snapshots:
                save_snapshot(model, data, config, recorder.run_dir/'held_after.png')
            placement = skills.place(config['part']['id'], config['target']['id'])
            status = 'success'
    except (Exception, KeyboardInterrupt) as exc:
        status = recorder.fail(exc, skills, 12)
        error = str(exc)
    if snapshots and skills:
        save_snapshot(skills.model, skills.data, skills.config, recorder.run_dir/'final.png')
    result = {'status':status,'error':error,'run_dir':str(recorder.run_dir),
              'task_kind':'loaded_transport_and_place_probe', 'assembly_success':False,
              'robot_installed_parts':0, 'physically_transferred_and_placed_parts':int(status=='success'),
              'policy_kind':'scripted_motion_fallback','api_calls':0, 'strategy':strategy,
              'setup':setup,'planning':planning,'preflight_failures':blocked_reports, 'placement':placement,
              'simulated_seconds':float(skills.data.time) if skills else None,'samples':recorder.samples,
              'measurements':measurements,'wall_elapsed_s':time.monotonic()-started}
    write_json(recorder.run_dir/'result.json',result)
    print(f'{status}: {recorder.run_dir}',flush=True)
    if error: print(error,flush=True)
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshots',action='store_true')
    parser.add_argument('--seed',type=int,default=3)
    parser.add_argument('--strategy',choices=['direct','rrt','direct_then_rrt'],default='direct_then_rrt')
    args=parser.parse_args()
    result=execute_trial(snapshots=args.snapshots,seed=args.seed,strategy=args.strategy)
    raise SystemExit(0 if result['status']=='success' else 1)
