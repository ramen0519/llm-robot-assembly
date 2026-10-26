"""Display the mounted UR3e/2F-85 and check a short gravity-loaded hold."""

import argparse
import math
import time

import mujoco
import mujoco.viewer
import numpy as np

from ur3e_with_gripper import load_robot_with_gripper, mount_error
from view_ur3e import set_camera


def run_hold(model, data, seconds, viewer=None):
    initial_arm = data.qpos[:6].copy()
    timestep = float(model.opt.timestep)
    steps = math.ceil(seconds / timestep)
    duration = steps * timestep
    frame_steps = max(1, round(1 / (60 * timestep)))
    started = time.monotonic()
    max_drift = max_speed = max_mount_error = max_limit_error = max_link_error = 0.0

    for step in range(steps + 1):
        if viewer is not None and not viewer.is_running():
            raise RuntimeError("Viewer closed before the hold check finished.")
        if step:
            # Hold home arm targets and gripper command 0; no open/close trajectory yet.
            mujoco.mj_step(model, data)
            mujoco.mj_forward(model, data)

        if not np.all(np.isfinite(data.qpos)) or not np.all(np.isfinite(data.qvel)):
            raise RuntimeError("Non-finite joint state.")
        if any(warning.number for warning in data.warning):
            raise RuntimeError("MuJoCo reported a physics warning.")
        if any(contact.dist <= 0 for contact in data.contact):
            raise RuntimeError("Unexpected contact in the empty mounting scene.")
        max_mount_error = max(max_mount_error, mount_error(model, data))
        max_drift = max(max_drift, float(np.rad2deg(np.max(np.abs(data.qpos[:6] - initial_arm)))))

        # MuJoCo joint stops are soft. Permit at most 0.001 rad of limit overshoot.
        q = data.qpos[model.jnt_qposadr]
        violation = np.maximum(model.jnt_range[:, 0] - q, q - model.jnt_range[:, 1])
        max_limit_error = max(max_limit_error, float(np.max(violation[model.jnt_limited != 0])))
        if max_limit_error > 0.001:
            raise RuntimeError("Joint limit overshoot exceeded 0.001 rad.")

        # The connect constraints keep the two four-bar finger linkages together.
        for row in range(data.ne):
            constraint_id = data.efc_id[row]
            if model.eq_type[constraint_id] == mujoco.mjtEq.mjEQ_CONNECT:
                max_link_error = max(max_link_error, abs(float(data.efc_pos[row])))
        if max_link_error > 0.0001:
            raise RuntimeError("Finger linkage residual exceeded 0.1 mm.")
        if data.time >= duration - 0.5 - 1e-9:
            max_speed = max(max_speed, float(np.rad2deg(np.max(np.abs(data.qvel)))))

        if viewer is not None and (step % frame_steps == 0 or step == steps):
            viewer.sync()
            time.sleep(max(0.0, started + float(data.time) - time.monotonic()))

    if max_drift > 2.0 or max_speed >= 0.5:
        raise RuntimeError(f"Hold not stable: arm drift={max_drift:.6f} deg, speed={max_speed:.6f} deg/s.")
    return {
        "duration_s": float(data.time),
        "arm_drift_deg": max_drift,
        "final_window_speed_deg_s": max_speed,
        "mount_position_error_m": max_mount_error,
        "joint_limit_overshoot_rad": max_limit_error,
        "linkage_residual_m": max_link_error,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Run the hold check without a viewer.")
    parser.add_argument("--seconds", type=float, default=5.0, help="Hold duration; at least 1 second.")
    args = parser.parse_args()
    if not math.isfinite(args.seconds) or args.seconds < 1:
        parser.error("--seconds must be finite and at least 1")

    try:
        model, data = load_robot_with_gripper()
        mass = sum(
            float(model.body_mass[i]) for i in range(model.nbody)
            if model.body(i).name.startswith("gripper_")
        )
        print(f"Joints: {model.njnt} (6 arm + 8 gripper); controls: {model.nu} (6 + 1)", flush=True)
        print(f"Gripper model mass: {mass:.6f} kg; gripper command: 0 (open)", flush=True)
        print(f"Gravity-loaded hold: {args.seconds:g} s. No opening/closing sequence.", flush=True)
        if args.check:
            report = run_hold(model, data, args.seconds)
        else:
            with mujoco.viewer.launch_passive(
                model, data, show_left_ui=False, show_right_ui=False,
            ) as viewer:
                with viewer.lock():
                    set_camera(viewer.cam)
                    viewer.cam.lookat[:] = [0.05, -0.10, 0.23]
                    viewer.cam.distance = 1.1
                viewer.sync()
                print("Mounted-gripper viewer opened.", flush=True)
                report = run_hold(model, data, args.seconds, viewer)
        for name, value in report.items():
            print(f"  {name}: {value:.9f}")
        print("No unexpected contact or engine warning.")
        print("Gripper mount and hold: PASS")
        return 0
    except (ValueError, RuntimeError) as error:
        print(f"Gripper mount and hold: FAIL ({error})")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
