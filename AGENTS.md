# Development instructions

## Scope

- Read `README.md` and the relevant sections of `docs/PROJECT_PLAN.md` before implementation.
- Build a working MuJoCo simulation before connecting real hardware.
- Keep the final target near 10 product parts; smaller assemblies are development milestones.
- Start with one arm and a single LangGraph agent. Fine-tuning and multi-agent implementation are deferred.
- Treat the laboratory presentation as reference material, not as user-authored code or proof that reusable code is available.
- UR3 and UR3e are distinct. The presentation names UR3e; verify the selected physical robot before fixing the model.

## Implementation

- Deliver small runnable increments with an explicit success criterion.
- Prefer existing robot models, IK solvers, and motion planners over implementing them from scratch.
- Preserve model sources and licenses. Validate model composition, actuator behavior, TCP, and collision geometry.
- Separate agent policy, skill execution, motion, environment adapters, verification, and memory.
- Start with deterministic scripted skills before adding LLM planning.
- Do not count teleportation, artificial attachment, mock results, or LLM self-evaluation as physical assembly success.
- Keep simulation and real observations, validation records, and success metrics distinct.
- Check the robot, gripper, carried part, and assembled parts along motion paths.
- Never replay an uncertain physical command blindly. Re-observe before deciding whether to continue.
- Real-robot execution is a separate operation; simulation development does not authorize moving hardware.

## Repository and validation

- Keep API credentials, private laboratory material, generated media, experiment runs, and local databases out of Git.
- Place live databases outside synced OneDrive folders; export closed backups or reports when needed.
- Document actual setup and execution commands once runnable code exists. Do not claim unrun checks passed.
- Validate the behavior changed; for robot dynamics, use real simulator runs as well as code checks.
- Use `codex/` for new work branches. Keep `main` suitable for sharing and reproducible milestones.
- Explain changes and remaining limitations in Korean for the user.
