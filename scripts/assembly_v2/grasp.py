"""Bounded antipodal surface candidates and full SE(3) proposals."""
from copy import deepcopy
import numpy as np
import trimesh
from scipy.spatial.transform import Rotation
from assembly_v12.contracts import digest,rigid
from .geometry import load_mesh
from .transforms import pose,inv,xyzw

VERSION='mesh_antipodal_full_pose/v2.0'
def candidates(part,current,revision,budget,profile,pad_center_tcp=(0.,0.,.006)):
    mesh=load_mesh(part['mesh']);limit=profile['resources'];seed=profile['seed']
    points,face=trimesh.sample.sample_surface(mesh,limit['surface_samples'],seed=seed)
    normals=mesh.face_normals[face]
    # Cast from just inside each surface towards the opposing physical surface.
    locations,rays,triangles=mesh.ray.intersects_location(points-normals*1e-6,-normals,multiple_hits=True)
    result=[];seen=set();rotation=np.asarray(current)[:3,:3]
    approaches=[rotation.T@v for v in (np.array([0.,0.,-1.]),np.array([0.,-1.,0.]),
                 np.array([1.,0.,0.]),np.array([-1.,0.,0.]),np.array([0.,1.,0.]),
                 np.array([0.,-1.,-1.])/np.sqrt(2),np.array([1.,0.,-1.])/np.sqrt(2),
                 np.array([0.,1.,-1.])/np.sqrt(2),np.array([-1.,0.,-1.])/np.sqrt(2))]
    raymap={}
    for p,r,f in zip(locations,rays,triangles):
        d=np.linalg.norm(p-points[r])
        if d>1e-5 and (r not in raymap or d<raymap[r][0]):raymap[r]=(d,p,f)
    # Prefer central contacts but retain different approach families before truncation.
    for rid,(width,opposite,fid) in sorted(raymap.items(),key=lambda item:np.linalg.norm((points[item[0]]+item[1][1])/2-mesh.center_mass)):
        budget.check('surface_candidates')
        if not .006<=width<=.077 or np.dot(normals[rid],mesh.face_normals[fid])>-.8:continue
        y=(opposite-points[rid])/width;middle=(points[rid]+opposite)/2
        for ai,z in enumerate(approaches):
            z=z-y*np.dot(z,y)
            if np.linalg.norm(z)<.3:continue
            z=z/np.linalg.norm(z);x=np.cross(y,z);r=np.column_stack((x,y,z))
            tcp=middle-r@np.asarray(pad_center_tcp)
            key=tuple(np.round(np.r_[tcp*1000,r.ravel()*10],1))
            if key in seen:continue
            seen.add(key)
            t=np.eye(4);t[:3,:3]=r;t[:3,3]=tcp
            world_z=rotation@z
            direction='top' if world_z[2]<-.9 else 'side' if abs(world_z[2])<.2 else 'oblique'
            result.append({'candidate_id':f'{part["part_id"]}:r{revision}:g{len(result)}','part_id':part['part_id'],
                'revision':revision,'observed_pose_hash':digest(np.asarray(current).tolist()),
                'mesh_sha256':part['mesh_sha256'],'collision_asset_hash':part['asset']['asset_hash'],
                'frame':'part_local','position_mm':(tcp*1000).tolist(),'quaternion_xyzw':xyzw(t),
                'opening_width_mm':float(min(85.,width*1000+8.)),'approach_axis_part':z.tolist(),'pregrasp_distance_m':.08,
                'generator_version':VERSION,'source':'python_geometry','approach_family':direction,'seed':seed,
                'geometry':{'contact_points_part_m':[points[rid].tolist(),opposite.tolist()],
                            'contact_normals_part':[normals[rid].tolist(),mesh.face_normals[fid].tolist()],
                            'contact_spacing_m':float(width),'pad_center_tcp_m':list(pad_center_tcp)},
                'checks':{'opposing_surfaces':'passed','opening_width':'passed','approach_sweep':'not_run',
                          'robot_ik':'not_run','physical_grasp':'not_run','installation':'not_run'}})
            if len(result)>=limit['max_raw_candidates']:break
        if len(result)>=limit['max_raw_candidates']:break
    # Round-robin by approach keeps side/oblique options in the model's bounded view.
    groups={key:[r for r in result if r['approach_family']==key] for key in ('top','side','oblique')}
    # Avoid presenting many near-identical central normals while omitting an
    # opposite approach that may be reachable by this one arm. The first central
    # sample is retained; subsequent samples greedily spread pose orientation.
    for family,rows in groups.items():
        if not rows:continue
        diverse=[rows.pop(0)]
        def separation(a,b):
            qa=np.asarray(a['quaternion_xyzw']);qb=np.asarray(b['quaternion_xyzw'])
            angle=2*np.arccos(np.clip(abs(qa@qb),0,1))
            distance=np.linalg.norm(np.asarray(a['position_mm'])-b['position_mm'])/100.
            return angle+min(distance,1.)
        while rows and len(diverse)<limit['max_presented_candidates']:
            index=max(range(len(rows)),key=lambda i:min(separation(rows[i],s) for s in diverse))
            diverse.append(rows.pop(index))
        groups[family]=diverse
    selected=[]
    while len(selected)<limit['max_presented_candidates'] and any(groups.values()):
        for key in groups:
            if groups[key] and len(selected)<limit['max_presented_candidates']:selected.append(groups[key].pop(0))
    return {'schema':VERSION,'raw_generated':len(result),'presented':selected,
            'omission_policy':'bounded central-contact seeds with farthest-pose diversity and approach-family round robin; not a proof of infeasibility',
            'revision':revision,'source':'python_geometry','candidate_generation_not_physical_success':True}

