"""Active-part skills with observation and collision checks for every other product part."""
import math
import mujoco
import numpy as np
from failure_report import FailureCode, FailureEvent
from insertion_skills import InsertionSkills, InsertionVerifier
from workcell_motion import WorkcellMotion
from workcell_scene import part_name
from workcell_verification import rotation_error


class MultiPostVerifier(InsertionVerifier):
    def __init__(self, model, active, registry, installed):
        entry = registry[active]
        super().__init__(model, entry['config'], entry['joint'])
        self.active, self.registry, self.installed = active, registry, installed
        self.other_geoms = {model.geom(part_name(e['config']) + '_geom').id: key
                            for key, e in registry.items() if key != active}

    def contact_policy(self, pair, *, allow_support, allow_grasp):
        allowed = super().contact_policy(pair, allow_support=allow_support, allow_grasp=allow_grasp)
        if allowed[0]:
            return allowed
        # Inactive post support does not become active-part support.
        for geom, name in self.other_geoms.items():
            if pair == {geom, self.table_geom} and name not in self.installed:
                return True, False, False, None
            if name in self.installed and geom in pair and pair & self.fixture_geoms:
                return True, False, False, None
        return allowed

    def check_other_parts(self, data):
        measurements = {}
        for geom, name in self.other_geoms.items():
            entry = self.registry[name]
            body = data.body(part_name(entry['config']))
            velocity = data.joint(part_name(entry['config']) + '_free').qvel
            expected = entry['joint']['target_part_center_world_m'] if name in self.installed else entry['spawn_center_world_m']
            error = float(np.linalg.norm(body.xpos - expected))
            angle = rotation_error(body.xmat.reshape(3, 3), entry['joint']['target_part_rotation_world'])
            supported = False
            for i, c in enumerate(data.contact):
                pair = {int(c.geom1), int(c.geom2)}
                support_geom = self.floor_geom if name in self.installed else self.table_geom
                if pair == {geom, support_geom}:
                    force = np.zeros(6)
                    mujoco.mj_contactForce(self.model, data, i, force)
                    supported |= c.dist <= 0 and force[0] > .01
            # The first second permits all free parts to settle from declared start poses.
            if data.time > 1. and (not supported or error > .001 or angle > math.radians(1)
                                   or np.linalg.norm(velocity[:3]) > .001 or np.linalg.norm(velocity[3:]) > .01):
                raise FailureEvent(FailureCode.PLACEMENT_NOT_CONFIRMED, f'Inactive product part moved or lost support: {name}',
                                   violations=[{'part_id': name, 'installed': name in self.installed,
                                                'position_error_m': error, 'orientation_error_rad': angle,
                                                'supported': bool(supported)}])
            measurements[name] = {'position_error_m': error, 'supported': bool(supported), 'installed': name in self.installed}
        return measurements

    def sample(self, data, reference, *, allow_support, allow_grasp):
        row = super().sample(data, reference, allow_support=allow_support, allow_grasp=allow_grasp)
        self.check_other_parts(data)
        return row


class MultiPostSkills(InsertionSkills):
    def __init__(self, model, data, active, registry, installed, on_sample=None, on_stage=None):
        entry = registry[active]
        super().__init__(model, data, entry['config'], entry['joint'], on_sample, on_stage)
        self.registry, self.installed, self.active = registry, installed, active
        self.verifier = MultiPostVerifier(model, active, registry, installed)
        self.motion = WorkcellMotion(model, data, self.verifier, on_sample)

    def observe(self):
        observation = super().observe()
        observation['all_parts'] = {
            name: {'position_world_m': self.data.body(part_name(e['config'])).xpos.tolist(),
                   'quaternion_wxyz': self.data.body(part_name(e['config'])).xquat.tolist(),
                   'installed': name in self.installed}
            for name, e in self.registry.items()}
        return observation
