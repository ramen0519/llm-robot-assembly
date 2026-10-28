"""Inspect the mounted gripper's nominal TCP frame without moving the robot."""

import argparse
import math
import time

import mujoco
import mujoco.viewer
import numpy as np

from ur3e_with_gripper import load_robot_with_gripper, mount_error


TCP_SITE_NAME = "gripper_pinch"
ATTACHMENT_SITE_NAME = "attachment_site"
# From the unchanged source models: mount 7 mm + base 3.8 mm + pinch 145 mm.
ATTACHMENT_TO_TCP_M = np.array([0.0, 0.0, 0.1558])
# The gripper base is rotated -90 degrees about attachment +Z.
ATTACHMENT_TO_TCP_ROTATION = np.array([[0.0, 1.0, 0.0], [-1.0, 0.0, 0.0], [0.0, 0.0, 1.0]])
AXIS_COLORS = ((1.0, 0.15, 0.15, 1.0), (0.15, 0.9, 0.2, 1.0), (0.15, 0.4, 1.0, 1.0))


def read_tcp_pose(data):
    """Return TCP position [m] and rotation in WORLD coordinates (copies).

    Rotation columns are the TCP's local X/Y/Z unit axes expressed in world.
    Call mj_forward after changing the state before reading this pose.
    """
    site = data.site(TCP_SITE_NAME)
    return site.xpos.copy(), site.xmat.reshape(3, 3).copy()


def check_tcp_frame(model, data):
    """Check this model's nominal definition, not physical tool calibration."""
    mount_error(model, data)
    site = model.site(TCP_SITE_NAME)
    if model.body(int(model.site_bodyid[site.id])).name != "gripper_base":
        raise ValueError("TCP must be fixed to gripper_base, not a moving finger.")
    if not np.allclose(site.pos, [0.0, 0.0, 0.145], atol=1e-10, rtol=0):
        raise ValueError("Expected the upstream pinch offset [0, 0, 0.145] m.")
    # MuJoCo stores local quaternions as wxyz. Both signs describe the same rotation.
    if not (math.isclose(abs(float(site.quat[0])), 1.0, abs_tol=1e-10, rel_tol=0)
            and np.allclose(site.quat[1:], 0.0, atol=1e-10, rtol=0)):
        raise ValueError("TCP local axes must match the upstream gripper_base axes.")

    position, rotation = read_tcp_pose(data)
    if not np.all(np.isfinite(position)) or not np.all(np.isfinite(rotation)):
        raise ValueError("TCP pose contains non-finite values.")
    wrist = data.site(ATTACHMENT_SITE_NAME)
    wrist_rotation = wrist.xmat.reshape(3, 3)
    relative_position = wrist_rotation.T @ (position - wrist.xpos)
    relative_rotation = wrist_rotation.T @ rotation
    report = {
        "attachment_position_error_m": float(np.linalg.norm(relative_position - ATTACHMENT_TO_TCP_M)),
        "attachment_rotation_error": float(np.max(np.abs(relative_rotation - ATTACHMENT_TO_TCP_ROTATION))),
        "orthogonality_error": float(np.max(np.abs(rotation.T @ rotation - np.eye(3)))),
        "determinant_error": abs(float(np.linalg.det(rotation)) - 1.0),
    }
    for name, error in report.items():
        if not math.isfinite(error) or error > 1e-9:
            raise ValueError(f"TCP frame check failed: {name}={error:.12g}")
    return report


def print_tcp_pose(data, report):
    position, rotation = read_tcp_pose(data)
    print(f"TCP site: {TCP_SITE_NAME}; reference frame: world; sim time: {data.time:.3f} s")
    print("Position [m]:  " + np.array2string(position, precision=6, suppress_small=True))
    print("Position [mm]: " + np.array2string(position * 1000, precision=3, suppress_small=True))
    for i, (axis, color) in enumerate(zip("XYZ", ("red", "green", "blue"))):
        vector = np.array2string(rotation[:, i], precision=6, suppress_small=True)
        print(f"TCP +{axis} ({color}) in world: {vector}")
    print("Local +Y: finger closing axis; local +Z: from gripper body toward fingertips.")
    print("Attachment -> TCP offset [m]: [0, 0, 0.1558]; relative rotation: Z -90 deg.")
    for name, error in report.items():
        print(f"  {name}: {error:.3e}")
    print("TCP frame definition: PASS", flush=True)


