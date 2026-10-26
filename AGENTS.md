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

## Guided development workflow

- This is the user's graduation project. Help the user understand and explain the work, assuming little prior robotics experience.
- Work on one small, coherent module or learning milestone at a time. Do not implement several modules in one turn unless the user explicitly requests it.
- Before making changes, explain the module's purpose, why it is needed, the files or tools involved, and a concrete success criterion in plain Korean.
- Explain meaningful commands, installations, dependency choices, code changes, and validation results as the work proceeds. Define unfamiliar terms when first used.
- After each increment, explain the relevant code and data flow, give the exact run command and expected result, and distinguish verified behavior from remaining limitations. For documentation-only changes, explain the changed guidance instead of inventing a run command.
- Keep explanations tied to the actual files and behavior so the user can later modify and present the project themselves.
- Finish the current increment and its explanation, then wait for the user's questions or instruction to continue before starting the next module. Do not demand a separate approval for every routine command within the agreed increment.
- Keep each implementation increment reviewable and suitable for a focused commit; do not bundle unrelated work.
