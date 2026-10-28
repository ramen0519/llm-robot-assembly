"""Close and reopen the mounted 2F-85; verify measured motion in an empty scene."""

import argparse
import csv
import math
from pathlib import Path
import time

import mujoco
import mujoco.viewer
import numpy as np

from failure_report import FailureCode, FailureEvent
from ur3e_with_gripper import load_robot_with_gripper, mount_error
from view_ur3e import set_camera


HOLD_END_S = 1.0
CLOSE_END_S = 5.0
CLOSED_HOLD_END_S = 7.0
OPEN_END_S = 11.0
TOTAL_S = 13.0
STABLE_WINDOW_S = 0.5
TRACE_PATH = Path(__file__).resolve().parents[1] / "runs/step06/gripper_cycle.csv"
TRACE_COLUMNS = (
    "time_s", "phase", "command", "pad1_gap_mm", "pad2_gap_mm",
    "arm_drift_deg", "max_joint_speed_deg_s", "driver_mismatch_deg",
    "joint_limit_overshoot_rad", "linkage_residual_mm", "pad_penetration_mm",
)


def command_at(time_s):
    """One command drives both fingers through the original tendon/linkages."""
    if time_s < HOLD_END_S:
        return 0.0, "open_hold"
    if time_s < CLOSE_END_S:
        progress = (time_s - HOLD_END_S) / (CLOSE_END_S - HOLD_END_S)
        return 255.0 * (3 * progress**2 - 2 * progress**3), "closing"
    if time_s < CLOSED_HOLD_END_S:
        return 255.0, "closed_hold"
    if time_s < OPEN_END_S:
        progress = (time_s - CLOSED_HOLD_END_S) / (OPEN_END_S - CLOSED_HOLD_END_S)
        return 255.0 * (1 - 3 * progress**2 + 2 * progress**3), "opening"
    return 0.0, "reopened_hold"


def pad_gaps_mm(model, data):
    """Signed gaps of the two opposing box pairs, projected on the closing axis.

    Each finger has two collision boxes. Subtract their projected half-widths
    from the center separation; this is not a distance between visual meshes
    or a real-gripper width calibration. Negative values mean overlap in this
    projection. Actual contact penetration is checked separately.
    """
    axis = data.body("gripper_base").xmat.reshape(3, 3)[:, 1]
    gaps = []
    for level in (1, 2):
        ids = [model.geom(f"gripper_{side}_pad{level}").id for side in ("right", "left")]
        if any(model.geom_type[g] != mujoco.mjtGeom.mjGEOM_BOX for g in ids):
            raise ValueError("Gap measurement requires the original box collision pads.")
        center_separation = abs(float((data.geom_xpos[ids[0]] - data.geom_xpos[ids[1]]) @ axis))
        half_widths = sum(
            float(np.abs(data.geom_xmat[g].reshape(3, 3).T @ axis) @ model.geom_size[g])
            for g in ids
        )
        gaps.append(1000.0 * (center_separation - half_widths))
    return gaps


