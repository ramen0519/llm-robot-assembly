"""Deterministic observe / approach / pick / place skills for one upright box."""

import math
import numpy as np

from failure_report import FailureCode, FailureEvent, json_safe
from view_tcp import read_tcp_pose
from workcell_scene import observe_workcell, part_name
from workcell_verification import WorkcellVerifier, rotation_error
from workcell_motion import WorkcellMotion


DOWNWARD_TCP = np.array([[0., -1., 0.], [-1., 0., 0.], [0., 0., -1.]])
GRASP_TCP_ABOVE_CENTER_M = 0.006
HOVER_M, LIFT_M, RELEASE_GAP_M = 0.08, 0.10, 0.002


class AssemblySkills:
    """One live simulator episode. No reset, retry, LLM or hardware commands here."""

    def __init__(self, model, data, config, on_sample=None, on_stage=None):
        self.model, self.data, self.config = model, data, config
        self.verifier = WorkcellVerifier(model, config)
        self.motion = WorkcellMotion(model, data, self.verifier, on_sample)
        self.on_stage = on_stage
        self.results = []

    def stage(self, name):
        self.motion.stage = name
        if self.on_stage:
            self.on_stage(name)

    def observe(self):
        return json_safe(observe_workcell(self.model, self.data, self.config))

    def settle(self):
        self.stage("settle_workcell")
        rows = self.motion.hold(3., allow_support=True, allow_grasp=False)
        if any(not r["supported"] or r["part_speed_m_s"] >= 0.001
               or r["part_angular_speed_rad_s"] >= 0.01 for r in rows):
            raise FailureEvent(FailureCode.NOT_SETTLED, "Part is not stably supported by table.")
        return self.observe()

    def approach(self, part_id):
        if part_id != self.config["part"]["id"] or self.motion.held_pose is not None:
            raise FailureEvent(FailureCode.INPUT_OR_PRECONDITION_INVALID, "Unknown part or gripper already occupied.")
        size = self.config["part"]["size_m"]
        if not (0.02 <= size[0] <= 0.06 and 0.02 <= size[1] <= 0.06 and 0.03 <= size[2] <= 0.08):
            raise FailureEvent(FailureCode.INPUT_OR_PRECONDITION_INVALID, "Box outside this skill's supported geometry envelope.")
        body = self.data.body(part_name(self.config))
        rotation = body.xmat.reshape(3, 3).copy()
        if np.dot(rotation[:, 2], [0, 0, 1]) < math.cos(math.radians(1)):
            raise FailureEvent(FailureCode.INPUT_OR_PRECONDITION_INVALID, "This skill requires an upright box.")
        row = self.verifier.sample(self.data, self.motion.reference, allow_support=True, allow_grasp=False)
        if not row["supported"] or row["part_speed_m_s"] > 0.001 or row["pad_gap_mm"] < 80:
            raise FailureEvent(FailureCode.INPUT_OR_PRECONDITION_INVALID, "Observe a settled box and an open gripper first.")
        grasp_position = body.xpos.copy() + [0, 0, GRASP_TCP_ABOVE_CENTER_M]
        grasp_rotation = rotation @ DOWNWARD_TCP
        self.stage("approach_hover")
        self.motion.move(grasp_position + [0, 0, HOVER_M], grasp_rotation)
        # Re-observe after the hover: do not descend toward an outdated object location.
        body = self.data.body(part_name(self.config))
        if (np.linalg.norm(body.xpos + [0, 0, GRASP_TCP_ABOVE_CENTER_M] - grasp_position) > 0.001
                or rotation_error(body.xmat.reshape(3, 3), rotation) > math.radians(1)):
            raise FailureEvent(FailureCode.INPUT_OR_PRECONDITION_INVALID, "Part moved during approach; re-observation required.")
        self.stage("approach_lower")
        rows = self.motion.move(grasp_position, grasp_rotation)
        result = {"skill": "approach/v1", "status": "success", "part_id": part_id,
                  "final_tcp_error_m": rows[-1]["tcp_position_error_m"], "observation": self.observe()}
        self.results.append(result)
        return result

    def pick(self, part_id):
        self.approach(part_id)
        self.stage("pick_close")
        # Stop on measured bilateral contact instead of crushing toward fully closed.
        consecutive = 0
        for i in range(1, math.ceil(4. / self.motion.dt) + 1):
            u = min(1., i * self.motion.dt / 4.)
            self.motion.gripper_command = 255. * (3*u*u - 2*u*u*u)
            row = self.motion.step(allow_support=True, allow_grasp=True)
            consecutive = consecutive + 1 if row["left_contact"] and row["right_contact"] else 0
            if consecutive * self.motion.dt >= 0.02:
                break
        else:
            raise FailureEvent(FailureCode.GRASP_NOT_CONFIRMED, "No bilateral contact within bounded closing motion.")
        self.motion.hold(1., allow_support=True, allow_grasp=True,
                         gripper_target=min(255., float(self.data.ten_length[0]) * 255. / 0.8 + 3.))
        self.stage("pick_confirm_contact")
        rows = self.motion.hold(1.5, allow_support=True, allow_grasp=True)
        if any(not r["left_contact"] or not r["right_contact"] or not 10 < r["pad_gap_mm"] < 70 for r in rows):
            raise FailureEvent(FailureCode.GRASP_NOT_CONFIRMED, "Closing command did not produce a stable bilateral grasp.",
                               violations=[{"last_contact_measurement": rows[-1]}])
        # A measured transform for checking/predicting poses, NEVER a simulator attachment.
        self.motion.held_pose = self.verifier.relative_part_pose(self.data)
        self.stage("pick_lift")
        p, r = read_tcp_pose(self.data)
        rows = self.motion.move(p + [0, 0, LIFT_M], r, allow_support=True, allow_grasp=True)
        if any(r["supported"] or r["part_bottom_clearance_m"] < 0.05 for r in rows):
            raise FailureEvent(FailureCode.GRASP_NOT_CONFIRMED, "Part did not lift clear of the table by at least 50 mm.")
        result = {"skill": "pick/v1", "status": "success", "part_id": part_id,
                  "minimum_final_clearance_m": min(r["part_bottom_clearance_m"] for r in rows),
                  "maximum_final_slip_m": max(r["relative_slip_m"] for r in rows), "observation": self.observe()}
        self.results.append(result)
        return result

    def place(self, part_id, target_id):
        if (part_id != self.config["part"]["id"] or target_id != self.config["target"]["id"]
                or self.motion.held_pose is None):
            raise FailureEvent(FailureCode.INPUT_OR_PRECONDITION_INVALID, "Place requires the observed held part and a known target.")
        held_position, held_rotation = self.motion.held_pose
        target_rotation = self.verifier.target_rotation @ held_rotation.T
        # Convert desired PART pose to a TCP pose using the measured grasp transform.
        target_position = self.verifier.target_position - target_rotation @ held_position
        self.stage("place_transfer")
        self.motion.move(target_position + [0, 0, LIFT_M], target_rotation, allow_support=False, allow_grasp=True)
        self.stage("place_lower")
        self.motion.move(target_position + [0, 0, RELEASE_GAP_M], target_rotation, allow_support=True, allow_grasp=True)
        self.stage("place_release")
        self.motion.held_pose = None  # Stop requiring a grasp; this does not change physics.
        self.motion.hold(4., allow_support=True, allow_grasp=True, gripper_target=0.)
        self.motion.hold(1., allow_support=True, allow_grasp=True)
        self.stage("place_retreat")
        p, r = read_tcp_pose(self.data)
        self.motion.move(p + [0, 0, HOVER_M], r, allow_support=True, allow_grasp=False)
        self.stage("place_verify")
        rows = self.motion.hold(1., allow_support=True, allow_grasp=False)
        checks = self.verifier.verify_placement(rows)
        result = {"skill": "place/v1", "status": "success", "part_id": part_id, "target_id": target_id,
                  "checks": checks, "maximum_final_position_error_m": max(r["target_position_error_m"] for r in rows),
                  "maximum_final_orientation_error_rad": max(r["target_orientation_error_rad"] for r in rows),
                  "observation": self.observe()}
        self.results.append(result)
        return result
