"""Compose the unchanged UR3e and Robotiq 2F-85 source models."""

from pathlib import Path

import mujoco
import numpy as np

from view_ur3e import JOINT_NAMES, SCENE_PATH


ROOT = Path(__file__).resolve().parents[1]
GRIPPER_PATH = ROOT / "models/robotiq_2f85/2f85.xml"


def build_robot_spec():
    """Return a fresh editable composition; source model files remain unchanged."""
    arm = mujoco.MjSpec.from_file(str(SCENE_PATH))
    gripper = mujoco.MjSpec.from_file(str(GRIPPER_PATH))

    # Global contact settings come from the gripper; keep the arm's implicitfast integrator.
    arm.option.cone = gripper.option.cone
    arm.option.impratio = gripper.option.impratio
    # No free joint: the gripper housing is rigidly mounted to the wrist.
    # Prefixes separate names such as "base", "black" and "collision" in the two models.
    arm.attach(gripper, site="attachment_site", prefix="gripper_")
    return arm


def load_robot_with_gripper():
    model = build_robot_spec().compile()
    data = mujoco.MjData(model)
    mujoco.mj_resetDataKeyframe(model, data, model.key("home").id)
    mujoco.mj_forward(model, data)

    if (model.nq, model.nv, model.nu, model.neq, model.ntendon) != (14, 14, 7, 3, 1):
        raise ValueError("Expected 6 arm joints, 8 gripper joints, 7 controls and gripper linkages.")
    if tuple(model.joint(i).name for i in range(6)) != JOINT_NAMES:
        raise ValueError("Arm joint ordering changed.")
    mount_id = model.body("gripper_base_mount").id
    if model.body_parentid[mount_id] != model.body("wrist_3_link").id:
        raise ValueError("Gripper must be mounted to wrist_3_link.")
    if model.body_jntnum[mount_id] != 0:
        raise ValueError("The gripper mount must have no relative joint.")

    motor_id = model.actuator("gripper_fingers_actuator").id
    if model.actuator_trntype[motor_id] != mujoco.mjtTrn.mjTRN_TENDON:
        raise ValueError("Gripper motor must use the original linkage tendon.")
    if not np.array_equal(model.actuator_ctrlrange[motor_id], [0, 255]):
        raise ValueError("Expected the upstream gripper control range 0..255.")
    if data.ctrl[motor_id] != 0:
        raise ValueError("This preview starts with the gripper open.")
    return model, data


def mount_error(model, data):
    # Upstream base_mount origin is 7 mm along the attachment site's local +Z.
    site = data.site("attachment_site")
    mount = data.body("gripper_base_mount")
    expected_position = site.xpos + site.xmat.reshape(3, 3) @ np.array([0, 0, 0.007])
    position_error = float(np.linalg.norm(mount.xpos - expected_position))
    rotation_error = float(np.max(np.abs(mount.xmat - site.xmat)))
    if position_error > 1e-8 or rotation_error > 1e-8:
        raise RuntimeError("Gripper attachment position/orientation does not match the wrist.")
    return position_error
