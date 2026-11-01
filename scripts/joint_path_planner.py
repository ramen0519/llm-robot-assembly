"""Existing RRT-Connect search + our MuJoCo geometry check and existing servo executor."""
import math
from pathlib import Path
import random
import sys
import time
import mujoco
import numpy as np
from failure_report import FailureCode, FailureEvent
from view_tcp import read_tcp_pose
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