def append_marker(scene, position, label, radius, color):
    if scene.ngeom >= scene.maxgeom:
        raise RuntimeError("Not enough visualization geometry slots.")
    geom = scene.geoms[scene.ngeom]
    mujoco.mjv_initGeom(geom, mujoco.mjtGeom.mjGEOM_SPHERE,
                       np.array([radius, radius, radius]), position, np.eye(3).ravel(), np.array(color))
    geom.label = label
    scene.ngeom += 1


def append_connector(scene, start, end, color, label="", arrow=True):
    if scene.ngeom >= scene.maxgeom:
        raise RuntimeError("Not enough visualization geometry slots.")
    geom = scene.geoms[scene.ngeom]
    kind = mujoco.mjtGeom.mjGEOM_ARROW if arrow else mujoco.mjtGeom.mjGEOM_LINE
    mujoco.mjv_initGeom(geom, kind, np.zeros(3), np.zeros(3), np.eye(3).ravel(), np.array(color))
    # Arrow width is in meters; line width is in pixels.
    mujoco.mjv_connector(geom, kind, 0.003 if arrow else 2.0, start, end)
    geom.label = label
    scene.ngeom += 1


def add_tcp_overlay(scene, data):
    """Append render-only markers. No masses, contacts or model geometry change."""
    position, rotation = read_tcp_pose(data)
    append_marker(scene, position, "TCP", 0.005, (1.0, 0.85, 0.2, 1.0))
    for i, axis in enumerate("XYZ"):
        append_connector(scene, position, position + 0.06 * rotation[:, i], AXIS_COLORS[i])
        # Connector labels sit at their common origin, so put separate labels at the tips.
        if scene.ngeom >= scene.maxgeom:
            raise RuntimeError("Not enough visualization geometry slots.")
        geom = scene.geoms[scene.ngeom]
        mujoco.mjv_initGeom(geom, mujoco.mjtGeom.mjGEOM_LABEL, np.zeros(3),
                           position + 0.08 * rotation[:, i], np.eye(3).ravel(), np.array(AXIS_COLORS[i]))
        geom.label = f"+{axis}"
        scene.ngeom += 1
    wrist = data.site(ATTACHMENT_SITE_NAME).xpos
    append_marker(scene, wrist, "Attachment", 0.004, (0.85, 0.85, 0.85, 1.0))
    append_connector(scene, wrist, position, (0.7, 0.7, 0.7, 1.0), arrow=False)


def set_tcp_camera(camera, data):
    position, _ = read_tcp_pose(data)
    camera.lookat[:] = position + np.array([0.0, 0.07, 0.055])
    camera.distance = 0.72
    camera.azimuth = 135
    camera.elevation = -20


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Print and check the home TCP pose without a viewer.")
    parser.add_argument("--seconds", type=float, default=0.0, help="Close after this many wall seconds; 0 waits for you.")
    args = parser.parse_args()
    if not math.isfinite(args.seconds) or args.seconds < 0:
        parser.error("--seconds must be finite and non-negative")
    try:
        model, data = load_robot_with_gripper()
        report = check_tcp_frame(model, data)
        print_tcp_pose(data, report)
        print("Nominal model TCP, not calibrated hardware TCP. Static preview: no mj_step().", flush=True)
        if args.check:
            return 0
        with mujoco.viewer.launch_passive(model, data, show_left_ui=False, show_right_ui=False) as viewer:
            with viewer.lock():
                set_tcp_camera(viewer.cam, data)
            started = time.monotonic()
            while viewer.is_running():
                if args.seconds and time.monotonic() - started >= args.seconds:
                    break
                with viewer.lock():
                    mujoco.mj_forward(model, data)
                    viewer.user_scn.ngeom = 0
                    add_tcp_overlay(viewer.user_scn, data)
                viewer.sync()
                time.sleep(1 / 60)
        print(f"TCP preview closed. Simulation time: {data.time:.3f} s")
        return 0
    except (ValueError, RuntimeError) as error:
        print(f"TCP frame definition: FAIL ({error})")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
