"""General CAD geometry, immutable goals, part paths and deterministic layout."""
from copy import deepcopy
from pathlib import Path
import math
import numpy as np
from scipy.spatial.transform import Rotation
import trimesh
from assembly_v12.contracts import InputError,check_export,read,write,digest,mesh,sha,rigid,relative
from .geometry import load_mesh,geometry_matches,prepare_asset,closest_distance
from .transforms import apply,inv
from .completion import load_profile
from .order_hint import AssemblyInput, SCHEMA as HINT_SCHEMA, UNITS as UNIT, read_hint

ROOT=Path(__file__).resolve().parents[2]
PROFILE=ROOT/'config/assembly_workcell.v2.json'

def source_geometry_check(root,contract,geometry,budget):
    for pid,row in contract['part_mapping'].items():
        budget.check('source_geometry')
        source=load_mesh(relative(root,row['source_mesh']),UNIT[geometry['source_unit']])
        native=load_mesh(relative(root,row['native_mesh']),.01)
        goal=rigid(row['assembly_matrix_mm']).copy();goal[:3,3]*=.001
        if row['mesh_coordinate_frame']=='assembly':source.apply_transform(inv(goal))
        if not geometry_matches(source,native):
            raise InputError('input_invalid','Source unit/placement does not match native geometry for '+pid)

def geometric_priority(folder,profile_path=None,fixed_base=None):
    """Ranks candidates only; the native planner must establish the actual chain."""
    folder=Path(folder);geometry=read(folder/'geometry.json')
    parts=geometry['parts']
    if not parts:raise InputError('input_incomplete','No CAD instances.')
    def score(p):
        size=np.asarray(p['bbox_size_mm']);center=np.asarray(p['bbox_center_mm'])
        return (center[2]-size[2]/2,-size[0]*size[1],str(p['part_id']))
    ranked=sorted(parts,key=score)
    aliases={p.get('source_part_id',p['part_id']):p['part_id'] for p in parts}
    base=aliases.get(fixed_base,fixed_base) or ranked[0]['part_id']
    if base not in {p['part_id'] for p in parts}:raise InputError('input_invalid','Unknown fixed base.')
    return {'fixed_base':base,'ranking':[p['part_id'] for p in reversed(ranked) if p['part_id']!=base],
            'source':'declared_base' if fixed_base else 'lowest_goal_support_extent_then_support_area',
            'fixed_fixture_assumption':'base bolted to table; native stability and separate reference still required',
            'not_robot_verified':True,'shape_classifier_required':False}

def decode_path(root,step,goal,world_transform):
    path=relative(root,step['path']);payload=np.load(path,allow_pickle=False)
    # Upstream packaged RedMax q is free-body xyz in cm and xyz Euler radians.
    if set(payload.files)!={'joint_q'}:raise InputError('input_invalid','Unknown native trajectory format.')
    q=payload['joint_q']
    if q.ndim!=2 or q.shape[1]!=6 or len(q)<2 or not np.isfinite(q).all():
        raise InputError('input_invalid','Invalid native free-body trajectory.')
    poses=[]
    for row in q:
        t=np.eye(4);t[:3,3]=row[:3]*.01;t[:3,:3]=Rotation.from_euler('xyz',row[3:]).as_matrix()
        poses.append(world_transform@t)
    # A different assembled search pose must never silently be treated as this CAD pose.
    valid=np.allclose(poses[0],goal,atol=2e-5,rtol=0)
    return {'source_path':step['path'],'sha256':sha(path),'source_frame':'native_assembly',
            'source_unit':'cm/rad','representation':'free_body_xyz_euler_xyz; NOT robot joints',
            'time_status':'not_provided_by_native_export','check_id':step['path_check'],
            'attempt':step['attempt'],'parent_uid':step['parent_uid'],'child_uid':step['child_uid'],
            'status':'usable_part_path' if valid else 'different_search_pose',
            'fallback_reason':None if valid else 'Native start differs from original CAD goal; keep order, replan robot path.',
            'world_part_poses':[t.tolist() for t in poses], 'robot_verified':False}

