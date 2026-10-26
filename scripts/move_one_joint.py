"""Move only the UR3e shoulder-pan target and verify the measured arrival."""

import argparse
import csv
import math
from pathlib import Path
import time

import mujoco
import mujoco.viewer

from view_ur3e import load_robot, set_camera


JOINT_NAME = "shoulder_pan_joint"
ACTUATOR_NAME = "shoulder_pan"
TARGET_DEG = -60.0
INITIAL_HOLD_S = 1.0
MOVE_S = 4.0
SETTLE_S = 2.0
TOTAL_S = INITIAL_HOLD_S + MOVE_S + SETTLE_S
STABLE_WINDOW_S = 0.5
ANGLE_TOL_DEG = 0.5
SPEED_TOL_DEG_S = 0.5
PEAK_SPEED_LIMIT_DEG_S = 15.0
OTHER_JOINT_TOL_DEG = 2.0
TRACE_PATH = Path(__file__).resolve().parents[1] / "runs/step04/one_joint.csv"
TRACE_COLUMNS = (
    "time_s", "command_deg", "actual_deg", "speed_deg_s", "other_joint_error_deg",
)


def commanded_angle(time_s, start_rad, target_rad):
    # Smoothstep starts and ends with zero reference velocity.
    progress = min(1.0, max(0.0, (time_s - INITIAL_HOLD_S) / MOVE_S))
    blend = 3 * progress**2 - 2 * progress**3
    return start_rad + (target_rad - start_rad) * blend


def check_state(model, data):
    """Reject invalid physics, joint-limit violations and contact in this empty scene."""
    if not all(math.isfinite(float(x)) for x in (*data.qpos, *data.qvel)):
        raise RuntimeError("Non-finite joint state.")
    if any(warning.number for warning in data.warning):
        raise RuntimeError("MuJoCo reported a simulation warning.")
    for joint_id in range(model.njnt):
        angle = float(data.qpos[model.jnt_qposadr[joint_id]])
        lower, upper = model.jnt_range[joint_id]
        if model.jnt_limited[joint_id] and not lower <= angle <= upper:
            raise RuntimeError(f"Joint limit exceeded: {model.joint(joint_id).name}")
    for contact in data.contact:
        if contact.dist <= 0:
            raise RuntimeError("Unexpected robot contact in the empty test scene.")


def run_motion(model, data, viewer=None):
    joint_id = model.joint(JOINT_NAME).id
    actuator_id = model.actuator(ACTUATOR_NAME).id
    qadr = int(model.jnt_qposadr[joint_id])
    vadr = int(model.jnt_dofadr[joint_id])
    if model.actuator_trnid[actuator_id, 0] != joint_id:
        raise ValueError("The actuator must drive the selected joint.")
    if abs(float(data.time)) > 1e-9:
        raise ValueError("Start each trial from a fresh home state.")

    initial_qpos = data.qpos.copy()
    start_rad = float(initial_qpos[qadr])
    target_rad = math.radians(TARGET_DEG)
    for limits in (model.jnt_range[joint_id], model.actuator_ctrlrange[actuator_id]):
        if not limits[0] <= target_rad <= limits[1]:
            raise ValueError("Target is outside this model's joint/control limits.")

    timestep = float(model.opt.timestep)
    step_count = round(TOTAL_S / timestep)
    if not math.isclose(step_count * timestep, TOTAL_S, abs_tol=1e-9):
        raise ValueError("Trial duration must be a whole number of physics steps.")
    frame_steps = max(1, round(1 / (60 * timestep)))
    rows = []
    wall_start = time.monotonic()
    check_state(model, data)

    for step in range(step_count):
        if viewer is not None and not viewer.is_running():
            raise RuntimeError("Viewer closed before the trial finished.")

        # This model uses a position servo: ctrl is a target angle in radians.
        # Other actuator targets keep their initial home values.
        data.ctrl[actuator_id] = commanded_angle(float(data.time), start_rad, target_rad)
        mujoco.mj_step(model, data)
        # Refresh geometry/contact data at the newly integrated joint positions.
        mujoco.mj_forward(model, data)
        check_state(model, data)

        other_error = max(
            abs(math.degrees(float(data.qpos[i] - initial_qpos[i])))
            for i in range(model.nq) if i != qadr
        )
        rows.append((
            float(data.time),
            math.degrees(float(data.ctrl[actuator_id])),
            math.degrees(float(data.qpos[qadr])),
            math.degrees(float(data.qvel[vadr])),
            other_error,
        ))

        if viewer is not None and (step % frame_steps == 0 or step == step_count - 1):
            viewer.sync()
            # Pace the viewer; headless runs calculate the same physics without waiting.
            time.sleep(max(0.0, wall_start + float(data.time) - time.monotonic()))

    return rows


