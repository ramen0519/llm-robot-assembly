"""A local, single-use record of a transport plan rejected before execution."""
from copy import deepcopy
import numpy as np

from failure_report import FailureCode

DATA_FIELDS = ('qpos', 'qvel', 'ctrl', 'act', 'mocap_pos', 'mocap_quat', 'eq_active',
               'qfrc_applied', 'xfrc_applied', 'xpos', 'xquat', 'geom_xpos', 'geom_xmat')
MODEL_FIELDS = ('body_pos', 'body_quat', 'body_mass', 'body_inertia', 'body_gravcomp',
                'geom_pos', 'geom_quat', 'geom_size', 'geom_contype', 'geom_conaffinity',
                'geom_friction', 'geom_margin', 'geom_gap', 'eq_data', 'jnt_range',
                'actuator_ctrlrange', 'actuator_forcerange', 'actuator_gainprm', 'actuator_biasprm')


def capture_transport_state(skills):
    return {'time': float(skills.data.time), 'stage': skills.motion.stage,
            'data': {key: getattr(skills.data, key).copy() for key in DATA_FIELDS},
            'model': {key: getattr(skills.model, key).copy() for key in MODEL_FIELDS},
            'gravity': skills.model.opt.gravity.copy(), 'timestep': float(skills.model.opt.timestep),
            'held': deepcopy(skills.motion.held_pose), 'reference': skills.motion.reference.copy(),
            'gripper': skills.motion.gripper_command,
            'ids': (skills.config['part']['id'], skills.config['target']['id']),
            'target_position': skills.verifier.target_position.copy(),
            'target_rotation': skills.verifier.target_rotation.copy(),
            'mating': (skills.verifier.mating_enabled, getattr(skills.verifier, 'mating_motion', False))}


def same_transport_state(skills, before):
    if (skills.data.time != before['time'] or skills.motion.stage != before['stage']
            or skills.motion.gripper_command != before['gripper']
            or (skills.verifier.mating_enabled, getattr(skills.verifier, 'mating_motion', False)) != before['mating']
            or skills.motion.held_pose is None or before['held'] is None
            or len(skills.motion.held_pose) != len(before['held'])
            or (skills.config['part']['id'], skills.config['target']['id']) != before['ids']
            or skills.model.opt.timestep != before['timestep']):
        return False
    pairs = [(getattr(skills.data, k), v) for k, v in before['data'].items()]
    pairs += [(getattr(skills.model, k), v) for k, v in before['model'].items()]
    pairs += [(skills.motion.reference, before['reference']), (skills.model.opt.gravity, before['gravity'])]
    pairs += [(skills.verifier.target_position, before['target_position']),
              (skills.verifier.target_rotation, before['target_rotation'])]
    pairs += list(zip(skills.motion.held_pose, before['held']))
    return all(np.array_equal(actual, expected) for actual, expected in pairs)


class RejectedInsert:
    def __init__(self, error, before, intent, position, rotation):
        self.error, self.before, self.intent = error, before, deepcopy(intent)
        self.position, self.rotation = np.array(position).copy(), np.array(rotation).copy()

    def available(self, skills, error=None):
        return (self.error.code == FailureCode.PLANNED_CONTACT
                and self.error.phase == 'insert_transfer/preflight'
                and (error is None or error is self.error)
                and self.before['stage'] == 'insert_transfer' and self.before['mating'] == (False, False)
                and same_transport_state(skills, self.before))

    def summary(self):
        return {'source_failure_code': self.error.code.value, 'source_phase': self.error.phase,
                'source_time_s': self.before['time'], 'rejected_reason': str(self.error),
                'transport_command_executed': False, 'same_state_confirmed': True,
                'requested_tcp_position_m': self.position.tolist(),
                'requested_tcp_rotation_matrix': self.rotation.tolist(),
                'alignment_offset_m': self.intent['alignment_offset_m'],
                'alternative': 'checked_direct_then_joint_path', 'runtime_code_generation': False}
