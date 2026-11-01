"""Geometry/physics probes are fixture tests, not robot assembly success."""
from pathlib import Path
import sys
import tempfile
import unittest
import mujoco
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from frame_product import FrameDimensions, export_product, product_parts, solid_for
from insertion_scene import build_insertion_scene
from move_tcp import ArmPositionController
from workcell_scene import part_name


class ProductSceneTests(unittest.TestCase):
    def test_ten_connected_solids_and_explicit_export_units(self):
        parts = product_parts()
        self.assertEqual(len(parts), 10)
        self.assertEqual(sum(not p['fixed'] for p in parts), 9)
        for part in parts:
            with self.subTest(part=part['id']):
                expected = sum(np.prod(b['size']) for b in part['boxes'])
                self.assertAlmostEqual(solid_for(part).volume(), expected, places=10)
        with tempfile.TemporaryDirectory() as folder:
            manifest = export_product(folder)
            self.assertEqual(manifest['units'], 'metre')
            self.assertFalse(manifest['robot_execution_validated'])
            self.assertEqual(len(list(Path(folder).glob('*.obj'))), 10)
            with self.assertRaises(ValueError):
                export_product(folder)

    def test_solid_parts_do_not_interpenetrate_in_nominal_assembly(self):
        parts = product_parts()
        solids = [solid_for(p).translate(p['position_product_m']) for p in parts]
        for i in range(len(solids)):
            for j in range(i):
                self.assertLess((solids[i] ^ solids[j]).volume(), 1e-12, (parts[i]['id'], parts[j]['id']))

    def test_invalid_dimensions_rejected(self):
        for dims in (FrameDimensions(hole_width=.018), FrameDimensions(post_width=float('nan')),
                     FrameDimensions(cover_height=.040)):
            with self.assertRaises(ValueError):
                product_parts(dims)

    def drop_probe(self, xy_offset):
        model, data, config, joint = build_insertion_scene()
        # Explicit independent test initial condition; never used by an assembly runner.
        q = data.joint(part_name(config) + '_free').qpos
        q[:3] = joint['target_part_center_world_m'] + np.array([*xy_offset, .030])
        mujoco.mj_forward(model, data)
        reference = data.qpos[:6].copy()
        controller = ArmPositionController(model)
        for _ in range(1500):
            controller.apply(data, reference)
            mujoco.mj_step(model, data)
        self.assertFalse(any(w.number for w in data.warning))
        self.assertEqual(model.neq, 3)  # Original gripper constraints only.
        self.assertTrue(np.isfinite(data.qpos).all())
        bottom = data.body(part_name(config)).xpos[2] - config['part']['size_m'][2] / 2
        return model, data, config, joint, bottom

    def test_hole_is_physically_open_and_has_floor(self):
        model, data, config, joint, bottom = self.drop_probe((0., 0.))
        self.assertAlmostEqual(bottom, joint['socket_floor_z_world_m'], delta=.0002)
        self.assertLess(bottom, joint['socket_rim_z_world_m'] - .015)
        pair = {model.geom(part_name(config) + '_geom').id, model.geom('assembly_base_floor').id}
        self.assertTrue(any({int(c.geom1), int(c.geom2)} == pair for c in data.contact))

    def test_wall_really_blocks_offset_post(self):
        _, _, _, joint, bottom = self.drop_probe((.030, 0.))
        self.assertAlmostEqual(bottom, joint['socket_rim_z_world_m'], delta=.0002)


if __name__ == '__main__':
    unittest.main()
