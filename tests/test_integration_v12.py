"""Contract tests are not physical or live-model success claims."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from assembly_v12.contracts import InputError, relative, rigid, write, read, digest, mesh, append, sha
from assembly_v12.preparation import solid_cells,classify,holes
from assembly_v12.context import History,world_pose
from assembly_v12.memory import Incidents,Library,validate_guidance,INSTALL_CHECKS
from frame_product import FrameDimensions,product_parts,solid_for


class Geometry(unittest.TestCase):
    def cells(self,part):
        m=solid_for(part).to_mesh()
        return solid_cells(np.asarray(m.vert_properties[:,:3]),np.asarray(m.tri_verts))

    def test_exact_cell_geometry_templates(self):
        for p in product_parts():
            c=self.cells(p)
            self.assertAlmostEqual(c['volume_m3'],c['mesh_volume_m3'],places=9)
            self.assertEqual(classify(c,p['fixed'])['kind'],p['kind'])

    def test_new_dimensions_and_names_do_not_change_feature_detection(self):
        d=FrameDimensions(base_width=.14,post_spacing=.07,post_width=.022,hole_width=.026,sleeve_width=.038,post_height=.064)
        for p in product_parts(d):
            expected=p['kind']; p['id']='unfamiliar-'+p['id']
            self.assertEqual(classify(self.cells(p),p['fixed'])['kind'],expected)

    def test_curved_or_slanted_faces_fail_closed(self):
        p=product_parts()[1]; m=solid_for(p).to_mesh()
        v=np.asarray(m.vert_properties[:,:3]).copy(); v[:,0]+=v[:,2]*.1
        with self.assertRaisesRegex(InputError,'Non-orthogonal'):
            solid_cells(v,np.asarray(m.tri_verts))

    def test_rigid_rejects_scale_and_ambiguous_coordinates(self):
        t=np.eye(4); t[0,0]=.001
        with self.assertRaises(InputError): rigid(t)

    def test_path_escape_rejected_before_access(self):
        for name in ('../secret','E:/secret','/etc/passwd','a\\b'):
            with self.assertRaises(InputError): relative(Path.cwd(),name)


class Conversation(unittest.TestCase):
    def test_same_history_pairs_outputs_and_replays_initial_images(self):
        with tempfile.TemporaryDirectory() as folder:
            f=Path(folder); (f/'a.png').write_bytes(b'image-a'); (f/'b.png').write_bytes(b'image-b')
            h=History(f/'history.jsonl',{'goal':True},[f/'a.png',f/'b.png'])
            response={'output':[{'type':'reasoning','id':'r','encrypted_content':'opaque','summary':[]},
                                {'type':'function_call','id':'fc','call_id':'c','name':'propose_assembly_action','arguments':'{}'}]}
            h.accept(response)
            with self.assertRaises(ValueError): h.accept(response)
            h.complete({'status':'preflight_rejected','executed':False})
            self.assertEqual(h.items[-1]['call_id'],'c')
            self.assertEqual(h.items[1]['encrypted_content'],'opaque')
            self.assertEqual(sum(x['type']=='input_image' for x in h.items[0]['content']),2)
            with self.assertRaises(ValueError): h.complete({'status':'success'})

    def test_pose_units_order(self):
        p=world_pose([.1,-.2,.3],[0,0,0,1])
        self.assertEqual(p['position_mm'],[100,-200,300]); self.assertEqual(p['quaternion_xyzw'],[0,0,0,1])


class Memory(unittest.TestCase):
    def incident(self,success=True):
        i=Incidents('run-a','collection','train-shape')
        scope={'domain':'assembly_simulation/v1.2','source':'mujoco_preflight_copy','phase':'pick_close',
               'failure_code':'UNEXPECTED_CONTACT','shape':'square','first_response':'pick','contract':'pinned','geometry':{}}
        i.event('part-a','target-a',{'part_id':'part-a','source':'mujoco_preflight_copy','scope':scope,
                                  'failure':{'source':'mujoco_preflight_copy'}})
        i.event('part-a','target-a',{'part_id':'part-a','source':'live_simulation','installation_verified':True,
                                  'executed':True,'proposal':{'skill':'insert'},
                                  'verification':{'checks':dict.fromkeys(INSTALL_CHECKS,True)}})
        return i.finalize(success)[0],scope

    def card(self,e):
        return {'applies_when':'같은 단계에서 접촉이 검출된 경우.','strategy':'현재 기하를 확인해 파지 후보를 수정한다.',
                'checks':'현재 장면의 집기와 원래 설치 완료를 검증한다.','evidence_refs':[v['id'] for v in e['events']]}

    def proof(self,folder,e):
        folder=Path(folder)
        for row in e['events']: append(folder/'events.jsonl',row)
        write(folder/'final_verification.json',[{'part_id':e['part_id'],'checks':dict.fromkeys(INSTALL_CHECKS,True)}])
        write(folder/'resolved_config.json',{'source':'offline_contract_fixture'})
        write(folder/'prepared.json',{'source':'offline_contract_fixture'})

    def test_pick_or_preflight_is_not_solved(self):
        i=Incidents('r','collection','f')
        i.event('a','t',{'source':'mujoco_preflight_copy','failure':{'failure_code':'PART_LOST'}})
        i.event('a','t',{'source':'live_simulation','result':{'status':'success','skill':'pick'}})
        self.assertEqual(i.finalize(True)[0]['registration'],'pending')

    def test_failed_run_solved_local_is_pending(self):
        e,_=self.incident(False)
        self.assertEqual(e['state'],'solved_local'); self.assertEqual(e['registration'],'pending')

    def test_snapshot_does_not_see_current_run_registration(self):
        with tempfile.TemporaryDirectory() as folder:
            lib=Library(folder); e,s=self.incident(); card=self.card(e)
            self.proof(folder,e)
            lib.register(e,card,s,folder,guidance_source='offline_fixture')
            self.assertEqual(lib.retrieve(s)[0],[])
            next_run=Library(folder)
            cards,log=next_run.retrieve(s)
            self.assertEqual(log['mode'],'metadata_only'); self.assertEqual(len(cards),1)
            self.assertIsNone(cards[0]['score'])
            self.assertEqual(lib.register(e,card,s,folder,guidance_source='offline_fixture')['status'],'duplicate')

    def test_tampered_archive_and_scope_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            lib=Library(folder); e,s=self.incident(); self.proof(folder,e)
            lib.register(e,self.card(e),s,folder,guidance_source='offline_fixture')
            frozen=Library(folder)
            self.assertEqual(frozen.retrieve({**s,'phase':'insert_lower'})[0],[])
            path=next(Path(folder).glob('*/archive.json')); data=read(path); data['guidance']['strategy']='changed'; write(path,data)
            with self.assertRaises(ValueError): frozen.retrieve(s)

    def test_deferral_and_unsupported_numeric_claim(self):
        e,_=self.incident()
        self.assertFalse(validate_guidance({'applies_when':None,'strategy':None,'checks':None,'evidence_refs':[]},e))
        card=self.card(e); card['strategy']='999 mm로 움직인다.'
        with self.assertRaises(ValueError): validate_guidance(card,e)

    def test_same_code_different_source_is_not_same_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            lib=Library(folder); e,s=self.incident(); self.proof(folder,e)
            lib.register(e,self.card(e),s,folder,guidance_source='offline_fixture')
            self.assertEqual(Library(folder).retrieve({**s,'source':'live_simulation'})[0],[])

    def test_solved_boolean_cannot_substitute_for_installation_checks(self):
        i=Incidents('r','collection','f')
        i.event('a','t',{'source':'mujoco_preflight_copy','failure':{'failure_code':'PART_LOST'}})
        i.event('a','t',{'source':'live_simulation','executed':True,'installation_verified':True,
                         'proposal':{'skill':'pick'},'verification':{'checks':dict.fromkeys(INSTALL_CHECKS,True)}})
        self.assertEqual(i.finalize(True)[0]['registration'],'pending')

    def test_incident_must_match_original_execution_log(self):
        with tempfile.TemporaryDirectory() as folder:
            lib=Library(folder);e,s=self.incident();self.proof(folder,e)
            e['events'][0]['invented']='not present in execution log'
            with self.assertRaisesRegex(ValueError,'original execution'):
                lib.register(e,self.card(e),s,folder,guidance_source='offline_fixture')

    def test_wrong_encoder_does_not_fabricate_visual_scores(self):
        with tempfile.TemporaryDirectory() as folder:
            lib=Library(folder);e,s=self.incident();self.proof(folder,e)
            lib.register(e,self.card(e),s,folder,guidance_source='offline_fixture')
            class Unavailable:
                def encode(self,path): raise ImportError('Explicit offline missing-encoder fixture')
            f=Path(folder)/'query.png';f.write_bytes(b'not decoded by fixture')
            cards,receipt=Library(folder,encoder=Unavailable()).retrieve(s,image=f)
            self.assertEqual(receipt['mode'],'metadata_only');self.assertIsNone(cards[0]['score'])
            self.assertEqual(receipt['errors'][0]['kind'],'encoder_error')

    def test_numbers_in_ids_are_not_physical_units(self):
        e,_=self.incident();card=self.card(e);card['strategy']='1 mm를 보정한다.'
        with self.assertRaises(ValueError):validate_guidance(card,e)
        e['events'][0]['grasp']={'z_mm':12.0}
        card=self.card(e);card['strategy']='12 mm라는 기존 파지값을 현재 형상에서 다시 검사한다.'
        self.assertTrue(validate_guidance(card,e))
        card['strategy']='12 deg로 변경한다.'
        with self.assertRaises(ValueError):validate_guidance(card,e)

    def test_visual_ranking_uses_matching_failure_not_another_image_in_the_incident(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);run=root/'run';run.mkdir();libdir=root/'library'
            class ContractEncoder:
                meta={'source':'OFFLINE_VECTOR_FIXTURE_NOT_DINO','camera':'offline_fixture'}
                version=digest(meta)
                def encode(self,path):
                    v=np.zeros(384,np.float32);v[Path(path).read_bytes()[0]]=1;return v
            first=run/'first.png';second=run/'second.png';query=run/'query.png'
            first.write_bytes(bytes([0]));second.write_bytes(bytes([1]));query.write_bytes(bytes([0]))
            incidents=Incidents('images','collection','offline')
            _,scope=self.incident();other={**scope,'phase':'insert_transfer/preflight'}
            for image,sc in ((first,scope),(second,other)):
                incidents.event('part-a','target-a',{'part_id':'part-a','scope':sc,'source':'mujoco_preflight_copy',
                    'failure':{'failure_snapshot':{'status':'exact_detector_snapshot','png':image.name,
                                                  'png_sha256':sha(image),'camera_version':'offline_fixture'}}})
            incidents.event('part-a','target-a',{'part_id':'part-a','source':'live_simulation','installation_verified':True,
                           'executed':True,'proposal':{'skill':'insert'},'verification':{'checks':dict.fromkeys(INSTALL_CHECKS,True)}})
            e=incidents.finalize(True)[0];self.proof(run,e)
            Library(libdir,encoder=ContractEncoder()).register(e,self.card(e),scope,run,guidance_source='offline_fixture')
            cards,receipt=Library(libdir,encoder=ContractEncoder()).retrieve(other,image=query,image_camera='offline_fixture')
            self.assertEqual(receipt['mode'],'visual');self.assertEqual(cards[0]['score'],0.)
            self.assertEqual(cards[0]['matched_failure_id'],'images:event:2')
            cards,receipt=Library(libdir,encoder=ContractEncoder()).retrieve(other,image=query,image_camera='other_camera')
            self.assertEqual(receipt['mode'],'metadata_only');self.assertIsNone(cards[0]['score'])
            self.assertEqual(receipt['errors'][0]['kind'],'camera_version_mismatch')


if __name__=='__main__': unittest.main()
