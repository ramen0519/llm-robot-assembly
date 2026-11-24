"""Synthetic transfer-contract fixtures, NOT native physics success evidence."""
from copy import deepcopy
from pathlib import Path
import json
import sys
import tempfile
import unittest
import zipfile
import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from assembly_v12.contracts import check_export,import_export,read,write,sha,digest,InputError


def inventory(root,d):
    d['artifacts']={p.relative_to(root).as_posix():sha(p) for p in root.rglob('*') if p.is_file() and p.name!='export_v1.json'}
    d['run_hash']=digest(d['artifacts']);write(root/'export_v1.json',d)


def bundle(root):
    root=Path(root);mapping={};source={};parts=[];native={}
    obj='v 0 0 0\nv 1 0 0\nv 0 1 0\nv 0 0 1\nf 1 3 2\nf 1 2 4\nf 1 4 3\nf 2 3 4\n'
    for pid in ('0','1','2'):
        t=np.eye(4);t[0,3]=int(pid)*20
        for sub in ('source','native'):
            p=root/'input'/sub/(pid+'.obj');p.parent.mkdir(parents=True,exist_ok=True);p.write_text(obj)
        source[pid+'.obj']=sha(root/'input/source'/(pid+'.obj'))
        mapping[pid]={'part_id':pid,'source_part_id':pid,'source_mesh':'source/'+pid+'.obj','source_sha256':source[pid+'.obj'],
                      'native_mesh':'native/'+pid+'.obj','native_mesh_sha256':source[pid+'.obj'],'native_mesh_frame':'part_local',
                      'mesh_coordinate_frame':'part_local','assembly_pose_applied_to_source_mesh':False,'assembly_matrix_mm':t.tolist()}
        parts.append({'part_id':pid,'bbox_size_mm':[10,10,10],'bbox_center_mm':[int(pid)*20+5,5,5],
                      'assembly_pose':{'translation_mm':t[:3,3].tolist(),'rotation_xyzw':[0,0,0,1]}})
        native[pid]={'final_state':[int(pid)*2,0,0,0,0,0]}
    write(root/'input/native/config.json',native)
    write(root/'native_input.json',{'sha256':{p.name:sha(p) for p in (root/'input/native').iterdir()}})
    coordinates={'reference_frame':'assembly','length_unit':'mm','rotation':'xyzw','assembly_mm_to_native_cm':np.diag([.1,.1,.1,1]).tolist()}
    write(root/'input/geometry.json',{'source_files':source,'source_unit':'cm','parts':parts,'coordinate_system':coordinates})
    write(root/'input/part_mapping.json',mapping);write(root/'input/views.json',{'offline_fixture':True})
    write(root/'input/surface_distances.json',[]);write(root/'incidents.json',[]);write(root/'media.json',{'offline_fixture':True})
    for n in ('top.png','oblique.png'):(root/'input'/n).write_bytes(b'Offline contract image fixture')
    steps=[];attempts=[];before=['0','1','2'];parent='initial'
    for index,pid in enumerate(('2','1')):
        attempt='a'+str(index);cid='p'+str(index);sid='s'+str(index)
        path='checks/'+cid+'.npz';(root/'checks').mkdir(exist_ok=True);(root/path).write_bytes(b'Offline trajectory fixture')
        write(root/('checks/'+cid+'.json'),{'stage':'path','target':pid,'passed':True,'attempt':attempt,'trajectory':path})
        write(root/('checks/'+sid+'.json'),{'stage':'stability','passed':True,'attempt':attempt})
        after=[x for x in before if x!=pid];child='state'+str(index)
        steps.append({'parent_uid':parent,'child_uid':child,'part':pid,'before':before,'after':after,'accepted':True,
                      'physics_pass':True,'attempt':attempt,'path_check':cid,'path':path,'stability_checks':[sid],
                      'fixed_base':'0','pose':None,'direction':[0,0,1],'additional_support':[]})
        attempts.append({'checks':[cid,sid]});before=after;parent=child
    write(root/'attempts.json',attempts)
    exported=deepcopy(mapping)
    for row in exported.values():
        for key in ('source_mesh','native_mesh'):row[key]='input/'+row[key]
    d={'schema':'disassembly_export/v1','robot_verified':False,'evidence_scope':'all_native_checks_and_media/v1',
       'source_hash':digest(source),'part_mapping':exported,'coordinate_system':coordinates,'initial_assembled_parts':['0','1','2'],
       'verified_transitions':steps,'remaining_parts':['0'],'reversed_local_removals':['1','2'],
       'completion':'COMPLETE','assembly_candidate':{'initial_base':'0','add_parts':['1','2']}}
    inventory(root,d);return d


class ExportContract(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name);self.d=bundle(self.root)

    def save(self):write(self.root/'export_v1.json',self.d)

    def test_complete_selected_chain_and_relocation(self):
        self.assertEqual(check_export(self.root/'export_v1.json')[0]['assembly_candidate']['add_parts'],['1','2'])
        with tempfile.TemporaryDirectory() as target:
            import_export(self.root,Path(target)/'copied')

    def test_other_success_branch_cannot_be_spliced(self):
        self.d['verified_transitions'][1]['parent_uid']='other_branch';self.save()
        with self.assertRaisesRegex(InputError,'Disconnected'):check_export(self.root/'export_v1.json')

    def test_cross_attempt_success_is_not_accepted(self):
        self.d['verified_transitions'][1]['attempt']='a0';self.save()
        with self.assertRaises(InputError):check_export(self.root/'export_v1.json')

    def test_wrong_residual_and_wrong_reverse_are_rejected(self):
        self.d['remaining_parts']=['1'];self.save()
        with self.assertRaises(InputError):check_export(self.root/'export_v1.json')

    def test_unit_is_not_guessed(self):
        g=read(self.root/'input/geometry.json');g['source_unit']=None;write(self.root/'input/geometry.json',g);inventory(self.root,self.d)
        with self.assertRaisesRegex(InputError,'unit'):check_export(self.root/'export_v1.json')

    def test_goal_mapping_cannot_apply_translation_twice(self):
        m=read(self.root/'input/part_mapping.json');m['1']['assembly_matrix_mm'][0][3]*=2
        write(self.root/'input/part_mapping.json',m);self.d['part_mapping']['1']['assembly_matrix_mm']=m['1']['assembly_matrix_mm'];inventory(self.root,self.d)
        with self.assertRaisesRegex(InputError,'goal pose'):check_export(self.root/'export_v1.json')

    def test_mutated_mesh_fails_hash_check(self):
        with (self.root/'input/native/1.obj').open('a') as f:f.write('v 100 100 100\n')
        with self.assertRaisesRegex(InputError,'hash mismatch'):check_export(self.root/'export_v1.json')

    def test_incomplete_evidence_does_not_promote_to_complete_input(self):
        self.d.pop('evidence_scope');self.save()
        with self.assertRaises(InputError):check_export(self.root/'export_v1.json')

    def test_zip_traversal_is_rejected(self):
        with tempfile.TemporaryDirectory() as target:
            z=Path(target)/'bad.zip'
            with zipfile.ZipFile(z,'w') as f:f.writestr('../secret','no')
            with self.assertRaises(InputError):import_export(z,Path(target)/'out')


if __name__=='__main__':unittest.main()
