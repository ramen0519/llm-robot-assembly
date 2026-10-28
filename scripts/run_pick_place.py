"""Run stages 12 (approach), 13 (pick) or 14 (pick and place) in MuJoCo."""

import argparse
from contextlib import nullcontext
import csv
import hashlib
import math
from pathlib import Path
import struct
import time
import zlib

import mujoco
import mujoco.viewer

from assembly_skills import AssemblySkills
from failure_report import (FailureCode, FailureEvent, FailureReport, TrialRecorder,
                            capture_observation, json_safe, utc_now, write_json)
from workcell_scene import DEFAULT_CONFIG, ROOT, build_workcell, load_config, set_workcell_camera


TRACE_FIELDS = ["stage", "simulation_time_s", "tcp_x_m", "tcp_y_m", "tcp_z_m",
                "part_x_m", "part_y_m", "part_z_m", "part_bottom_clearance_m",
                "part_speed_m_s", "part_angular_speed_rad_s", "arm_speed_rad_s",
                "tracking_error_rad", "robot_speed_rad_s", "target_position_error_m",
                "target_orientation_error_rad", "pad_gap_mm", "gripper_command", "supported",
                "left_contact", "right_contact", "penetration_m", "relative_slip_m",
                "relative_rotation_rad", "tcp_position_error_m", "tcp_orientation_error_rad"]


def save_snapshot(model, data, config, path):
    """Offscreen rendering of the actual physical state, with a standard-library PNG writer."""
    camera = mujoco.MjvCamera()
    set_workcell_camera(camera, config)
    camera.lookat[:] = [0, -0.02, config["table"]["top_center_world_m"][2] + 0.18]
    camera.lookat[:2] += config["table"]["top_center_world_m"][:2]
    camera.distance = 1.15
    camera.elevation = -27
    with mujoco.Renderer(model, height=720, width=960) as renderer:
        renderer.update_scene(data, camera=camera)
        rgb = renderer.render().copy()
    def chunk(kind, payload):
        return struct.pack("!I", len(payload)) + kind + payload + struct.pack("!I", zlib.crc32(kind + payload) & 0xffffffff)
    raw = b"".join(b"\x00" + row.tobytes() for row in rgb)
    Path(path).write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack("!2I5B", 960, 720, 8, 2, 0, 0, 0))
                           + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


class AssemblyRecorder:
    def __init__(self, output_root):
        folder = TrialRecorder(output_root)  # Reuse the unique run-directory convention only.
        self.run_dir, self.run_id = folder.run_dir, folder.run_id
        self.trace_path = self.run_dir / "skill_trace.csv"
        self.last_valid = None
        self.samples = 0

    def remember(self, skills, row):
        self.samples += 1
        observation = capture_observation(skills.model, skills.data)
        if observation["valid"]:
            observation["workcell"] = skills.observe()
            observation["measurements"] = json_safe(row)
            self.last_valid = observation

    def fail(self, error, skills, through):
        if isinstance(error, FailureEvent):
            event = error
        elif isinstance(error, KeyboardInterrupt):
            event = FailureEvent(FailureCode.CANCELLED, "Interrupted by user.", kinematics_fresh=False)
        elif isinstance(error, ValueError):
            event = FailureEvent(FailureCode.INPUT_OR_PRECONDITION_INVALID, str(error), kinematics_fresh=False)
        else:
            event = FailureEvent(FailureCode.UNCLASSIFIED_ERROR, str(error), kinematics_fresh=False)
        actual, goal = None, None
        phase = skills.motion.stage if skills else "setup"
        if skills:
            actual = capture_observation(skills.model, skills.data, kinematics_fresh=event.kinematics_fresh)
            if actual["valid"]:
                actual["workcell"] = skills.observe()
            goal = {"through_step": through, "current_motion_goal": skills.motion.goal,
                    "part_id": skills.config["part"]["id"], "target_id": skills.config["target"]["id"],
                    "desired_part_center_world_m": skills.verifier.target_position,
                    "desired_part_rotation_world": skills.verifier.target_rotation}
        status = "cancelled" if event.code == FailureCode.CANCELLED else "failed"
        report = FailureReport(
            schema_version=2, run_id=self.run_id, action_id=phase, skill="scripted_pick_place/v1",
            environment="simulation", status=status, failure_code=event.code, message=str(event),
            phase=event.phase or phase, detected_at_utc=utc_now(),
            simulation_time_s=float(skills.data.time) if skills else None,
            validation_profile="one_box_pick_place/v1", goal=goal, actual=actual,
            last_valid_observation=self.last_valid, candidate=event.candidate,
            violations=event.violations, exception_type=type(error.__cause__ or error).__name__,
            cause_hypotheses=[], response="stop_without_retry")
        write_json(self.run_dir / "failure_report.json", report)
        return status


