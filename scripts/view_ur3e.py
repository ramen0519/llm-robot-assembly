"""Load a UR3e model and show its home pose without advancing physics time."""

import argparse
import math
from pathlib import Path
import time

import mujoco
import mujoco.viewer


SCENE_PATH = Path(__file__).resolve().parents[1] / "scenes" / "ur3e_preview.xml"
JOINT_NAMES = (
    "shoulder_pan_joint",
    "shoulder_lift_joint",
    "elbow_joint",
    "wrist_1_joint",
    "wrist_2_joint",
    "wrist_3_joint",
)


def load_robot():
    model = mujoco.MjModel.from_xml_path(str(SCENE_PATH))
    data = mujoco.MjData(model)

    # A keyframe is a saved pose. Setting the initial pose is not robot control.
    mujoco.mj_resetDataKeyframe(model, data, model.key("home").id)
    mujoco.mj_forward(model, data)

    names = tuple(model.joint(i).name for i in range(model.njnt))
    if names != JOINT_NAMES or model.nq != 6 or model.nv != 6:
        raise ValueError("The selected model must contain the six expected UR3e joints.")
    if any(kind != mujoco.mjtJoint.mjJNT_HINGE for kind in model.jnt_type):
        raise ValueError("The six arm joints must be revolute joints.")
    if not all(math.isfinite(float(value)) for value in data.qpos):
        raise ValueError("The initial joint angles must be finite.")
    return model, data


def set_camera(camera):
    camera.lookat[:] = [0, 0, 0.25]
    camera.distance = 1.35
    camera.azimuth = 135
    camera.elevation = -20


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Check loading without a viewer.")
    parser.add_argument(
        "--seconds", type=float, default=0,
        help="Close the viewer after this many wall-clock seconds; 0 keeps it open.",
    )
    args = parser.parse_args()
    if not math.isfinite(args.seconds) or args.seconds < 0:
        parser.error("--seconds must be a finite, non-negative number")

    model, data = load_robot()
    print(f"MuJoCo: {mujoco.__version__}", flush=True)
    print(f"Joints: {model.njnt}, degrees of freedom: {model.nv}", flush=True)
    print(f"Meshes: {model.nmesh}, actuators: {model.nu} (not driven)", flush=True)
    for index, name in enumerate(JOINT_NAMES):
        print(f"  {name}: {math.degrees(float(data.qpos[index])):.1f} deg", flush=True)
    print("UR3e load: OK", flush=True)
    print("Static preview: physics time does not advance.", flush=True)
    if args.check:
        return

    with mujoco.viewer.launch_passive(
        model, data, show_left_ui=False, show_right_ui=False
    ) as viewer:
        with viewer.lock():
            set_camera(viewer.cam)
        viewer.sync()
        print("UR3e viewer opened. Close the window to exit.", flush=True)
        started = time.monotonic()
        while viewer.is_running():
            if args.seconds and time.monotonic() - started >= args.seconds:
                break
            # Refresh the view only. There is intentionally no mj_step() here.
            viewer.sync()
            time.sleep(1 / 60)

    print(f"Preview closed. Simulation time: {data.time:.3f} s", flush=True)


if __name__ == "__main__":
    main()
