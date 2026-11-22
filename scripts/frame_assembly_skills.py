"""Compound-part support/grasp checks and measured insertion skills for the frame."""
import math
from copy import copy
import mujoco
import numpy as np
from assembly_skills import DOWNWARD_TCP, HOVER_M
from failure_report import FailureCode, FailureEvent
from insertion_skills import InsertionSkills, InsertionVerifier
from workcell_motion import WorkcellMotion
from workcell_scene import part_name
from workcell_verification import WorkcellVerifier, rotation_error


class FrameVerifier(InsertionVerifier):
    def __init__(self, model, active, registry, installed):
        entry = registry[active]
        super().__init__(model, entry['config'], entry['joint'])
        self.active, self.registry, self.installed = active, registry, installed
        self.mating_motion = False
        self.geom_part = {model.geom(g).id: name for name, e in registry.items() for g in e['collision_geom_names']}

    def support_relation(self, part, counterpart):
        entry = self.registry[part]
        if counterpart == self.table_geom:
            return part not in self.installed
        supports = entry['support_ids']
        if counterpart == self.floor_geom:
            return 'base_floor' in supports
        if counterpart in self.fixture_geoms:
            return 'base_top' in supports
        return self.geom_part.get(counterpart) in supports

    def mating_relation(self, part, counterpart):
        target = self.registry[part].get('fixed_base_id', 'base') if counterpart in self.fixture_geoms else self.geom_part.get(counterpart)
        return target in self.registry[part]['mating_ids']

    def contacts(self, data, *, allow_support, allow_grasp, planned=False):
        table_support_force, fixture_support_force = 0., 0.
        sides = {'left': False, 'right': False}
        penetration, max_force = 0., 0.
        for index, c in enumerate(data.contact):
            a, b = int(c.geom1), int(c.geom2)
            pa, pb = self.geom_part.get(a), self.geom_part.get(b)
            active_geom, counterpart = (a, b) if pa == self.active else ((b, a) if pb == self.active else (None, None))
            grasp = active_geom is not None and counterpart in self.pads
            fixture_mating = active_geom is not None and self.mating_enabled and self.mating_relation(self.active, counterpart)
            active_table = active_geom is not None and counterpart == self.table_geom
            allowed = (allow_grasp and grasp) or (allow_support and active_table) or fixture_mating
            if active_geom is None:
                # Previously installed pairs retain only their declared mating/support contacts.
                allowed = any(part is not None and (
                    (other == self.table_geom and part not in self.installed)
                    or (part in self.installed and self.mating_relation(part, other)))
                    for part, other in ((pa, b), (pb, a)))
            if not allowed:
                names = [self.model.geom(g).name or self.model.body(self.model.geom_bodyid[g]).name for g in (a, b)]
                raise FailureEvent(FailureCode.PLANNED_CONTACT if planned else FailureCode.UNEXPECTED_CONTACT,
                                   f'Forbidden compound-part contact: {", ".join(names)}',
                                   violations=[{'geoms': names, 'position_world_m': c.pos.tolist(),
                                                'signed_distance_m': float(c.dist)}])
            penetration = max(penetration, -float(c.dist))
            if penetration > .003:
                raise FailureEvent(FailureCode.PLANNED_CONTACT if planned else FailureCode.UNEXPECTED_CONTACT,
                                   'Compound-part penetration exceeded 3 mm.')
            force = np.zeros(6)
            if not planned:
                mujoco.mj_contactForce(self.model, data, index, force)
            loaded = c.dist <= 0 and force[0] > .01
            vertical = abs(float(c.frame[2])) > .9
            if active_geom is not None:
                if c.dist <= 0 and vertical and active_table:
                    table_support_force += max(0., float(force[0])) * abs(float(c.frame[2]))
                if c.dist <= 0 and vertical and fixture_mating and self.support_relation(self.active, counterpart):
                    fixture_support_force += max(0., float(force[0])) * abs(float(c.frame[2]))
                if grasp and loaded:
                    sides['left' if 'left' in self.pads[counterpart] else 'right'] = True
                if fixture_mating:
                    max_force = max(max_force, float(force[0]))
        if not planned and max_force > 8.:
            raise FailureEvent(FailureCode.INSERTION_CONTACT_LIMIT, 'Compound-part contact-point normal force exceeded 8 N.',
                               violations=[{'fixture_contact_force_n': max_force}])
        return {'supported': table_support_force + fixture_support_force > .01, 'fixture_supported': fixture_support_force > .01,
                'left_contact': sides['left'], 'right_contact': sides['right'],
                'penetration_m': penetration, 'fixture_contact_force_n': max_force}

    def verify_other_parts(self, data, *, require_settled=True):
        # One fresh scan per physical sample, not one full scan per inactive part.
        # No cache: mutations at the same simulation time must still be observed.
        support_forces = {}
        for index, contact in enumerate(data.contact):
            if contact.dist > 0 or abs(float(contact.frame[2])) <= .9:
                continue
            a, b = int(contact.geom1), int(contact.geom2)
            supported_names = [part for part, other in ((self.geom_part.get(a), b), (self.geom_part.get(b), a))
                               if part is not None and self.support_relation(part, other)]
            if supported_names:
                force = np.zeros(6)
                mujoco.mj_contactForce(self.model, data, index, force)
                for part in supported_names:
                    support_forces[part] = support_forces.get(part, 0.) + max(0., float(force[0])) * abs(float(contact.frame[2]))
        for name, entry in self.registry.items():
            if name == self.active:
                continue
            body = data.body(part_name(entry['config']))
            velocity = data.joint(part_name(entry['config']) + '_free').qvel
            expected = entry['joint']['target_part_center_world_m'] if name in self.installed else entry['spawn_center_world_m']
            error = float(np.linalg.norm(body.xpos - expected))
            angle = rotation_error(body.xmat.reshape(3, 3), entry['joint']['target_part_rotation_world'])
            supported = support_forces.get(name, 0.) > .01
            # Contact can briefly move a supported mating partner. Keep pose/support
            # bounds unchanged; the final verification window still requires rest.
            transient_mate = (not require_settled and name in self.installed
                              and name in self.registry[self.active]['mating_ids'])
            linear_limit, angular_limit = (.010, .100) if transient_mate else (.001, .010)
            if data.time > 1. and (not supported or error > .001 or angle > math.radians(1)
                                   or np.linalg.norm(velocity[:3]) > linear_limit or np.linalg.norm(velocity[3:]) > angular_limit):
                raise FailureEvent(FailureCode.PLACEMENT_NOT_CONFIRMED, f'Other component is not preserved: {name}',
                                   violations=[{'part_id': name, 'installed': name in self.installed,
                                                'position_error_m': error, 'orientation_error_rad': angle, 'supported': bool(supported),
                                                'linear_speed_m_s': float(np.linalg.norm(velocity[:3])),
                                                'angular_speed_rad_s': float(np.linalg.norm(velocity[3:])),
                                                'linear_limit_m_s': linear_limit, 'angular_limit_rad_s': angular_limit,
                                                'require_settled': not transient_mate}])

    def sample(self, data, reference, *, allow_support, allow_grasp):
        row = WorkcellVerifier.sample(self, data, reference, allow_support=allow_support, allow_grasp=allow_grasp)
        row['insertion_depth_m'] = self.joint['socket_rim_z_world_m'] - self.table_z - row['part_bottom_clearance_m']
        self.verify_other_parts(data, require_settled=not self.mating_motion)
        return row

    def verify_insertion(self, rows):
        checks = super().verify_insertion(rows)
        checks['declared_mating_support'] = checks.pop('socket_floor_support')
        return checks


