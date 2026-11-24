"""Existing RRT-Connect search + our MuJoCo geometry check and existing servo executor."""
import math
from pathlib import Path
import random
import sys
import time
import mujoco
import mink
from mink.exceptions import NoSolutionFound
import numpy as np
from failure_report import FailureCode, FailureEvent
from view_tcp import read_tcp_pose, TCP_SITE_NAME
from solve_tcp_ik import pose_errors
from view_ur3e import JOINT_NAMES
from workcell_motion import CartesianPlan

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'third_party'))
from motion_planners.rrt_connect import rrt_connect

UPSTREAM_COMMIT = 'dba9aac6c5204b303c8d36b6a8291d1bbb6ba678'
RESOLUTION_RAD = math.radians(.25)


class _BudgetExpired(RuntimeError):
    pass


def plan_joint_path(motion, goal_arm, *, allow_support=True, allow_grasp=False, seed=0,
                    max_time_s=15., max_iterations=1000, search_margin_rad=None):
    if (type(seed) is not int or type(max_iterations) is not int or not 1 <= max_iterations <= 10000
            or not 0 < max_time_s <= 60 or not math.isfinite(max_time_s)):
        raise ValueError('Invalid planning budget/seed.')
    model, data = motion.model, motion.data
    start_q, start_v, start_ctrl = data.qpos.copy(), data.qvel.copy(), data.ctrl.copy()
    mocap_p, mocap_q, sim_time = data.mocap_pos.copy(), data.mocap_quat.copy(), float(data.time)
    start, goal = start_q[:6].copy(), np.asarray(goal_arm, dtype=float)
    bounds = model.jnt_range[:6].copy()
    lower, upper = bounds[:, 0] + 1e-6, bounds[:, 1] - 1e-6
    if goal.shape != (6,) or not np.isfinite(goal).all() or np.any(goal < lower) or np.any(goal > upper):
        raise FailureEvent(FailureCode.INPUT_OR_PRECONDITION_INVALID, 'Expected six finite goal angles within joint limits.')
    if search_margin_rad is not None:
        if not math.isfinite(search_margin_rad) or not 0 < search_margin_rad <= math.pi:
            raise ValueError('Invalid bounded search margin.')
        lower = np.maximum(lower, np.minimum(start, goal) - search_margin_rad)
        upper = np.minimum(upper, np.maximum(start, goal) + search_margin_rad)
    started, checks, rejected = time.monotonic(), 0, 0
    rng = np.random.default_rng(seed)
    def budget():
        if getattr(motion, 'budget', None): motion.budget.check('rrt_search')
        if time.monotonic() - started > max_time_s:
            raise _BudgetExpired()
    def extend(a, b):
        a, b = np.asarray(a), np.asarray(b)
        count = max(1, math.ceil(float(np.max(np.abs(b - a))) / RESOLUTION_RAD))
        for i in range(1, count + 1):
            budget()
            yield a + (i / count) * (b - a)
    def collision(arm):
        nonlocal checks, rejected
        budget()
        checks += 1
        q = start_q.copy()
        q[:6] = arm
        if not np.isfinite(q[:6]).all() or np.any(q[:6] < lower) or np.any(q[:6] > upper):
            rejected += 1
            return True
        try:
            motion._candidate(q, allow_support, allow_grasp)
        except FailureEvent as exc:
            if exc.code != FailureCode.PLANNED_CONTACT:
                raise
            rejected += 1
            return True
        return False
    random_state = random.getstate()
    search_needed = False
    try:
        random.seed(seed)  # Existing library uses Python random for tree selection.
        if collision(start) or collision(goal):
            raise FailureEvent(FailureCode.PLANNED_CONTACT, 'Joint-space start or goal is in collision.')
        direct = [start, *extend(start, goal)]
        if any(collision(q) for q in direct):
            search_needed = True
            path = rrt_connect(start, goal, lambda a, b: float(np.linalg.norm(np.asarray(a) - b)),
                               lambda: rng.uniform(lower, upper), extend, collision,
                               max_iterations=max_iterations, max_time=max_time_s - (time.monotonic() - started))
        else:
            path = direct
        if path is None:
            raise _BudgetExpired()
        # Recheck every returned edge at the execution sampling resolution.
        dense = [np.asarray(path[0])]
        for a, b in zip(path[:-1], path[1:]):
            for q in extend(a, b):
                if collision(q):
                    raise FailureEvent(FailureCode.PLANNED_CONTACT, 'Returned RRT edge failed final preflight.')
                if not np.allclose(dense[-1], q, atol=1e-12, rtol=0):
                    dense.append(q)
        if not np.allclose(dense[0], start, atol=1e-9) or not np.allclose(dense[-1], goal, atol=1e-9):
            raise FailureEvent(FailureCode.MODEL_OR_CONTROL_INVALID, 'Planner did not connect requested endpoints.')
    except _BudgetExpired as exc:
        raise FailureEvent(FailureCode.MOTION_PATH_NOT_FOUND,
                           'No fully checked motion path found within budget; feasibility remains unknown.',
                           violations=[{'collision_queries': checks, 'rejected_queries': rejected,
                                        'elapsed_s': time.monotonic() - started, 'max_time_s': max_time_s}]) from exc
    finally:
        random.setstate(random_state)
    scratch = mujoco.MjData(model)
    scratch.qpos[:] = start_q
    scratch.qpos[:6] = goal
    mujoco.mj_kinematics(model, scratch)
    position, rotation = read_tcp_pose(scratch)
    knots = np.asarray(dense if len(dense) > 1 else [start, goal])
    derivative = (len(knots) - 1) * float(np.max(np.abs(np.diff(knots, axis=0))))
    duration = max(3., 1.5 * derivative / math.radians(5))
    motion.goal = {'kind': 'tcp_pose', 'frame': 'world', 'position_m': position.copy(), 'rotation_matrix': rotation.copy()}
    plan = CartesianPlan(start_q, sim_time, knots, position.copy(), rotation.copy(), duration, checks,
                         start_v, start_ctrl, mocap_p, mocap_q)
    metadata = {'planner': 'RRT-Connect' if search_needed else 'checked_joint_interpolation',
                'upstream_commit': UPSTREAM_COMMIT, 'seed': seed, 'search_bounds_rad': np.column_stack((lower, upper)),
                'collision_queries': checks, 'rejected_queries': rejected, 'waypoints': len(knots),
                'resolution_rad': RESOLUTION_RAD, 'wall_planning_s': time.monotonic() - started,
                'duration_s': duration, 'physics_executed_during_planning': False}
    return plan, metadata


