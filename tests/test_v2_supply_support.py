from pathlib import Path
import sys,unittest
import numpy as np,trimesh
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from assembly_v2.supply_support import pedestal_footprint


class FiniteSupplySupport(unittest.TestCase):
    def test_wide_flat_face_supported_without_vertices_over_pedestal(self):
        box=trimesh.creation.box(extents=[.046,.070,.020])
        width,note=pedestal_footprint(box,np.eye(3),[.0161,.0665])
        np.testing.assert_allclose(width,[.0161,.0665])
        self.assertEqual(note['selection'],'preferred')
        self.assertGreater(note['attempts'][0]['contact_area_m2'],0)

    def test_hollow_underside_needs_support_at_outer_rim(self):
        bars=[]
        for x in (-.0225,.0225):
            bar=trimesh.creation.box(extents=[.001,.070,.020]);bar.apply_translation([x,0,0]);bars.append(bar)
        for y in (-.0345,.0345):
            bar=trimesh.creation.box(extents=[.044,.001,.020]);bar.apply_translation([0,y,0]);bars.append(bar)
        rim=trimesh.util.concatenate(bars)
        width,note=pedestal_footprint(rim,np.eye(3),[.0161,.0665])
        self.assertEqual(note['selection'],'full_footprint')
        self.assertFalse(note['attempts'][0]['accepted'])
        self.assertEqual(note['attempts'][0]['contact_area_m2'],0.)
        np.testing.assert_allclose(width,[.046,.070])

    def test_point_contact_does_not_certify_finite_pedestal(self):
        vertices=np.array([[0,0,0],[.01,0,.02],[0,.01,.02],[-.01,-.01,.02]])
        tetra=trimesh.Trimesh(vertices=vertices,faces=[[0,2,1],[0,3,2],[0,1,3],[1,2,3]],process=True)
        width,note=pedestal_footprint(tetra,np.eye(3),[.01,.01])
        self.assertIsNone(width);self.assertEqual(note['selection'],'table_fallback')


if __name__=='__main__':unittest.main()
