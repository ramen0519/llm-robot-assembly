"""Structured failure evidence for the current simulated TCP skill; no recovery policy."""

from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timezone
from enum import Enum
import json
import math
from pathlib import Path
import uuid

import mujoco
import numpy as np


class FailureCode(str, Enum):
    INPUT_OR_PRECONDITION_INVALID = "INPUT_OR_PRECONDITION_INVALID"
    MODEL_OR_CONTROL_INVALID = "MODEL_OR_CONTROL_INVALID"
    IK_NOT_CONVERGED = "IK_NOT_CONVERGED"
    PLANNED_CONTACT = "PLANNED_CONTACT"
    UNEXPECTED_CONTACT = "UNEXPECTED_CONTACT"
    MOTION_LIMIT_EXCEEDED = "MOTION_LIMIT_EXCEEDED"
    TCP_NOT_REACHED = "TCP_NOT_REACHED"
    NOT_SETTLED = "NOT_SETTLED"
    GRIPPER_STATE_INVALID = "GRIPPER_STATE_INVALID"
    SIMULATION_INVALID = "SIMULATION_INVALID"
    CANCELLED = "CANCELLED"
    INCOMPLETE_RECORD = "INCOMPLETE_RECORD"
    UNCLASSIFIED_ERROR = "UNCLASSIFIED_ERROR"


