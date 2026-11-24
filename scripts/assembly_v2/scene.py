"""Dynamic rigid mesh bodies; target poses are never substituted into main execution."""
from pathlib import Path
import mujoco
import numpy as np
from ur3e_with_gripper import build_robot_spec
from workcell_scene import compile_workcell,TABLE_GEOM
from assembly_v12.contracts import sha
from .transforms import wxyz

def body_name(pid,base):return 'assembly_base' if pid==base else 'workcell_part_'+pid

def build_scene(prepared,*,reference=False):
    spec=build_robot_spec();profile=prepared['profile'];completion=prepared['completion_profile']
    spec.option.timestep=completion['physics_timestep_s'];spec.option.gravity=completion['gravity_m_s2']
    spec.body('base').pos=profile['robot_base_world_m']
    center,size=prepared['table_center_world_m'],prepared['table_size_xy_m']
    table=spec.worldbody.add_body(name='workcell_table',pos=center)
    table.add_geom(name=TABLE_GEOM,type=mujoco.mjtGeom.mjGEOM_BOX,pos=[0,0,-.02],size=[size[0]/2,size[1]/2,.02],
                   friction=profile['friction'],rgba=[.53,.59,.64,1])
    for index,(pid,part) in enumerate(prepared['parts'].items()):
        fixed=pid==prepared['base_id'];name=body_name(pid,prepared['base_id'])
        t=np.asarray(part['target'] if reference or fixed else part['initial'])
        body=spec.worldbody.add_body(name=name,pos=t[:3,3],quat=wxyz(t))
        if not fixed:
            body.add_freejoint(name=name+'_free')
            body.mass=part['mass_kg'];body.ipos=part['asset']['center_mass_m']
            inertia=np.asarray(part['asset']['inertia_per_kg'])*part['mass_kg']
            body.fullinertia=[inertia[0,0],inertia[1,1],inertia[2,2],inertia[0,1],inertia[0,2],inertia[1,2]]
            body.explicitinertial=True
        folder=Path(part['asset']['folder'])
        for i,asset in enumerate([part['asset']['visual'],*part['asset']['hulls']]):
            path=folder/asset['path']
            if sha(path)!=asset['sha256']:raise ValueError('Collision/visual asset changed after preparation.')
            mesh_name=f'product_{pid}_mesh_{i}'
            spec.add_mesh(name=mesh_name,file=str(path))
            visual=i==0
            color=[.18,.42,.70,1] if fixed else [[.95,.55,.08,1],[.3,.72,.38,1],[.75,.35,.65,1]][index%3]
            body.add_geom(name=name+('_visual' if visual else f'_collision_{i-1}'),
                          type=mujoco.mjtGeom.mjGEOM_MESH,meshname=mesh_name,mass=0,
                          contype=0 if visual else 1,conaffinity=0 if visual else 1,
                          group=2 if visual else 3,rgba=color if visual else [0,0,0,0],
                          friction=profile['friction'])
    model,data=compile_workcell(spec,free_part_count=len(prepared['order']))
    if model.neq!=3:raise ValueError('No extra weld/attachment constraints permitted.')
    return model,data