def plan_tcp_path(motion, target_position, target_rotation, *, max_ik_iterations=400, **planner_options):
    """Mink endpoint IK, then joint-space search; IK iterations are not robot motion."""
    p, r = np.asarray(target_position, dtype=float), np.asarray(target_rotation, dtype=float)
    if (p.shape != (3,) or r.shape != (3, 3) or not np.isfinite(p).all() or not np.isfinite(r).all()
            or not np.allclose(r.T @ r, np.eye(3), atol=1e-8) or abs(np.linalg.det(r) - 1) > 1e-8
            or type(max_ik_iterations) is not int or not 1 <= max_ik_iterations <= 1000):
        raise FailureEvent(FailureCode.INPUT_OR_PRECONDITION_INVALID, 'Invalid TCP goal/IK budget.')
    model, data = motion.model, motion.data
    task = mink.FrameTask(TCP_SITE_NAME, 'site', position_cost=1., orientation_cost=1., lm_damping=1e-3)
    task.set_target(mink.SE3.from_rotation_and_translation(mink.SO3.from_matrix(r), p))
    freeze = mink.DofFreezingTask(model, dof_indices=list(range(6, model.nv)))
    limits = mink.ConfigurationLimit(model, min_distance_from_limits=-.001)
    limits.lower[:6] = model.jnt_range[:6, 0] + 1e-6
    limits.upper[:6] = model.jnt_range[:6, 1] - 1e-6
    velocity_limit = mink.VelocityLimit(model, {name: 1. for name in JOINT_NAMES})
    # Mink remains the IK solver. A single starting posture can be trapped near
    # a singularity for side/oblique tool orientations; test bounded alternatives
    # in scratch configurations, never by resetting the live robot.
    started=time.monotonic();time_limit=planner_options.get('max_time_s',15.)
    seed=planner_options.get('seed',0);rng=np.random.default_rng(seed);attempts=[];last_error=None
    starts=[data.qpos.copy()]
    for _ in range(3):
        q=data.qpos.copy();q[:6]+=rng.uniform(-1.5,1.5,6)
        q[:6]=np.clip(q[:6],model.jnt_range[:6,0]+1e-5,model.jnt_range[:6,1]-1e-5);starts.append(q)
    for seed_index,q in enumerate(starts):
        configuration=mink.Configuration(model,q=q);converged=False;position_error=angle_error=None
        for iteration in range(max_ik_iterations+1):
            if getattr(motion,'budget',None):motion.budget.check('endpoint_ik')
            if time.monotonic()-started>=time_limit:break
            position_error,angle_error=pose_errors(configuration.data,p,r)
            if position_error<=.0001 and angle_error<=math.radians(.05):converged=True;break
            if iteration==max_ik_iterations:break
            try:
                velocity=mink.solve_ik(configuration,[task],.02,'daqp',limits=[limits,velocity_limit],constraints=[freeze])
            except NoSolutionFound:break
            if not np.isfinite(velocity).all():break
            configuration.integrate_inplace(velocity,.02)
        attempts.append({'seed_index':seed_index,'iterations':iteration,'converged':converged,
                         'position_error_m':position_error,'orientation_error_rad':angle_error})
        if not converged:continue
        if not np.allclose(configuration.q[6:],data.qpos[6:],atol=1e-8,rtol=0):
            raise FailureEvent(FailureCode.MODEL_OR_CONTROL_INVALID,'Endpoint IK modified frozen non-arm coordinates.')
        remaining=time_limit-(time.monotonic()-started)
        if remaining<=0:break
        try:
            options={**planner_options,'max_time_s':remaining}
            plan,metadata=plan_joint_path(motion,configuration.q[:6],**options)
            break
        except FailureEvent as exc:
            if exc.code not in (FailureCode.PLANNED_CONTACT,FailureCode.MOTION_PATH_NOT_FOUND):raise
            last_error=exc;attempts[-1]['path_rejected']=exc.code.value
    else:plan=None
    if 'plan' not in locals() or plan is None:
        code=last_error.code if last_error else FailureCode.IK_NOT_CONVERGED
        raise FailureEvent(code,'No checked endpoint/path in the bounded multistart search; no physical command executed.',
                           violations=[{'attempts':attempts,'time_limit_s':time_limit}])
    # The servo verifier measures against the requested Cartesian goal, not just
    # against the slightly approximate FK position of the IK solution.
    plan.target_position, plan.target_rotation = p.copy(), r.copy()
    motion.goal = {'kind': 'tcp_pose', 'frame': 'world', 'position_m': p.copy(), 'rotation_matrix': r.copy()}
    metadata.update(ik_iterations=iteration, ik_position_error_m=position_error, ik_orientation_error_rad=angle_error)
    metadata.update(ik_multistart_attempts=attempts,endpoint_and_path_seconds=time.monotonic()-started)
    return plan, metadata
