"""Synthetic contract fixtures; never evidence of physical assembly success."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from disassembly_adapter import PlanContractError, bind_coordinates, candidate_from_report, read_json

DIGEST = "a" * 64
IDENTITY = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]


def fixture():
    parts, removed, selected = ["base", "a", "b"], [], []
    for i, part in enumerate(["b", "a"]):
        rest = [p for p in parts if p != part]
        selected.append({
            "event_id": f"fixture-event-{i}", "action_id": f"fixture-action-{i}", "state_id": f"fixture-state-{i}",
            "remaining_parts": parts.copy(), "status": "PASS", "accepted_for_transition": True,
            "deadline_exceeded": False,
            "choice": {"part_id": part, "pose_id": "pose0", "skill_id": "single_force_v1",
                       "direction_id": "zp", "second_direction_id": None, "support_ids": ["base"]},
            "context": {"run_id": "contract-fixture-not-physics", "state_id": f"fixture-state-{i}",
                        "action_id": f"fixture-action-{i}", "base_part": "base",
                        "remaining_parts": parts.copy(), "parts_rest": rest, "parts_removed": removed.copy(),
                        "pose_matrix": copy.deepcopy(IDENTITY),
                        "pose_catalog": [{"pose_id": "pose0", "matrix": copy.deepcopy(IDENTITY)}],
                        "task_context": {"task": {"assembly_id": "synthetic-contract-fixture"}}},
            "raw": {"status": "PASS", "feasible": True, "path_success": True, "backend": "ASAP_RedMax",
                    "stability_success": True, "stability_check_calls": 1},
        })
        parts, removed = rest, [*removed, part]
    return {"schema_version": 1, "run_id": "contract-fixture-not-physics", "status": "PASS",
            "success": True, "selected_plan_continuity_checked": True,
            "selected_plan_continuity_errors": [], "selected_plan": selected,
            "terminal_parts": ["base"], "stats": {"sequence": ["b", "a"]}}


class DisassemblyAdapterTests(unittest.TestCase):
    def candidate(self, report=None):
        return candidate_from_report(fixture() if report is None else report, source_sha256=DIGEST)

    def test_reverse_is_candidate_with_evidence_and_no_invented_units(self):
        report = fixture()
        original = copy.deepcopy(report)
        result = self.candidate(report)
        self.assertEqual([x["part_id"] for x in result["assembly_steps"]], ["a", "b"])
        self.assertEqual(result["assembly_steps"][0]["required_present_parts"], ["base"])
        self.assertFalse(result["robot_execution_validated"])
        self.assertFalse(result["coordinate_binding_validated"])
        self.assertIsNone(result["source"]["length_scale_m"])
        self.assertEqual(result["assembly_steps"][0]["source_force_direction_id"], "zp")
        self.assertEqual(report, original)

    def test_partial_result_and_late_edge_rejected(self):
        for mutation in ("partial", "late", "unaccepted", "raw_failure"):
            report = fixture()
            if mutation == "partial":
                report["status"], report["success"] = "UNKNOWN", False
            elif mutation == "late":
                report["selected_plan"][0]["deadline_exceeded"] = True
            elif mutation == "unaccepted":
                report["selected_plan"][0]["accepted_for_transition"] = False
            else:
                report["selected_plan"][0]["raw"]["path_success"] = False
            with self.subTest(mutation=mutation), self.assertRaises(PlanContractError):
                self.candidate(report)

    def test_recomputed_continuity_does_not_trust_top_level_flag(self):
        report = fixture()
        report["selected_plan"][1]["remaining_parts"] = ["base", "b"]
        with self.assertRaises(PlanContractError):
            self.candidate(report)

    def test_missing_final_step_and_wrong_sequence_rejected(self):
        report = fixture()
        report["selected_plan"].pop()
        with self.assertRaises(PlanContractError):
            self.candidate(report)
        report = fixture()
        report["stats"]["sequence"].reverse()
        with self.assertRaises(PlanContractError):
            self.candidate(report)

    def test_removed_support_and_duplicate_events_rejected(self):
        report = fixture()
        report["selected_plan"][0]["choice"]["support_ids"] = ["b"]
        with self.assertRaises(PlanContractError):
            self.candidate(report)
        report = fixture()
        report["selected_plan"][1]["event_id"] = report["selected_plan"][0]["event_id"]
        with self.assertRaises(PlanContractError):
            self.candidate(report)

    def test_reflection_pose_and_changed_cad_rejected(self):
        report = fixture()
        report["selected_plan"][0]["context"]["pose_matrix"][0][0] = -1
        with self.assertRaises(PlanContractError):
            self.candidate(report)
        report = fixture()
        report["selected_plan"][1]["context"]["task_context"]["task"]["assembly_id"] = "different"
        with self.assertRaises(PlanContractError):
            self.candidate(report)

    def test_binding_requires_explicit_units_and_complete_mapping(self):
        candidate = self.candidate()
        with self.assertRaises(PlanContractError):
            bind_coordinates(candidate, {"schema_version": 1, "source_report_sha256": DIGEST})
        binding = {"schema_version": 1, "source_report_sha256": DIGEST,
                   "source_length_scale_m": .001, "source_frame": "declared_fixture_frame",
                   "world_from_source_m": IDENTITY, "part_map": {"base": "base", "a": "a", "b": "b"},
                   "assembled_part_poses_world_m": {p: IDENTITY for p in candidate["part_ids"]}}
        result = bind_coordinates(candidate, binding)
        self.assertTrue(result["coordinate_binding_validated"])
        self.assertFalse(result["robot_execution_validated"])
        self.assertFalse(candidate["coordinate_binding_validated"])
        binding["source_report_sha256"] = "b" * 64
        with self.assertRaises(PlanContractError):
            bind_coordinates(candidate, binding)

    def test_millimetre_scale_applies_only_to_source_translation(self):
        report = fixture()
        for edge in report["selected_plan"]:
            edge["context"]["pose_matrix"][0][3] = 100
            edge["context"]["pose_catalog"][0]["matrix"][0][3] = 100
        candidate = self.candidate(report)
        transform = copy.deepcopy(IDENTITY)
        transform[0][3] = .2
        binding = {"schema_version": 1, "source_report_sha256": DIGEST, "source_length_scale_m": .001,
                   "source_frame": "fixture", "world_from_source_m": transform,
                   "part_map": {p: p for p in candidate["part_ids"]},
                   "assembled_part_poses_world_m": {p: IDENTITY for p in candidate["part_ids"]}}
        result = bind_coordinates(candidate, binding)
        self.assertAlmostEqual(result["assembly_steps"][0]["source_assembly_pose_world_m"][0][3], .3)
        self.assertEqual(result["assembly_steps"][0]["source_pose_matrix"][0][3], 100)

    def test_json_duplicate_keys_and_nonfinite_numbers_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "input.json"
            for value in ('{"a":1,"a":2}', '{"a": NaN}', '{"a": Infinity}'):
                path.write_text(value, encoding="utf-8")
                with self.assertRaises(PlanContractError):
                    read_json(path)

    def test_malformed_nested_payloads_rejected_as_contract_errors(self):
        for mutation in ("edge", "context", "task_context", "catalog", "stats", "missing_action"):
            report = fixture()
            if mutation == "edge":
                report["selected_plan"][0] = 4
            elif mutation == "context":
                report["selected_plan"][0]["context"] = None
            elif mutation == "task_context":
                report["selected_plan"][0]["context"]["task_context"] = None
            elif mutation == "catalog":
                report["selected_plan"][0]["context"]["pose_catalog"] = [None]
            elif mutation == "missing_action":
                del report["selected_plan"][0]["action_id"]
            else:
                report["stats"] = None
            with self.subTest(mutation=mutation), self.assertRaises(PlanContractError):
                self.candidate(report)


if __name__ == "__main__":
    unittest.main()
