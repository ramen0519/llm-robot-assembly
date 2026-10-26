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

Upstream notes that actuator and damping/armature values have not been carefully tuned and that adding a Robotiq 2F-85 may cause instability. The current project step verifies loading, joint structure and static rendering only. Dynamics, collision geometry accuracy, actuator behavior, gripper attachment and actual robot calibration are not yet validated.

The attachment_site is an upstream attachment reference; it is not a calibrated tool center point for a physical gripper.