def choose_initial(asset,goal_rotation,mesh=None):
    transforms=np.asarray([x['transform'] for x in asset['stable_poses']]);probabilities=np.asarray([x['probability'] for x in asset['stable_poses']])
    if not len(transforms):raise InputError('layout_search_exhausted','No finite support-pose candidate.')
    # Supply parts must stand reliably before a robot approaches them. Goal
    # orientation is only a tie-breaker: the former angle-first ranking chose
    # narrow-edge equilibria (e.g. probability .00235 instead of .39428).
    # This geometric probability is NOT a measured physical success rate.
    order=sorted(range(len(transforms)),key=lambda i:(-round(float(probabilities[i]),12),
        float(Rotation.from_matrix(goal_rotation.T@transforms[i,:3,:3]).magnitude()),i))
    stability=[]
    if mesh is not None:
        from scipy.spatial import ConvexHull,QhullError
        for i,t in enumerate(transforms):
            points=np.asarray(mesh.vertices)@t[:3,:3].T
            com=np.asarray(asset['center_mass_m'])@t[:3,:3].T
            bottom=float(points[:,2].min());height=float(com[2]-bottom)
            support=points[points[:,2]<=bottom+1e-6,:2]
            margin=0.
            try:
                hull=ConvexHull(support)
                margin=float(np.min(-(hull.equations[:,:2]@com[:2]+hull.equations[:,2])))
            except QhullError:pass
            tip=float(np.arctan2(max(0.,margin),max(height,1e-9)))
            stability.append({'index':i,'com_support_margin_m':margin,'com_height_m':height,
                'minimum_tipping_angle_rad':tip,'robust_support_screen':bool(margin>=.003 and tip>=np.deg2rad(10))})
        robust=[row['index'] for row in stability if row['robust_support_screen']]
        if robust:
            order=sorted(robust,key=lambda i:(float(Rotation.from_matrix(goal_rotation.T@transforms[i,:3,:3]).magnitude()),
                                              -round(float(probabilities[i]),12),i))
    return transforms[order[0],:3,:3],{'source':'mesh_support_hull_center_of_mass',
        'support_screen':stability,'support_screen_scope':'static geometry only; native settling still required',
        'minimum_support_margin_m':.003,'minimum_tipping_angle_deg':10,

        'selection':'robust_support_then_goal_rotation' if mesh is not None and robust else 'stability_probability_first_goal_rotation_tiebreak', 'selected_index':order[0],
        'candidates':len(transforms),'selected_probability':float(probabilities[order[0]]),'physical_settling':'not_run'}

