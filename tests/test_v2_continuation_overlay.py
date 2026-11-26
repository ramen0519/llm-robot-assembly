from pathlib import Path
import sys,tempfile,unittest
from copy import deepcopy
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from assembly_v12.contracts import digest,sha,write
from check_component_continuation import allowed_search_overlay

class OverlayTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        mesh=self.root/'part.obj';mesh.write_text('v 0 0 0\n',encoding='utf-8')
        self.original={'profile':{'gravity':[0,0,-9.81],'resources':{'max_raw_candidates':4096}},
            'parts':{'p':{'mesh':str(mesh),'mesh_sha256':sha(mesh),'target':[0,0,0],'mass':.1}}}
    def tearDown(self):self.tmp.cleanup()
    def update(self):
        p=deepcopy(self.original);p['profile']['resources']['max_raw_candidates']=8192
        p['profile_path']=str(self.root/'profile.json');p['profile_hash']=digest(p['profile']);write(p['profile_path'],p['profile'])
        return p
    def test_search_cap_is_allowed_but_physics_and_goals_are_not(self):
        self.assertEqual(allowed_search_overlay(self.original,self.update())['status'],'passed')
        for key,value in [('target',[0,0,.001]),('mass',.2)]:
            p=self.update();p['parts']['p'][key]=value
            with self.assertRaises(ValueError):allowed_search_overlay(self.original,p)
    def test_profile_digest_must_describe_actual_profile(self):
        p=self.update();p['profile_hash']='stale'
        with self.assertRaises(ValueError):allowed_search_overlay(self.original,p)
    def test_changed_source_mesh_is_rejected(self):
        p=self.update();Path(p['parts']['p']['mesh']).write_text('changed',encoding='utf-8')
        with self.assertRaises(ValueError):allowed_search_overlay(self.original,p)

if __name__=='__main__':unittest.main()
