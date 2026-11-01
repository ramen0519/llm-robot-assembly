"""One bounded, measured recovery for pre-insertion alignment failure only."""
import numpy as np

from failure_report import FailureCode, FailureEvent, capture_observation, json_safe
from view_tcp import read_tcp_pose


def recover_alignment(skills, failure):
    """Re-observe, retreat vertically, realign. Does not reset or re-grasp a lost part.

    The caller owns a single-attempt recovery budget. This function never retries.
    A successful return means alignment was recovered, not assembly completion.
    """
    if (not isinstance(failure, FailureEvent) or failure.code != FailureCode.ALIGNMENT_OUT_OF_TOLERANCE
            or skills.motion.stage != 'insert_alignment_check' or skills.motion.held_pose is None):
        raise FailureEvent(FailureCode.INPUT_OR_PRECONDITION_INVALID, 'Only a held-part pre-insertion alignment failure supports this recovery.')
    before = capture_observation(skills.model, skills.data)
    if not before['valid']:
        raise FailureEvent(FailureCode.SIMULATION_INVALID, 'Recovery requires a fresh valid observation.')
    # Read the actual live state again. The old failure report alone is not authority to move.
    row = skills.verifier.sample(skills.data, skills.motion.reference, allow_support=False, allow_grasp=True)
    skills.verifier.check_held(skills.data, row, skills.motion.held_pose)
    body = skills.data.body(skills.verifier.part_body)
    lateral = float(np.linalg.norm(body.xpos[:2] - skills.verifier.target_position[:2]))
    bottom = skills.verifier.table_z + row['part_bottom_clearance_m']
    clearance = float(bottom - skills.verifier.joint['socket_rim_z_world_m'])
    from workcell_verification import rotation_error
    orientation = rotation_error(body.xmat.reshape(3, 3), skills.verifier.target_rotation)
    if (lateral > .020 or clearance < .020 or orientation > np.deg2rad(1)
            or row['supported'] or row['part_speed_m_s'] > .001):
        raise FailureEvent(FailureCode.INPUT_OR_PRECONDITION_INVALID, 'Measured state is outside the bounded alignment-recovery envelope.',
                           violations=[{'lateral_error_m': lateral, 'rim_clearance_m': clearance,
                                        'orientation_error_rad': orientation, 'part_speed_m_s': row['part_speed_m_s']}])
    initial_time = float(skills.data.time)
    skills.stage('recover_reobserve_hold')
    skills.motion.hold(.25, allow_support=False, allow_grasp=True)
    skills.stage('recover_raise')
    p, r = read_tcp_pose(skills.data)
    skills.motion.move(p + [0., 0., .010], r, allow_support=False, allow_grasp=True)
    # Recompute from the current measured held-part transform, never from the faulty target.
    measured_p, measured_r = skills.verifier.relative_part_pose(skills.data)
    target_r = skills.verifier.target_rotation @ measured_r.T
    target_p = skills.verifier.target_position - target_r @ measured_p
    current_p, _ = read_tcp_pose(skills.data)
    target_p[2] = max(target_p[2] + .100, current_p[2])
    skills.stage('recover_realign')
    skills.motion.move(target_p, target_r, allow_support=False, allow_grasp=True)
    skills.verifier.verify_alignment(skills.data)
    after = capture_observation(skills.model, skills.data)
    result = {'skill': 'recover_alignment/v1', 'status': 'success', 'assembly_complete': False,
              'source_failure': failure.code.value, 'before': before, 'after': after,
              'lateral_error_before_m': lateral,
              'lateral_error_after_m': float(np.linalg.norm(skills.data.body(skills.verifier.part_body).xpos[:2] - skills.verifier.target_position[:2])),
              'elapsed_simulation_s': float(skills.data.time) - initial_time,
              'reset_used': False, 'observation_source': 'sim_ground_truth'}
    skills.results.append(json_safe(result))
    return result
