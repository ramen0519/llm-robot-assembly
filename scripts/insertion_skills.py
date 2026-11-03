"""Physically grasp, align, insert, release and independently verify one post."""
import math
from copy import copy, deepcopy
import mujoco
import numpy as np

from assembly_skills import AssemblySkills, HOVER_M, LIFT_M
from failure_report import FailureCode, FailureEvent, json_safe
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
            raise FailureEvent(FailureCode.INSERTION_CONTACT_LIMIT, 'Post/fixture normal force exceeded 8 N.',
                               violations=[{'fixture_contact_force_n': normal_max, 'maximum_n': 8.}])
        bottom = self.table_z + row['part_bottom_clearance_m']
        row.update(fixture_supported=bool(floor_supported), fixture_contact_force_n=normal_max,
                   insertion_depth_m=self.joint['socket_rim_z_world_m'] - bottom)
        return row

    def verify_alignment(self, data):
        body = data.body(self.part_body)
        from workcell_verification import rotation_error
        error_xy = float(np.linalg.norm(body.xpos[:2] - self.target_position[:2]))
        error_angle = rotation_error(body.xmat.reshape(3, 3), self.target_rotation)
        bottom = body.xpos[2] - np.abs(body.xmat.reshape(3, 3)[2]) @ self.half_size
        rim_clearance = float(bottom - self.joint['socket_rim_z_world_m'])
        checks = {'lateral_under_1mm': error_xy < .001,
                  'orientation_under_1deg': error_angle < math.radians(1),
                  'above_rim_at_least_5mm': rim_clearance >= .005}
        if not all(checks.values()):
            raise FailureEvent(FailureCode.ALIGNMENT_OUT_OF_TOLERANCE, 'Measured post/socket alignment is outside the insertion envelope.',
                               violations=[{'checks': checks, 'lateral_error_m': error_xy,
                                            'orientation_error_rad': error_angle, 'rim_clearance_m': rim_clearance}])
        return checks

    def verify_pre_release(self, rows):
        depth = min((r['insertion_depth_m'] for r in rows), default=float('-inf'))
        required = self.joint['nominal_insertion_depth_m'] - .002
        if depth < required:
            raise FailureEvent(FailureCode.INSERTION_DEPTH_NOT_REACHED, 'Post has not reached the pre-release insertion depth.',
                               violations=[{'minimum_observed_depth_m': depth, 'required_depth_m': required}])
        return {'pre_release_depth_reached': True}

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
    def __init__(self, model, data, config, joint, on_sample=None, on_stage=None, *,
                 transport_strategy='direct', on_transport_preflight_failure=None):
        if transport_strategy not in ('direct', 'direct_then_rrt'):
            raise ValueError('Unknown insertion transport strategy.')
        self.transport_strategy = transport_strategy
        self.on_transport_preflight_failure = on_transport_preflight_failure
        self.transport_attempts = []
        self.replan_attempts = []
        self._rejected_insert = None
        super().__init__(model, data, config, on_sample, on_stage)
        self.verifier = InsertionVerifier(model, config, joint)
        self.motion = WorkcellMotion(model, data, self.verifier, on_sample)

    def observe(self, *, serialize=True):
        observation = super().observe(serialize=serialize)
        observation['target'].update(source='configured_assembly_mating_pose',
                                     desired_part_center_m=self.verifier.target_position.tolist())
        observation['assembly_joint'] = {k: (v.tolist() if isinstance(v, np.ndarray) else deepcopy(v))
                                         for k, v in self.verifier.joint.items()}
        return observation

    def grasp_geometry(self):
        from post_grasp import grasp_geometry
        return grasp_geometry(self)

    def _grasp_trial_copy(self):
        return InsertionSkills(self.model, copy(self.data), self.config, self.verifier.joint,
                               transport_strategy=self.transport_strategy)

    def held_pad_geometry(self):
        from post_grasp import pad_clearance_at_seating
        return pad_clearance_at_seating(self)

    def check_grasp(self, grasp):
        """Try the proposed grasp and nominal insertion on an independent copy before live motion."""
        from post_grasp import bounds_issue
        outcome = {'grasp': dict(grasp), 'source': 'mujoco_preflight_copy',
                   'live_start_s': float(self.data.time), 'accepted': False}
        issue = bounds_issue(grasp, self.config['part']['size_m'])
        if issue:
            return {**outcome, 'phase': 'grasp_bounds', 'failure_code': 'INPUT_OR_PRECONDITION_INVALID',
                    'message': issue}
        trial = self._grasp_trial_copy()
        trial.motion.reference = self.motion.reference.copy()
        trial.motion.gripper_command = self.motion.gripper_command
        try:
            trial.pick(self.config['part']['id'], grasp=grasp)
            outcome['held_pad_geometry'] = trial.held_pad_geometry()
            result = trial.insert(self.config['part']['id'], self.config['target']['id'])
        except FailureEvent as error:
            if error.code in (FailureCode.MODEL_OR_CONTROL_INVALID, FailureCode.SIMULATION_INVALID):
                raise
            return {**outcome, 'phase': error.phase or trial.motion.stage, 'failure_code': error.code.value,
                    'message': str(error), 'violations': json_safe(error.violations),
                    'copy_end_s': float(trial.data.time)}
        return {**outcome, 'accepted': True, 'copy_end_s': float(trial.data.time),
                'checks': result['checks'], 'minimum_final_depth_m': result['minimum_final_depth_m'],
                **({'sleeve_fit': result['sleeve_fit']} if 'sleeve_fit' in result else {})}

    check_post_grasp = check_grasp  # Existing first-post callers.

    def insert_replan_available(self, error=None):
        return self._rejected_insert is not None and self._rejected_insert.available(self, error)

    def insert_replan_context(self):
        return self._rejected_insert.summary() if self.insert_replan_available() else None

    def replan_insert(self, part_id, target_id):
        rejected = self._rejected_insert
        self._rejected_insert = None  # Consume even when a stale request is rejected.
        if (rejected is None or not rejected.available(self)
                or part_id != rejected.intent['part_id'] or target_id != rejected.intent['target_id']):
            raise FailureEvent(FailureCode.INPUT_OR_PRECONDITION_INVALID,
                               'Need an unchanged, unexecuted insert-transfer rejection before policy replanning.')
        attempt = {'source': rejected.summary(), 'status': 'started', 'start_time_s': float(self.data.time),
                   'transport_executed': False}
        self.replan_attempts.append(attempt)
        previous_attempts = len(self.transport_attempts)
        try:
            # Recheck the current physical grasp in the existing transport planner.
            # Preserve the original intent, including an explicit scenario perturbation.
            result = self.insert(part_id, target_id, alignment_offset_m=rejected.intent['alignment_offset_m'],
                                 transport_strategy='direct_then_rrt')
            attempt['status'] = 'success'
            return result
        except (Exception, KeyboardInterrupt) as error:
            attempt['status'] = 'failed'
            attempt['failure_code'] = getattr(error, 'code', FailureCode.UNCLASSIFIED_ERROR).value
            raise
        finally:
            attempt['end_time_s'] = float(self.data.time)
            transfers = self.transport_attempts[previous_attempts:]
            # A failed executor may already have issued commands. Do not label it unexecuted.
            attempt['transport_executed'] = any(a['execution_started'] for a in transfers)
            attempt['transport_completed'] = any(a['status'] == 'executed' for a in transfers)

    def _transfer(self, position, rotation, *, strategy=None, intent=None):
        strategy = self.transport_strategy if strategy is None else strategy
        if strategy == 'direct':
            from rejected_insert import capture_transport_state, RejectedInsert
            before = capture_transport_state(self) if intent is not None else None
            try:
                plan = self.motion.plan(position, rotation, allow_support=False, allow_grasp=True)
            except FailureEvent as error:
                if before is not None:
                    rejected = RejectedInsert(error, before, intent, position, rotation)
                    if rejected.available(self):
                        self._rejected_insert = rejected
                raise
            # This executor is reached only after the complete plan passed preflight.
            return self.motion.execute(plan, allow_support=False, allow_grasp=True)
        from loaded_transport import plan_loaded_transfer
        attempt = {'part_id': self.config['part']['id'], 'stage': self.motion.stage,
                   'start_time_s': float(self.data.time), 'status': 'planning', 'planning': None,
                   'execution_started': False}
        self.transport_attempts.append(attempt)
        try:
            plan, metadata = plan_loaded_transfer(self, position, rotation,
                strategy=strategy,
                on_preflight_failure=self.on_transport_preflight_failure)
            attempt['planning'] = metadata
            attempt['execution_started'] = True
            rows = self.motion.execute(plan, allow_support=False, allow_grasp=True)
            attempt['status'] = 'executed'
            return rows
        except (Exception, KeyboardInterrupt) as exc:
            attempt['status'] = 'failed'
            attempt['failure_code'] = getattr(exc, 'code', FailureCode.UNCLASSIFIED_ERROR).value
            raise
        finally:
            attempt['end_time_s'] = float(self.data.time)

    def insertion_evidence(self, rows):
        return {'checks': self.verifier.verify_insertion(rows)}

    def insert(self, part_id, target_id, *, alignment_offset_m=None, transport_strategy=None):
        self._rejected_insert = None
        if transport_strategy is not None and transport_strategy not in ('direct', 'direct_then_rrt'):
            raise ValueError('Unknown insertion transport strategy override.')
        if (part_id != self.config['part']['id'] or target_id != self.config['target']['id']
                or self.motion.held_pose is None):
            raise FailureEvent(FailureCode.INPUT_OR_PRECONDITION_INVALID, 'Insertion needs a currently held known part.')
        offset = np.asarray([0., 0.] if alignment_offset_m is None else alignment_offset_m, dtype=float)
        if offset.shape != (2,) or not np.isfinite(offset).all() or np.max(np.abs(offset)) > .02:
            raise FailureEvent(FailureCode.INPUT_OR_PRECONDITION_INVALID, 'Alignment offset must be a finite XY vector within 20 mm.')
        held_p, held_r = self.motion.held_pose
        tcp_rotation = self.verifier.target_rotation @ held_r.T
        tcp_position = self.verifier.target_position - tcp_rotation @ held_p
        self.stage('insert_transfer')
        self._transfer(tcp_position + [offset[0], offset[1], LIFT_M], tcp_rotation, strategy=transport_strategy,
                       intent={'part_id': part_id, 'target_id': target_id, 'alignment_offset_m': offset.tolist()})
        self.stage('insert_alignment_check')
        self.verifier.verify_alignment(self.data)
        # Only the selected post may touch this fixed base. Robot/base remains forbidden.
        self.verifier.mating_enabled = True
        self.stage('insert_lower')
        rows = self.motion.move(tcp_position + [0, 0, .001], tcp_rotation, allow_support=True, allow_grasp=True)
        self.verifier.verify_pre_release(rows)
        self.stage('insert_release')
        self.motion.held_pose = None
        self.motion.hold(4., allow_support=True, allow_grasp=True, gripper_target=0.)
        self.motion.hold(1., allow_support=True, allow_grasp=True)
        self.stage('insert_retreat')
        p, r = read_tcp_pose(self.data)
        self.motion.move(p + [0, 0, HOVER_M], r, allow_support=True, allow_grasp=False)
        self.stage('insert_verify')
        rows = self.motion.hold(1., allow_support=True, allow_grasp=False)
        evidence = self.insertion_evidence(rows)
        result = {'skill': 'insert_post/v1', 'status': 'success', 'part_id': part_id, 'target_id': target_id,
                  **evidence, 'minimum_final_depth_m': min(r['insertion_depth_m'] for r in rows),
                  'maximum_final_position_error_m': max(r['target_position_error_m'] for r in rows),
                  'observation': self.observe()}
        self.results.append(result)
        return result