class FailureEvent(RuntimeError):
    """A detector's facts. RuntimeError compatibility keeps earlier demos usable."""

    def __init__(self, code, message, *, phase=None, violations=None, candidate=None,
                 kinematics_fresh=True):
        super().__init__(message)
        self.code = code
        self.phase = phase
        self.violations = violations or []
        self.candidate = candidate
        self.kinematics_fresh = kinematics_fresh


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def json_safe(value):
    """Strict JSON: unknown/non-finite measurements are null, never NaN or zero."""
    if is_dataclass(value):
        return json_safe(asdict(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, np.ndarray):
        return json_safe(value.tolist())
    if isinstance(value, np.generic):
        return json_safe(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    return value


def write_json(path, payload):
    """Replace only a complete JSON document; a storage failure propagates to caller."""
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(json_safe(payload), ensure_ascii=False, indent=2,
                                    allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def capture_observation(model, data, *, source="sim_ground_truth", kinematics_fresh=True):
    """Copy evidence only. Never step, forward, reset, or repair the observed state."""
    finite = all(np.all(np.isfinite(v)) for v in (data.qpos, data.qvel, data.ctrl))
    warnings = [{"index": i, "count": int(w.number), "last_info": int(w.lastinfo)}
                for i, w in enumerate(data.warning) if w.number]
    reasons = []
    if not finite or not math.isfinite(float(data.time)):
        reasons.append("non_finite_state; non-finite entries are stored as null")
    if warnings:
        reasons.append("physics_warning")
    if not kinematics_fresh:
        reasons.append("forward_kinematics_not_confirmed_for_this_state")
    tcp, contacts = None, None
    if not reasons:
        # This recorder intentionally supports the current mounted UR3e/2F-85 model.
        site = data.site("gripper_pinch")
        if np.all(np.isfinite(site.xpos)) and np.all(np.isfinite(site.xmat)):
            tcp = {"frame": "world", "position_m": site.xpos.copy(),
                   "rotation_matrix": site.xmat.reshape(3, 3).copy()}
        else:
            reasons.append("non_finite_tcp_pose")
        contacts = []
        for contact in data.contact:
            geoms = []
            for geom_id in (int(contact.geom1), int(contact.geom2)):
                body_id = int(model.geom_bodyid[geom_id])
                geoms.append({"geom_id": geom_id, "geom_name": model.geom(geom_id).name,
                              "body_name": model.body(body_id).name})
            distance = float(contact.dist)
            contacts.append({"geoms": geoms, "signed_distance_m": distance,
                             "penetration_m": max(0.0, -distance) if math.isfinite(distance) else None,
                             "position_world_m": contact.pos.copy()})
            if not math.isfinite(distance) or not np.all(np.isfinite(contact.pos)):
                reasons.append("non_finite_contact")
    return json_safe({
        "source": source,
        # A candidate's MjData.time does not mean that a robot moved to that time/pose.
        "simulation_time_s": float(data.time) if source == "sim_ground_truth" else None,
        "valid": not reasons, "invalid_reasons": sorted(set(reasons)),
        "qpos_rad": data.qpos.copy(), "qvel_rad_s": data.qvel.copy(),
        "ctrl": data.ctrl.copy(), "tcp": tcp, "contacts": contacts,
        "physics_warnings": warnings,
    })


@dataclass
class FailureReport:
    schema_version: int
    run_id: str
    action_id: str
    skill: str
    environment: str
    status: str
    failure_code: FailureCode
    message: str
    phase: str
    detected_at_utc: str
    simulation_time_s: float | None
    validation_profile: str
    goal: dict | None
    actual: dict | None
    last_valid_observation: dict | None
    candidate: dict | None
    violations: list
    exception_type: str
    cause_hypotheses: list
    response: str


class TrialRecorder:
    """One directory per attempt; no databases and no failure file on success."""

    def __init__(self, output_root):
        self.run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ") + "_" + uuid.uuid4().hex[:8]
        self.run_dir = Path(output_root) / self.run_id
        self.run_dir.mkdir(parents=True, exist_ok=False)
        self.trace_path = self.run_dir / "tcp_motion.csv"
        self.goal = None
        self.last_valid = None
        self.model_metadata = None

    def set_context(self, model, goal):
        self.goal = json_safe(goal)
        self.model_metadata = {
            "simulator": "MuJoCo", "simulator_version": mujoco.__version__,
            "timestep_s": float(model.opt.timestep),
            "joint_names_in_qpos_order": [model.joint(i).name for i in range(model.njnt)],
            "actuator_names_in_ctrl_order": [model.actuator(i).name for i in range(model.nu)],
            "ctrl_units": "arm position servos: rad; gripper tendon command: 0..255",
        }

    def remember_valid(self, model, data, row):
        observation = capture_observation(model, data)
        if observation["valid"]:
            observation["measurements"] = json_safe(row)
            self.last_valid = observation

    def fail(self, error, phase, model=None, data=None):
        if isinstance(error, FailureEvent):
            event = error
        elif isinstance(error, KeyboardInterrupt):
            event = FailureEvent(FailureCode.CANCELLED, "Interrupted by user.", kinematics_fresh=False)
        else:
            event = FailureEvent(FailureCode.UNCLASSIFIED_ERROR, str(error), kinematics_fresh=False)
        actual = (capture_observation(model, data, kinematics_fresh=event.kinematics_fresh)
                  if model is not None and data is not None else None)
        if actual and actual["tcp"] and self.goal:
            tcp, goal = actual["tcp"], self.goal
            rotation_delta = np.asarray(goal["rotation_matrix"]).T @ np.asarray(tcp["rotation_matrix"])
            actual["goal_error"] = {
                "position_mm": float(np.linalg.norm(np.asarray(tcp["position_m"]) - goal["position_m"])) * 1000,
                "orientation_deg": math.degrees(math.acos(float(np.clip((np.trace(rotation_delta) - 1) / 2, -1, 1)))),
            }
        status = "cancelled" if event.code == FailureCode.CANCELLED else "failed"
        report = FailureReport(
            schema_version=1, run_id=self.run_id, action_id="move_tcp_001", skill="move_tcp/v1",
            environment="simulation", status=status, failure_code=event.code, message=str(event),
            phase=event.phase or phase, detected_at_utc=utc_now(),
            simulation_time_s=float(data.time) if data is not None else None,
            validation_profile="tcp_move/v1", goal=self.goal, actual=actual,
            last_valid_observation=self.last_valid, candidate=event.candidate,
            violations=event.violations,
            exception_type=type(error.__cause__ or error).__name__,
            cause_hypotheses=[], response="stop_without_retry",
        )
        payload = json_safe(report)
        payload["model"] = self.model_metadata
        payload["trace_file"] = self.trace_path.name if self.trace_path.exists() else None
        path = self.run_dir / "failure_report.json"
        write_json(path, payload)
        self.finish(status, failure_file=path.name)
        return path

    def finish(self, status, *, failure_file=None):
        write_json(self.run_dir / "result.json", {
            "schema_version": 1, "run_id": self.run_id, "environment": "simulation",
            "status": status, "finished_at_utc": utc_now(), "failure_report_file": failure_file,
            "trace_file": self.trace_path.name if self.trace_path.exists() else None,
        })