def measure_state(model, data, initial_arm, phase):
    """Read actual state, including all enabled contacts and linkage constraints."""
    if not all(np.all(np.isfinite(values)) for values in (data.qpos, data.qvel, data.ctrl)):
        raise FailureEvent(FailureCode.SIMULATION_INVALID, "Non-finite joint/control state.", kinematics_fresh=False)
    if any(warning.number for warning in data.warning):
        raise FailureEvent(FailureCode.SIMULATION_INVALID, "MuJoCo reported a physics warning.", kinematics_fresh=False)
    try:
        mount_error(model, data)
    except RuntimeError as error:
        raise FailureEvent(FailureCode.MODEL_OR_CONTROL_INVALID, str(error)) from error

    right = {model.geom(f"gripper_right_pad{i}").id for i in (1, 2)}
    left = {model.geom(f"gripper_left_pad{i}").id for i in (1, 2)}
    penetration = 0.0
    for contact in data.contact:
        if contact.dist > 0:
            continue
        opposing_pads = (
            (contact.geom1 in right and contact.geom2 in left)
            or (contact.geom2 in right and contact.geom1 in left)
        )
        if not opposing_pads:
            names = [model.geom(g).name or f"geom#{g}" for g in (contact.geom1, contact.geom2)]
            raise FailureEvent(FailureCode.UNEXPECTED_CONTACT, f"Unexpected contact: {names[0]} / {names[1]}")
        penetration = max(penetration, -float(contact.dist) * 1000.0)

    q = data.qpos[model.jnt_qposadr]
    violation = np.maximum(model.jnt_range[:, 0] - q, q - model.jnt_range[:, 1])
    limit_error = max(0.0, float(np.max(violation[model.jnt_limited != 0])))
    link_error = max((
        abs(float(data.efc_pos[row])) * 1000.0 for row in range(data.ne)
        if model.eq_type[data.efc_id[row]] == mujoco.mjtEq.mjEQ_CONNECT
    ), default=0.0)
    driver_angles = [float(data.joint(f"gripper_{side}_driver_joint").qpos[0]) for side in ("right", "left")]
    gap1, gap2 = pad_gaps_mm(model, data)
    return {
        "time_s": float(data.time),
        "phase": phase,
        "command": float(data.actuator("gripper_fingers_actuator").ctrl[0]),
        "pad1_gap_mm": gap1,
        "pad2_gap_mm": gap2,
        "arm_drift_deg": float(np.rad2deg(np.max(np.abs(data.qpos[:6] - initial_arm)))),
        "max_joint_speed_deg_s": float(np.rad2deg(np.max(np.abs(data.qvel)))),
        "driver_mismatch_deg": math.degrees(abs(driver_angles[0] - driver_angles[1])),
        "joint_limit_overshoot_rad": limit_error,
        "linkage_residual_mm": link_error,
        "pad_penetration_mm": penetration,
    }


def check_path_sample(row):
    # Scene-specific tolerances, not certified physical robot limits.
    limits = {
        "arm_drift_deg": 2.0,
        "driver_mismatch_deg": 0.5,
        "joint_limit_overshoot_rad": 0.001,
        # Closing loads the soft four-bar constraints more than step 05's open hold.
        "linkage_residual_mm": 0.2,
        "pad_penetration_mm": 0.1,
    }
    for name, limit in limits.items():
        if not math.isfinite(row[name]) or row[name] > limit:
            code = (FailureCode.SIMULATION_INVALID if not math.isfinite(row[name]) else
                    FailureCode.MOTION_LIMIT_EXCEEDED if name in ("arm_drift_deg", "joint_limit_overshoot_rad")
                    else FailureCode.GRIPPER_STATE_INVALID)
            raise FailureEvent(code, f"{name}={row[name]:.9f} exceeded {limit} at {row['time_s']:.3f} s.",
                               violations=[{"metric": name, "observed": row[name], "maximum": limit,
                                            "sample_time_s": row["time_s"]}])


def run_cycle(model, data, viewer=None, trace_path=TRACE_PATH):
    """Advance physics via ctrl only; stream measurements, including partial trials."""
    if abs(float(data.time)) > 1e-9:
        raise ValueError("Start each trial from a fresh home state.")
    motor_id = model.actuator("gripper_fingers_actuator").id
    initial_ctrl = data.ctrl.copy()
    initial_arm = data.qpos[:6].copy()
    timestep = float(model.opt.timestep)
    steps = round(TOTAL_S / timestep)
    if not math.isclose(steps * timestep, TOTAL_S, abs_tol=1e-9):
        raise ValueError("Trial duration must be a whole number of physics steps.")
    frame_steps = max(1, round(1 / (60 * timestep)))
    started = time.monotonic()
    rows = []
    last_phase = None
    trace_path.parent.mkdir(parents=True, exist_ok=True)

    with trace_path.open("w", encoding="utf-8", newline="") as trace:
        writer = csv.DictWriter(trace, fieldnames=TRACE_COLUMNS)
        writer.writeheader()
        for step in range(steps + 1):
            if viewer is not None and not viewer.is_running():
                raise RuntimeError("Viewer closed before the complete open/close trial.")
            if step:
                command, phase = command_at(float(data.time))
                if not 0.0 <= command <= 255.0:
                    raise ValueError("Gripper command is outside 0..255.")
                # Retain all six arm targets. Never assign qpos during execution.
                data.ctrl[:] = initial_ctrl
                data.ctrl[motor_id] = command
                mujoco.mj_step(model, data)
                # Refresh contacts and body poses at the new joint positions.
                mujoco.mj_forward(model, data)
            else:
                phase = "open_hold"
            row = measure_state(model, data, initial_arm, phase)
            writer.writerow(row)
            rows.append(row)
            check_path_sample(row)
            if phase != last_phase:
                print(f"  {row['time_s']:6.3f} s: {phase}", flush=True)
                last_phase = phase
            if viewer is not None and (step % frame_steps == 0 or step == steps):
                viewer.sync()
                time.sleep(max(0.0, started + float(data.time) - time.monotonic()))
    return rows