def layout(parts,base,order,profile,budget):
    bounds=np.array([p['target_bounds_world_m'] for p in parts.values()])
    lo,hi=bounds[:,0].min(0),bounds[:,1].max(0)
    reserved=(lo[:2]-profile['grid_margin_m'],hi[:2]+profile['grid_margin_m'])
    used=[];attempts=[];fixtures=[]
    supply=profile.get('supply_pedestal',{})
    if supply.get('enabled'):
        if (set(supply)!={'enabled','height_m','maximum_part_height_m','short_axis_fraction','long_axis_fraction'} or
            not 0<supply['height_m']<=.08 or not 0<supply['maximum_part_height_m']<=.10 or
            not 0<supply['short_axis_fraction']<=supply['long_axis_fraction']<=1):
            raise InputError('input_invalid','Invalid common supply pedestal geometry settings.')
    robot=np.asarray(profile['robot_base_world_m'])
    for pid in order:
        p=parts[pid];m=load_mesh(p['mesh']);r,note=choose_initial(p['asset'],np.asarray(p['target'])[:3,:3],m)
        v=m.vertices@r.T;low,high=v.min(0),v.max(0)
        ext=high-low;stride=max(.07,float(max(ext[:2]))+profile['grid_margin_m'])
        chosen=None
        for cell in range(profile['max_grid_cells']):
            budget.check('layout_search')
            row,col=divmod(cell,8)
            center=np.asarray(profile['grid_first_center_xy_m'])+[-col*stride,row*stride]
            box=(center-ext[:2]/2,center+ext[:2]/2)
            intersects=lambda a,b:bool(np.all(a[0]<b[1]) and np.all(b[0]<a[1]))
            reason=None
            if intersects(box,reserved):reason='goal_and_approach_reserved'
            elif any(intersects((box[0]-.015,box[1]+.015),b) for b in used):reason='supply_overlap'
            elif np.linalg.norm(center-robot[:2])>.50:reason='robot_radial_screen'
            elif np.linalg.norm(center-robot[:2])<.12:reason='robot_mount_reserved'
            attempts.append({'part_id':pid,'cell':cell,'center_xy_m':center.tolist(),'rejected':reason})
            if reason:continue
            t=np.eye(4);t[:3,:3]=r;t[:2,3]=center-(low[:2]+high[:2])/2
            t[2,3]=profile['tabletop_z_m']-low[2]+profile['spawn_clearance_m']
            if supply.get('enabled') and ext[2]<=supply['maximum_part_height_m']:
                height=supply['height_m'];width=ext[:2]*supply['short_axis_fraction']
                axis=int(np.argmax(ext[:2]));width[axis]=ext[axis]*supply['long_axis_fraction']
                from .supply_support import pedestal_footprint
                width,support_note=pedestal_footprint(m,r,width)
                note={**note,'finite_support_screen':support_note}
                if width is not None:
                    t[2,3]+=height
                    fixture={'part_id':pid,'body_name':'supply_pedestal_'+pid,'geom_name':'supply_pedestal_'+pid+'_collision',
                        'kind':'fixed_supply_pedestal','center_world_m':[*center,profile['tabletop_z_m']+height/2],
                        'size_xyz_m':[*width,height],'top_z_m':profile['tabletop_z_m']+height,
                        'source':'common_profile_and_stable_mesh_footprint','support':'gravity_and_contact_only',
                        'physical_settling':'not_run','assumption':'Requires this explicit physical supply fixture; no weld or extra arm.'}
                    fixtures.append(fixture)
                    note={**note,'supply_fixture':fixture['geom_name'],'support_plane_z_m':fixture['top_z_m']}
            chosen=t;used.append(box);break
        if chosen is None:raise InputError('layout_search_exhausted',json_message({'part':pid,'attempts':attempts[-20:]}))
        p['initial']=chosen.tolist();p['initial_pose_evidence']=note
    corners=[lo[:2],hi[:2],robot[:2]-.12,robot[:2]+.12]+[x for b in used for x in b]
    low=np.min(corners,0)-profile['table_margin_m'];high=np.max(corners,0)+profile['table_margin_m']
    return {'supply_fixtures':fixtures,'reserved_goal_xy_m':[reserved[0].tolist(),reserved[1].tolist()],
            'attempts':attempts,'geometry_status':'passed','settling_status':'not_run','robot_access_status':'not_run',
            'table_center_world_m':[*((low+high)/2),profile['tabletop_z_m']],
            'table_size_xy_m':np.maximum(high-low,profile['table_min_size_xy_m']).tolist()}

def json_message(value):
    import json
    return json.dumps(value)

