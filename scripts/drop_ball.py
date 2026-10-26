"""View a ball falling onto a floor. This is a small physics demonstration."""

import argparse
from pathlib import Path
import time

import mujoco
import mujoco.viewer


SCENE_PATH = Path(__file__).resolve().parents[1] / "scenes" / "falling_ball.xml"


def load_scene():
    # Model: fixed scene settings. Data: changing position, velocity and time.
    model = mujoco.MjModel.from_xml_path(str(SCENE_PATH))
    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)
    return model, data


def set_camera(camera):
    camera.lookat[:] = [0, 0, 0.25]
    camera.distance = 1.5
    camera.azimuth = 135
    camera.elevation = -20


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--seconds", type=float, default=0,
        help="Close after this many wall-clock seconds; 0 keeps the viewer open.",
    )
    args = parser.parse_args()
    if args.seconds < 0:
        parser.error("--seconds must be non-negative")

    model, data = load_scene()
    print("Opening the falling-ball viewer.", flush=True)
    print("Each trial lasts 3 simulated seconds, then resets to the start.", flush=True)
    print("Close the viewer window to exit.", flush=True)

    with mujoco.viewer.launch_passive(model, data) as viewer:
        with viewer.lock():
            set_camera(viewer.cam)
        viewer.sync()
        print("Viewer opened.", flush=True)
        started = time.monotonic()

        while viewer.is_running():
            frame_started = time.monotonic()
            if args.seconds and frame_started - started >= args.seconds:
                break

            # About 60 display updates per simulated second; physics uses 2 ms steps.
            with viewer.lock():
                if data.time >= 3.0:
                    # Reset is a new trial, not a movement during the falling trial.
                    mujoco.mj_resetData(model, data)
                    mujoco.mj_forward(model, data)
                mujoco.mj_step(model, data, nstep=8)
            viewer.sync()

            # Physics time and the computer's elapsed time are different quantities.
            remaining = 8 * model.opt.timestep - (time.monotonic() - frame_started)
            if remaining > 0:
                time.sleep(remaining)

    print("Viewer closed.", flush=True)


if __name__ == "__main__":
    main()
