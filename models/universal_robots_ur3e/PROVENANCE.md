# UR3e model provenance

- Source repository: https://github.com/SouthColumn76/universal_robots_ur3e
- Pinned upstream commit: `5f042ffca6b5885fd18f5448e17b71ab46274fa3`
- Snapshot: https://github.com/SouthColumn76/universal_robots_ur3e/tree/5f042ffca6b5885fd18f5448e17b71ab46274fa3
- Retrieved: 2026-10-26.
- License: BSD-3-Clause; original notice retained in `LICENSE.txt`.
- Copyright notice in the upstream license: Copyright 2018 ROS Industrial Consortium.
- Copied without changes: `ur3e.xml`, `README.md`, `LICENSE.txt`, all 20 `assets/*.obj` files.
- Local additions in this directory: this provenance document only.

The upstream model is a community MJCF conversion derived from the ROS Industrial URDF description linked in its README. It is not presented here as a manufacturer-validated MuJoCo model or as an official MuJoCo Menagerie model.

The upstream README mentions a scene.xml and links to LICENSE, but this snapshot contains no scene.xml and names the license LICENSE.txt. Our own scene is ../../scenes/ur3e_preview.xml and our viewer is ../../scripts/view_ur3e.py.

Upstream notes that actuator and damping/armature values have not been carefully tuned and that adding a Robotiq 2F-85 may cause instability. The project verifies loading, joint structure and static rendering. Step 04 additionally checks one unladen shoulder-pan movement from the home pose using the unchanged upstream actuators, with measured arrival and per-step contact/limit checks; see ../../docs/STEP_04_ONE_JOINT.md. This is a limited dynamics check, not general validation of actuator behavior, collision geometry accuracy, gripper attachment or actual robot calibration.

The attachment_site is an upstream attachment reference; it is not a calibrated tool center point for a physical gripper.