def relations_and_operations(parts,order,budget):
    placed={};pairs=[]
    for pid,p in parts.items():
        m=load_mesh(p['mesh']);m.apply_transform(p['target']);placed[pid]=m
    ids=list(parts)
    for i,a in enumerate(ids):
        for b in ids[i+1:]:
            budget.check('goal_adjacency')
            ma,mb=placed[a],placed[b]
            if np.any(ma.bounds[0]>.002+mb.bounds[1]) or np.any(mb.bounds[0]>.002+ma.bounds[1]):continue
            adjacent=False
            for first,second in ((ma,mb),(mb,ma)):
                sample=np.concatenate([first.vertices,first.triangles_center])
                if len(sample)>2048:sample=sample[np.linspace(0,len(sample)-1,2048,dtype=int)]
                distance=closest_distance(second,sample)
                if np.min(distance)<=.001:adjacent=True;break
            if adjacent:pairs.append(sorted([a,b]))
    for pid in order:
        p=parts[pid];p['mating_ids']=[b if a==pid else a for a,b in pairs if pid in (a,b)]
        p['operation']={'kind':'generic_goal_placement','not_applicable_reason':
            'No geometrically verified straight insertion axis/depth/reference surfaces; CAD pose and physical support are checked.'}
        path=p.get('part_path',{});poses=np.asarray(path.get('world_part_poses',[]))
        if path.get('status')!='usable_part_path' or len(poses)<2:continue
        delta=poses[-1,:3,3]-poses[0,:3,3]
        if np.linalg.norm(delta)<.002:continue
        axis=delta/np.linalg.norm(delta)
        translations=poses[:,:3,3]-poses[0,:3,3]
        perpendicular=translations-np.outer(translations@axis,axis)
        straight=np.max(np.linalg.norm(perpendicular,axis=1))<.0001 and np.max(np.abs(poses[:,:3,:3]-poses[0,:3,:3]))<1e-5
        if not straight:continue
        own=placed[pid];leading=float(np.min(own.vertices@axis))
        for other in p['mating_ids']:
            support=placed[other]
            if support.is_convex:continue
            # A through-hole/socket is detected from empty volume inside the mating hull,
            # not from a component label. The mouth plane comes from actual surface extent.
            probe=own.triangles_center
            nested=support.convex_hull.contains(probe)
            if not nested.any():continue
            mouth=float(np.max(support.vertices@axis));depth=mouth-leading
            if depth<=.001 or depth>float(np.ptp(own.vertices@axis))+.001:continue
            p['operation']={'kind':'linear_insertion','axis_world':axis.tolist(),'goal_leading_projection_m':leading,
                'mouth_projection_m':mouth,'nominal_depth_m':depth,'support_id':other,
                'geometry_evidence':{'native_path_sha256':path['sha256'],'mating_mesh_sha256':parts[other]['mesh_sha256'],
                                     'nested_surface_samples':int(nested.sum()),'definition':'surface mouth/leading support projection along verified straight part removal'}}
            break
    return pairs

def read_assembly_input(path, budget):
    path = Path(path).absolute()
    if read(path).get('schema') == HINT_SCHEMA:
        return read_hint(path, budget)[0]
    contract, geometry = check_export(path)
    source_geometry_check(path.parent, contract, geometry, budget)
    steps = contract['verified_transitions']
    if any(step.get('additional_support') for step in steps):
        raise InputError('support_not_available','One arm with only the declared fixed base; extra native supports are not supplied.')
    candidate = contract['assembly_candidate']
    return AssemblyInput(path, candidate['initial_base'], candidate['add_parts'], contract['part_mapping'],
                         geometry['source_unit'], contract['run_hash'], contract['source_hash'], steps)


def metric_part_mesh(source, row, goal):
    if not source.advisory:
        return load_mesh(relative(source.path.parent, row['native_mesh']), .01)
    mesh = load_mesh(relative(source.path.parent, row['source_mesh']), UNIT[source.unit])
    if row['mesh_coordinate_frame'] == 'assembly':
        mesh.apply_transform(inv(goal))
    return mesh


