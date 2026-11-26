"""Read-only paired IK evidence for a nominal source/installation grasp."""
import time
from types import SimpleNamespace
import mujoco
import numpy as np
from joint_path_planner import _tcp_ik_candidates
from .scene import body_name
from .transforms import body_pose


def nominal_transport_hint(screen, local, seconds):
    skills = screen.skills
    started = time.monotonic()
    result = {'source': 'nominal_source_then_goal_ik', 'physical_success': False,
              'collision': 'not_run', 'path': 'not_run', 'actual_grasp_transform': 'unknown',
              'source_status': 'not_run', 'status': 'not_run',
              'scope': 'bounded_kinematic_ranking_not_motion_or_feasibility'}
    if seconds <= 0:
        return result
    iterations = skills.prepared['profile']['resources']['ik_iterations']
    source = body_pose(skills.data, body_name(skills.active, skills.prepared['base_id'])) @ local
    attempts = []
    first = next(_tcp_ik_candidates(skills.motion, source[:3, 3], source[:3, :3],
                                  iterations, min(2., seconds), 0, attempts), None)
    result.update(source_attempts=attempts, source_status='converged' if first is not None else 'not_found')
    if first is None:
        return result
    left = seconds-(time.monotonic()-started)
    if left <= 0:
        return result
    # Only the scratch IK initial guess changes; no pose is assigned to a
    # physics scene, and every proposed grasp still needs the full live checks.
    scratch = mujoco.MjData(skills.model)
    mujoco.mj_copyData(scratch, skills.model, skills.data)
    source_q = first[0]
    scratch.qpos[:6] = source_q[:6]
    mujoco.mj_forward(skills.model, scratch)
    motion = SimpleNamespace(model=skills.model, data=scratch, budget=skills.budget)
    target = screen.goal @ local
    attempts = []
    final = next(_tcp_ik_candidates(motion, target[:3, 3], target[:3, :3],
                                   iterations, min(2., left), 0, attempts), None)
    result.update(attempts=attempts, status='converged' if final is not None else 'not_found',
                  source_arm_rad=source_q[:6].tolist())
    if final is not None:
        result.update(goal_arm_rad=final[0][:6].tolist(),
                      source_goal_max_joint_distance_rad=float(np.max(np.abs(final[0][:6]-source_q[:6]))))
    return result


def refinement_trials():
    """Try symmetric straight approaches before deeper contact or tilting."""
    for offset in (.5, 1., 1.5, 2., 2.5, 3., 3.5, 4.):
        for yaw in (0., 180., 90., -90.):
            yield yaw, 0., offset
    for yaw in (30., -30., 60., -60., 90., -90.):
        for tilt in (15., -15., 30., -30.):
            for offset in (.5, 1., 1.5, 2., 2.5, 3., 3.5, 4.):
                yield yaw, tilt, offset
