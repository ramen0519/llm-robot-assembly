"""Specific regression checks for wrong branches, transforms and frame preservation."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
from import_framework_battery import DEFAULT_BUNDLE,read,validate_chain,verify_bundle


@unittest.skipUnless((DEFAULT_BUNDLE/"transfer.json").is_file(),"Import the provided local CAD evidence first.")
class TransferTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result=read(DEFAULT_BUNDLE/"source/worker_result.json")

    def test_selected_chain_reaches_only_fixed_base(self):
        order,steps=validate_chain(self.result,set("012345"),"0")
        self.assertEqual(order,["2","3","1","4","5"])
        self.assertEqual(steps[-1]["remaining_after"],["0"])
        self.assertTrue(all(not s["additional_supports"] for s in steps))

    def test_reject_successful_edge_from_other_parent(self):
        bad=deepcopy(self.result)
        bad["selected_plan"][1]["parent"]=["0","1","2","3","4","5"]
        with self.assertRaisesRegex(ValueError,"mixed"):
            validate_chain(bad,set("012345"),"0")

    def test_reject_incomplete_success_chain(self):
        bad=deepcopy(self.result)
        bad["selected_plan"]=bad["selected_plan"][:-1]
        with self.assertRaisesRegex(ValueError,"fixed base alone"):
            validate_chain(bad,set("012345"),"0")

    def test_reject_unmodeled_support(self):
        bad=deepcopy(self.result)
        bad["selected_plan"][0]["sim_info"]["parts_fix"]=["3"]
        with self.assertRaisesRegex(ValueError,"Extra support"):
            validate_chain(bad,set("012345"),"0")

    def test_reject_failed_edge(self):
        bad=deepcopy(self.result)
        bad["selected_plan"][0]["sim_info"]["feasible"]=False
        with self.assertRaises(ValueError):
            validate_chain(bad,set("012345"),"0")

    def test_embedded_coordinates_reconstruct_once(self):
        transfer=verify_bundle(DEFAULT_BUNDLE)
        self.assertEqual(transfer["assembly_candidate"],["5","4","1","3","2"])
        self.assertAlmostEqual(transfer["coordinates"]["metres_per_source_unit"],.024)


if __name__=="__main__":
    unittest.main()
