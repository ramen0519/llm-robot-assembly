"""Graph routing, physical success, failure evidence and no automatic replay."""

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from assembly_skills import AssemblySkills
from run_pick_place import execute_trial


class PickPlaceGraphTests(unittest.TestCase):
    def run_graph(self, **kwargs):
        with tempfile.TemporaryDirectory() as temp:
            result, folder = execute_trial(through=15, output_root=temp, **kwargs)
            state = json.loads((folder / "graph_state.json").read_text(encoding="utf-8"))
            events = ([json.loads(line) for line in (folder / "graph_events.jsonl").read_text(
                encoding="utf-8").splitlines()] if (folder / "graph_events.jsonl").exists() else [])
            report = (json.loads((folder / "failure_report.json").read_text(encoding="utf-8"))
                      if (folder / "failure_report.json").exists() else None)
        return result, state, events, report

    def test_graph_executes_and_verifies_real_pick_place(self):
        result, state, events, report = self.run_graph()
        self.assertEqual(result["status"], "success")
        self.assertEqual(state["status"], "success")
        self.assertIsNone(report)
        self.assertIsNone(state["failure_report"])
        self.assertEqual([a["skill"] for a in state["completed_actions"]], ["pick", "place"])
        self.assertEqual([e["action"]["skill"] for e in events if e["node"] == "execute"], ["pick", "place"])
        self.assertEqual(events[-1]["node"], "finish")
        self.assertTrue(all(state["verification"]["checks"].values()))
        self.assertLess(state["verification"]["measurements"]["target_position_error_m"], 0.003)
        self.assertEqual(state["observation"]["simulation_time_s"], result["simulation_time_s"])
        self.assertGreater(result["validated_samples"], 1000)

    def test_physical_gripper_fault_stops_before_place_and_preserves_report(self):
        holder, stages = {}, []
        def fault(stage, skills):
            stages.append(stage)
            if stage == "pick_close":
                original = skills.motion.controller.apply
                skills.motion.controller.apply = lambda data, reference, gripper_command=0.: original(data, reference, 0.)
                holder["skills"] = skills
        result, state, events, report = self.run_graph(on_stage=fault)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(report["failure_code"], "GRASP_NOT_CONFIRMED")
        self.assertEqual(state["failure_report"], report)
        self.assertEqual(report["skill"], "langgraph_pick_place/v1")
        self.assertEqual(state["completed_actions"], [])
        self.assertEqual([e["node"] for e in events].count("execute"), 1)
        self.assertFalse(any(s.startswith("place") or s == "pick_lift" for s in stages))
        self.assertEqual(holder["skills"].data.time, report["simulation_time_s"])
        self.assertIsNotNone(report["actual"]["contacts"])
        self.assertEqual(report["response"], "stop_without_retry")

    def test_stale_observation_rejected_before_any_pick(self):
        holder = {}
        def change_after_selection(event, skills):
            if event["node"] == "select_action":
                # Real physics advancement invalidates the previously observed state.
                skills.motion.hold(0.002, allow_support=True, allow_grasp=False)
                holder["time"] = skills.data.time
        with patch.object(AssemblySkills, "pick", side_effect=AssertionError("Must not move")) as pick:
            result, state, events, report = self.run_graph(on_graph_event=change_after_selection)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(report["failure_code"], "INPUT_OR_PRECONDITION_INVALID")
        self.assertEqual(report["simulation_time_s"], holder["time"])
        self.assertEqual(state["completed_actions"], [])
        pick.assert_not_called()

    def test_lost_grasp_during_place_preserves_verified_pick(self):
        holder, stages = {}, []
        def fault(stage, skills):
            stages.append(stage)
            if stage == "place_transfer":
                original = skills.motion.controller.apply
                skills.motion.controller.apply = lambda data, reference, gripper_command=0.: original(data, reference, 0.)
                holder["skills"] = skills
        result, state, events, report = self.run_graph(on_stage=fault)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(report["failure_code"], "PART_LOST")
        self.assertEqual([a["skill"] for a in state["completed_actions"]], ["pick"])
        self.assertEqual(state["action"]["skill"], "place")
        self.assertEqual(state["failure_report"], report)
        self.assertEqual([e["node"] for e in events].count("execute"), 2)
        self.assertNotIn("place_release", stages)
        self.assertEqual(holder["skills"].data.time, report["simulation_time_s"])

    def test_claimed_success_without_physical_lift_is_rejected(self):
        # Negative control only: this mocked claim is never counted as a success.
        def false_success(skills, part_id):
            return {"skill": "pick/v1", "status": "success", "part_id": part_id}
        with patch.object(AssemblySkills, "pick", new=false_success), patch.object(AssemblySkills, "place") as place:
            result, state, events, report = self.run_graph()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(report["failure_code"], "GRASP_NOT_CONFIRMED")
        self.assertEqual(events[-1]["node"], "verify")
        self.assertEqual(state["completed_actions"], [])
        place.assert_not_called()

    def test_cancellation_is_terminal_without_replay(self):
        stages = []
        def cancel(stage, skills):
            stages.append(stage)
            if stage == "pick_close":
                raise KeyboardInterrupt()
        result, state, events, report = self.run_graph(on_stage=cancel)
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(state["status"], "cancelled")
        self.assertEqual(report["failure_code"], "CANCELLED")
        self.assertFalse(any(s.startswith("place") for s in stages))
        self.assertEqual([e["node"] for e in events].count("execute"), 1)

    def test_setup_failure_is_recorded_without_entering_graph(self):
        result, state, events, report = self.run_graph(config_path="does_not_exist.toml")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(state["phase"], "runner")
        self.assertEqual(events, [])
        self.assertEqual(state["failure_report"], report)


if __name__ == "__main__":
    unittest.main()
