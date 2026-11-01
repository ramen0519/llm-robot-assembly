"""Explicit free-space transport planning for an already observed physical grasp."""
import numpy as np
from failure_report import FailureCode, FailureEvent
from joint_path_planner import plan_tcp_path


def _observe_free_grasp(skills):
    motion = skills.motion
    if motion.stage not in ('transport_transfer', 'place_transfer', 'insert_transfer'):
        raise FailureEvent(FailureCode.INPUT_OR_PRECONDITION_INVALID, 'Detours are restricted to free-space transfer stages.')
    if (motion.held_pose is None or getattr(skills.verifier, 'mating_enabled', False)
            or getattr(skills.verifier, 'mating_motion', False)):
        raise FailureEvent(FailureCode.INPUT_OR_PRECONDITION_INVALID, 'Need a held part outside contact-insertion phases.')
    row = motion.sample(allow_support=False, allow_grasp=True)
    if row['supported'] or row['part_bottom_clearance_m'] < .05:
        raise FailureEvent(FailureCode.INPUT_OR_PRECONDITION_INVALID, 'Part must be lifted at least 50 mm above the table.')
    return row


def plan_loaded_transfer(skills, position, rotation, *, strategy='direct_then_rrt', seed=3,
                         max_time_s=20., on_preflight_failure=None):
    """Return a fully checked plan. Never execute, reset or retry a motor command."""
    if strategy not in ('direct', 'rrt', 'direct_then_rrt'):
        raise ValueError('Unknown transport strategy.')
    before = _observe_free_grasp(skills)
    motion, data = skills.motion, skills.data
    start = {name: getattr(data, name).copy() for name in ('qpos', 'qvel', 'ctrl', 'mocap_pos', 'mocap_quat')}
    start_time = float(data.time)
    failure = None
    if strategy != 'rrt':
        try:
            plan = motion.plan(position, rotation, allow_support=False, allow_grasp=True)
            return plan, {'strategy_requested': strategy, 'planner': 'checked_cartesian_waypoints',
                          'fallback_used': False, 'direct_failure': None, 'fresh_grasp': before,
                          'physics_executed_during_planning': False}
        except FailureEvent as exc:
            if exc.code != FailureCode.PLANNED_CONTACT or strategy == 'direct':
                raise
            failure = {'failure_code': exc.code.value, 'phase': exc.phase, 'message': str(exc)}
            if on_preflight_failure:
                on_preflight_failure(exc)
    # A planned collision is not an executed collision. Re-observe and require the
    # exact same live state before proposing an alternate path.
    if data.time != start_time or any(not np.array_equal(getattr(data, name), value) for name, value in start.items()):
        raise FailureEvent(FailureCode.INPUT_OR_PRECONDITION_INVALID, 'State changed after preflight; start from a new observation.')
    fresh = _observe_free_grasp(skills)
    plan, metadata = plan_tcp_path(motion, position, rotation, allow_support=False, allow_grasp=True,
                                   seed=seed, max_time_s=max_time_s, search_margin_rad=.5)
    if data.time != start_time or any(not np.array_equal(getattr(data, name), value) for name, value in start.items()):
        raise FailureEvent(FailureCode.MODEL_OR_CONTROL_INVALID, 'Transport planning changed live physical state.')
    return plan, {**metadata, 'strategy_requested': strategy, 'fallback_used': failure is not None,
                  'direct_failure': failure, 'fresh_grasp': fresh,
                  'scope': 'free_space_held_part_transport; no Cartesian orientation constraint along RRT path'}
