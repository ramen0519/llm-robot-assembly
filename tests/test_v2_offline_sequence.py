from pathlib import Path
from tempfile import TemporaryDirectory
import sys,unittest
import numpy as np,trimesh
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from offline_sequence import shape_transforms
from assembly_v2.packets import candidate_packet

class OfflineSequence(unittest.TestCase):
    def test_reuse_requires_full_surface_match_not_equal_bounds(self):
        with TemporaryDirectory() as folder:
            root=Path(folder);a=trimesh.creation.box([.01,.02,.03]);b=a.copy()
            b.apply_translation([.031,-.02,.003])
            c=trimesh.creation.cylinder(radius=.005,height=.03,sections=16)
            c.apply_scale([1,2,1])
            for name,mesh in [('a',a),('b',b),('c',c)]:mesh.export(root/(name+'.obj'))
            part=lambda n:{'mesh':str(root/(n+'.obj'))}
            match=shape_transforms(part('a'),part('b'))
            self.assertTrue(match)
            self.assertTrue(any(np.allclose(t[:3,3],[.031,-.02,.003]) and np.allclose(t[:3,:3],np.eye(3)) for t in match))
            self.assertEqual(shape_transforms(part('a'),part('c')),[])

    def test_policy_packet_keeps_optional_repair_choices(self):
        row={'candidate_id':'p:r1:g1','checks':{'physical_grasp':'not_run'},
             'geometry_pose_alternatives':{'physical_success':False,'alternatives':[{'position_mm':[1,2,3]}]},
             'private_debug':'omitted'}
        packet=candidate_packet(row)
        self.assertEqual(packet['geometry_pose_alternatives'],row['geometry_pose_alternatives'])
        self.assertNotIn('private_debug',packet)
        self.assertEqual(candidate_packet({'candidate_id':'legacy'}),{'candidate_id':'legacy'})

if __name__=='__main__':unittest.main()