def execute_trial(config_path=DEFAULT_CONFIG, *, through=14, headless=True, snapshots=False,
                  output_root=ROOT / "runs/step12_14", on_stage=None):
    if through not in (12, 13, 14):
        raise ValueError("through must be 12, 13 or 14")
    recorder = AssemblyRecorder(output_root)
    skills = None
    status, error_message = "failed", None
    try:
        config_path = Path(config_path)
        config = load_config(config_path)
        model, data = build_workcell(config)
        write_json(recorder.run_dir / "resolved_config.json", {
            "config": config, "config_file": str(config_path.resolve()),
            "sha256": hashlib.sha256(config_path.read_bytes()).hexdigest(),
            "simulator": "MuJoCo", "version": mujoco.__version__, "timestep_s": float(model.opt.timestep),
            "robot": "ur3e", "gripper": "robotiq_2f85", "through_step": through,
            "environment": "simulation", "observation_source": "sim_ground_truth"})
        context = nullcontext(None) if headless else mujoco.viewer.launch_passive(model, data)
        with context as viewer, recorder.trace_path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=TRACE_FIELDS, extrasaction="raise")
            writer.writeheader()
            clock = {"wall": time.monotonic(), "sim": 0., "last_draw": -1.}
            if viewer:
                with viewer.lock():
                    set_workcell_camera(viewer.cam, config)
            def sample(row):
                writer.writerow(row)
                recorder.remember(skills, row)
                if viewer:
                    if not viewer.is_running():
                        raise FailureEvent(FailureCode.CANCELLED, "Viewer closed during execution.")
                    if data.time - clock["last_draw"] >= 1 / 60:
                        viewer.sync()
                        time.sleep(max(0., clock["wall"] + data.time - clock["sim"] - time.monotonic()))
                        clock["last_draw"] = float(data.time)
            def stage(name):
                print(f"[{data.time:7.3f} s] {name}", flush=True)
                if on_stage:
                    on_stage(name, skills)
                if snapshots and name in ("approach_hover", "place_transfer", "place_verify"):
                    save_snapshot(model, data, config, recorder.run_dir / f"{name}.png")
                # Planning/render time does not count as physical time in the viewer.
                clock["wall"], clock["sim"] = time.monotonic(), float(data.time)
            skills = AssemblySkills(model, data, config, sample, stage)
            skills.settle()
            part_id = config["part"]["id"]
            if through == 12:
                skills.approach(part_id)
            else:
                skills.pick(part_id)
                if through == 14:
                    skills.place(part_id, config["target"]["id"])
            if snapshots:
                save_snapshot(model, data, config, recorder.run_dir / "final.png")
            status = "success"
            if viewer:
                print("Verification complete. Static final preview; close the window to finish.", flush=True)
                try:
                    while viewer.is_running():
                        viewer.sync()
                        time.sleep(1 / 30)
                except KeyboardInterrupt:
                    pass
    except (Exception, KeyboardInterrupt) as error:
        error_message = f"{type(error).__name__}: {error}"
        print(error_message, flush=True)
        try:
            status = recorder.fail(error, skills, through)
        except Exception as storage_error:
            raise RuntimeError(f"Original failure: {error_message}; could not save failure report: {storage_error}") from error
    result = {"schema_version": 2, "run_id": recorder.run_id, "environment": "simulation",
              "through_step": through, "status": status, "finished_at_utc": utc_now(),
              "error": error_message, "trace_file": recorder.trace_path.name,
              "failure_report_file": None if status == "success" else "failure_report.json",
              "validated_samples": recorder.samples,
              "simulation_time_s": float(skills.data.time) if skills else None,
              "completed_skills": skills.results if skills else [],
              "plans": skills.motion.plans if skills else [],
              "final_observation": skills.observe() if status == "success" else None}
    write_json(recorder.run_dir / "result.json", result)
    print(f"Step {through}: {status.upper()} | {recorder.run_dir}", flush=True)
    return json_safe(result), recorder.run_dir


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--through", type=int, choices=(12, 13, 14), default=14)
    parser.add_argument("--check", action="store_true", help="Headless physical simulation and verification.")
    parser.add_argument("--snapshots", action="store_true", help="Save real MuJoCo rendered states under ignored runs/.")
    args = parser.parse_args()
    result, _ = execute_trial(args.config, through=args.through, headless=args.check, snapshots=args.snapshots)
    return 0 if result["status"] == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
