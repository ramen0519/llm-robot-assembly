"""Measured contact, grasp and placement checks for the one-box workcell."""

import math

import mujoco
import numpy as np

from failure_report import FailureCode, FailureEvent
from move_gripper import pad_gaps_mm
from ur3e_with_gripper import mount_error
from view_tcp import read_tcp_pose
from workcell_scene import TABLE_GEOM, part_name, table_to_world, yaw_rotation


PAD_NAMES = tuple(f"gripper_{side}_pad{i}" for side in ("left", "right") for i in (1, 2))


def rotation_error(a, b):
    return math.acos(float(np.clip((np.trace(a.T @ b) - 1) / 2, -1, 1)))


class WorkcellVerifier:
    def __init__(self, model, config):
        self.model, self.config = model, config
        self.part_body = model.body(part_name(config)).id
        self.part_geom = model.geom(part_name(config) + "_geom").id
        self.table_geom = model.geom(TABLE_GEOM).id
        self.pads = {model.geom(name).id: name for name in PAD_NAMES}
        self.table_z = config["table"]["top_center_world_m"][2]
        self.half_size = np.asarray(config["part"]["size_m"]) / 2
        self.target_position = table_to_world(config, [*config["target"]["xy_table_m"], self.half_size[2]])
        self.target_rotation = yaw_rotation(config["table"]["yaw_deg"] + config["target"]["yaw_deg"])

    def contacts(self, data, *, allow_support, allow_grasp, planned=False):
        """Only explicitly allowed geom pairs; never allow an entire robot/object category."""
        sides = {"left": False, "right": False}
        support = False
        maximum_penetration = 0.0
        for index, contact in enumerate(data.contact):
            pair = {int(contact.geom1), int(contact.geom2)}
            is_support = pair == {self.part_geom, self.table_geom}
            pad = next((g for g in pair if g in self.pads), None)
            is_grasp = self.part_geom in pair and pad is not None
            if not ((allow_support and is_support) or (allow_grasp and is_grasp)):
                names = [self.model.geom(int(g)).name or f"geom#{g}" for g in pair]
                raise FailureEvent(FailureCode.PLANNED_CONTACT if planned else FailureCode.UNEXPECTED_CONTACT,
                                   f"Forbidden contact: {names}")
            maximum_penetration = max(maximum_penetration, -float(contact.dist))
            if maximum_penetration > 0.003:
                raise FailureEvent(FailureCode.PLANNED_CONTACT if planned else FailureCode.UNEXPECTED_CONTACT,
                                   "Contact penetration exceeded 3 mm.",
                                   violations=[{"metric": "penetration_m", "observed": maximum_penetration,
                                                "maximum": 0.003}])
            force = np.zeros(6)
            mujoco.mj_contactForce(self.model, data, index, force)
            loaded = contact.dist <= 0 and force[0] > 0.01
            support |= is_support and loaded
            if is_grasp and loaded:
                sides["left" if "left" in self.pads[pad] else "right"] = True
        return {"supported": bool(support), "left_contact": sides["left"], "right_contact": sides["right"],
                "penetration_m": maximum_penetration}

    def physical_state(self, data, reference):
        if (not math.isfinite(float(data.time))
                or not all(np.all(np.isfinite(v)) for v in (data.qpos, data.qvel, data.ctrl))
                or any(w.number for w in data.warning)):
            raise FailureEvent(FailureCode.SIMULATION_INVALID, "Invalid simulation state or physics warning.",
                               kinematics_fresh=False)
        if np.any(data.qfrc_applied) or np.any(data.xfrc_applied) or np.any(self.model.body_gravcomp):
            raise FailureEvent(FailureCode.MODEL_OR_CONTROL_INVALID, "External forces / body gravity cancellation are not allowed.")
        mount_error(self.model, data)
        limited = self.model.jnt_limited.astype(bool)
        q, bounds = data.qpos[self.model.jnt_qposadr[limited]], self.model.jnt_range[limited]
        if np.any(q < bounds[:, 0] - 0.001) or np.any(q > bounds[:, 1] + 0.001):
            raise FailureEvent(FailureCode.MOTION_LIMIT_EXCEEDED, "Joint limit overshoot exceeded 0.001 rad.")
        for row in range(data.ne):
            if (self.model.eq_type[data.efc_id[row]] == mujoco.mjtEq.mjEQ_CONNECT
                    and abs(data.efc_pos[row]) > 0.0002):
                raise FailureEvent(FailureCode.GRIPPER_STATE_INVALID, "Linkage residual exceeded 0.2 mm.")
        speed = float(np.max(np.abs(data.qvel[:6])))
        tracking = float(np.max(np.abs(data.qpos[:6] - reference)))
        if speed > math.radians(15) or tracking > math.radians(2):
            raise FailureEvent(FailureCode.MOTION_LIMIT_EXCEEDED, "Arm speed >15 deg/s or tracking error >2 deg.",
                               violations=[{"arm_speed_rad_s": speed, "tracking_error_rad": tracking}])
        return speed, tracking

    def sample(self, data, reference, *, allow_support, allow_grasp):
        arm_speed, tracking = self.physical_state(data, reference)
        contacts = self.contacts(data, allow_support=allow_support, allow_grasp=allow_grasp)
        tcp, _ = read_tcp_pose(data)
        body = data.body(self.part_body)
        pos, rot = body.xpos.copy(), body.xmat.reshape(3, 3).copy()
        velocity = data.joint(part_name(self.config) + "_free").qvel
        bottom = pos[2] - np.abs(rot[2]) @ self.half_size
        return {
            "simulation_time_s": float(data.time),
            **{f"tcp_{axis}_m": float(tcp[i]) for i, axis in enumerate("xyz")},
            **{f"part_{axis}_m": float(pos[i]) for i, axis in enumerate("xyz")},
            "part_bottom_clearance_m": float(bottom - self.table_z),
            "part_speed_m_s": float(np.linalg.norm(velocity[:3])),
            "part_angular_speed_rad_s": float(np.linalg.norm(velocity[3:])),
            "arm_speed_rad_s": arm_speed, "tracking_error_rad": tracking,
            "robot_speed_rad_s": float(np.max(np.abs(data.qvel[:14]))),
            "target_position_error_m": float(np.linalg.norm(pos - self.target_position)),
            "target_orientation_error_rad": rotation_error(self.target_rotation, rot),
            "pad_gap_mm": float(min(pad_gaps_mm(self.model, data))),
            "gripper_command": float(data.ctrl[self.model.actuator("gripper_fingers_actuator").id]),
            **contacts,
        }

    def relative_part_pose(self, data):
        p, r = read_tcp_pose(data)
        body = data.body(self.part_body)
        return r.T @ (body.xpos - p), r.T @ body.xmat.reshape(3, 3)

    def check_held(self, data, row, held_pose):
        p, r = self.relative_part_pose(data)
        slip = float(np.linalg.norm(p - held_pose[0]))
        rotation_slip = rotation_error(r, held_pose[1])
        if (not row["left_contact"] or not row["right_contact"]
                or slip > 0.005 or rotation_slip > math.radians(5)):
            raise FailureEvent(FailureCode.PART_LOST, "Bilateral grasp lost or relative part pose drifted.",
                               violations=[{"relative_slip_m": slip, "relative_rotation_rad": rotation_slip,
                                            "left_contact": row["left_contact"], "right_contact": row["right_contact"]}])
        row["relative_slip_m"], row["relative_rotation_rad"] = slip, rotation_slip

    def verify_placement(self, rows):
        checks = {
            "table_support": all(r["supported"] for r in rows),
            "fingers_released": all(not r["left_contact"] and not r["right_contact"] and r["pad_gap_mm"] > 80 for r in rows),
            "position_under_3mm": max(r["target_position_error_m"] for r in rows) <= 0.003,
            "orientation_under_3deg": max(r["target_orientation_error_rad"] for r in rows) <= math.radians(3),
            "linear_speed_under_1mm_s": max(r["part_speed_m_s"] for r in rows) < 0.001,
            "angular_speed_under_0_01rad_s": max(r["part_angular_speed_rad_s"] for r in rows) < 0.01,
            "penetration_under_1mm": max(r["penetration_m"] for r in rows) < 0.001,
        }
        if not all(checks.values()):
            raise FailureEvent(FailureCode.PLACEMENT_NOT_CONFIRMED, "Measured placement criteria failed.",
                               violations=[{"checks": checks}])
        return checks
