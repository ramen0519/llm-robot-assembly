"""Execute one IK target through position servos and verify measured TCP arrival."""

import argparse
import csv
from dataclasses import dataclass
import math
from pathlib import Path
import time

from mink.exceptions import MinkError
import mujoco
import mujoco.viewer
import numpy as np

from move_gripper import measure_state, check_path_sample
from solve_tcp_ik import DEFAULT_OFFSET_M, check_candidate, pose_errors, solve_tcp_pose
from ur3e_with_gripper import load_robot_with_gripper
from view_tcp import TCP_SITE_NAME, add_tcp_overlay, append_marker, read_tcp_pose, set_tcp_camera
from view_ur3e import JOINT_NAMES


HOLD_S, MOVE_S, SETTLE_S = 1.0, 4.0, 2.0
TOTAL_S = HOLD_S + MOVE_S + SETTLE_S
STABLE_WINDOW_S = 0.5
POSITION_TOL_MM, ORIENTATION_TOL_DEG = 1.0, 0.5
FINAL_JOINT_SPEED_DEG_S = 0.5
PEAK_JOINT_SPEED_DEG_S = 15.0
TRACE_PATH = Path(__file__).resolve().parents[1] / "runs/step09/tcp_motion.csv"


@dataclass
class MotionPlan:
    start_arm: np.ndarray
    goal_arm: np.ndarray
    start_position: np.ndarray
    target_position: np.ndarray
    target_rotation: np.ndarray
    preflight_samples: int


def reference_at(time_s, start, goal):
    """Joint-space smoothstep: zero reference velocity at each end."""
    u = float(np.clip((time_s - HOLD_S) / MOVE_S, 0.0, 1.0))
    return start + (goal - start) * (3 * u**2 - 2 * u**3)


def prepare_motion(model, data):
    """Solve IK and check sampled nominal poses without moving the live state."""
    if (abs(float(data.time)) > 1e-9 or not np.allclose(data.qvel, 0, atol=1e-12, rtol=0)
            or not np.allclose(data.qpos, model.key("home").qpos, atol=1e-10, rtol=0)):
        raise ValueError("This increment starts from a fresh home state.")
    start_position, rotation = read_tcp_pose(data)
    target_position = start_position + DEFAULT_OFFSET_M
    result = solve_tcp_pose(model, data, target_position, rotation)
    if not result.success:
        raise RuntimeError(f"IK failed: {result.reason}; no motion started.")
    start, goal = data.qpos[:6].copy(), result.qpos[:6].copy()
    # At least 101 samples, and <= 0.25 deg joint increments. No continuous guarantee.
    intervals = max(100, math.ceil(float(np.max(np.abs(goal - start))) / math.radians(0.25)))
    scratch = mujoco.MjData(model)
    for fraction in np.linspace(0.0, 1.0, intervals + 1):
        scratch.qpos[:] = data.qpos
        scratch.qpos[:6] = start + fraction * (goal - start)
        check_candidate(model, scratch, data.qpos, list(range(6, 14)))
    return MotionPlan(start, goal, start_position, target_position, rotation, intervals + 1)


class ArmPositionController:
    """Add gravity feedforward through the existing, force-limited arm servos."""

    def __init__(self, model):
        self.model = model
        self.actuators = np.array([model.actuator(name.removesuffix("_joint")).id for name in JOINT_NAMES])
        self.kp = model.actuator_gainprm[self.actuators, 0].copy()
        for index, actuator in enumerate(self.actuators):
            joint = model.joint(JOINT_NAMES[index]).id
            bias = model.actuator_biasprm[actuator]
            if not (
                model.jnt_qposadr[joint] == index and model.jnt_dofadr[joint] == index
                and model.actuator_trntype[actuator] == mujoco.mjtTrn.mjTRN_JOINT
                and model.actuator_trnid[actuator, 0] == joint
                and np.array_equal(model.actuator_gear[actuator], [1, 0, 0, 0, 0, 0])
                and model.actuator_gaintype[actuator] == mujoco.mjtGain.mjGAIN_FIXED
                and model.actuator_biastype[actuator] == mujoco.mjtBias.mjBIAS_AFFINE
                and model.actuator_dyntype[actuator] == mujoco.mjtDyn.mjDYN_NONE
                and self.kp[index] > 0 and bias[0] == 0
                and bias[1] == -self.kp[index] and bias[2] < 0
                and model.actuator_ctrllimited[actuator] and model.actuator_forcelimited[actuator]
            ):
                raise ValueError("Gravity feedforward requires the original unit-gear position servos.")
        self.gravity_data = mujoco.MjData(model)
        self.gripper_actuator = model.actuator("gripper_fingers_actuator").id

    def gravity_torque(self, data):
        # Only this calculation copy has zero velocity; never reset live qpos/qvel.
        self.gravity_data.qpos[:] = data.qpos
        self.gravity_data.qvel[:] = 0.0
        mujoco.mj_forward(self.model, self.gravity_data)
        return self.gravity_data.qfrc_bias[:6].copy()

    def apply(self, data, reference):
        # tau = kp * (ctrl - q) - kd * qvel. This adds tau_gravity via the actuator.
        command = reference + self.gravity_torque(data) / self.kp
        bounds = self.model.actuator_ctrlrange[self.actuators]
        if (not np.all(np.isfinite(command)) or np.any(command < bounds[:, 0])
                or np.any(command > bounds[:, 1])):
            raise RuntimeError("Compensated motor command exceeds control limits.")
        data.ctrl[self.actuators] = command
        data.ctrl[self.gripper_actuator] = 0.0  # Open; fingers still follow physical dynamics.


