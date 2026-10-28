"""Solve one nearby TCP pose with Mink; preview only, without motor execution."""

import argparse
from dataclasses import dataclass
import math
import time

import mink
from mink.exceptions import MinkError, NoSolutionFound
import mujoco
import mujoco.viewer
import numpy as np

from failure_report import FailureCode, FailureEvent, capture_observation
from ur3e_with_gripper import load_robot_with_gripper
from view_tcp import (
    TCP_SITE_NAME, add_tcp_overlay, append_connector, append_marker,
    check_tcp_frame, read_tcp_pose, set_tcp_camera,
)
from view_ur3e import JOINT_NAMES


DEFAULT_OFFSET_M = np.array([0.0, 0.0, 0.03])  # WORLD +Z, not TCP-local +Z.
POSITION_TOL_M = 0.001
ORIENTATION_TOL_RAD = math.radians(0.5)
IK_DT = 0.02  # Numerical integration step; no physical simulation time passes.
MAX_ITERATIONS = 200


@dataclass
class IKResult:
    success: bool
    reason: str
    qpos: np.ndarray
    iterations: int
    position_error_m: float
    orientation_error_rad: float


def pose_errors(data, target_position, target_rotation):
    """Independently measure world position and shortest rotation errors using FK."""
    position, rotation = read_tcp_pose(data)
    cosine = (np.trace(target_rotation.T @ rotation) - 1.0) / 2.0
    return float(np.linalg.norm(position - target_position)), math.acos(float(np.clip(cosine, -1, 1)))


def check_candidate(model, data, initial_qpos, frozen_dofs, *, phase="ik"):
    """Check one static candidate. This is not a continuous-path certificate."""
    def reject(code, message, *, fresh=False):
        raise FailureEvent(code, message, phase=phase,
                           candidate=capture_observation(model, data, source=f"{phase}_candidate",
                                                         kinematics_fresh=fresh))

    if not np.all(np.isfinite(data.qpos)):
        reject(FailureCode.IK_NOT_CONVERGED, "IK produced a non-finite configuration.")
    # This demo has 14 scalar hinge joints, so qpos and velocity indices coincide.
    limited = model.jnt_limited.astype(bool)
    q = data.qpos[model.jnt_qposadr[limited]]
    bounds = model.jnt_range[limited]
    if np.any(q < bounds[:, 0] - 1e-8) or np.any(q > bounds[:, 1] + 1e-8):
        reject(FailureCode.MOTION_LIMIT_EXCEEDED, "IK candidate violates joint limits.")
    if not np.allclose(data.qpos[frozen_dofs], initial_qpos[frozen_dofs], atol=1e-9, rtol=0):
        reject(FailureCode.MODEL_OR_CONTROL_INVALID, "IK changed a frozen gripper joint.")
    mujoco.mj_forward(model, data)
    try:
        check_tcp_frame(model, data)
    except (ValueError, RuntimeError) as error:
        reject(FailureCode.MODEL_OR_CONTROL_INVALID, str(error), fresh=True)
    # Check all model-enabled contacts, including the gripper and floor.
    # The open, unloaded demo allows none; no carrying or insertion contact is modeled yet.
    if data.ncon:
        contact = data.contact[0]
        names = [model.geom(int(i)).name or str(i) for i in (contact.geom1, contact.geom2)]
        reject(FailureCode.PLANNED_CONTACT, f"IK candidate has a contact: {names[0]} / {names[1]}", fresh=True)


