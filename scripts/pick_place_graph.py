"""Step 15: deterministic LangGraph around the existing physical skills.

Only JSON-compatible observations/results enter State. The live MuJoCo model,
controller and skills stay in this invocation's closure. No LLM, checkpoint
resume, node retry, parallel motion or simulator reset is used.
"""

from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langsmith import tracing_context

from failure_report import FailureCode, FailureEvent, capture_observation, json_safe


class PickPlaceState(TypedDict):
    part_id: str
    target_id: str
    status: str
    observation: dict | None
    observation_revision: int
    action: dict | None
    last_result: dict | None
    verification: dict | None
    completed_actions: list[dict]
    failure_report: dict | None
    error: str | None


def _snapshot(skills):
    observation = capture_observation(skills.model, skills.data)
    if not observation["valid"]:
        raise FailureEvent(FailureCode.SIMULATION_INVALID, "Graph observation is invalid.")
    observation["workcell"] = skills.observe()
    return observation


def run_pick_place_graph(skills, *, failure_handler, on_event=None):
    """Run one new episode, stopping on the first failure without retrying it.

    failure_handler writes the existing FailureReport and returns its JSON
    payload. on_event records node boundaries; neither callback is a policy.
    """
    initial: PickPlaceState = {
        "part_id": skills.config["part"]["id"],
        "target_id": skills.config["target"]["id"],
        "status": "running",
        "observation": None,
        "observation_revision": 0,
        "action": None,
        "last_result": None,
        "verification": None,
        "completed_actions": [],
        "failure_report": None,
        "error": None,
    }

    def settle(state):
        skills.settle()
        return {}

    def observe(state):
        return {"observation": _snapshot(skills),
                "observation_revision": state["observation_revision"] + 1}

    def select_action(state):
        # A fixed baseline policy. A later LLM policy can propose this same
        # action contract; it will still pass the executor and verifier.
        names = [action["skill"] for action in state["completed_actions"]]
        if names not in ([], ["pick"], ["pick", "place"]):
            raise ValueError("Unexpected action history.")
        if names == ["pick", "place"]:
            return {"action": None}
        name = "pick" if not names else "place"
        action = {"action_id": f"{len(names) + 1:03d}_{name}", "skill": name,
                  "part_id": state["part_id"],
                  "observation_revision": state["observation_revision"]}
        if name == "place":
            action["target_id"] = state["target_id"]
        return {"action": action, "last_result": None, "verification": None}

    def execute(state):
        action = state["action"]
        if (not action
                or action["observation_revision"] != state["observation_revision"]
                or state["observation"] != _snapshot(skills)):
            raise FailureEvent(FailureCode.INPUT_OR_PRECONDITION_INVALID,
                               "Action requires the current observation; re-observe before execution.")
        if action["part_id"] != skills.config["part"]["id"]:
            raise ValueError("Unknown part.")
        if action["skill"] == "pick":
            if state["completed_actions"]:
                raise ValueError("Pick cannot be replayed.")
            result = skills.pick(action["part_id"])
        elif action["skill"] == "place":
            if ([a["skill"] for a in state["completed_actions"]] != ["pick"]
                    or action["target_id"] != skills.config["target"]["id"]):
                raise ValueError("Place requires a verified pick and the configured target.")
            result = skills.place(action["part_id"], action["target_id"])
        else:
            raise ValueError("Unsupported skill.")
        return {"last_result": json_safe(result)}

    def verify(state):
        action, result = state["action"], state["last_result"]
        if (not isinstance(result, dict) or result.get("status") != "success"
                or result.get("skill") != action["skill"] + "/v1"
                or result.get("part_id") != action["part_id"]):
            raise ValueError("Skill did not return a matching verified result.")
        is_pick = action["skill"] == "pick"
        # This reads the current physical state, without stepping or modifying
        # it. The existing skills also verify their full settling windows.
        row = skills.verifier.sample(skills.data, skills.motion.reference,
                                    allow_support=True, allow_grasp=is_pick)
        if is_pick:
            if (skills.motion.held_pose is None or row["supported"]
                    or row["part_bottom_clearance_m"] < 0.05):
                raise FailureEvent(FailureCode.GRASP_NOT_CONFIRMED,
                                   "Graph could not confirm a held, lifted part.")
            skills.verifier.check_held(skills.data, row, skills.motion.held_pose)
            checks = {"held_and_lifted": True, "bilateral_contact_and_slip": True}
        else:
            checks = skills.verifier.verify_placement([row])
            if (skills.motion.held_pose is not None
                    or result.get("target_id") != action["target_id"]
                    or result.get("checks") != checks):
                raise FailureEvent(FailureCode.PLACEMENT_NOT_CONFIRMED,
                                   "Placement result disagrees with the current measured state.")
        return {"verification": {"source": "sim_ground_truth",
                                 "checks": checks, "measurements": json_safe(row)},
                "completed_actions": [*state["completed_actions"], action.copy()]}

    def finish(state):
        if ([a["skill"] for a in state["completed_actions"]] != ["pick", "place"]
                or not state["verification"] or not all(state["verification"]["checks"].values())):
            raise ValueError("Both actions must be physically verified before success.")
        return {"status": "success"}

    def guarded(name, function):
        def node(state):
            try:
                update = function(state)
            except (Exception, KeyboardInterrupt) as error:
                # Persist evidence before returning a terminal state. Failure
                # to write evidence propagates to the runner, never to motion.
                report = failure_handler(error)
                update = {"status": report["status"], "failure_report": report,
                          "error": f"{type(error).__name__}: {error}"}
            current = {**state, **update}
            if on_event:
                on_event(json_safe({
                    "node": name, "status": current["status"],
                    "simulation_time_s": float(skills.data.time),
                    "observation_revision": current["observation_revision"],
                    "action": current["action"],
                    "completed_actions": current["completed_actions"],
                    "error": current["error"],
                }))
            return update
        return node

    graph = StateGraph(PickPlaceState)
    for name, function in (("settle", settle), ("observe", observe),
                           ("select_action", select_action), ("execute", execute),
                           ("verify", verify), ("finish", finish)):
        graph.add_node(name, guarded(name, function))
    graph.add_edge(START, "settle")
    for source, destination in (("settle", "observe"), ("observe", "select_action"),
                                ("execute", "verify"), ("verify", "observe")):
        graph.add_conditional_edges(
            source,
            lambda state, target=destination: target if state["status"] == "running" else END,
            [destination, END],
        )
    graph.add_conditional_edges(
        "select_action",
        lambda state: END if state["status"] != "running" else (
            "execute" if state["action"] else "finish"),
        ["execute", "finish", END],
    )
    graph.add_edge("finish", END)
    # No remote tracing, durable checkpointing or automatic replay of side
    # effects. One invocation owns one live simulator episode.
    with tracing_context(enabled=False):
        return json_safe(graph.compile().invoke(initial, {"recursion_limit": 24}))