def measure_motion(model, data, plan, reference, controller):
    # Reuse the mounted-gripper checks; arm drift here is error relative to the reference.
    phase = "hold" if data.time < HOLD_S - 1e-9 else "move" if data.time < HOLD_S + MOVE_S - 1e-9 else "settle"
    row = measure_state(model, data, reference, phase)
    check_path_sample(row)
    if data.ncon:
        contact = data.contact[0]
        names = [model.geom(int(i)).name or str(i) for i in (contact.geom1, contact.geom2)]
        raise RuntimeError(f"Contact is not allowed during this open-gripper move: {names}")
    if row["max_joint_speed_deg_s"] > PEAK_JOINT_SPEED_DEG_S:
        raise RuntimeError("Measured joint speed exceeded 15 deg/s.")
    if not all(80 <= row[key] <= 90 for key in ("pad1_gap_mm", "pad2_gap_mm")):
        raise RuntimeError("The empty gripper did not remain open.")
    row["tracking_error_deg"] = row.pop("arm_drift_deg")
    row["gripper_command"] = row.pop("command")
    position, _ = read_tcp_pose(data)
    position_error, orientation_error = pose_errors(data, plan.target_position, plan.target_rotation)
    jp, jr = np.zeros((3, model.nv)), np.zeros((3, model.nv))
    mujoco.mj_jacSite(model, data, jp, jr, model.site(TCP_SITE_NAME).id)
    row.update({
        "tcp_position_error_mm": position_error * 1000,
        "tcp_orientation_error_deg": math.degrees(orientation_error),
        "tcp_speed_mm_s": float(np.linalg.norm(jp @ data.qvel)) * 1000,
        "tcp_angular_speed_deg_s": math.degrees(float(np.linalg.norm(jr @ data.qvel))),
        "max_arm_speed_deg_s": float(np.rad2deg(np.max(np.abs(data.qvel[:6])))),
        "contact_count": int(data.ncon),
    })
    for axis, value in zip("xyz", position):
        row[f"tcp_{axis}_m"] = float(value)
    for i, name in enumerate(JOINT_NAMES):
        actuator = controller.actuators[i]
        row[f"{name}_reference_rad"] = float(reference[i])
        row[f"{name}_actual_rad"] = float(data.qpos[i])
        row[f"{name}_ctrl_rad"] = float(data.ctrl[actuator])
        row[f"{name}_force_nm"] = float(data.actuator_force[actuator])
    if not all(math.isfinite(value) for key, value in row.items() if key != "phase"):
        raise RuntimeError("Non-finite measurement.")
    return row


def add_motion_overlay(scene, data, plan):
    add_tcp_overlay(scene, data)
    append_marker(scene, plan.start_position, "Start", 0.003, (1.0, 0.2, 0.8, 1.0))
    # A small unlabelled cyan marker avoids overlapping the TCP label on arrival.
    append_marker(scene, plan.target_position, "", 0.003, (0.0, 1.0, 1.0, 1.0))