def solve_tcp_pose(model, initial_data, target_position, target_rotation, max_iterations=MAX_ITERATIONS):
    """Find a pose from this model's open-gripper state without changing initial_data.

    Solver iterations are candidate configurations, NOT a robot trajectory.
    Failure to converge does not prove the target is unreachable from every seed.
    """
    target_position = np.array(target_position, dtype=float, copy=True)
    target_rotation = np.array(target_rotation, dtype=float, copy=True)
    if (target_position.shape != (3,) or target_rotation.shape != (3, 3)
            or not np.all(np.isfinite(target_position)) or not np.all(np.isfinite(target_rotation))):
        raise ValueError("Target must be a finite world position (3,) and rotation (3, 3).")
    if (not np.allclose(target_rotation.T @ target_rotation, np.eye(3), atol=1e-8, rtol=0)
            or not math.isclose(float(np.linalg.det(target_rotation)), 1.0, abs_tol=1e-8, rel_tol=0)):
        raise ValueError("Target orientation must be a proper rotation matrix.")
    if not isinstance(max_iterations, int) or max_iterations < 1:
        raise ValueError("max_iterations must be a positive integer.")
    if (model.nq != 14 or model.nv != 14
            or np.any(model.jnt_type != mujoco.mjtJoint.mjJNT_HINGE)):
        raise ValueError("This increment supports only the mounted 14-hinge UR3e / 2F-85 model.")
    arm_dofs = [int(model.jnt_dofadr[model.joint(name).id]) for name in JOINT_NAMES]
    frozen_dofs = sorted(set(range(model.nv)) - set(arm_dofs))
    initial_qpos = initial_data.qpos.copy()
    if not np.all(np.isfinite(initial_qpos)):
        raise ValueError("Initial joint positions must be finite.")
    if not np.allclose(initial_qpos[frozen_dofs], 0.0, atol=1e-10, rtol=0):
        raise ValueError("This demo expects the nominal open gripper; loaded states are not supported yet.")

    # Mink owns a separate MjData. Never change the live state or its controls here.
    configuration = mink.Configuration(model, q=initial_qpos)
    task = mink.FrameTask(TCP_SITE_NAME, "site", position_cost=1.0, orientation_cost=1.0, lm_damping=1e-3)
    task.set_target(mink.SE3.from_rotation_and_translation(mink.SO3.from_matrix(target_rotation), target_position))
    freeze = mink.DofFreezingTask(model, dof_indices=frozen_dofs)
    limits = [
        mink.ConfigurationLimit(model),
        # Bound each numerical update to <= 0.02 rad; this is not an actuator speed command.
        mink.VelocityLimit(model, {name: 1.0 for name in JOINT_NAMES}),
    ]
    for iteration in range(max_iterations + 1):
        check_candidate(model, configuration.data, initial_qpos, frozen_dofs)
        position_error, orientation_error = pose_errors(configuration.data, target_position, target_rotation)
        success = position_error <= POSITION_TOL_M and orientation_error <= ORIENTATION_TOL_RAD
        if success or iteration == max_iterations:
            return IKResult(success, "pose_tolerance" if success else "iteration_limit",
                            configuration.q.copy(), iteration, position_error, orientation_error)
        try:
            velocity = mink.solve_ik(configuration, [task], IK_DT, "daqp", limits=limits, constraints=[freeze])
        except NoSolutionFound as error:
            raise FailureEvent(FailureCode.IK_NOT_CONVERGED, str(error), phase="ik",
                               candidate=capture_observation(model, configuration.data,
                                                             source="ik_candidate")) from error
        if not np.all(np.isfinite(velocity)):
            raise FailureEvent(FailureCode.IK_NOT_CONVERGED, "IK solver returned non-finite velocity.", phase="ik")
        configuration.integrate_inplace(velocity, IK_DT)
    raise AssertionError("Bounded IK loop must return or raise.")


def add_ik_overlay(scene, data, start_position):
    add_tcp_overlay(scene, data)
    append_marker(scene, start_position, "Start", 0.004, (1.0, 0.2, 0.8, 1.0))
    position, _ = read_tcp_pose(data)
    append_connector(scene, start_position, position, (1.0, 0.2, 0.8, 1.0), arrow=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Calculate and verify without opening a viewer.")
    parser.add_argument("--seconds", type=float, default=0.0, help="Static preview duration; 0 waits for you to close it.")
    args = parser.parse_args()
    if not math.isfinite(args.seconds) or args.seconds < 0:
        parser.error("--seconds must be finite and non-negative")
    try:
        model, initial_data = load_robot_with_gripper()
        start_position, start_rotation = read_tcp_pose(initial_data)
        target_position = start_position + DEFAULT_OFFSET_M
        result = solve_tcp_pose(model, initial_data, target_position, start_rotation)
        print("World-frame TCP target: +30 mm upward, initial orientation preserved.")
        print("Start [m]:  " + np.array2string(start_position, precision=6))
        print("Target [m]: " + np.array2string(target_position, precision=6))
        print("Joint angles [deg]: initial -> IK candidate")
        for name in JOINT_NAMES:
            index = model.jnt_qposadr[model.joint(name).id]
            print(f"  {name:22s} {math.degrees(initial_data.qpos[index]):10.5f} -> {math.degrees(result.qpos[index]):10.5f}")
        print(f"Iterations: {result.iterations}; stop reason: {result.reason}")
        print(f"Position error: {result.position_error_m * 1000:.6f} mm (limit 1 mm)")
        print(f"Orientation error: {math.degrees(result.orientation_error_rad):.6f} deg (limit 0.5 deg)")
        print(f"TCP IK calculation: {'PASS' if result.success else 'FAIL'}", flush=True)
        if not result.success:
            return 1
        print("Joint limits and frozen gripper: PASS; no contacts detected at checked candidates.")
        print("Static candidate preview only. No motor execution, no mj_step(), no verified motion path.", flush=True)
        if args.check:
            return 0
        # Initialize a separate static preview. Assigning qpos here is not physical motion.
        preview = mujoco.MjData(model)
        preview.qpos[:] = result.qpos
        mujoco.mj_forward(model, preview)
        with mujoco.viewer.launch_passive(model, preview, show_left_ui=False, show_right_ui=False) as viewer:
            with viewer.lock():
                set_tcp_camera(viewer.cam, preview)
            started = time.monotonic()
            while viewer.is_running():
                if args.seconds and time.monotonic() - started >= args.seconds:
                    break
                with viewer.lock():
                    mujoco.mj_forward(model, preview)
                    viewer.user_scn.ngeom = 0
                    add_ik_overlay(viewer.user_scn, preview, start_position)
                viewer.sync()
                time.sleep(1 / 60)
        print(f"IK preview closed. Simulation time: {preview.time:.3f} s")
        return 0
    except (ValueError, RuntimeError, MinkError) as error:
        print(f"TCP IK calculation: FAIL ({error})")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
