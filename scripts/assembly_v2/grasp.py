"""Bounded antipodal surface candidates and full SE(3) proposals."""
from copy import deepcopy
import numpy as np
import trimesh
from scipy.spatial.transform import Rotation
from assembly_v12.contracts import digest,rigid
from .geometry import load_mesh
from .transforms import pose,inv,xyzw
from .tooling import calibrated_pad_center

VERSION='mesh_antipodal_full_pose/v2.8'
def diverse_poses(rows,count,budget):
    """Same farthest-pose rule with incremental vectorized distances and deadline checks."""
    if not rows:return []
    quats=np.asarray([r['quaternion_xyzw'] for r in rows],float)
    positions=np.asarray([r['position_mm'] for r in rows],float)/100.
    chosen=[0];minimum=np.full(len(rows),np.inf)
    while len(chosen)<min(count,len(rows)):
        budget.check('candidate_diversity')
        index=chosen[-1]
        angles=2*np.arccos(np.clip(np.abs(quats@quats[index]),0,1))
        distances=np.minimum(np.linalg.norm(positions-positions[index],axis=1),1.)
        minimum=np.minimum(minimum,angles+distances);minimum[chosen]=-np.inf
        chosen.append(int(np.argmax(minimum)))
    return [rows[i] for i in chosen]


def contact_seed_order(raymap,points,normals,mesh,minimum_width,budget):
    """Cover the actual surface BEFORE the raw pose cap, including axial ends.

    Start near the mass centre, then spread contact midpoints, antipodal normals
    and widths. This changes search coverage, not physical acceptance criteria.
    """
    items=[(rid,row) for rid,row in raymap.items()
           if minimum_width<=row[0]<=.077 and np.dot(normals[rid],mesh.face_normals[row[2]])<=-.8]
    if not items:return
    mids=np.asarray([(points[rid]+row[1])/2 for rid,row in items])
    normal=np.asarray([normals[rid] for rid,row in items])
    width=np.asarray([row[0] for _,row in items])
    extent=np.maximum(np.asarray(mesh.extents),.01)
    central=np.argsort(np.linalg.norm(mids-mesh.center_mass,axis=1),kind='stable')
    index=int(central[0]);central_cursor=0
    selected=np.zeros(len(items),dtype=bool);minimum=np.full(len(items),np.inf)
    for turn in range(len(items)):
        budget.check('contact_seed_diversity')
        selected[index]=True
        yield items[index]
        distance=np.linalg.norm((mids-mids[index])/extent,axis=1)
        distance+=.35*(1.-np.clip(np.abs(normal@normal[index]),0.,1.))
        distance+=.25*np.abs(width-width[index])/.077
        minimum=np.minimum(minimum,distance);minimum[selected]=-np.inf
        if (turn+1)%2:
            index=int(np.argmax(minimum))
        else:
            while central_cursor<len(items) and selected[central[central_cursor]]:central_cursor+=1
            if central_cursor<len(items):index=int(central[central_cursor])


def presented_families(ranked,count):
    """Keep top/side/oblique alternatives even when one status dominates."""
    groups={family:[row for row in ranked if row['approach_family']==family]
            for family in ('top','side','oblique')}
    output=[]
    while len(output)<count and any(groups.values()):
        for group in groups.values():
            if group and len(output)<count:output.append(group.pop(0))
    return output


