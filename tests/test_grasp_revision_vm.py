"""The measured inputs belong to the v2 contract; legacy candidates keep their old boundary."""
import sys
import unittest
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from grasp_skill_runtime import revision_geometry
from grasp_skill_candidates import inspect_source
from grasp_skill_vm import execute_source
from llm_runtime import load_llm_config


class RevisionGeometryTests(unittest.TestCase):
    def test_prediction_and_contact_require_their_respective_sources(self):
        trial = {'held_pad_geometry': {'predicted_lowest_pad_height_relative_to_rim_mm': -2.37},
                 'violations': [{'geoms': ['assembly_base_wall_0_1', 'gripper_right_pad1'],
                                 'signed_distance_m': -0.00066}],
                 'trace_summary': {'pick_completed': True}}
        self.assertEqual(revision_geometry(trial), {'predicted_pad_to_rim_clearance_mm': -2.37,
            'pick_completed_in_copy': True, 'pad_contacts_socket_wall': True})
        trial['held_pad_geometry'] = {}
        trial['violations'][0]['geoms'][0] = 'table'
        result = revision_geometry(trial)
        self.assertIsNone(result['predicted_pad_to_rim_clearance_mm'])
        self.assertFalse(result['pad_contacts_socket_wall'])

    def test_new_geometry_is_readable_only_in_version_two(self):
        # Synthetic branch test; this source is never submitted to the robot or to the model.
        code = ('def propose(ctx):\n'
                '    prev = ctx["previous_grasp"]\n'
                '    dims = ctx["part_size_xyz_mm"]\n'
                '    value = ctx["predicted_pad_to_rim_clearance_mm"]\n'
                '    if value == None:\n        return None\n'
                '    return {"x_mm": 0, "y_mm": 0, "z_mm": value, "jaw_yaw_deg": 0}')
        ctx = {'part_size_xyz_mm': [20, 20, 60], 'previous_grasp': {},
               'predicted_pad_to_rim_clearance_mm': -2.37}
        self.assertFalse(execute_source(code, ctx)['execution_confirmed'])
        self.assertEqual(execute_source(code, ctx, context_version=2)['output']['z_mm'], -2.37)
        ctx['predicted_pad_to_rim_clearance_mm'] = None
        result = execute_source(code, ctx, context_version=2)
        self.assertEqual(result['status'], 'returned')
        self.assertIsNone(result['output'])

    def test_live_profile_reserves_enough_for_one_high_request(self):
        config = load_llm_config(ROOT / 'config/llm.astra-high-recovery.toml').validate(live=True)
        self.assertEqual((config.model, config.reasoning_effort), ('gpt-6-astra', 'high'))
        self.assertEqual(config.max_model_requests, 1)
        self.assertEqual(config.request_reserve_usd(), Decimal('0.3548'))
        self.assertLessEqual(config.request_reserve_usd(), Decimal(config.max_cost_usd))

    def test_membership_is_limited_to_declared_keys_and_unmodified_grasp_alias(self):
        code = ('def propose(ctx):\n    size = ctx["part_size_xyz_mm"]\n'
                '    prev = ctx["previous_grasp"]\n'
                '    if "observation_is_current" not in ctx or "z_mm" not in prev:\n'
                '        return None\n'
                '    return {"x_mm": 0, "y_mm": 0, "z_mm": prev["z_mm"], "jaw_yaw_deg": 0}')
        inspect_source(code, context_version=2)
        for invalid in (code.replace('"z_mm" not in prev', '"mystery" not in prev'),
                        code.replace('"z_mm" not in prev', '"z_mm" not in size'),
                        code.replace('    if ', '    prev = size\n    if '),
                        code.replace('"z_mm" not in prev', 'size[0] not in prev')):
            with self.assertRaises(ValueError):
                inspect_source(invalid, context_version=2)
        with self.assertRaises(ValueError):
            inspect_source(code)
