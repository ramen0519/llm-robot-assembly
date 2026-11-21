"""Six source-CAD parts, one UR3e/2F-85 and explicit stationary fixtures."""
from pathlib import Path

import mujoco
import numpy as np
from scipy.spatial.transform import Rotation

from import_framework_battery import DEFAULT_BUNDLE, ROOT, read, sha, verify_bundle
from ur3e_with_gripper import build_robot_spec
from workcell_scene import compile_workcell

DEFAULT_CELL = ROOT / "config/framework_battery.example.json"
COLORS = [[.20,.35,.52,1],[.18,.48,.30,1],[.76,.55,.25,1],[.42,.51,.66,1],[.65,.34,.34,1],[.30,.59,.48,1]]


def body_name(pid):
    return "battery_part_"+pid


def build_scene(bundle=DEFAULT_BUNDLE, config_path=DEFAULT_CELL):
    bundle=Path(bundle)
    transfer=verify_bundle(bundle)
    config=read(config_path)
    collisions=read(bundle/config.get("collision_directory","collision")/"manifest.json")
    if collisions["transfer_sha256"]!=sha(bundle/"transfer.json"):
        raise ValueError("Collision meshes refer to different transfer.")
    for rel,digest in collisions["files_sha256"].items():
        if sha(bundle/rel)!=digest:
            raise ValueError("Collision mesh changed.")
    rotation=np.asarray(config["product_rotation_world"],dtype=float)
    if not np.allclose(rotation.T@rotation,np.eye(3)) or not np.isclose(np.linalg.det(rotation),1):
        raise ValueError("Invalid product rotation.")
    product_vertices=[]
    for part in transfer["parts"].values():
        center=np.asarray(part["assembled_body_position_product_m"])
        half=np.asarray(part["size_m"])/2
        for x in [-1,1]:
            for y in [-1,1]:
                for z in [-1,1]:
                    product_vertices.append(center+half*[x,y,z])
    product_vertices=np.asarray(product_vertices)
    product_center=(product_vertices.min(0)+product_vertices.max(0))/2
    rotated=(product_vertices-product_center)@rotation.T
    origin=np.array([*config["product_center_xy_m"],config["fixture_top_z_m"]-rotated[:,2].min()])
    translation=origin-rotation@product_center
    quat=Rotation.from_matrix(rotation).as_quat()[[3,0,1,2]]
    spec=build_robot_spec()
    spec.body("base").pos=config["robot_base_world_m"]
    table=spec.worldbody.add_body(name="battery_table",pos=[0,0,config["table_top_z_m"]])
    size=np.asarray(config["table_size_m"])
    table.add_geom(name="battery_tabletop",type=mujoco.mjtGeom.mjGEOM_BOX,
                   pos=[0,0,-size[2]/2],size=size/2,rgba=[.53,.59,.64,1])
    height=config["fixture_top_z_m"]-config["table_top_z_m"]
    jig=spec.worldbody.add_body(name="battery_jig",pos=[*config["product_center_xy_m"],config["table_top_z_m"]+height/2])
    jig.add_geom(name="battery_backing_jig",type=mujoco.mjtGeom.mjGEOM_BOX,
                 size=[*(np.asarray(config["fixture_size_xy_m"])/2),height/2],
                 friction=config["friction"],rgba=[.25,.28,.32,1])
    registry={}
    for pid,part in transfer["parts"].items():
        center=np.asarray(part["assembled_body_position_product_m"])
        target=translation+rotation@center
        half=np.asarray(part["size_m"])/2
        if pid==transfer["fixed_base"]:
            initial=target
        else:
            bottom_to_origin=float(np.abs(rotation[2])@half)
            initial=np.array([*config["supply_xy_m"][pid],
                              config["table_top_z_m"]+config["supply_pedestal_height_m"]+bottom_to_origin+.0003])
            support=spec.worldbody.add_body(name="battery_supply_"+pid)
            support.add_geom(name="battery_pedestal_"+pid,type=mujoco.mjtGeom.mjGEOM_BOX,
                             pos=[*initial[:2],config["table_top_z_m"]+config["supply_pedestal_height_m"]/2],
                             size=[.014,.014 if pid!="1" else .003,config["supply_pedestal_height_m"]/2],
                             rgba=[.30,.32,.34,1],friction=config["friction"])
        body=spec.worldbody.add_body(name=body_name(pid),pos=initial,quat=quat)
        if pid!=transfer["fixed_base"]:
            body.add_freejoint(name=body_name(pid)+"_free")
            # Explicit, documented uniform-box inertia. This geom never collides.
            body.add_geom(name="battery_inertia_"+pid,type=mujoco.mjtGeom.mjGEOM_BOX,size=half,
                          mass=config["assumed_mass_kg"][pid],contype=0,conaffinity=0,group=5,rgba=[0,0,0,0])
        visual="battery_visual_"+pid
        spec.add_mesh(name=visual,file=str((bundle/part["local_mesh"]).resolve()))
        body.add_geom(name=visual,type=mujoco.mjtGeom.mjGEOM_MESH,meshname=visual,
                      mass=0,contype=0,conaffinity=0,group=2,rgba=COLORS[int(pid)])
        names=[]
        for index,rel in enumerate(collisions["parts"][pid]["files"]):
            name=f"battery_collision_{pid}_{index}"
            spec.add_mesh(name=name,file=str((bundle/rel).resolve()))
            body.add_geom(name=name,type=mujoco.mjtGeom.mjGEOM_MESH,meshname=name,
                          mass=0,group=3,rgba=[*COLORS[int(pid)][:3],.35],
                          friction=config["friction"],solref=[.005,1],solimp=[.95,.99,.001,.5,2])
            names.append(name)
        registry[pid]={**part,"target_position_world_m":target.tolist(),
                       "target_rotation_world":rotation.tolist(),"initial_position_world_m":initial.tolist(),
                       "collision_geoms":names,"initial_support":"battery_pedestal_"+pid if pid!="0" else "battery_backing_jig"}
    model,data=compile_workcell(spec,free_part_count=5)
    if len([j for j in spec.joints if j.type==mujoco.mjtJoint.mjJNT_FREE])!=5:
        raise ValueError("Expected five unactuated parts.")
    return model,data,registry,config,transfer,spec


def audit_target_contacts(model,data,registry,installed_ids):
    """Inspect exact CAD targets in a calculation copy, without advancing live time."""
    scratch=mujoco.MjData(model)
    scratch.qpos[:]=data.qpos
    for pid in installed_ids:
        joint=scratch.joint(body_name(pid)+"_free")
        joint.qpos[:3]=registry[pid]["target_position_world_m"]
        joint.qpos[3:]=Rotation.from_matrix(registry[pid]["target_rotation_world"]).as_quat()[[3,0,1,2]]
    mujoco.mj_fwdPosition(model,scratch)
    owner={model.geom(n).id:pid for pid,part in registry.items() for n in part["collision_geoms"]}
    rows=[]
    for c in scratch.contact:
        a,b=owner.get(int(c.geom1)),owner.get(int(c.geom2))
        if a is not None and b is not None and a!=b:
            rows.append({"parts":[a,b],"geoms":[model.geom(c.geom1).name,model.geom(c.geom2).name],
                         "signed_distance_m":float(c.dist),"position_m":c.pos.tolist()})
    return {"source":"nominal_CAD_target_calculation_copy","physics_steps":0,
            "parts_placed_in_copy":list(installed_ids),"contacts":rows,
            "maximum_penetration_m":max([max(0,-r["signed_distance_m"]) for r in rows],default=0.),
            "acceptance_m":.0001}