class FrameSkills(InsertionSkills):
    def __init__(self, model, data, active, registry, installed, on_sample=None, on_stage=None, *,
                 transport_strategy='direct', on_transport_preflight_failure=None, empty_approach_strategy=None):
        if empty_approach_strategy not in (None, 'direct', 'direct_then_rrt'):
            raise ValueError('Unknown empty-gripper approach strategy.')
        entry = registry[active]
        super().__init__(model, data, entry['config'], entry['joint'], on_sample, on_stage,
            transport_strategy=transport_strategy, on_transport_preflight_failure=on_transport_preflight_failure)
        self.registry, self.installed, self.active = registry, installed, active
        self.empty_approach_strategy = transport_strategy if empty_approach_strategy is None else empty_approach_strategy
        self.empty_approach_attempts = []
        self.verifier = FrameVerifier(model, active, registry, installed)
        self.motion = WorkcellMotion(model, data, self.verifier, on_sample)

    def stage(self, name):
        self.verifier.mating_motion = name in ('insert_lower', 'insert_release', 'insert_retreat')
        super().stage(name)

    def observe(self, *, serialize=True):
        observation = super().observe(serialize=serialize)
        entry = self.registry[self.active]
        if entry['kind'] == 'cover':
            observation['assembly_joint'].update(required_support_count=len(entry['support_ids']),
                                                 required_support_ids=list(entry['support_ids']))
        observation['all_parts'] = {name: {'position_world_m': self.data.body(part_name(e['config'])).xpos.tolist(),
                                           'quaternion_wxyz': self.data.body(part_name(e['config'])).xquat.tolist(),
                                           'installed': name in self.installed}
                                    for name, e in self.registry.items()}
        return observation

    def _grasp_trial_copy(self):
        return FrameSkills(self.model, copy(self.data), self.active, self.registry, set(self.installed),
                           transport_strategy=self.transport_strategy, empty_approach_strategy=self.empty_approach_strategy)

    def grasp_geometry(self):
        kind = self.registry[self.active]['kind']
        if kind == 'post':
            return super().grasp_geometry()
        packet = super().grasp_geometry()
        packet.pop('socket_insertion_depth_mm')
        dims = self.verifier.joint['dimensions_m']
        if kind == 'cover':
            from post_grasp import unloaded_pad_reach_profile
            entry = self.registry[self.active]
            supports = entry['support_ids']
            tab = self.model.geom(part_name(self.config) + '_grip_tab')
            plate_bottom = -packet['part_size_xyz_mm'][2] / 2
            plate_top = plate_bottom + 1000*dims['cover_height']
            body = self.data.body(self.verifier.part_body)
            bottom_world = body.xpos[2] + body.xmat.reshape(3, 3)[2, 2]*plate_bottom/1000
            packet.update(shape='square perforated plate with one integral raised rectangular tab',
                          frame='origin at center of total bounding box including tab; +z is up',
                          plate_thickness_mm=1000*dims['cover_height'],
                          unloaded_closure_profile_mm=[{'jaw_gap': gap, 'pad_reach_below_tcp': reach}
                                                       for gap, reach in unloaded_pad_reach_profile()],
                          closure_note='Measured on the same empty gripper. Above solid sheet, local clearance '
                                       '= proposed z_mm minus pad_reach_below_tcp minus plate_top_z. '
                                       'It must stay positive along closure to the width of the grasped faces.',
                          plate_solid_z_range_mm=[plate_bottom, plate_top],
                          plate_underside_center_above_table_mm=round(float((bottom_world-self.verifier.table_z)*1000), 3),
                          tab_solid_z_range_mm=[round(float((tab.pos[2]-tab.size[2])*1000), 3),
                                                round(float((tab.pos[2]+tab.size[2])*1000), 3)],
                          above_plate_geometry='Only the tab has opposed vertical faces above the perforated sheet. '
                                               'Outside the bores, the plate is solid between its bottom and top.',
                          tab_center_xyz_mm=(tab.pos * 1000).round(4).tolist(),
                          tab_size_xyz_mm=(tab.size * 2000).round(4).tolist(),
                          bore_centers_xyz_mm={k: (np.array(c)*1000).round(4).tolist()
                                               for k, c in entry['bore_centers_part_m'].items()},
                          square_bore_width_mm=1000*dims['hole_width'],
                          square_post_width_mm=1000*dims['post_width'],
                          required_support_ids=list(supports), required_support_count=len(supports),
                          seating_surface=f'plate underside rests on all {len(supports)} sleeve tops; posts pass through respective holes',
                          tcp_note='TCP is the midpoint between the two pads. The pads must close on opposed solid faces.')
            return packet
        post = next(name for name in self.registry[self.active]['requires'] if name != self.registry[self.active].get('fixed_base_id', 'base'))
        packet.update(shape='upright square sleeve with a centered through-hole',
                      frame='origin at geometric center of sleeve; axes follow the sleeve; +z is up',
                      bore_width_mm=1000*dims['hole_width'], mating_post_id=post,
                      mating_post_width_mm=1000*dims['post_width'],
                      nominal_exposed_post_height_mm=1000*self.verifier.joint['nominal_insertion_depth_m'],
                      seating_surface='top surface of fixed base; post passes through the sleeve bore',
                      tcp_note='TCP is the midpoint between jaws and can lie in the empty bore. Pads contact solid walls.')
        return packet

    def check_grasp(self, grasp):
        outcome = super().check_grasp(grasp)
        if self.registry[self.active]['kind'] != 'cover' or outcome['accepted'] or outcome['phase'] == 'grasp_bounds':
            return outcome
        packet = self.grasp_geometry()
        plate_top = packet['plate_solid_z_range_mm'][1]
        outcome['unloaded_clearance_over_solid_plate_mm'] = [
            {'jaw_gap': row['jaw_gap'], 'pad_bottom_minus_plate_top': round(grasp['z_mm'] - row['pad_reach_below_tcp'] - plate_top, 2)}
            for row in packet['unloaded_closure_profile_mm']]
        if outcome['phase'].startswith('approach_lower') and outcome.get('violations'):
            # The cover has not been picked up. Translate the collision bodies into plate-local facts.
            lowest = grasp['z_mm'] - max(high for _, high in packet['open_pad_spans_along_tcp_positive_z_mm'])
            entry = self.registry[self.active]
            contacts = []
            for violation in outcome['violations']:
                for name in violation.get('geoms', []):
                    if name in entry['collision_geom_names']:
                        geom = self.model.geom(name)
                        contacts.append({'feature': 'raised tab' if name.endswith('_grip_tab') else 'solid part of flat plate',
                                         'geom': name, 'top_z_part_mm': round(float((geom.pos[2]+geom.size[2])*1000), 2)})
            outcome['approach_geometry'] = {'cover_contacts': contacts,
                'proposed_open_pad_bottom_z_part_mm': round(lowest, 2),
                'predicted_open_pad_bottom_minus_plate_top_mm': round(lowest - plate_top, 2),
                'sign': 'Negative is below plate top at proposed TCP. Check available space along BOTH jaw paths.'}
        return outcome

    def held_pad_geometry(self):
        if self.registry[self.active]['kind'] == 'post':
            return super().held_pad_geometry()
        from post_grasp import lowest_pad_above_part_bottom
        lowest = round(float(lowest_pad_above_part_bottom(self) * 1000), 2)
        if self.registry[self.active]['kind'] == 'cover':
            thickness = self.verifier.joint['dimensions_m']['cover_height'] * 1000
            return {'lowest_pad_above_plate_top_mm': round(lowest - thickness, 2)}
        return {'lowest_pad_above_sleeve_bottom_mm': lowest,
                'predicted_lowest_pad_height_relative_to_seated_base_top_mm': lowest}

    def _move_to_hover(self, position, rotation):
        if self.empty_approach_strategy == 'direct':
            return self.motion.move(position, rotation)
        from empty_approach import plan_empty_approach
        attempt = {'part_id': self.active, 'stage': self.motion.stage,
                   'start_time_s': float(self.data.time), 'status': 'planning', 'planning': None}
        self.empty_approach_attempts.append(attempt)
        try:
            plan, metadata = plan_empty_approach(self, position, rotation,
                on_preflight_failure=self.on_transport_preflight_failure)
            attempt['planning'] = metadata
            rows = self.motion.execute(plan, allow_support=True, allow_grasp=False)
            attempt['status'] = 'executed'
            return rows
        except (Exception, KeyboardInterrupt) as exc:
            attempt['status'] = 'failed'
            attempt['failure_code'] = getattr(exc, 'code', FailureCode.UNCLASSIFIED_ERROR).value
            raise
        finally:
            attempt['end_time_s'] = float(self.data.time)

    def approach(self, part_id, *, grasp=None):
        if part_id != self.active or self.motion.held_pose is not None or part_id in self.installed:
            raise FailureEvent(FailureCode.INPUT_OR_PRECONDITION_INVALID, 'Need a known uninstalled free component and empty gripper.')
        entry = self.registry[part_id]
        if not set(entry['requires']) <= self.installed | {entry.get('fixed_base_id', 'base')}:
            raise FailureEvent(FailureCode.INPUT_OR_PRECONDITION_INVALID, 'Component prerequisites are not assembled.')
        body = self.data.body(self.verifier.part_body)
        rotation = body.xmat.reshape(3, 3).copy()
        row = self.verifier.sample(self.data, self.motion.reference, allow_support=True, allow_grasp=False)
        if (np.dot(rotation[:, 2], [0, 0, 1]) < math.cos(math.radians(1))
                or not row['supported'] or row['part_speed_m_s'] > .001 or row['pad_gap_mm'] < 80):
            raise FailureEvent(FailureCode.INPUT_OR_PRECONDITION_INVALID, 'Need a settled upright component and open gripper.')
        center = body.xpos.copy()
        if grasp is None:
            target = center + rotation @ np.asarray(entry['grasp_offset_part_m'])
            orientation = rotation @ DOWNWARD_TCP
        else:
            from post_grasp import bounds_issue, world_grasp
            issue = bounds_issue(grasp, self.config['part']['size_m'])
            if issue:
                raise FailureEvent(FailureCode.INPUT_OR_PRECONDITION_INVALID, issue)
            target, orientation = world_grasp(center, rotation, grasp, DOWNWARD_TCP)
        self.stage('approach_hover')
        self._move_to_hover(target + [0., 0., HOVER_M], orientation)
        body = self.data.body(self.verifier.part_body)
        if (np.linalg.norm(body.xpos - center) > .001
                or rotation_error(body.xmat.reshape(3, 3), rotation) > math.radians(1)):
            raise FailureEvent(FailureCode.INPUT_OR_PRECONDITION_INVALID, 'Component moved during approach.')
        self.stage('approach_lower')
        rows = self.motion.move(target, orientation)
        result = {'skill': 'approach_component/v1', 'status': 'success', 'part_id': part_id,
                  'final_tcp_error_m': rows[-1]['tcp_position_error_m'], 'observation': self.observe()}
        if grasp is not None:
            result.update(proposed_grasp=dict(grasp), commanded_tcp_world_m=target.tolist(),
                          commanded_tcp_rotation_world=orientation.tolist())
        self.results.append(result)
        return result

    def verify_sleeve_fit(self):
        """Check the four actual post edges at both openings in the sleeve's own frame."""
        entry = self.registry[self.active]
        post_id = next(name for name in entry['requires'] if name != self.registry[self.active].get('fixed_base_id', 'base'))
        height = self.config['part']['size_m'][2]
        return {'sleeve_height_mm': 1000*height, **self._measure_post_in_bore(post_id, np.zeros(3), height)}

    def _measure_post_in_bore(self, post_id, bore_center_local, thickness):
        # Intersect each measured post edge with both opening planes in the moving plate/tube frame.
        if post_id not in self.installed:
            raise FailureEvent(FailureCode.INPUT_OR_PRECONDITION_INVALID, 'Mating post is not installed.')
        post = self.data.body(part_name(self.registry[post_id]['config']))
        part = self.data.body(self.verifier.part_body)
        axes = part.xmat.reshape(3, 3)
        post_center = axes.T @ (post.xpos - part.xpos) - bore_center_local
        post_axes = axes.T @ post.xmat.reshape(3, 3)
        half = np.asarray(self.registry[post_id]['config']['part']['size_m']) / 2
        hole_half = self.verifier.joint['dimensions_m']['hole_width'] / 2
        if post_axes[2, 2] <= 0.99:
            raise FailureEvent(FailureCode.PLACEMENT_NOT_CONFIRMED, f'{post_id} is not aligned with its bore.')
        clearance = hole_half
        end_margin = half[2]
        for x in (-half[0], half[0]):
            for y in (-half[1], half[1]):
                edge_center = post_center + post_axes[:, 0] * x + post_axes[:, 1] * y
                for z in (-thickness / 2, thickness / 2):
                    distance = (z - edge_center[2]) / post_axes[2, 2]
                    xy = edge_center[:2] + post_axes[:2, 2] * distance
                    clearance = min(clearance, hole_half - np.max(np.abs(xy)))
                    end_margin = min(end_margin, half[2] - abs(distance))
        fit = {'post_id': post_id,
               'minimum_bore_side_clearance_mm': 1000*float(clearance),
               'minimum_post_extension_past_openings_mm': 1000*float(end_margin)}
        if clearance < 0 or end_margin < 0:
            raise FailureEvent(FailureCode.PLACEMENT_NOT_CONFIRMED, 'Post does not pass through both openings.',
                               violations=[fit])
        return fit

    def verify_cover_fit(self):
        entry = self.registry[self.active]
        bores = entry['bore_centers_part_m']
        supports = entry['support_ids']
        if (not bores or not supports or len(bores) != len(supports)
                or set(entry['requires']) != set(bores) | set(supports)
                or not set(supports).issubset(self.installed)):
            raise FailureEvent(FailureCode.INPUT_OR_PRECONDITION_INVALID,
                               'Cover verification requires all declared mating posts and supporting sleeves.')
        thickness = self.verifier.joint['dimensions_m']['cover_height']
        fits = {name: self._measure_post_in_bore(name, np.asarray(center), thickness)
                for name, center in bores.items()}
        forces = dict.fromkeys(supports, 0.)
        for index, c in enumerate(self.data.contact):
            pair = [self.verifier.geom_part.get(int(g)) for g in (c.geom1, c.geom2)]
            if self.active not in pair or c.dist > 0 or abs(c.frame[2]) <= .9:
                continue
            other = pair[1 - pair.index(self.active)]
            if other in forces:
                force = np.zeros(6)
                mujoco.mj_contactForce(self.model, self.data, index, force)
                forces[other] += max(0., float(force[0])) * abs(float(c.frame[2]))
        if any(value <= .01 for value in forces.values()):
            raise FailureEvent(FailureCode.PLACEMENT_NOT_CONFIRMED, f'Cover is not supported on all {len(supports)} sleeves.',
                               violations=[{'vertical_normal_load_n': forces}])
        return {'plate_thickness_mm': thickness*1000, 'bores': fits, 'vertical_normal_load_n': forces}

    def insertion_evidence(self, rows):
        evidence = super().insertion_evidence(rows)
        if self.registry[self.active]['kind'] == 'sleeve':
            evidence['sleeve_fit'] = self.verify_sleeve_fit()
            evidence['checks']['post_through_both_sleeve_openings'] = True
        elif self.registry[self.active]['kind'] == 'cover':
            evidence['cover_fit'] = self.verify_cover_fit()
            if len(evidence['cover_fit']['bores']) == 4:
                # Four-post episodes retain their published verifier contract.
                evidence['checks'].update(four_posts_through_both_openings=True, four_sleeves_support_cover=True)
            else:
                evidence['checks'].update(all_posts_through_both_openings=True, all_sleeves_support_cover=True)
        return evidence

    def insert(self, part_id, target_id, **kwargs):
        result = super().insert(part_id, target_id, **kwargs)
        result['skill'] = {'post': 'insert_post/v1', 'sleeve': 'insert_sleeve/v1', 'cover': 'seat_cover/v1'}[self.registry[part_id]['kind']]
        return result
