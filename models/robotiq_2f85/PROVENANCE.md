# Robotiq 2F-85 model provenance

- Source: https://github.com/google-deepmind/mujoco_menagerie
- Source directory: robotiq_2f85
- Pinned commit: f3b6013629c7b94f8ee93fd977b161fa8350f9bc
- Snapshot: https://github.com/google-deepmind/mujoco_menagerie/tree/f3b6013629c7b94f8ee93fd977b161fa8350f9bc/robotiq_2f85
- Retrieved: 2026-10-26.
- License: BSD-2-Clause, original LICENSE retained.
- Copyright: Copyright (c) 2013, ROS-Industrial.
- All 14 upstream files copied without modification, including 8 STL meshes.
- Local addition: this document only.

The source is the simplified Menagerie MJCF derived from the ROS-Industrial URDF cited in its README. It is not a calibration of the particular gripper in the laboratory.

Our scripts/ur3e_with_gripper.py composes this model with the existing UR3e scene via MjSpec.attach at attachment_site, using the gripper_ name prefix. It preserves the upstream root offset (0, 0, 0.007 m), inertia, joints, tendon, equality constraints, collision shapes and exclusions. It copies elliptic cone and impratio=10 to the combined global options while retaining the arm's implicitfast integrator and 0.002 s timestep.

The rigid wrist mounting models the hardware connection; no object is artificially attached to the fingers. The upstream pinch site is a simulated reference, not a calibrated physical TCP. Finger command 0 is held for this step; open/close cycles and grasping are subsequent validation tasks.