def prepare(root,folder,profile_path,budget, input_name='export_v1.json'):
    root,folder=Path(root).absolute(),Path(folder).absolute()
    source = read_assembly_input(root/input_name, budget)
    profile=read(profile_path or PROFILE);completion=load_profile()
    base, order = source.base, source.order
    parts={};total=0
    global_t=np.eye(4);global_t[:3,:3]=Rotation.from_quat(profile['assembly_rotation_xyzw']).as_matrix()
    assembled=[]
    for pid,row in source.parts.items():
        budget.check('part_normalization')
        goal=rigid(row['assembly_matrix_mm']).copy();goal[:3,3]*=.001
        m=metric_part_mesh(source,row,goal)
        total+=len(m.vertices)
        if total>profile['resources']['max_vertices_total']:raise InputError('resource_limit','Mesh vertex budget exceeded.')
        assembled.append(apply(global_t@goal,m.vertices))
        target=folder/'assets'/pid/'source_metric.obj';target.parent.mkdir(parents=True,exist_ok=True);m.export(target,digits=16)
        parts[pid]={'part_id':pid,'source_part_id':row.get('source_part_id',pid),'mesh':str(target),
            'mesh_sha256':sha(target),'source_mesh_sha256':row['source_sha256'],'original_goal':goal.tolist(),
            'cad_to_body':np.eye(4).tolist(),'body_origin':'original_part_local_origin',
            'source_coordinate_frame':row['mesh_coordinate_frame'],'source_unit':source.unit}
    allv=np.concatenate(assembled);baseverts=assembled[list(parts).index(base)]
    global_t[:2,3]=np.asarray(profile['assembly_center_xy_m'])-(allv[:,:2].min(0)+allv[:,:2].max(0))/2
    global_t[2,3]=profile['tabletop_z_m']-baseverts[:,2].min()
    for pid,p in parts.items():
        m=load_mesh(p['mesh']);goal=global_t@np.asarray(p['original_goal']);p['target']=goal.tolist()
        p['target_world_m']=goal[:3,3].tolist();p['size']=m.extents.tolist()
        v=apply(goal,m.vertices);p['target_bounds_world_m']=[v.min(0).tolist(),v.max(0).tolist()]
        asset=prepare_asset(p['mesh'],folder/'assets'/pid/'collision',profile,budget);p['asset']=asset
        p['mass_kg']=max(profile['minimum_mass_kg'],asset['volume_m3']*profile['density_kg_m3'])
        p['mass_source']='assumed_common_density; original_mesh_volume_and_inertia'
    if sum(len(p['asset']['hulls']) for p in parts.values())>profile['resources']['max_collision_geoms']:
        raise InputError('resource_limit','Collision geom budget exceeded.')
    for step in source.steps:
        pid=step['part'];parts[pid]['part_path']=decode_path(root,step,np.asarray(parts[pid]['target']),global_t)
        parts[pid]['support_conditions']={k:step.get(k) for k in ('fixed_base','additional_support','pose','direction','stability_checks')}
    placement=layout(parts,base,order,profile,budget)
    if sum(len(p['asset']['hulls']) for p in parts.values())+len(placement.get('supply_fixtures',[]))>profile['resources']['max_collision_geoms']:
        raise InputError('resource_limit','Product plus supply fixture collision geometry budget exceeded.')
    pairs=relations_and_operations(parts,order,budget)
    prepared={'schema':'prepared_assembly/v2.0','base_id':base,'order':order,'parts':parts,'profile':profile,
        'completion_profile':completion,'completion_profile_hash':digest(completion),
        'common_world_from_assembly':global_t.tolist(),'fixed_base_initial_pose':parts[base]['target'],
        'table_center_world_m':placement['table_center_world_m'],'table_size_xy_m':placement['table_size_xy_m'],
        'layout':placement,'allowed_goal_contact_pairs':pairs,'source_run_hash':source.run_hash,
        'profile_path':str(Path(profile_path or PROFILE).absolute()),'profile_hash':digest(profile),
        'source_geometry_hash':source.geometry_hash,
        'asset_hash':digest({pid:p['asset']['asset_hash'] for pid,p in parts.items()}),
        'manual_product_adjustments':False,'reference_status':'not_run','physics_status':'not_run'}
    if source.advisory:
        prepared.update(input_hint=str(source.path), order_semantics='advisory_base_first')
    else:
        prepared['input_export']=str(source.path)
    write(folder/'prepared.json',prepared);return prepared