def assess_cycle(rows):
    """Require measured closed AND reopened states; a successful command is insufficient."""
    if not rows or not math.isclose(rows[-1]["time_s"], TOTAL_S, abs_tol=1e-7):
        raise ValueError("The full 13-second trial was not recorded.")

    def window(end):
        selected = [r for r in rows if end - STABLE_WINDOW_S - 1e-9 <= r["time_s"] <= end + 1e-9]
        if not selected or selected[-1]["time_s"] - selected[0]["time_s"] < STABLE_WINDOW_S - 0.005:
            raise ValueError("An endpoint measurement window is incomplete.")
        return selected

    initial = window(HOLD_END_S)
    closed = window(CLOSED_HOLD_END_S)
    reopened = window(TOTAL_S)
    gap_keys = ("pad1_gap_mm", "pad2_gap_mm")
    initial_gaps = [float(np.mean([r[k] for r in initial])) for k in gap_keys]
    closed_max = max(r[k] for r in closed for k in gap_keys)
    reopened_min = min(r[k] for r in reopened for k in gap_keys)
    return_error = max(abs(r[k] - initial_gaps[i]) for r in reopened for i, k in enumerate(gap_keys))
    end_speed = max(r["max_joint_speed_deg_s"] for r in closed + reopened)
    checks = {
        "Initial and reopened pad gaps within 80..90 mm": all(80 <= r[k] <= 90 for r in initial + reopened for k in gap_keys),
        "Closed pad gaps within -0.1..2 mm": all(-0.1 <= r[k] <= 2 for r in closed for k in gap_keys),
        "Reopened gap differs from initial by <= 1 mm": return_error <= 1.0,
        "Closed/reopened window speed < 0.5 deg/s": end_speed < 0.5,
    }
    print(f"Initial open gaps: {initial_gaps[0]:.6f}, {initial_gaps[1]:.6f} mm")
    print(f"Closed-window largest gap: {closed_max:.6f} mm")
    print(f"Reopened-window smallest gap: {reopened_min:.6f} mm")
    print(f"Maximum return gap error: {return_error:.6f} mm")
    print(f"Endpoint-window maximum joint speed: {end_speed:.9f} deg/s")
    for key in ("arm_drift_deg", "driver_mismatch_deg", "joint_limit_overshoot_rad", "linkage_residual_mm", "pad_penetration_mm"):
        print(f"Maximum {key}: {max(r[key] for r in rows):.9f}")
    for label, passed in checks.items():
        print(f"  [{'PASS' if passed else 'FAIL'}] {label}")
    return all(checks.values())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--headless", action="store_true", help="Run the same physics without a viewer.")
    args = parser.parse_args()
    print("Gripper: hold open 1 s -> close 4 s -> hold 2 s -> open 4 s -> hold 2 s.", flush=True)
    print("Command 0..255 is a model control value, NOT millimeters or degrees.", flush=True)
    print(f"Measured trace (overwritten each run): {TRACE_PATH}", flush=True)
    try:
        model, data = load_robot_with_gripper()
        if args.headless:
            rows = run_cycle(model, data)
        else:
            with mujoco.viewer.launch_passive(model, data, show_left_ui=False, show_right_ui=False) as viewer:
                with viewer.lock():
                    set_camera(viewer.cam)
                    viewer.cam.lookat[:] = [0.05, -0.10, 0.23]
                    viewer.cam.distance = 1.0
                viewer.sync()
                print("Viewer opened; it closes after this 13-second trial.", flush=True)
                rows = run_cycle(model, data, viewer)
        passed = assess_cycle(rows)
        print("Path checks passed; only bounded contact between opposing finger pads is allowed.")
        print(f"Gripper open/close: {'PASS' if passed else 'FAIL'}")
        return 0 if passed else 1
    except (RuntimeError, ValueError) as error:
        print(f"Gripper open/close: FAIL ({error})")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
