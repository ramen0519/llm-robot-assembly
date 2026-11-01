"""Checked free-space approach for an open, empty gripper before grasp descent."""
import numpy as np
from failure_report import FailureCode, FailureEvent
from joint_path_planner import plan_tcp_path


def _observe_empty_approach(skills):
    motion = skills.motion
    if (motion.stage != 'approach_hover' or motion.held_pose is not None
            or skills.verifier.mating_enabled or getattr(skills.verifier, 'mating_motion', False)):
        raise FailureEvent(FailureCode.INPUT_OR_PRECONDITION_INVALID, 'Need an empty gripper in the free-space hover approach stage.')
    row = motion.sample(allow_support=True, allow_grasp=False)
    if (not row['supported'] or row['pad_gap_mm'] < 80 or row['part_speed_m_s'] >= .001
            or row['part_angular_speed_rad_s'] >= .01):
        raise FailureEvent(FailureCode.INPUT_OR_PRECONDITION_INVALID, 'Re-observe a settled supported part and fully open gripper.')
    return row


def plan_empty_approach(skills, position, rotation, *, seed=3, max_time_s=20., on_preflight_failure=None):
    """Plan only. Does not open fingers, change live poses or replay uncertain motion."""
    before = _observe_empty_approach(skills)
    motion, data = skills.motion, skills.data
    state = {name: getattr(data, name).copy() for name in ('qpos', 'qvel', 'ctrl', 'mocap_pos', 'mocap_quat')}
    moment = float(data.time)
    def unchanged():
        if data.time != moment or any(not np.array_equal(getattr(data, name), value) for name, value in state.items()):
            raise FailureEvent(FailureCode.INPUT_OR_PRECONDITION_INVALID, 'State changed after preflight; observe again.')
    try:
        plan = motion.plan(position, rotation, allow_support=True, allow_grasp=False)
        unchanged()
        return plan, {'planner': 'checked_cartesian_waypoints', 'fallback_used': False,
                      'fresh_observation': before, 'physics_executed_during_planning': False}
    except FailureEvent as exc:
        if exc.code != FailureCode.PLANNED_CONTACT:
            raise
        failure = {'failure_code': exc.code.value, 'phase': exc.phase, 'message': str(exc)}
        if on_preflight_failure:
            on_preflight_failure(exc)
    unchanged()
    fresh = _observe_empty_approach(skills)
    plan, metadata = plan_tcp_path(motion, position, rotation, allow_support=True, allow_grasp=False,
                                   seed=seed, max_time_s=max_time_s, search_margin_rad=.5)
    unchanged()
    return plan, {**metadata, 'fallback_used': True, 'direct_failure': failure,
                  'fresh_observation': fresh, 'scope': 'open_empty_gripper_hover_approach_only'}
