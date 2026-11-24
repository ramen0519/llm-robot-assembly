"""Raw CAD reference regression only; does not manufacture a native success chain."""
import argparse
from pathlib import Path
import shutil
import numpy as np
import mujoco
from assembly_v12.contracts import read,write,sha,digest,relative,rigid
from assembly_v2.geometry import load_mesh,prepare_asset
from assembly_v2.preparation import PROFILE,layout,relations_and_operations
from assembly_v2.completion import load_profile
from assembly_v2.budget import Budget
from assembly_v2.scene import build_scene
from assembly_v2.skills import MeshSkills
from assembly_v2.verification import observe_window,MeshVerifier
from assembly_v2.media import Recorder

def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--product',required=True);ap.add_argument('--output',required=True);ap.add_argument('--seconds',type=float,default=300)
    a=ap.parse_args();source=Path(a.product).absolute();out=Path(a.output).absolute();out.mkdir(parents=True,exist_ok=False)
    result={'scope':'reference_scene_only; no robot assembly or native disassembly claimed','api_calls':0,'status':'not_run'}
    try:
        raw=read(source);factor={'m':1.,'cm':.01,'mm':.001,'inch':.0254}[raw['unit']]
        base=raw['fixed_base'];order=[r['part_id'] for r in raw['parts'] if r['part_id']!=base];profile=read(PROFILE);budget=Budget(a.seconds)
        original={};goals={};assembly=[]
        for row in raw['parts']:
            pid=row['part_id'];m=load_mesh(relative(source.parent,row['mesh']),factor);t=rigid(row['assembly_matrix_mm']).copy();t[:3,3]*=.001
            if row['mesh_coordinate_frame']!='part_local':raise ValueError('This diagnostic requires explicit local source meshes.')
            original[pid]=m;goals[pid]=t;assembly.extend(m.vertices@t[:3,:3].T+t[:3,3])
        bounds=np.asarray(assembly);world=np.eye(4);world[:2,3]=np.asarray(profile['assembly_center_xy_m'])-(bounds.min(0)[:2]+bounds.max(0)[:2])/2
        world[2,3]=profile['tabletop_z_m']-bounds[:,2].min();parts={}
        for pid,m in original.items():
            path=out/'assets'/pid/'source_metric.obj';path.parent.mkdir(parents=True);m.export(path);asset=prepare_asset(path,path.parent/'collision',profile,budget)
            t=world@goals[pid];v=m.vertices@t[:3,:3].T+t[:3,3]
            parts[pid]={'part_id':pid,'mesh':str(path),'mesh_sha256':sha(path),'source_mesh_sha256':sha(path),'asset':asset,
                'mass_kg':max(.001,m.volume*profile['density_kg_m3']),'size':m.extents.tolist(),
                'original_goal':goals[pid].tolist(),'target':t.tolist(),'target_world_m':t[:3,3].tolist(),
                'target_bounds_world_m':[v.min(0).tolist(),v.max(0).tolist()],
                'part_path':{'status':'not_provided','reason':'reference regression only, no native proof'}}
        placement=layout(parts,base,order,profile,budget);pairs=relations_and_operations(parts,order,budget);completion=load_profile()
        p={'schema':'prepared_assembly/v2.0','validation_scope':'reference_fixture_without_native_chain','base_id':base,'order':order,
           'parts':parts,'profile':profile,'completion_profile':completion,'completion_profile_hash':digest(completion),
           'fixed_base_initial_pose':parts[base]['target'],'table_center_world_m':placement['table_center_world_m'],
           'table_size_xy_m':placement['table_size_xy_m'],'layout':placement,'allowed_goal_contact_pairs':pairs,
           'asset_hash':digest({pid:r['asset']['asset_hash'] for pid,r in parts.items()})}
        write(out/'prepared.json',p);model,data=build_scene(p,reference=True);mujoco.mj_saveModel(model,str(out/'reference_model.mjb'),None)
        skills=MeshSkills(model,data,p,order[0],set(order),{},budget);skills.verifier.reference_mode=True;skills.retreat_complete=True
        evidence=observe_window(skills,order,check_id='reference_regression',source='reference_scene',model_hash=sha(out/'reference_model.mjb'),prealignment={},reference=True)
        write(out/'reference_verification.json',evidence);result.update(reference=evidence['status'],part_count=len(parts))
        contacts=[]
        for pid in order:
            verifier=MeshVerifier(model,p,pid,set(order),{});verifier.reference_mode=True
            row=verifier.contacts(data,allow_support=True,allow_grasp=False)
            contacts.append({'active':pid,'status':'passed','hard_penetration_m':row['hard_penetration_m']})
        result['contact_order_checks']=contacts;result['status']='passed' if evidence['status']=='passed' else 'failed'
        recorder=Recorder(out,model,data,p);recorder.goal_images()
        # Goal/reference images only. No invented main execution video or states.
    except Exception as exc:result.update(status='failed',error=type(exc).__name__+': '+str(exc))
    write(out/'reference_result.json',result);print(out/'reference_result.json',flush=True)

if __name__=='__main__':main()
