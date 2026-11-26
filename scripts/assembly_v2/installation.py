"""Geometry-derived approach clearance, separate from endpoint/path/dynamics checks."""
import numpy as np
import mujoco
from view_tcp import read_tcp_pose
from .geometry import load_mesh
from .scene import body_name
from .transforms import body_pose,apply


def geom_world_points(model,data,gid):
    kind=model.geom_type[gid]
    if kind==mujoco.mjtGeom.mjGEOM_MESH:
        mid=int(model.geom_dataid[gid]);start=int(model.mesh_vertadr[mid]);count=int(model.mesh_vertnum[mid])
        points=model.mesh_vert[start:start+count]
    elif kind==mujoco.mjtGeom.mjGEOM_BOX:
        corners=np.array([[x,y,z] for x in (-1,1) for y in (-1,1) for z in (-1,1)])
        points=corners*model.geom_size[gid]
    else:
        # Conservative sphere bounds for an unfamiliar collision primitive.
        extent=model.geom_rbound[gid]
        points=np.array([[x,y,z] for x in (-extent,extent) for y in (-extent,extent) for z in (-extent,extent)])
    return points@data.geom_xmat[gid].reshape(3,3).T+data.geom_xpos[gid]


def approach_offset(skills,goal,goal_tcp,*,axis_world=None,extra_m=0.):
    op=skills.prepared['parts'][skills.active]['operation']
    if op['kind']=='linear_insertion':
        axis=np.asarray(op['axis_world'],float);minimum=op['nominal_depth_m']+.02
        basis='defined_insertion_axis_and_actual_tool_envelope'
    else:
        if skills.prepared['profile'].get('placement_approach')=='gravity_opposed':
            axis=-np.asarray(skills.model.opt.gravity,float);axis/=np.linalg.norm(axis)
            basis='gravity_opposed_candidate_requiring_swept_collision_validation'
        else:
            axis=-goal_tcp[:3,2].copy();axis[2]=max(.25,axis[2]);axis/=np.linalg.norm(axis)
            basis='actual_held_tool_and_installed_geometry'
        minimum=.08
    if axis_world is not None:
        proposed=np.asarray(axis_world,float)
        if proposed.shape!=(3,) or not np.isfinite(proposed).all() or abs(np.linalg.norm(proposed)-1)>1e-6:raise ValueError('Invalid unit approach axis.')
        if op['kind']=='linear_insertion' and not np.allclose(proposed,axis,atol=1e-8,rtol=0):raise ValueError('Cannot change defined insertion axis.')
        axis=proposed;basis='policy_direction_with_current_geometry_envelope'
    if not np.isfinite(extra_m) or not 0<=extra_m<=.04:raise ValueError('Invalid extra clearance.')
    tcp_p,tcp_r=read_tcp_pose(skills.data)
    actual_tool=np.concatenate([geom_world_points(skills.model,skills.data,g) for g in skills.verifier.gripper_geoms])
    goal_tool=((actual_tool-tcp_p)@tcp_r)@goal_tcp[:3,:3].T+goal_tcp[:3,3]
    part=skills.prepared['parts'][skills.active]
    moving=np.concatenate([goal_tool,apply(goal,load_mesh(part['mesh']).vertices)])
    obstacles=[]
    for pid in sorted(skills.installed|{skills.prepared['base_id']}):
        row=skills.prepared['parts'][pid]
        obstacles.append(apply(body_pose(skills.data,body_name(pid,skills.prepared['base_id'])),load_mesh(row['mesh']).vertices))
    obstacle_max=float(np.max(np.concatenate(obstacles)@axis));moving_min=float(np.min(moving@axis))
    distance=max(minimum,obstacle_max-moving_min+.01)+extra_m
    # Only a proposed waypoint; full robot and swept held geometry are checked next.
    return axis*distance,{'basis':basis,'minimum_distance_m':minimum,'selected_distance_m':distance,
        'margin_m':.01,'policy_extra_m':extra_m,'obstacle_projection_max_m':obstacle_max,'moving_projection_min_m':moving_min,
        'included_obstacles':sorted(skills.installed|{skills.prepared['base_id']}),
        'not_a_feasibility_or_physical_success_check':True}
