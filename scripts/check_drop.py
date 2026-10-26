"""Check gravity, floor contact and settling without opening a graphics window."""

import math

import mujoco

from drop_ball import load_scene


def check_scene(model):
    # Start from the XML's initial state for every check.
    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)
    ball_id = model.geom("ball_geom").id
    floor_id = model.geom("floor").id
    radius = float(model.geom_size[ball_id, 0])
    floor_z = float(data.geom_xpos[floor_id, 2])
    initial_z = float(data.body("ball").xpos[2])
    resting_z = floor_z + radius

    # These thresholds are for this one introductory sphere/plane scene.
    sample_time = 0.2
    duration = 3.0
    stable_window = 0.5
    first_contact = None
    sample_z = None
    sample_vz = None
    min_z = initial_z
    stable_height_error = 0.0
    stable_speed = 0.0
    stable_contact = True
    stable_samples = 0

    steps = round(duration / model.opt.timestep)
    for _ in range(steps):
        mujoco.mj_step(model, data)
        # Refresh derived positions and contact information at the new time.
        mujoco.mj_forward(model, data)
        z = float(data.body("ball").xpos[2])
        velocity = data.joint("ball_free").qvel[:3]
        speed = math.sqrt(sum(float(v) ** 2 for v in velocity))
        min_z = min(min_z, z)
        touching_floor = any(
            {int(contact.geom1), int(contact.geom2)} == {ball_id, floor_id}
            and contact.dist <= 0
            for contact in data.contact[:data.ncon]
        )
        if touching_floor and first_contact is None:
            first_contact = float(data.time)
        if sample_z is None and data.time >= sample_time - 1e-9:
            sample_time = float(data.time)
            sample_z = z
            sample_vz = float(velocity[2])
        if data.time >= duration - stable_window - 1e-9:
            stable_samples += 1
            stable_height_error = max(stable_height_error, abs(z - resting_z))
            stable_speed = max(stable_speed, speed)
            stable_contact = stable_contact and touching_floor

    # Compare to Earth's free-fall prediction, independent of the configured gravity.
    expected_z = initial_z - 0.5 * 9.81 * sample_time**2
    expected_contact = math.sqrt(2 * (initial_z - resting_z) / 9.81)
    checks = {
        "free fall at 0.2 s": (
            sample_z is not None and abs(sample_z - expected_z) < 0.005
            and sample_vz < -1.0
        ),
        "floor contact at the expected time": (
            first_contact is not None and abs(first_contact - expected_contact) < 0.01
        ),
        "no fall through the floor": min_z >= resting_z - 0.02,
        "settled for the final 0.5 s": (
            stable_samples > 0 and stable_contact
            and stable_height_error < 0.001 and stable_speed < 0.01
        ),
    }

    print(f"Initial centre height: {initial_z:.6f} m")
    print(f"Height at {sample_time:.3f} s: {sample_z:.6f} m (theory: {expected_z:.6f} m)")
    print(f"First floor contact: {first_contact} s (theory: {expected_contact:.6f} s)")
    print(f"Minimum centre height: {min_z:.6f} m")
    print(f"Final centre height: {float(data.body('ball').xpos[2]):.6f} m")
    print(f"Final-window max height error: {stable_height_error:.6f} m")
    print(f"Final-window max speed: {stable_speed:.6f} m/s")
    for label, passed in checks.items():
        print(f"{'PASS' if passed else 'FAIL'}: {label}")
    return all(checks.values())


if __name__ == "__main__":
    scene_model, _ = load_scene()
    raise SystemExit(0 if check_scene(scene_model) else 1)
