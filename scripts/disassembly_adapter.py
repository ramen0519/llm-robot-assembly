"""Read an explicit-action planner report as an assembly candidate, never a robot command."""

import argparse
import hashlib
import json
import math
from pathlib import Path

import numpy as np

from failure_report import TrialRecorder, utc_now, write_json
from workcell_scene import ROOT


class PlanContractError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise PlanContractError(message)


def identifiers(value, label):
    require(isinstance(value, list) and all(type(x) is str and 0 < len(x) <= 512 for x in value),
            f"{label}: expected nonempty string IDs")
    require(len(value) == len(set(value)), f"{label}: duplicate IDs")
    return value


def rigid_transform(value, label):
    require(isinstance(value, list) and len(value) == 4
            and all(isinstance(row, list) and len(row) == 4 for row in value),
            f"{label}: expected a 4x4 matrix")
    require(all(type(x) in (int, float) and math.isfinite(x) for row in value for x in row),
            f"{label}: non-finite or non-numeric matrix")
    matrix = np.asarray(value, dtype=float)
    rotation = matrix[:3, :3]
    require(np.allclose(matrix[3], [0, 0, 0, 1], atol=1e-6, rtol=0)
            and np.allclose(rotation.T @ rotation, np.eye(3), atol=1e-5, rtol=0)
            and abs(np.linalg.det(rotation) - 1) <= 1e-5,
            f"{label}: scale, shear or reflection is not a rigid transform")
    return matrix


def _object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def read_json(path):
    path = Path(path)
    require(path.is_file() and path.stat().st_size <= 32 * 1024 * 1024,
            "Expected an existing JSON file no larger than 32 MiB")
    raw = path.read_bytes()
    def reject_constant(value):
        raise PlanContractError(f"Non-finite JSON constant: {value}")
    return json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_object,
                      parse_constant=reject_constant), hashlib.sha256(raw).hexdigest()