def _candidate_pass(part,current,revision,budget,profile,pad_center_tcp=(0.,0.,.006),*,tcp_rotation=None,goal_screen=None,seed_coverage='central'):
    if profile.get('fingertip_concept') and not profile.get('grasp_pad_center_calibration'):
        raise ValueError('Experimental fingertips require measured empty-gripper contact calibration; stock TCP offsets cannot be assumed.')
    mesh=load_mesh(part['mesh']);limit=profile['resources'];seed=profile['seed']
    minimum_width=profile.get('minimum_contact_spacing_m',.006)
    if type(minimum_width) not in (int,float) or not np.isfinite(minimum_width) or not .001<=minimum_width<=.006:
        raise ValueError('Minimum contact spacing must be 1..6 mm; actual grasp is still required.')
    points,face=trimesh.sample.sample_surface(mesh,limit['surface_samples'],seed=seed)
    normals=mesh.face_normals[face]
    # Cast from just inside each surface towards the opposing physical surface.
    locations,rays,triangles=mesh.ray.intersects_location(points-normals*1e-6,-normals,multiple_hits=True)
    result=[];seen=set();rotation=np.asarray(current)[:3,:3]
    approaches=[rotation.T@v for v in (np.array([0.,0.,-1.]),np.array([0.,-1.,0.]),
                 np.array([1.,0.,0.]),np.array([-1.,0.,0.]),np.array([0.,1.,0.]),
                 np.array([0.,-1.,-1.])/np.sqrt(2),np.array([1.,0.,-1.])/np.sqrt(2),
                 np.array([0.,1.,-1.])/np.sqrt(2),np.array([-1.,0.,-1.])/np.sqrt(2))]
    # A stable supply pose can differ from the CAD installation orientation.
    # Generate approaches aligned at BOTH ends, then actually test the motion.
    source_approach_count=len(approaches)
    if 'target' in part:
        goal_rotation=np.asarray(part['target'])[:3,:3]
        approaches += [goal_rotation.T@rotation@v for v in approaches[:]]
    raymap={}
    for p,r,f in zip(locations,rays,triangles):
        d=np.linalg.norm(p-points[r])
        if d>1e-5 and (r not in raymap or d<raymap[r][0]):raymap[r]=(d,p,f)
    # A raw-pose cap must not consume its entire budget near the mesh centre.
    ordered=sorted(raymap.items(),key=lambda item:np.linalg.norm((points[item[0]]+item[1][1])/2-mesh.center_mass)) if seed_coverage=='central' else contact_seed_order(raymap,points,normals,mesh,minimum_width,budget)
    for rid,(width,opposite,fid) in ordered:
        budget.check('surface_candidates')
        if not minimum_width<=width<=.077 or np.dot(normals[rid],mesh.face_normals[fid])>-.8:continue
        y=(opposite-points[rid])/width;middle=(points[rid]+opposite)/2
        contact_center=calibrated_pad_center(profile,width,pad_center_tcp)
        for ai,z in enumerate(approaches):
            z=z-y*np.dot(z,y)
            if np.linalg.norm(z)<.3:continue
            z=z/np.linalg.norm(z);x=np.cross(y,z);r=np.column_stack((x,y,z))
            tcp=middle-r@contact_center
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
                'generator_version':VERSION,'source':'python_geometry','approach_family':direction,'seed':seed,'approach_reference':'current_world' if ai<source_approach_count else 'goal_world',
                'geometry':{'contact_points_part_m':[points[rid].tolist(),opposite.tolist()],
                            'contact_normals_part':[normals[rid].tolist(),mesh.face_normals[fid].tolist()],
                            'contact_spacing_m':float(width),'pad_center_tcp_m':contact_center.tolist()},
                'checks':{'opposing_surfaces':'passed','opening_width':'passed','approach_sweep':'not_run',
                          'robot_ik':'not_run','physical_grasp':'not_run','installation':'not_run'}})
            if len(result)>=limit['max_raw_candidates']:break
        if len(result)>=limit['max_raw_candidates']:break
    # Round-robin by approach keeps side/oblique options in the model's bounded view.
    groups={key:[r for r in result if r['approach_family']==key] for key in ('top','side','oblique')}
    # Avoid presenting many near-identical central normals while omitting an
    # opposite approach that may be reachable by this one arm. The first central
    # sample is retained; subsequent samples greedily spread pose orientation.
    screen_count=min(216,limit['max_presented_candidates']*12) if goal_screen else limit['max_presented_candidates']
    for family,rows in groups.items():
        # Include narrow and wide contacts, not only central opposing faces.
        if goal_screen and rows:
            ordered=sorted(rows,key=lambda r:r['geometry']['contact_spacing_m'])
            thirds=np.array_split(np.arange(len(ordered)),3)
            bands=[diverse_poses([ordered[i] for i in indexes],max(1,screen_count//3),budget) for indexes in thirds]
            diverse=[]
            while any(bands):
                for band in bands:
                    if band:diverse.append(band.pop(0))
            groups[family]=diverse[:screen_count]
        else:groups[family]=diverse_poses(rows,screen_count,budget)
    selected=[]
    while len(selected)<screen_count and any(groups.values()):
        for key in groups:
            if groups[key] and len(selected)<screen_count:selected.append(groups[key].pop(0))
    for row in selected:
        row['motion_hint']={'status':'not_measured' if tcp_rotation is None else 'orientation_estimate_only',
                            'approach_reference':row.get('approach_reference','current_world')}
        if part.get('target') is not None:
            local_rotation=Rotation.from_quat(row['quaternion_xyzw']).as_matrix()
            goal_tcp_rotation=np.asarray(part['target'])[:3,:3]@local_rotation
            row['motion_hint']['nominal_goal_tcp_axis_world']=goal_tcp_rotation[:,2].tolist()
            row['motion_hint']['goal_top_approach_deviation_rad']=float(np.arccos(np.clip(-goal_tcp_rotation[2,2],-1.,1.)))
            row['motion_hint']['goal_orientation_scope']='rigid grasp prediction only; measured grasp and endpoint IK still required'
        if tcp_rotation is not None:
            proposed=rotation@Rotation.from_quat(row['quaternion_xyzw']).as_matrix()
            row['motion_hint']['tcp_orientation_change_rad']=float(Rotation.from_matrix(np.asarray(tcp_rotation).T@proposed).magnitude())
            row['motion_hint']['not_a_feasibility_check']=True
    screen_summary=None
    if goal_screen:
        for row in selected:
            row['checks']['nominal_goal_tool']=goal_screen(row)
        # Nominal clearance only prioritizes the bounded model view. It never
        # replaces actual closure/lift or the measured-held installation test.
        rank={'clear_nominal':0,'not_run':1,'blocked_nominal':2}
        selected.sort(key=lambda r:(rank[r['checks']['nominal_goal_tool']['status']],
                        -min((x['minimum_distance_m'] for x in r['checks']['nominal_goal_tool'].get('gap_trials',[])),default=-1.),
                        r['motion_hint'].get('tcp_orientation_change_rad',0.)))
        screen_summary={'source':'nominal_tool_projection','screened':len(selected),
                        'counts':{k:sum(r['checks']['nominal_goal_tool']['status']==k for r in selected) for k in rank},
                        'all':[{ 'candidate_id':r['candidate_id'],'goal_tool':r['checks']['nominal_goal_tool']} for r in selected]}
        selected=presented_families(selected,limit['max_presented_candidates'])
    return {'schema':VERSION,'raw_generated':len(result),'presented':selected,'nominal_screen':screen_summary,
            'omission_policy':'bounded spatially diverse contact seeds, farthest-pose diversity and final approach-family round robin; not a proof of infeasibility',
            'revision':revision,'source':'python_geometry','candidate_generation_not_physical_success':True}



def _opening_views(pool,screen):
    if screen is not None and hasattr(screen,'source_openings'):
        for row in pool['presented']:
            result=(screen.opening_and_closure(row) if hasattr(screen,'opening_and_closure') else screen.source_openings(row));row['checks']['nominal_source_tool']=result
            opening=result.get('recommended_opening_mm')
            if opening is not None:
                row['opening_width_before_source_screen_mm']=row['opening_width_mm']
                row['opening_width_mm']=opening
            if hasattr(screen,'closure_preview'):
                diagnostic=deepcopy(row)
                if opening is None and result.get('approach_only_recommendation_mm') is not None:
                    diagnostic['opening_width_mm']=result['approach_only_recommendation_mm']
                row['checks']['nominal_full_tool_closure']=screen.closure_preview(diagnostic)
                if hasattr(screen,'closure_repairs'):
                    row['geometry_pose_alternatives']=screen.closure_repairs(diagnostic,row['checks']['nominal_full_tool_closure'])
    return pool


def candidates(part,current,revision,budget,profile,pad_center_tcp=(0.,0.,.006),*,tcp_rotation=None,goal_screen=None):
    """Preserve central coverage and add surface extremes within one raw cap.

    Small legacy budgets retain one pass. The explicit hour profile provides
    8,192 raw slots, split equally; two passes never receive two wall budgets.
    """
    if profile.get('fingertip_concept') and not profile.get('grasp_pad_center_calibration'):
        raise ValueError('Experimental fingertips require measured empty-gripper contact calibration; stock TCP offsets cannot be assumed.')
    cap=profile['resources']['max_raw_candidates']
    common=dict(tcp_rotation=tcp_rotation,goal_screen=goal_screen)
    if cap<1024 or goal_screen is None:
        return _opening_views(_candidate_pass(part,current,revision,budget,profile,pad_center_tcp,**common),goal_screen)
    modes=('central','spatial');pools=[];offset=0
    for index,mode in enumerate(modes):
        local=deepcopy(profile)
        local['resources']['max_raw_candidates']=cap//2 if index==0 else cap-cap//2
        pool=_candidate_pass(part,current,revision,budget,local,pad_center_tcp,seed_coverage=mode,**common)
        def identifier(value):
            prefix,number=value.rsplit('g',1)
            return prefix+'g'+str(int(number)+offset)
        for row in pool['presented']:
            row['candidate_id']=identifier(row['candidate_id']);row['seed_coverage']=mode
        if pool.get('nominal_screen'):
            for row in pool['nominal_screen']['all']:row['candidate_id']=identifier(row['candidate_id'])
        pools.append(pool);offset+=pool['raw_generated']
    rows=[row for pool in pools for row in pool['presented']]
    rank={'clear_nominal':0,'not_run':1,'blocked_nominal':2}
    rows.sort(key=lambda r:(rank[r['checks']['nominal_goal_tool']['status']],
              -min((x['minimum_distance_m'] for x in r['checks']['nominal_goal_tool'].get('gap_trials',[])),default=-1.),
              r['motion_hint'].get('tcp_orientation_change_rad',0.)))
    unique=[];seen=set()
    for row in rows:
        key=tuple(np.round(np.r_[row['position_mm'],np.asarray(row['quaternion_xyzw'])*1000],5))
        if key not in seen:unique.append(row);seen.add(key)
    all_screen=[row for pool in pools for row in pool['nominal_screen']['all']]
    pool={'schema':VERSION,'raw_generated':offset,
            'presented':presented_families(unique,profile['resources']['max_presented_candidates']),
            'nominal_screen':{'source':'nominal_tool_projection','screened':len(all_screen),
                'counts':{k:sum(r['goal_tool']['status']==k for r in all_screen) for k in rank},'all':all_screen},
            'seed_coverage':list(modes),'raw_count_includes_duplicate_trials':True,
            'omission_policy':'bounded central and spatial surface passes, family-balanced view; not a proof of infeasibility',
            'revision':revision,'source':'python_geometry','candidate_generation_not_physical_success':True}

    return _opening_views(pool,goal_screen)


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
