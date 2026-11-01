from pathlib import Path
import json
import sys
import tempfile
import unittest
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from export_planner_input import export_bundle


class PlannerExportTests(unittest.TestCase):
    def test_ten_local_meshes_keep_metre_scale_and_explicit_body_frames(self):
        with tempfile.TemporaryDirectory() as temporary:
            bundle = export_bundle(temporary)
            manifest = json.loads((bundle / 'planner_input_manifest.json').read_text())
            binding = json.loads((bundle / 'binding_template.json').read_text())
            config = json.loads((bundle / 'asap_input/config.json').read_text())
            self.assertEqual(set(config), {str(i) for i in range(10)})
            self.assertEqual(manifest['part_map']['0'], 'base')
            self.assertEqual(manifest['source_length_scale_m'], 1.)
            self.assertEqual(manifest['planner_execution_status'], 'not_run')
            self.assertFalse(manifest['robot_execution_validated'])
            self.assertIsNone(binding['source_report_sha256'])
            for source_id, part in manifest['part_map'].items():
                vertices = np.array([[float(x) for x in line.split()[1:]] for line in
                                     (bundle / 'asap_input' / (source_id + '.obj')).read_text().splitlines() if line.startswith('v ')])
                assembled = vertices + config[source_id]['final_state']
                self.assertTrue(np.isfinite(assembled).all())
                frames = binding['robot_body_frame_mapping'][part]
                np.testing.assert_allclose(np.array(frames['world_from_cad_m']) @ frames['cad_from_robot_body_m'],
                                           frames['world_from_robot_body_m'], atol=1e-10)
            cover = binding['robot_body_frame_mapping']['cover']
            self.assertGreater(abs(cover['cad_from_robot_body_m'][2][3]), .01)
            base = np.array([[float(x) for x in line.split()[1:]] for line in
                             (bundle / 'asap_input/0.obj').read_text().splitlines() if line.startswith('v ')])
            self.assertAlmostEqual(float(np.ptp(base[:, 0])), .160, places=7)


if __name__ == '__main__':
    unittest.main()