def assess_motion(rows):
    """Use measured angles and velocities, not the command, to judge arrival."""
    if not rows:
        raise ValueError("No measurements were recorded.")
    stable = [row for row in rows if row[0] >= TOTAL_S - STABLE_WINDOW_S - 1e-9]
    if not stable:
        raise ValueError("The final stability window was not reached.")
    window_error = max(abs(row[2] - TARGET_DEG) for row in stable)
    window_speed = max(abs(row[3]) for row in stable)
    peak_speed = max(abs(row[3]) for row in rows)
    other_error = max(row[4] for row in rows)
    checks = {
        "Full 7-second trial": math.isclose(rows[-1][0], TOTAL_S, abs_tol=1e-7),
        "Final-window angle error <= 0.5 deg": window_error <= ANGLE_TOL_DEG,
        "Final-window speed < 0.5 deg/s": window_speed < SPEED_TOL_DEG_S,
        "Peak speed <= 15 deg/s": peak_speed <= PEAK_SPEED_LIMIT_DEG_S,
        "Other-joint drift <= 2 deg": other_error <= OTHER_JOINT_TOL_DEG,
    }
    print(f"Final actual angle: {rows[-1][2]:.6f} deg")
    print(f"Final-window max error: {window_error:.6f} deg")
    print(f"Final-window max speed: {window_speed:.6f} deg/s")
    print(f"Peak measured speed: {peak_speed:.6f} deg/s")
    print(f"Max other-joint drift: {other_error:.6f} deg")
    for label, passed in checks.items():
        print(f"  [{'PASS' if passed else 'FAIL'}] {label}")
    return all(checks.values())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--headless", action="store_true", help="Run without opening a viewer.")
    args = parser.parse_args()
    model, data = load_robot()
    print(f"Joint: {JOINT_NAME}; target: {TARGET_DEG:.1f} deg", flush=True)
    print("Hold 1 s -> smooth move 4 s -> settle 2 s.", flush=True)
    print("Other joints keep their home targets. Physics timestep: 0.002 s.", flush=True)

    try:
        if args.headless:
            rows = run_motion(model, data)
        else:
            with mujoco.viewer.launch_passive(
                model, data, show_left_ui=False, show_right_ui=False,
            ) as viewer:
                with viewer.lock():
                    set_camera(viewer.cam)
                viewer.sync()
                print("Viewer opened. It closes after one trial.", flush=True)
                rows = run_motion(model, data, viewer)

        TRACE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with TRACE_PATH.open("w", newline="", encoding="utf-8") as trace:
            writer = csv.writer(trace)
            writer.writerow(TRACE_COLUMNS)
            writer.writerows(rows)
        passed = assess_motion(rows)
        print("Path checks: no contact, joint-limit violation or engine warning.")
        print(f"Trace: {TRACE_PATH}")
        print(f"One-joint motion: {'PASS' if passed else 'FAIL'}")
        return 0 if passed else 1
    except (RuntimeError, ValueError) as error:
        print(f"One-joint motion: FAIL ({error})")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