def run_motion(model, data, plan, viewer=None, trace_path=TRACE_PATH):
    """Execute via ctrl + mj_step only; stop the simulation immediately on a failed check."""
    if (abs(float(data.time)) > 1e-9 or not np.allclose(data.qpos[:6], plan.start_arm, atol=1e-10, rtol=0)):
        raise ValueError("The plan must be executed from its unchanged starting state.")
    timestep = float(model.opt.timestep)
    steps = round(TOTAL_S / timestep)
    if timestep > 0.002 + 1e-12 or not math.isclose(steps * timestep, TOTAL_S, abs_tol=1e-9):
        raise ValueError("Use a timestep <= 0.002 s that divides the 7-second trial exactly.")
    controller = ArmPositionController(model)
    frame_steps = max(1, round(1 / (60 * timestep)))
    wall_start, last_phase = time.monotonic(), None
    rows = []
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    with trace_path.open("w", encoding="utf-8", newline="") as trace:
        writer = None
        for step in range(steps + 1):
            if viewer is not None and not viewer.is_running():
                raise RuntimeError("Viewer closed before the trial finished; trial incomplete.")
            # Measure the actual integrated state, including the initial pose.
            reference = reference_at(float(data.time), plan.start_arm, plan.goal_arm)
            try:
                row = measure_motion(model, data, plan, reference, controller)
            except RuntimeError as error:
                raise RuntimeError(f"At {data.time:.3f} s: {error}") from error
            if writer is None:
                writer = csv.DictWriter(trace, fieldnames=list(row))
                writer.writeheader()
            writer.writerow(row)
            rows.append(row)
            if row["phase"] != last_phase:
                print(f"  {data.time:.3f} s: {row['phase']}", flush=True)
                last_phase = row["phase"]
            if viewer is not None and (step % frame_steps == 0 or step == steps):
                with viewer.lock():
                    viewer.user_scn.ngeom = 0
                    add_motion_overlay(viewer.user_scn, data, plan)
                viewer.sync()
                time.sleep(max(0.0, wall_start + float(data.time) - time.monotonic()))
            if step == steps:
                break
            controller.apply(data, reference)
            mujoco.mj_step(model, data)
            mujoco.mj_forward(model, data)  # Refresh TCP and contacts at the new state.
            if not math.isclose(float(data.time), (step + 1) * timestep, abs_tol=1e-8):
                raise RuntimeError("Physics time did not advance as expected.")
    return rows


def assess_motion(rows, timestep=0.002):
    times = np.array([r["time_s"] for r in rows])
    if (len(rows) != round(TOTAL_S / timestep) + 1 or abs(times[0]) > 1e-8
            or abs(times[-1] - TOTAL_S) > 1e-8
            or not np.allclose(np.diff(times), timestep, atol=1e-8, rtol=0)):
        raise ValueError("A full, continuous 7-second trial is required.")
    stable = [r for r in rows if r["time_s"] >= TOTAL_S - STABLE_WINDOW_S - 1e-9]
    maxima = {key: max(r[key] for r in stable) for key in (
        "tcp_position_error_mm", "tcp_orientation_error_deg", "max_joint_speed_deg_s",
        "tcp_speed_mm_s", "tcp_angular_speed_deg_s",
    )}
    checks = {
        "Final-window TCP position error <= 1 mm": maxima["tcp_position_error_mm"] <= POSITION_TOL_MM,
        "Final-window TCP orientation error <= 0.5 deg": maxima["tcp_orientation_error_deg"] <= ORIENTATION_TOL_DEG,
        "Final-window joint speed < 0.5 deg/s": maxima["max_joint_speed_deg_s"] < FINAL_JOINT_SPEED_DEG_S,
        "Final-window TCP speed < 1 mm/s": maxima["tcp_speed_mm_s"] < 1.0,
        "Final-window TCP angular speed < 0.5 deg/s": maxima["tcp_angular_speed_deg_s"] < 0.5,
        "No detected contacts throughout trial": all(r["contact_count"] == 0 for r in rows),
    }
    for key, value in maxima.items():
        print(f"  Final-window max {key}: {value:.9f}")
    for key in ("max_arm_speed_deg_s", "max_joint_speed_deg_s", "tracking_error_deg",
                "joint_limit_overshoot_rad", "linkage_residual_mm"):
        print(f"  Whole-trial max {key}: {max(r[key] for r in rows):.9f}")
    for label, passed in checks.items():
        print(f"  [{'PASS' if passed else 'FAIL'}] {label}")
    return all(checks.values())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--headless", action="store_true", help="Run identical physics without a viewer.")
    args = parser.parse_args()
    try:
        model, data = load_robot_with_gripper()
        plan = prepare_motion(model, data)
        print(f"Nominal joint-path preflight: {plan.preflight_samples} poses checked.")
        print("World TCP target: " + np.array2string(plan.target_position, precision=6))
        print("Hold 1 s -> joint-space move 4 s -> settle 2 s; gripper stays open.")
        print("Gravity feedforward through force-limited servos; physical motion, no teleportation.")
        print(f"Measured trace (overwritten on execution): {TRACE_PATH}", flush=True)
        if args.headless:
            rows = run_motion(model, data, plan)
        else:
            with mujoco.viewer.launch_passive(model, data, show_left_ui=False, show_right_ui=False) as viewer:
                with viewer.lock():
                    set_tcp_camera(viewer.cam, data)
                rows = run_motion(model, data, plan, viewer)
        passed = assess_motion(rows, float(model.opt.timestep))
        print(f"TCP physical motion: {'PASS' if passed else 'FAIL'}")
        print("Validation covers this simulated scene and sampled motion only; no hardware command was sent.")
        return 0 if passed else 1
    except (ValueError, RuntimeError, MinkError) as error:
        print(f"TCP physical motion: FAIL ({error})")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