def candidate_from_report(report, *, source_sha256):
    require(isinstance(report, dict) and type(report.get("schema_version")) is int
            and report["schema_version"] == 1, "Unsupported planner report schema")
    require(report.get("status") == "PASS" and report.get("success") is True,
            "A partial, UNKNOWN, REJECTED or ERROR result is not a complete assembly candidate")
    require(report.get("selected_plan_continuity_checked") is True
            and report.get("selected_plan_continuity_errors") == [],
            "Planner did not report an accepted continuous solution")
    selected = report.get("selected_plan")
    require(isinstance(selected, list) and 1 <= len(selected) <= 255, "Missing selected plan")
    require(all(isinstance(edge, dict) for edge in selected), "Invalid selected edge")
    require(isinstance(report.get("run_id"), str) and bool(report["run_id"]), "Missing run ID")
    require(isinstance(source_sha256, str) and len(source_sha256) == 64
            and all(c in "0123456789abcdef" for c in source_sha256), "Invalid report digest")
    initial = identifiers(selected[0].get("remaining_parts"), "initial parts").copy()
    require(len(initial) >= 2, "Need at least two initial parts")
    remaining, removed, source_steps = initial.copy(), [], []
    event_ids = set()
    source_task = None
    require(isinstance(selected[0].get("context"), dict), "Missing initial context")
    fixed_base = selected[0]["context"].get("base_part")
    require(fixed_base is None or fixed_base in initial, "Invalid fixed base")
    for index, attempt in enumerate(selected):
        require(isinstance(attempt, dict), "Invalid attempt")
        require(attempt.get("status") == "PASS" and attempt.get("accepted_for_transition") is True
                and attempt.get("deadline_exceeded") is False, "Unaccepted or late selected edge")
        context, choice, raw = attempt.get("context"), attempt.get("choice"), attempt.get("raw")
        require(all(isinstance(x, dict) for x in (context, choice, raw)), "Missing edge evidence")
        require(attempt.get("remaining_parts") == remaining and context.get("remaining_parts") == remaining,
                "Disassembly state continuity mismatch")
        part = choice.get("part_id")
        require(part in remaining and part != fixed_base, "Invalid or repeated removed part")
        child = [p for p in remaining if p != part]
        require(context.get("parts_rest") == child and context.get("base_part") == fixed_base,
                "Rest parts or fixed base changed")
        require(set(identifiers(context.get("parts_removed"), "removed parts")) == set(removed),
                "Removed-parts history mismatch")
        supports = identifiers(choice.get("support_ids"), "support IDs")
        require(set(supports).issubset(child), "Support is not a remaining part")
        require(choice.get("skill_id") in ("single_force_v1", "turn_force_two_leg_v1"),
                "Unsupported source path skill")
        require(choice.get("direction_id") in ("xp", "xn", "yp", "yn", "zp", "zn"),
                "Unknown source force direction")
        second = choice.get("second_direction_id")
        require((choice["skill_id"] == "single_force_v1" and second is None)
                or (choice["skill_id"] == "turn_force_two_leg_v1"
                    and second in ("xp", "xn", "yp", "yn", "zp", "zn")),
                "Invalid second force direction")
        require(raw.get("status") == "PASS" and raw.get("feasible") is True
                and raw.get("backend") == "ASAP_RedMax" and raw.get("path_success") is True,
                "Selected edge lacks successful ASAP/RedMax path evidence")
        event_id = attempt.get("event_id")
        require(isinstance(event_id, str) and event_id and event_id not in event_ids,
                "Missing or duplicate event ID")
        event_ids.add(event_id)
        require(all(isinstance(attempt.get(key), str) and attempt[key] for key in ("action_id", "state_id")),
                "Missing action/state identity")
        require(context.get("run_id") == report["run_id"]
                and context.get("action_id") == attempt.get("action_id")
                and context.get("state_id") == attempt.get("state_id"), "Edge identity mismatch")
        require(isinstance(context.get("task_context"), dict), "Missing task context")
        task = context["task_context"].get("task")
        require(isinstance(task, dict) and isinstance(task.get("assembly_id"), str)
                and task.get("assembly_id"), "Missing source assembly identity")
        if source_task is None:
            source_task = task
        require(task == source_task, "Source CAD/configuration identity changed within plan")
        matrix = rigid_transform(context.get("pose_matrix"), "selected assembly pose")
        catalog = context.get("pose_catalog")
        require(isinstance(catalog, list) and all(isinstance(p, dict) for p in catalog),
                "Missing or malformed source pose catalog")
        matches = [p for p in catalog if p.get("pose_id") == choice.get("pose_id")]
        require(len(matches) == 1 and np.allclose(
            rigid_transform(matches[0].get("matrix"), "catalog pose"), matrix, atol=1e-6, rtol=0),
            "Selected pose does not match its source catalog")
        source_steps.append({
            "source_step": index + 1, "part_id": part, "required_present_parts": child,
            "source_support_ids": supports.copy(), "source_pose_id": choice["pose_id"],
            "source_pose_matrix": matrix.tolist(),
            "source_force_skill": choice["skill_id"], "source_force_direction_id": choice["direction_id"],
            "source_second_force_direction_id": second,
            "evidence": {"event_id": event_id, "action_id": attempt["action_id"],
                         "backend": raw["backend"], "status": raw["status"],
                         "path_success": raw["path_success"],
                         "stability_success": raw.get("stability_success"),
                         "stability_check_calls": raw.get("stability_check_calls")},
        })
        removed.append(part)
        remaining = child
    require(len(remaining) == 1 and report.get("terminal_parts") == remaining,
            "Plan does not end with the declared singleton seed")
    require(isinstance(report.get("stats"), dict) and report["stats"].get("sequence") == removed,
            "Selected plan disagrees with source sequence")
    return {
        "schema_version": 1, "kind": "assembly_candidate", "status": "candidate",
        "robot_execution_validated": False, "coordinate_binding_validated": False,
        "source": {"format": "explicit_action_v1", "run_id": report["run_id"],
                   "report_sha256": source_sha256, "assembly_id": source_task["assembly_id"],
                   "source_commit": source_task.get("source_commit"),
                   "input_sha256": source_task.get("input_sha256", {}),
                   "length_scale_m": None, "frame": None},
        "part_ids": initial, "seed_part_id": remaining[0], "source_fixed_base": fixed_base,
        "disassembly_order": removed,
        "assembly_steps": [dict(step, assembly_step=i + 1)
                           for i, step in enumerate(reversed(source_steps))],
        "required_robot_checks": [
            "explicit_length_scale_and_frames", "assembled_part_target_poses",
            "grasp_and_tool_transform", "single_arm_fixture_and_supports",
            "physical_reorientation_between_source_poses",
            "collision_free_robot_gripper_carried_part_paths", "physical_insertion_and_goal_verification"],
        "limitations": [
            "Reversed removal order is a candidate, not a robot trajectory.",
            "Source force directions are preserved, not converted into TCP displacement.",
            "Source stability/path evidence does not establish robot grasp or assembly success."],
    }


