"""Physically grasp, align, insert, release and independently verify one post."""
import math
import mujoco
import numpy as np

from assembly_skills import AssemblySkills, HOVER_M, LIFT_M
from failure_report import FailureCode, FailureEvent
from insertion_scene import BASE_NAME
from view_tcp import read_tcp_pose
from workcell_motion import WorkcellMotion
from workcell_verification import WorkcellVerifier


class InsertionVerifier(WorkcellVerifier):
    def __init__(self, model, config, joint):
        super().__init__(model, config)
        self.joint = joint
        self.target_position = np.array(joint['target_part_center_world_m'])
        self.target_rotation = np.array(joint['target_part_rotation_world'])
        self.fixture_geoms = {i for i in range(model.ngeom) if model.geom(i).name.startswith(BASE_NAME + '_')}
        self.floor_geom = model.geom(BASE_NAME + '_floor').id
        self.mating_enabled = False

    def contact_policy(self, pair, *, allow_support, allow_grasp):
        original = super().contact_policy(pair, allow_support=allow_support, allow_grasp=allow_grasp)
        if self.mating_enabled and self.part_geom in pair and pair & self.fixture_geoms:
            return True, self.floor_geom in pair, False, None
        return original

    def sample(self, data, reference, *, allow_support, allow_grasp):
        row = super().sample(data, reference, allow_support=allow_support, allow_grasp=allow_grasp)
        floor_supported, normal_max = False, 0.
        for i, c in enumerate(data.contact):
            pair = {int(c.geom1), int(c.geom2)}
            if self.part_geom in pair and pair & self.fixture_geoms:
                force = np.zeros(6)
                mujoco.mj_contactForce(self.model, data, i, force)
                normal_max = max(normal_max, float(force[0]))
                floor_supported |= self.floor_geom in pair and c.dist <= 0 and force[0] > .01
        if normal_max > 8.:
            raise FailureEvent(FailureCode.UNEXPECTED_CONTACT, 'Post/fixture normal force exceeded 8 N.',
                               violations=[{'fixture_contact_force_n': normal_max, 'maximum_n': 8.}])
        bottom = self.table_z + row['part_bottom_clearance_m']
        row.update(fixture_supported=bool(floor_supported), fixture_contact_force_n=normal_max,
                   insertion_depth_m=self.joint['socket_rim_z_world_m'] - bottom)
        return row

    def verify_insertion(self, rows):
        if not rows:
            raise FailureEvent(FailureCode.INPUT_OR_PRECONDITION_INVALID, 'Need a measured final observation window.')
        checks = {
            'socket_floor_support': all(r['fixture_supported'] for r in rows),
            'released': all(not r['left_contact'] and not r['right_contact'] and r['pad_gap_mm'] > 80 for r in rows),
            'target_position_under_1mm': max(r['target_position_error_m'] for r in rows) < .001,
            'target_orientation_under_1deg': max(r['target_orientation_error_rad'] for r in rows) < math.radians(1),
            'insertion_depth_reached': all(abs(r['insertion_depth_m'] - self.joint['nominal_insertion_depth_m']) < .001 for r in rows),
            'linear_speed_under_1mm_s': max(r['part_speed_m_s'] for r in rows) < .001,
            'angular_speed_under_0_01rad_s': max(r['part_angular_speed_rad_s'] for r in rows) < .01,
            'penetration_under_1mm': max(r['penetration_m'] for r in rows) < .001,
        }
        if not all(checks.values()):
            raise FailureEvent(FailureCode.PLACEMENT_NOT_CONFIRMED, 'Measured insertion criteria failed.',
                               violations=[{'checks': checks}])
        return checks


class InsertionSkills(AssemblySkills):
    def __init__(self, model, data, config, joint, on_sample=None, on_stage=None):
        super().__init__(model, data, config, on_sample, on_stage)
        self.verifier = InsertionVerifier(model, config, joint)
        self.motion = WorkcellMotion(model, data, self.verifier, on_sample)

    def observe(self):
        observation = super().observe()
        observation['target'].update(source='configured_assembly_mating_pose',
                                     desired_part_center_m=self.verifier.target_position.tolist())
        observation['assembly_joint'] = {k: (v.tolist() if isinstance(v, np.ndarray) else v)
                                         for k, v in self.verifier.joint.items()}
        return observation

    def insert(self, part_id, target_id):
        if (part_id != self.config['part']['id'] or target_id != self.config['target']['id']
                or self.motion.held_pose is None):
            raise FailureEvent(FailureCode.INPUT_OR_PRECONDITION_INVALID, 'Insertion needs a currently held known part.')
        held_p, held_r = self.motion.held_pose
        tcp_rotation = self.verifier.target_rotation @ held_r.T
        tcp_position = self.verifier.target_position - tcp_rotation @ held_p
        self.stage('insert_transfer')
        self.motion.move(tcp_position + [0, 0, LIFT_M], tcp_rotation, allow_support=False, allow_grasp=True)
        # Only the selected post may touch this fixed base. Robot/base remains forbidden.
        self.verifier.mating_enabled = True
        self.stage('insert_lower')
        rows = self.motion.move(tcp_position + [0, 0, .001], tcp_rotation, allow_support=True, allow_grasp=True)
        if any(r['insertion_depth_m'] < self.verifier.joint['nominal_insertion_depth_m'] - .002 for r in rows):
            raise FailureEvent(FailureCode.PLACEMENT_NOT_CONFIRMED, 'Post has not reached the pre-release insertion depth.')
        self.stage('insert_release')
        self.motion.held_pose = None
        self.motion.hold(4., allow_support=True, allow_grasp=True, gripper_target=0.)
        self.motion.hold(1., allow_support=True, allow_grasp=True)
        self.stage('insert_retreat')
        p, r = read_tcp_pose(self.data)
        self.motion.move(p + [0, 0, HOVER_M], r, allow_support=True, allow_grasp=False)
        self.stage('insert_verify')
        rows = self.motion.hold(1., allow_support=True, allow_grasp=False)
        checks = self.verifier.verify_insertion(rows)
        result = {'skill': 'insert_post/v1', 'status': 'success', 'part_id': part_id, 'target_id': target_id,
                  'checks': checks, 'minimum_final_depth_m': min(r['insertion_depth_m'] for r in rows),
                  'maximum_final_position_error_m': max(r['target_position_error_m'] for r in rows),
                  'observation': self.observe()}
        self.results.append(result)
        return result
