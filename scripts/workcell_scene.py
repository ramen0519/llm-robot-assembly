"""Build a tabletop workcell from a measured-or-example layout, without executing a skill."""

import math
from pathlib import Path
import re
import tomllib

import mujoco
import numpy as np

from ur3e_with_gripper import build_robot_spec
from view_tcp import check_tcp_frame
from view_ur3e import JOINT_NAMES


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config/workcell.example.toml"
TABLE_GEOM = "workcell_tabletop"


def yaw_rotation(degrees):
    radians = math.radians(degrees)
    c, s = math.cos(radians), math.sin(radians)
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]], dtype=float)


def yaw_quat_wxyz(degrees):
    half = math.radians(degrees) / 2
    return np.array([math.cos(half), 0, 0, math.sin(half)])


def table_to_world(config, position_table_m):
    return (np.asarray(config["table"]["top_center_world_m"])
            + yaw_rotation(config["table"]["yaw_deg"]) @ np.asarray(position_table_m))


def part_name(config):
    return "workcell_part_" + config["part"]["id"]


def target_name(config):
    return "workcell_target_" + config["target"]["id"]


def validate_config(config):
    """Reject typos, ambiguous frames and unsupported geometry rather than silently ignoring them."""
    fields = {
        "": {"schema_version", "profile_name", "measurement_status", "table", "robot", "part", "target", "contact"},
        "table": {"top_center_world_m", "size_xy_m", "thickness_m", "leg_width_m", "yaw_deg"},
        "robot": {"model", "gripper", "base_xy_table_m", "mount_height_m", "mount_size_xy_m", "yaw_deg"},
        "part": {"id", "shape", "size_m", "mass_kg", "spawn_xy_table_m", "yaw_deg", "spawn_clearance_m"},
        "target": {"id", "xy_table_m", "size_xy_m", "yaw_deg"},
        "contact": {"friction"},
    }
    for section, expected in fields.items():
        values = config if not section else config.get(section)
        if not isinstance(values, dict) or set(values) != expected:
            raise ValueError(f"{section or 'root'} requires exactly these fields: {sorted(expected)}")
    if type(config["schema_version"]) is not int or config["schema_version"] != 1:
        raise ValueError("Only workcell schema_version = 1 is supported.")
    if not isinstance(config["profile_name"], str) or not config["profile_name"].strip():
        raise ValueError("profile_name must be a non-empty string.")
    if config["measurement_status"] not in ("example_unmeasured", "measured_nominal"):
        raise ValueError("measurement_status must be example_unmeasured or measured_nominal.")
    if config["robot"]["model"] != "ur3e" or config["robot"]["gripper"] != "robotiq_2f85":
        raise ValueError("Only the current UR3e + Robotiq 2F-85 model is supported; UR3 is a different model.")
    if config["part"]["shape"] != "box":
        raise ValueError("Only a box test part is supported in step 11.")
    for section in ("part", "target"):
        identifier = config[section]["id"]
        if not isinstance(identifier, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", identifier):
            raise ValueError(f"{section}.id must be a short lowercase identifier, e.g. part_a.")
    if config["part"]["id"] == config["target"]["id"]:
        raise ValueError("part.id and target.id must be distinct.")

    def numeric(section, field, length=None, *, positive=False, nonnegative=False):
        value = config[section][field]
        values = value if length is not None else [value]
        if length is not None and (not isinstance(values, list) or len(values) != length):
            raise ValueError(f"{section}.{field} must contain {length} numbers.")
        if any(type(v) not in (int, float) or not math.isfinite(v) for v in values):
            raise ValueError(f"{section}.{field} must contain finite numbers.")
        if positive and any(v <= 0 for v in values):
            raise ValueError(f"{section}.{field} must be positive.")
        if nonnegative and any(v < 0 for v in values):
            raise ValueError(f"{section}.{field} must be non-negative.")

    numeric("table", "top_center_world_m", 3)
    numeric("table", "size_xy_m", 2, positive=True)
    numeric("table", "thickness_m", positive=True)
    numeric("table", "leg_width_m", positive=True)
    numeric("robot", "base_xy_table_m", 2)
    numeric("robot", "mount_height_m", nonnegative=True)
    numeric("robot", "mount_size_xy_m", 2, positive=True)
    numeric("part", "size_m", 3, positive=True)
    numeric("part", "mass_kg", positive=True)
    numeric("part", "spawn_xy_table_m", 2)
    numeric("part", "spawn_clearance_m", nonnegative=True)
    numeric("target", "xy_table_m", 2)
    numeric("target", "size_xy_m", 2, positive=True)
    numeric("contact", "friction", 3, nonnegative=True)
    for section in ("table", "robot", "part", "target"):
        numeric(section, "yaw_deg")
    table, part, target = config["table"], config["part"], config["target"]
    if table["top_center_world_m"][2] <= table["thickness_m"]:
        raise ValueError("The tabletop underside must be above the world floor (Z=0).")
    if 2 * table["leg_width_m"] >= min(table["size_xy_m"]):
        raise ValueError("Table legs must fit inside the tabletop without overlapping.")

    def fits_on_table(xy, size_xy, yaw):
        half_extent = np.abs(yaw_rotation(yaw)[:2, :2]) @ (np.asarray(size_xy) / 2)
        return np.all(np.abs(xy) + half_extent < np.asarray(table["size_xy_m"]) / 2)

    robot = config["robot"]
    if not fits_on_table(robot["base_xy_table_m"], robot["mount_size_xy_m"], robot["yaw_deg"]):
        raise ValueError("The robot mounting footprint must fit on the tabletop in this layout version.")
    if not fits_on_table(part["spawn_xy_table_m"], part["size_m"][:2], part["yaw_deg"]):
        raise ValueError("The whole rotated part footprint must fit on the tabletop.")
    if not fits_on_table(target["xy_table_m"], target["size_xy_m"], target["yaw_deg"]):
        raise ValueError("The whole rotated target region must fit on the tabletop.")
    if np.any(np.asarray(part["size_m"][:2]) > target["size_xy_m"]):
        raise ValueError("The target region must fit the part when aligned to the target yaw.")


def load_config(path=DEFAULT_CONFIG):
    # utf-8-sig also accepts copies saved with a Windows UTF-8 BOM.
    config = tomllib.loads(Path(path).read_text(encoding="utf-8-sig"))
    validate_config(config)
    return config


def build_workcell(config):
    validate_config(config)
    spec = build_robot_spec()
    table, robot, part, target = (config[key] for key in ("table", "robot", "part", "target"))
    table_yaw = table["yaw_deg"]
    base = spec.body("base")
    base.pos = table_to_world(config, [*robot["base_xy_table_m"], robot["mount_height_m"]])
    base.quat = yaw_quat_wxyz(table_yaw + robot["yaw_deg"])
    surface = spec.worldbody.add_body(name="workcell_table", pos=table["top_center_world_m"],
                                      quat=yaw_quat_wxyz(table_yaw))
    width, depth = table["size_xy_m"]
    thick, leg = table["thickness_m"], table["leg_width_m"]
    surface.add_geom(name=TABLE_GEOM, type=mujoco.mjtGeom.mjGEOM_BOX,
                     pos=[0, 0, -thick / 2], size=[width / 2, depth / 2, thick / 2],
                     rgba=[0.53, 0.59, 0.64, 1], friction=config["contact"]["friction"])
    leg_height = table["top_center_world_m"][2] - thick
    for index, (x, y) in enumerate(((-1, -1), (-1, 1), (1, -1), (1, 1))):
        surface.add_geom(name=f"workcell_leg_{index}", type=mujoco.mjtGeom.mjGEOM_BOX,
                         pos=[x * (width - leg) / 2, y * (depth - leg) / 2, -thick - leg_height / 2],
                         size=[leg / 2, leg / 2, leg_height / 2], rgba=[0.25, 0.30, 0.35, 1])
    if robot["mount_height_m"] > 0:
        surface.add_geom(name="workcell_robot_mount", type=mujoco.mjtGeom.mjGEOM_BOX,
                         pos=[*robot["base_xy_table_m"], robot["mount_height_m"] / 2],
                         quat=yaw_quat_wxyz(robot["yaw_deg"]),
                         size=[*(np.asarray(robot["mount_size_xy_m"]) / 2), robot["mount_height_m"] / 2],
                         rgba=[0.30, 0.33, 0.36, 1])
    surface.add_site(name=target_name(config), type=mujoco.mjtGeom.mjGEOM_BOX,
                     pos=[*target["xy_table_m"], 0.001], quat=yaw_quat_wxyz(target["yaw_deg"]),
                     size=[*(np.asarray(target["size_xy_m"]) / 2), 0.001], rgba=[0.1, 0.85, 0.3, 0.35])
    # Visual-only tabletop frame: white origin, red +X, green +Y, blue +Z.
    surface.add_site(name="workcell_table_origin", type=mujoco.mjtGeom.mjGEOM_SPHERE,
                     pos=[0, 0, 0.004], size=[0.006], rgba=[1, 1, 1, 1])
    for axis, end, color in (("x", [0.1, 0, 0.004], [1, 0.15, 0.15, 1]),
                             ("y", [0, 0.1, 0.004], [0.15, 0.9, 0.2, 1]),
                             ("z", [0, 0, 0.104], [0.15, 0.4, 1, 1])):
        surface.add_site(name=f"workcell_table_{axis}", type=mujoco.mjtGeom.mjGEOM_CAPSULE,
                         fromto=[0, 0, 0.004, *end], size=[0.002], rgba=color)
    part_body = spec.worldbody.add_body(
        name=part_name(config),
        pos=table_to_world(config, [*part["spawn_xy_table_m"], part["size_m"][2] / 2 + part["spawn_clearance_m"]]),
        quat=yaw_quat_wxyz(table_yaw + part["yaw_deg"]))
    part_body.add_freejoint(name=part_name(config) + "_free")
    part_body.add_geom(name=part_name(config) + "_geom", type=mujoco.mjtGeom.mjGEOM_BOX,
                       size=np.asarray(part["size_m"]) / 2, mass=part["mass_kg"],
                       rgba=[0.95, 0.44, 0.08, 1], friction=config["contact"]["friction"])
    model = spec.compile()
    data = mujoco.MjData(model)
    # Composition extends home with the new free body's configured pose.
    # Initialising a trial is separate from physical motion during that trial.
    mujoco.mj_resetDataKeyframe(model, data, model.key("home").id)
    mujoco.mj_forward(model, data)
    if (model.nq, model.nv, model.nu, model.neq, model.ntendon) != (21, 20, 7, 3, 1):
        raise ValueError("Expected the original robot/gripper and one unactuated free body.")
    if tuple(model.joint(i).name for i in range(6)) != JOINT_NAMES:
        raise ValueError("Arm joint ordering changed.")
    check_tcp_frame(model, data)
    return model, data


def observe_workcell(model, data, config):
    """Read current physics state; do not overwrite the part with its configured initial pose."""
    part = data.body(part_name(config))
    velocity = data.joint(part_name(config) + "_free").qvel
    table_rotation = yaw_rotation(config["table"]["yaw_deg"])
    return {
        "source": "sim_ground_truth", "simulation_time_s": float(data.time),
        "table_top_frame": {"parent_frame": "world", "origin_m": config["table"]["top_center_world_m"],
                            "rotation_matrix": table_rotation},
        "robot_base": {"frame": "world", "position_m": data.body("base").xpos.copy(),
                       "quaternion_xyzw": data.body("base").xquat[[1, 2, 3, 0]].copy()},
        "part": {"id": config["part"]["id"], "frame": "world", "position_m": part.xpos.copy(),
                 "quaternion_xyzw": part.xquat[[1, 2, 3, 0]].copy(),
                 "position_table_m": table_rotation.T @ (part.xpos - config["table"]["top_center_world_m"]),
                 "linear_speed_m_s": float(np.linalg.norm(velocity[:3])),
                 "angular_speed_rad_s": float(np.linalg.norm(velocity[3:]))},
        "target": {"id": config["target"]["id"], "source": "configured_target", "frame": "world",
                   "desired_part_center_m": table_to_world(config, [*config["target"]["xy_table_m"], config["part"]["size_m"][2] / 2]),
                   "desired_quaternion_xyzw": yaw_quat_wxyz(config["table"]["yaw_deg"] + config["target"]["yaw_deg"])[[1, 2, 3, 0]]},
        "contacts": [{"geom_ids": [int(c.geom1), int(c.geom2)],
                      "geom_names": [model.geom(int(g)).name or f"geom#{g}" for g in (c.geom1, c.geom2)],
                      "signed_distance_m": float(c.dist)} for c in data.contact],
    }


def set_workcell_camera(camera, config):
    camera.lookat[:] = config["table"]["top_center_world_m"]
    camera.lookat[2] *= 0.8  # Include the table legs as well as the robot.
    camera.distance = 2.0 * max(*config["table"]["size_xy_m"], config["table"]["top_center_world_m"][2])
    camera.azimuth = 135 + config["table"]["yaw_deg"]
    camera.elevation = -25
