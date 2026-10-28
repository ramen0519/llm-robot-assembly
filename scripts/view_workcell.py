"""Check a configurable table, mounted robot and freely settling test part."""

import argparse
from contextlib import nullcontext
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import time
import uuid

import mujoco
import mujoco.viewer
import numpy as np

from failure_report import write_json
from move_gripper import pad_gaps_mm
from move_tcp import ArmPositionController
from ur3e_with_gripper import mount_error
from workcell_scene import (DEFAULT_CONFIG, ROOT, TABLE_GEOM, build_workcell, load_config,
                            observe_workcell, part_name, set_workcell_camera, yaw_rotation)


DURATION_S, STABLE_WINDOW_S = 3.0, 0.5
# Scene-settling tolerances only, not grasp/insertion precision or real material deformation.
MAX_TRANSIENT_PENETRATION_M = 0.003
MAX_SETTLED_PENETRATION_M = 0.001


def check_workcell(model, data, config, viewer=None):
    """Only hold the robot and let the part settle. No pick/place, resets or attachments."""
    if data.time != 0:
        raise ValueError("Start a new workcell trial at simulation time zero.")
    timestep = float(model.opt.timestep)
    steps = round(DURATION_S / timestep)
    if timestep <= 0 or timestep > 0.002 + 1e-12 or not math.isclose(steps * timestep, DURATION_S, abs_tol=1e-9):
        raise ValueError("Use a positive timestep <= 0.002 s that divides this trial exactly.")
    controller = ArmPositionController(model)
    initial_arm = data.qpos[:6].copy()
    allowed_contact = {model.geom(TABLE_GEOM).id, model.geom(part_name(config) + "_geom").id}
    initial_part_rotation = yaw_rotation(config["table"]["yaw_deg"] + config["part"]["yaw_deg"])
    settled_height = config["part"]["size_m"][2] / 2
    stable = []
    first_support_s = None
    peak_arm_error = peak_penetration = 0.0
    frame_steps = max(1, round(1 / (60 * timestep)))
    started = time.monotonic()
    for step in range(steps + 1):
        if viewer is not None and not viewer.is_running():
            raise RuntimeError("Viewer closed before the 3-second workcell check completed.")
        with viewer.lock() if viewer is not None else nullcontext():
            if step:
                controller.apply(data, initial_arm)
                mujoco.mj_step(model, data)
                mujoco.mj_forward(model, data)
            if not math.isclose(data.time, step * timestep, abs_tol=1e-8):
                raise RuntimeError("Physics time did not advance as expected.")
            if not all(np.all(np.isfinite(v)) for v in (data.qpos, data.qvel, data.ctrl)):
                raise RuntimeError("Non-finite physics state.")
            if any(w.number for w in data.warning):
                raise RuntimeError("MuJoCo reported a physics warning.")
            if np.any(data.qfrc_applied) or np.any(data.xfrc_applied):
                raise RuntimeError("External perturbations invalidate this settling check.")
            mount_error(model, data)
            supported = False
            penetration = 0.0
            for contact in data.contact:
                if {int(contact.geom1), int(contact.geom2)} != allowed_contact:
                    names = [model.geom(int(g)).name or f"geom#{g}" for g in (contact.geom1, contact.geom2)]
                    raise RuntimeError(f"Unexpected workcell contact at {data.time:.3f} s: {names}")
                supported = supported or contact.dist <= 0
                penetration = max(penetration, -float(contact.dist))
                if penetration > MAX_TRANSIENT_PENETRATION_M:
                    raise RuntimeError("Transient part/table penetration exceeded 3 mm.")
            peak_penetration = max(peak_penetration, penetration)
            if supported and first_support_s is None:
                first_support_s = float(data.time)
            limited = model.jnt_limited.astype(bool)
            angles = data.qpos[model.jnt_qposadr[limited]]
            bounds = model.jnt_range[limited]
            if np.any(angles < bounds[:, 0] - 0.001) or np.any(angles > bounds[:, 1] + 0.001):
                raise RuntimeError("Robot/gripper joint limit overshoot exceeded 0.001 rad.")
            for row in range(data.ne):
                if model.eq_type[data.efc_id[row]] == mujoco.mjtEq.mjEQ_CONNECT and abs(data.efc_pos[row]) > 0.0002:
                    raise RuntimeError("Gripper linkage residual exceeded 0.2 mm.")
            if not all(80 <= gap <= 90 for gap in pad_gaps_mm(model, data)):
                raise RuntimeError("The empty gripper did not remain open.")
            arm_error = float(np.rad2deg(np.max(np.abs(data.qpos[:6] - initial_arm))))
            peak_arm_error = max(peak_arm_error, arm_error)
            if arm_error > 2.0:
                raise RuntimeError("The held arm drifted by more than 2 degrees.")
            observation = observe_workcell(model, data, config)
            if data.time >= DURATION_S - STABLE_WINDOW_S - 1e-9:
                part = observation["part"]
                delta_rotation = initial_part_rotation.T @ data.body(part_name(config)).xmat.reshape(3, 3)
                stable.append({
                    "supported": supported,
                    "penetration_m": penetration,
                    "height_error_m": abs(part["position_table_m"][2] - settled_height),
                    "xy_error_m": float(np.linalg.norm(part["position_table_m"][:2] - config["part"]["spawn_xy_table_m"])),
                    "orientation_error_deg": math.degrees(math.acos(float(np.clip((np.trace(delta_rotation) - 1) / 2, -1, 1)))),
                    "linear_speed_m_s": part["linear_speed_m_s"],
                    "angular_speed_rad_s": part["angular_speed_rad_s"],
                    "robot_speed_deg_s": float(np.rad2deg(np.max(np.abs(data.qvel[:14])))),
                })
        if viewer is not None and (step % frame_steps == 0 or step == steps):
            viewer.sync()
            time.sleep(max(0.0, started + float(data.time) - time.monotonic()))
    maxima = {key: float(max(row[key] for row in stable)) for key in stable[0] if key != "supported"}
    checks = {
        "supported_in_entire_final_window": all(row["supported"] for row in stable),
        "settled_penetration_under_1mm": maxima["penetration_m"] < MAX_SETTLED_PENETRATION_M,
        "height_error_under_1mm": maxima["height_error_m"] < 0.001,
        "horizontal_drift_under_1mm": maxima["xy_error_m"] < 0.001,
        "orientation_error_under_0.5deg": maxima["orientation_error_deg"] < 0.5,
        "part_linear_speed_under_1mm_s": maxima["linear_speed_m_s"] < 0.001,
        "part_angular_speed_under_0.01rad_s": maxima["angular_speed_rad_s"] < 0.01,
        "robot_settled_under_0.5deg_s": maxima["robot_speed_deg_s"] < 0.5,
    }
    return {"status": "success" if all(checks.values()) else "failed", "profile": "workcell_settle/v1",
            "duration_s": float(data.time), "stable_window_s": [DURATION_S - STABLE_WINDOW_S, DURATION_S],
            "first_support_s": first_support_s, "peak_arm_error_deg": peak_arm_error,
            "peak_penetration_m": peak_penetration,
            "penetration_limits_m": {"transient": MAX_TRANSIENT_PENETRATION_M, "settled": MAX_SETTLED_PENETRATION_M},
            "final_window_maxima": maxima, "checks": checks, "observation": observation}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="Workcell TOML profile.")
    parser.add_argument("--check", action="store_true", help="Run physics and validation without opening a viewer.")
    args = parser.parse_args()
    try:
        config = load_config(args.config)
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ") + "_" + uuid.uuid4().hex[:8]
        output = ROOT / "runs/step11" / run_id
        output.mkdir(parents=True, exist_ok=False)
        write_json(output / "resolved_config.json", config)
        print(f"Profile: {config['profile_name']} ({config['measurement_status']})")
        print(f"Configuration: {args.config.resolve()}")
        print(f"Run directory: {output}", flush=True)
        try:
            model, data = build_workcell(config)
            if args.check:
                report = check_workcell(model, data, config)
            else:
                with mujoco.viewer.launch_passive(model, data, show_left_ui=False, show_right_ui=False) as viewer:
                    with viewer.lock():
                        set_workcell_camera(viewer.cam, config)
                    report = check_workcell(model, data, config, viewer)
                    # Save before inspection; later GUI interaction is not part of this trial.
                    save_report(output, report, config)
                    print(f"Physics check: {report['status']}. Static inspection; close the viewer to exit.", flush=True)
                    try:
                        while viewer.is_running():
                            viewer.sync()
                            time.sleep(1 / 60)
                    except KeyboardInterrupt:
                        pass  # Closing static inspection does not cancel an already completed trial.
        except (ValueError, RuntimeError, KeyboardInterrupt) as error:
            report = {"status": "cancelled" if isinstance(error, KeyboardInterrupt) else "failed", "error": str(error)}
        save_report(output, report, config)
        for name, passed in report.get("checks", {}).items():
            print(f"  {'PASS' if passed else 'FAIL'}: {name}")
        if "error" in report:
            print(report["error"])
        print("Workcell setup: " + ("PASS" if report["status"] == "success" else "FAIL"))
        print("This checks layout and settling only. No grasp, insertion, reachability or hardware motion is verified.")
        return 0 if report["status"] == "success" else 1
    except (ValueError, OSError) as error:
        print(f"Workcell setup: FAIL ({error})")
        return 1


def save_report(output, report, config):
    report = dict(report, environment="simulation", measurement_status=config["measurement_status"],
                  config_file="resolved_config.json", simulator_version=mujoco.__version__,
                  config_sha256=hashlib.sha256(json.dumps(config, sort_keys=True).encode("utf-8")).hexdigest())
    write_json(output / "workcell_check.json", report)


if __name__ == "__main__":
    raise SystemExit(main())
