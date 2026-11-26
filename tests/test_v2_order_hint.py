"""A recommendation carries CAD and identity; robot success has to be measured separately."""
from copy import deepcopy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import trimesh

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from assembly_v12.contracts import InputError, read, sha, write
from assembly_v2 import entry
from assembly_v2.budget import Budget
from assembly_v2.order_hint import read_hint
from assembly_v2.preparation import metric_part_mesh
from assembly_v2.validation import check_prepared


class OrderHintTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        cad = self.root/'cad'
        cad.mkdir()
        rows = []
        hashes = {}
        for pid, z, frame in [('base', 5, 'part_local'), ('pin', 15, 'assembly')]:
            m = trimesh.creation.box(extents=[20, 16, 10])
            goal = np.eye(4)
            goal[:3, 3] = [30, 12, z]
            if frame == 'assembly':
                m.apply_transform(goal)
            m.export(cad/(pid+'.obj'))
            hashes[pid] = sha(cad/(pid+'.obj'))
            rows.append({'part_id':pid, 'mesh':pid+'.obj', 'mesh_coordinate_frame':frame,
                         'assembly_matrix_mm':goal.tolist()})
        write(cad/'assembly.json', {'unit':'mm', 'parts':rows})
        self.path = self.root/'assembly_hint.json'
        self.hint = {'schema':'assembly_order_hint/v1', 'assembly_id':'test',
                     'order_semantics':'advisory_base_first', 'suggested_order':['base','pin'],
                     'cad_manifest':'cad/assembly.json', 'cad_manifest_sha256':sha(cad/'assembly.json'),
                     'mesh_sha256':hashes}
        write(self.path, self.hint)

    def test_independent_mesh_frames_and_order_integrity(self):
        src, files = read_hint(self.path, Budget(30))
        self.assertEqual((src.base, src.order, src.steps), ('base',['pin'],[]))
        self.assertEqual(len(files), 3)
        for row in src.parts.values():
            goal=np.array(row['assembly_matrix_mm']); goal[:3,3]*=.001
            metric=metric_part_mesh(src,row,goal)
            np.testing.assert_allclose(metric.center_mass,[0,0,0],atol=1e-9)
            np.testing.assert_allclose(metric.extents,[.020,.016,.010])
        for order in (['base','base'], ['pin'], ['base','other']):
            write(self.path, {**self.hint,'suggested_order':order})
            with self.assertRaises(InputError):read_hint(self.path,Budget(10))
        write(self.path, {**self.hint,'mesh_sha256':{**self.hint['mesh_sha256'],'pin':'bad'}})
        with self.assertRaises(InputError):read_hint(self.path,Budget(10))

    def test_subdivided_extrusion_preserves_hole_and_rejects_taper(self):
        from assembly_v2.geometry import extrusion_hulls, quality
        ring = trimesh.creation.annulus(r_min=.004, r_max=.012, height=.006, sections=12).subdivide()
        ring.apply_transform(trimesh.transformations.rotation_matrix(.47,[1.,2.,3.]))
        ring.apply_translation([.018,.02,.06])
        hulls = extrusion_hulls(ring,max_hulls=128)
        self.assertAlmostEqual(sum(h.volume for h in hulls), ring.volume, places=12)
        evidence=quality(ring,hulls,{'seed':0,'quality_samples':192,
                                    'maximum_surface_error_m':.00025,'volume_relative_error_max':.02})
        self.assertEqual((evidence['status'], evidence['filled_empty_probes']), ('passed',0))
        with self.assertRaises(ValueError):
            extrusion_hulls(ring,max_hulls=5)
        with self.assertRaises(ValueError):
            extrusion_hulls(trimesh.creation.cone(radius=.01,height=.02,sections=12))

    def test_small_decomposition_facets_retain_material_and_scale(self):
        from itertools import product
        from scipy.spatial import ConvexHull
        from assembly_v2.geometry import decomposition_hull
        # A millimetre cube with a tiny triangular bevel has ten extreme points.
        points = list(product((0., 1.), repeat=3))[:-1] + [(1.,1.,.9999), (1.,.9999,1.), (.9999,1.,1.)]
        vertices = np.asarray(points) * .001 + [.012,.005,.03]
        truth = ConvexHull(vertices)
        actual = decomposition_hull(vertices, truth.simplices)
        self.assertTrue(actual.is_volume)
        self.assertEqual(len(actual.faces), len(truth.simplices))
        np.testing.assert_allclose(actual.volume, truth.volume, atol=0., rtol=1e-8)
        for point in vertices:
            self.assertLess(np.linalg.norm(actual.vertices - point,axis=1).min(), 1e-12)

    def test_merged_boxes_cover_each_occupied_cell_once(self):
        from assembly_v2.geometry import merge_occupied_cells
        axes=[np.array([0.,.2,.5,.9,1.7]),np.array([0.,1.,4.]),np.array([0.,2.,3.,7.])]
        mask=np.ones((4,2,3),dtype=bool)
        mask[1:3,:,1]=False
        boxes=merge_occupied_cells({'axes':axes,'occupied':mask})
        for index in np.ndindex(mask.shape):
            pt=np.array([(axes[i][j]+axes[i][j+1])/2 for i,j in enumerate(index)])
            covers=sum(np.all(np.abs(pt-box['center'])<box['size']/2) for box in boxes)
            self.assertEqual(covers,int(mask[index]))

    def test_triangulated_orthogonal_hole_keeps_its_original_boundary(self):
        from assembly_v12.preparation import solid_cells
        outer=trimesh.creation.box(extents=[.06,.05,.01])
        hole=trimesh.creation.box(extents=[.02,.02,.02])
        ring=trimesh.boolean.difference([outer,hole],engine='manifold')
        ring=ring.subdivide().subdivide()
        with self.assertRaises(InputError):solid_cells(ring.vertices,ring.faces,limit=12)
        grid=solid_cells(ring.vertices,ring.faces,limit=12,surface_planes_only=True)
        self.assertAlmostEqual(grid['volume_m3'],ring.volume,places=11)
        for box in grid['boxes']:
            self.assertFalse(np.all(np.abs(box['center'])<np.asarray(box['size'])/2))

    def test_real_prepare_entry_cache_and_tamper_detection(self):
        pin=self.root/'cad/pin.obj'
        geometry=trimesh.load_mesh(pin)
        geometry.apply_translation([.000003,0.,0.])  # Three nanometres in the millimetre input.
        geometry.export(pin,digits=13)
        self.hint['mesh_sha256']['pin']=sha(pin)
        write(self.path,self.hint)
        dest=self.root/'prepared'
        with patch.object(entry,'bridge',side_effect=AssertionError('A hint cannot call disassembly')):
            self.assertEqual(entry.main(['prepare','--input',str(self.path),'--output',str(dest),'--seconds','90']),0)
        result=read(dest/'result.json')
        self.assertEqual(result['reference_status'],'not_run')
        self.assertEqual(result['api_calls'],0)
        p=read(dest/'prepared.json')
        self.assertEqual(p['order_semantics'],'advisory_base_first')
        source=trimesh.load_mesh(p['parts']['pin']['mesh'])
        self.assertAlmostEqual(source.bounds[0,0],float(geometry.bounds[0,0])*1e-3-.030,places=13)
        self.assertTrue(check_prepared(p,Budget(30)))
        self.assertNotIn('input_export',p)
        self.assertNotIn('part_path',p['parts']['pin'])
        forged=deepcopy(p);forged['parts']['pin']['support_conditions']={'fixed_base':'base'}
        with self.assertRaisesRegex(ValueError,'cannot certify'):check_prepared(forged,Budget(20))
        (Path(p['input_hint']).parent/'cad/pin.obj').write_text('changed')
        with self.assertRaises(InputError):check_prepared(p,Budget(20))


if __name__ == '__main__':
    unittest.main()