def resolve(value,pool,part,current,revision,policy_source):
    if not isinstance(value,dict) or value.get('mode') not in ('candidate','pose'):raise ValueError('Expected candidate or full pose.')
    source_id=None
    if value['mode']=='candidate':
        if set(value)!={'mode','candidate_id'}:raise ValueError('Unexpected candidate fields.')
        matches=[c for c in pool['presented'] if c['candidate_id']==value['candidate_id']]
        if len(matches)!=1:raise ValueError('Unknown candidate ID.')
        row=deepcopy(matches[0]);source_id=row['candidate_id']
        if row['revision']!=revision or row['observed_pose_hash']!=digest(np.asarray(current).tolist()):
            raise ValueError('Stale geometry candidate.')
        provenance='astra_candidate_selection' if policy_source=='astra' else 'scripted_candidate_selection'
    else:
        if set(value)!={'mode','frame','position_mm','quaternion_xyzw','opening_width_mm','parent_candidate_id'} or value['frame']!='part_local':
            raise ValueError('Full pose contract mismatch.')
        row=deepcopy(value);source_id=row['parent_candidate_id']
        if source_id is not None and source_id not in {c['candidate_id'] for c in pool['presented']}:raise ValueError('Stale parent candidate.')
        provenance=('astra_pose_revision' if source_id else 'astra_direct_pose') if policy_source=='astra' else 'development_pose'
    t=pose(np.asarray(row['position_mm'],float)*.001,row['quaternion_xyzw'])
    width=row['opening_width_mm']
    if type(width) not in (float,int) or not np.isfinite(width) or not 1<=width<=85:raise ValueError('Opening outside actual 2F-85 range.')
    bounds=np.asarray(part['asset']['bounds_m']);center=bounds.mean(0)
    if np.linalg.norm(t[:3,3]-center)>np.linalg.norm(bounds[1]-bounds[0])+.16:
        raise ValueError('Pose is unreasonably far from the actual surface; no bbox-interior TCP restriction is used.')
    return {'schema':'resolved_grasp/v2.0','part_id':part['part_id'],'revision':revision,
            'observed_pose_hash':digest(np.asarray(current).tolist()),'tcp_in_part':t.tolist(),
            'position_mm':(t[:3,3]*1000).tolist(),'quaternion_xyzw':xyzw(t),
            'approach_axis_world':(np.asarray(current)[:3,:3]@t[:3,2]).tolist(),
            'opening_width_mm':float(width),'pregrasp_distance_m':.08,'source':provenance,
            'parent_candidate_id':source_id,'submitted_grasp':deepcopy(value),'candidate_evidence':row if value['mode']=='candidate' else None}

GRASP_SCHEMA={'anyOf':[
    {'type':'object','properties':{'mode':{'type':'string','enum':['candidate']},'candidate_id':{'type':'string'}},
     'required':['mode','candidate_id'],'additionalProperties':False},
    {'type':'object','properties':{'mode':{'type':'string','enum':['pose']},'frame':{'type':'string','enum':['part_local']},
     'position_mm':{'type':'array','items':{'type':'number'},'minItems':3,'maxItems':3},
     'quaternion_xyzw':{'type':'array','items':{'type':'number'},'minItems':4,'maxItems':4},
     'opening_width_mm':{'type':'number'},'parent_candidate_id':{'type':['string','null']}},
     'required':['mode','frame','position_mm','quaternion_xyzw','opening_width_mm','parent_candidate_id'],'additionalProperties':False},
    {'type':'null'}]}
