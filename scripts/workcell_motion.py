"""Current-state Cartesian waypoints, Mink IK, sampled preflight and servo execution."""

from dataclasses import dataclass
import math

import mink
from mink.exceptions import NoSolutionFound
import mujoco
import numpy as np
from scipy.spatial.transform import Rotation, Slerp

from failure_report import FailureCode, FailureEvent, capture_observation
from move_tcp import ArmPositionController
from solve_tcp_ik import pose_errors
from view_tcp import TCP_SITE_NAME, read_tcp_pose
from view_ur3e import JOINT_NAMES


@dataclass
class CartesianPlan:
    start_qpos: np.ndarray
    start_time_s: float
    arm_knots: np.ndarray
    target_position: np.ndarray
    target_rotation: np.ndarray
    duration_s: float
    checked_samples: int


class WorkcellMotion:
    """Local waypoint motion, not a global obstacle-avoiding planner or hardware driver."""

    def __init__(self, model, data, verifier, on_sample=None):
        self.model, self.data, self.verifier = model, data, verifier
        self.controller = ArmPositionController(model)
        self.on_sample = on_sample
        self.stage = "initialise"
        self.goal = None
        self.gripper_command = 0.0
        self.reference = data.qpos[:6].copy()
        self.held_pose = None
        self.dt = float(model.opt.timestep)
        if not 0 < self.dt <= 0.002:
            raise ValueError("Workcell execution requires timestep <= 0.002 s.")
        self.plans = []

    def _candidate(self, qpos, allow_support, allow_grasp):
        scratch = mujoco.MjData(self.model)
        scratch.qpos[:] = qpos
        # Moving obstacles are not encoded in qpos. Copy their current poses too.
        scratch.mocap_pos[:] = self.data.mocap_pos
        scratch.mocap_quat[:] = self.data.mocap_quat
        mujoco.mj_forward(self.model, scratch)
        if self.held_pose is not None:
            # Prediction in a calculation copy ONLY. No weld/attachment on the live body.
            p, r = read_tcp_pose(scratch)
            joint = self.model.body(self.verifier.part_body).jntadr[0]
            address = self.model.jnt_qposadr[joint]
            scratch.qpos[address:address + 3] = p + r @ self.held_pose[0]
            quat = np.empty(4)
            mujoco.mju_mat2Quat(quat, (r @ self.held_pose[1]).ravel())
            scratch.qpos[address + 3:address + 7] = quat
            mujoco.mj_forward(self.model, scratch)
        try:
            self.verifier.contacts(scratch, allow_support=allow_support, allow_grasp=allow_grasp, planned=True)
        except FailureEvent as error:
            error.phase = self.stage + "/preflight"
            error.candidate = capture_observation(self.model, scratch, source="nominal_path_candidate")
            raise

    def plan(self, target_position, target_rotation, *, allow_support, allow_grasp):
        """Build a complete, checked segment before sending any motor command."""
        p = np.asarray(target_position, dtype=float)
        r = np.asarray(target_rotation, dtype=float)
        if (p.shape != (3,) or r.shape != (3, 3) or not np.isfinite(p).all() or not np.isfinite(r).all()
                or not np.allclose(r.T @ r, np.eye(3), atol=1e-8) or abs(np.linalg.det(r) - 1) > 1e-8):
            raise FailureEvent(FailureCode.INPUT_OR_PRECONDITION_INVALID, "Invalid world TCP target.")
        self.goal = {"kind": "tcp_pose", "frame": "world", "position_m": p.copy(), "rotation_matrix": r.copy()}
        start_p, start_r = read_tcp_pose(self.data)
        distance = float(np.linalg.norm(p - start_p))
        angle = float((Rotation.from_matrix(start_r).inv() * Rotation.from_matrix(r)).magnitude())
        if distance > 1.0:
            raise FailureEvent(FailureCode.INPUT_OR_PRECONDITION_INVALID, "Local segment exceeds 1 metre.")
        intervals = max(1, math.ceil(distance / 0.01), math.ceil(angle / math.radians(3)))
        interpolation = Slerp([0, 1], Rotation.from_matrix([start_r, r]))
        configuration = mink.Configuration(self.model, q=self.data.qpos.copy())
        task = mink.FrameTask(TCP_SITE_NAME, "site", position_cost=1., orientation_cost=1., lm_damping=1e-3)
        # Velocity indices differ from qpos indices for the free part (6 vs 7 components).
        freeze = mink.DofFreezingTask(self.model, dof_indices=list(range(6, self.model.nv)))
        limits = mink.ConfigurationLimit(self.model, min_distance_from_limits=-0.001)
        limits.lower[:6] = self.model.jnt_range[:6, 0] + 1e-6
        limits.upper[:6] = self.model.jnt_range[:6, 1] - 1e-6
        velocity_limit = mink.VelocityLimit(self.model, {name: 1. for name in JOINT_NAMES})
        knots = [self.data.qpos[:6].copy()]
        checked = 0
        self._candidate(self.data.qpos.copy(), allow_support, allow_grasp)
        for u in np.linspace(0, 1, intervals + 1)[1:]:
            goal_p, goal_r = start_p + u * (p - start_p), interpolation(u).as_matrix()
            task.set_target(mink.SE3.from_rotation_and_translation(mink.SO3.from_matrix(goal_r), goal_p))
            converged = False
            for _ in range(201):
                pe, re = pose_errors(configuration.data, goal_p, goal_r)
                if pe <= 0.0001 and re <= math.radians(0.05):
                    converged = True
                    break
                try:
                    velocity = mink.solve_ik(configuration, [task], 0.02, "daqp",
                                             limits=[limits, velocity_limit], constraints=[freeze])
                except NoSolutionFound as error:
                    raise FailureEvent(FailureCode.IK_NOT_CONVERGED, str(error), phase=self.stage + "/ik",
                                       candidate=capture_observation(self.model, configuration.data,
                                                                     source="ik_candidate")) from error
                if not np.isfinite(velocity).all():
                    raise FailureEvent(FailureCode.IK_NOT_CONVERGED, "Non-finite IK velocity.")
                configuration.integrate_inplace(velocity, 0.02)
            if not converged:
                raise FailureEvent(FailureCode.IK_NOT_CONVERGED, "Waypoint IK iteration budget exhausted.",
                                   phase=self.stage + "/ik",
                                   candidate=capture_observation(self.model, configuration.data, source="ik_candidate"))
            if not np.allclose(configuration.q[6:], self.data.qpos[6:], atol=1e-8, rtol=0):
                raise FailureEvent(FailureCode.MODEL_OR_CONTROL_INVALID, "IK modified frozen non-arm coordinates.")
            goal_arm = configuration.q[:6].copy()
            if np.any(goal_arm < limits.lower[:6]) or np.any(goal_arm > limits.upper[:6]):
                raise FailureEvent(FailureCode.MOTION_LIMIT_EXCEEDED, "IK arm exceeds limits.")
            # Check the interpolation that will actually be executed, <= 0.25 deg per joint.
            samples = max(2, math.ceil(float(np.max(np.abs(goal_arm - knots[-1]))) / math.radians(0.25)) + 1)
            for fraction in np.linspace(0, 1, samples):
                q = self.data.qpos.copy()
                q[:6] = knots[-1] + fraction * (goal_arm - knots[-1])
                self._candidate(q, allow_support, allow_grasp)
                checked += 1
            knots.append(goal_arm)
        knots = np.asarray(knots)
        # Uniform knot parameter + smoothstep time scaling, bounded reference speed.
        max_derivative = intervals * float(np.max(np.abs(np.diff(knots, axis=0))))
        duration = max(3., 1.5 * distance / 0.025, 1.5 * max_derivative / math.radians(5))
        return CartesianPlan(self.data.qpos.copy(), float(self.data.time), knots, p.copy(), r.copy(), duration, checked)

    def sample(self, *, allow_support, allow_grasp):
        row = self.verifier.sample(self.data, self.reference, allow_support=allow_support, allow_grasp=allow_grasp)
        row["stage"] = self.stage
        if self.held_pose is not None:
            self.verifier.check_held(self.data, row, self.held_pose)
        if self.goal and self.goal["kind"] == "tcp_pose":
            pe, re = pose_errors(self.data, self.goal["position_m"], self.goal["rotation_matrix"])
            row["tcp_position_error_m"], row["tcp_orientation_error_rad"] = pe, re
        if self.on_sample is not None:
            self.on_sample(row)
        return row

    def step(self, *, allow_support, allow_grasp):
        previous_time = float(self.data.time)
        self.controller.apply(self.data, self.reference, self.gripper_command)
        mujoco.mj_step(self.model, self.data)
        mujoco.mj_forward(self.model, self.data)
        if not math.isclose(self.data.time, previous_time + self.dt, abs_tol=1e-8):
            raise FailureEvent(FailureCode.SIMULATION_INVALID, "Simulation time advanced incorrectly.")
        return self.sample(allow_support=allow_support, allow_grasp=allow_grasp)

    def hold(self, duration_s, *, allow_support, allow_grasp, gripper_target=None):
        start_command = self.gripper_command
        rows = []
        self.sample(allow_support=allow_support, allow_grasp=allow_grasp)
        count = math.ceil(duration_s / self.dt)
        for i in range(1, count + 1):
            if gripper_target is not None:
                u = i / count
                self.gripper_command = start_command + (gripper_target - start_command) * (3 * u*u - 2*u*u*u)
            row = self.step(allow_support=allow_support, allow_grasp=allow_grasp)
            if i > count - math.ceil(0.5 / self.dt):
                rows.append(row)
        return rows

    def execute(self, plan, *, allow_support, allow_grasp):
        if (not math.isclose(self.data.time, plan.start_time_s, abs_tol=1e-10)
                or not np.allclose(self.data.qpos, plan.start_qpos, atol=1e-10, rtol=0)):
            raise FailureEvent(FailureCode.INPUT_OR_PRECONDITION_INVALID, "Plan is stale; observe and plan again.")
        self.plans.append({"stage": self.stage, "duration_s": plan.duration_s,
                           "waypoints": len(plan.arm_knots), "checked_samples": plan.checked_samples,
                           "target_position_m": plan.target_position, "target_rotation": plan.target_rotation})
        self.sample(allow_support=allow_support, allow_grasp=allow_grasp)
        count = math.ceil(plan.duration_s / self.dt)
        for i in range(1, count + 1):
            u = i / count
            cursor = (3*u*u - 2*u*u*u) * (len(plan.arm_knots) - 1)
            index = min(int(cursor), len(plan.arm_knots) - 2)
            fraction = cursor - index
            self.reference = plan.arm_knots[index] * (1 - fraction) + plan.arm_knots[index + 1] * fraction
            self.step(allow_support=allow_support, allow_grasp=allow_grasp)
        rows = self.hold(1.5, allow_support=allow_support, allow_grasp=allow_grasp)
        if any(r["tcp_position_error_m"] > 0.001 or r["tcp_orientation_error_rad"] > math.radians(0.5) for r in rows):
            raise FailureEvent(FailureCode.TCP_NOT_REACHED, "TCP did not settle within 1 mm / 0.5 deg.",
                               violations=[{"position_error_m": max(r["tcp_position_error_m"] for r in rows),
                                            "orientation_error_rad": max(r["tcp_orientation_error_rad"] for r in rows)}])
        if any(r["robot_speed_rad_s"] > math.radians(0.5) for r in rows):
            raise FailureEvent(FailureCode.NOT_SETTLED, "Robot did not settle below 0.5 deg/s.")
        return rows

    def move(self, position, rotation, *, allow_support=True, allow_grasp=False):
        plan = self.plan(position, rotation, allow_support=allow_support, allow_grasp=allow_grasp)
        return self.execute(plan, allow_support=allow_support, allow_grasp=allow_grasp)