def bind_coordinates(candidate, binding):
    """Validate an explicit coordinate manifest; still not execution approval."""
    require(isinstance(binding, dict) and type(binding.get("schema_version")) is int
            and binding["schema_version"] == 1, "Invalid binding schema")
    require(binding.get("source_report_sha256") == candidate["source"]["report_sha256"],
            "Binding refers to a different report")
    scale = binding.get("source_length_scale_m")
    require(type(scale) in (int, float) and math.isfinite(scale) and scale > 0,
            "An explicit positive metres-per-source-unit scale is required")
    frame = binding.get("source_frame")
    require(isinstance(frame, str) and bool(frame.strip()), "Explicit source frame is required")
    transform = rigid_transform(binding.get("world_from_source_m"), "world-from-source transform")
    mapping = binding.get("part_map")
    require(isinstance(mapping, dict) and set(mapping) == set(candidate["part_ids"]),
            "Part mapping must cover all source parts")
    mapped = identifiers(list(mapping.values()), "mapped part IDs")
    poses = binding.get("assembled_part_poses_world_m")
    require(isinstance(poses, dict) and set(poses) == set(mapped), "Missing assembled target poses")
    for name, pose in poses.items():
        rigid_transform(pose, f"assembled pose {name}")
    result = json.loads(json.dumps(candidate, allow_nan=False))
    result["coordinate_binding_validated"] = True
    result["source"].update(length_scale_m=float(scale), frame=frame)
    result["binding"] = json.loads(json.dumps(binding, allow_nan=False))
    for step in result["assembly_steps"]:
        source_pose = np.asarray(step["source_pose_matrix"], dtype=float)
        source_pose[:3, 3] *= scale
        step["source_assembly_pose_world_m"] = (transform @ source_pose).tolist()
        step["mapped_part_id"] = mapping[step["part_id"]]
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--binding", type=Path)
    parser.add_argument("--output-root", type=Path, default=ROOT / "runs/step16")
    args = parser.parse_args()
    recorder = TrialRecorder(args.output_root)
    try:
        report, digest = read_json(args.report)
        candidate = candidate_from_report(report, source_sha256=digest)
        if args.binding:
            binding, _ = read_json(args.binding)
            candidate = bind_coordinates(candidate, binding)
        write_json(recorder.run_dir / "assembly_candidate.json", candidate)
        write_json(recorder.run_dir / "result.json", {
            "status": "candidate_imported", "run_id": recorder.run_id, "finished_at_utc": utc_now(),
            "source_report_sha256": digest, "robot_execution_validated": False,
            "coordinate_binding_validated": candidate["coordinate_binding_validated"],
            "candidate_file": "assembly_candidate.json"})
        print(f"CANDIDATE_IMPORTED: seed={candidate['seed_part_id']}, "
              f"additions={[s['part_id'] for s in candidate['assembly_steps']]}")
        print(f"Robot execution NOT validated | {recorder.run_dir}")
        return 0
    except (ValueError, TypeError, KeyError, OSError) as error:
        write_json(recorder.run_dir / "result.json", {
            "status": "rejected", "error_type": type(error).__name__, "message": str(error),
            "robot_execution_validated": False, "finished_at_utc": utc_now()})
        print(f"REJECTED: {error} | {recorder.run_dir}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
