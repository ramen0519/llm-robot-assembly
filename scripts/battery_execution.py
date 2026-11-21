"""Scripted CAD manipulation using existing Mink motion and original gripper servos."""
import math

import mujoco
import numpy as np
from scipy.spatial.transform import Rotation

from assembly_skills import DOWNWARD_TCP
from battery_scene import body_name
from failure_report import FailureCode, FailureEvent
from move_gripper import pad_gaps_mm
from view_tcp import read_tcp_pose
from workcell_motion import WorkcellMotion
from workcell_verification import PAD_NAMES, WorkcellVerifier, rotation_error


class BatteryVerifier(WorkcellVerifier):
    def __init__(self, model, registry, config, part_id, installed):
        self.model,self.registry,self.config=model,registry,config
        self.pid,self.installed=part_id,installed
        self.part_body=model.body(body_name(part_id)).id
        self.pads={model.geom(n).id:n for n in PAD_NAMES}
        self.owner={model.geom(n).id:p for p,r in registry.items() for n in r["collision_geoms"]}
        self.supports={model.geom("battery_pedestal_"+p).id:p for p in registry if p!="0"}
        self.jig=model.geom("battery_backing_jig").id
        self.target_position=np.asarray(registry[part_id]["target_position_world_m"])
        self.target_rotation=np.asarray(registry[part_id]["target_rotation_world"])
        self.half_size=np.asarray(registry[part_id]["size_m"])/2
        self.mating=False
        self.reference_other=None
        self.max_penetration=config["verification"]["maximum_penetration_m"]

    def contacts(self,data,*,allow_support,allow_grasp,planned=False):
        values={"supported":False,"left_contact":False,"right_contact":False,"penetration_m":0.,
                "target_supported":False,"support_force_n":0.,"left_force_n":0.,"right_force_n":0.}
        for index,contact in enumerate(data.contact):
            a,b=int(contact.geom1),int(contact.geom2)
            pa,pb=self.owner.get(a),self.owner.get(b)
            active=pa==self.pid or pb==self.pid
            other=b if pa==self.pid else a
            pad=other if other in self.pads else None
            grasp=active and pad is not None
            source_support=active and self.supports.get(other)==self.pid
            target_support=active and (other==self.jig or self.owner.get(other) in self.installed|{"0"})
            background=(pa is not None and self.supports.get(b)==pa or pb is not None and self.supports.get(a)==pb)
            installed_contact=(pa in self.installed and (b==self.jig or pb in self.installed|{"0"})
                               or pb in self.installed and (a==self.jig or pa in self.installed|{"0"}))
            allowed=(grasp and allow_grasp or source_support and allow_support
                     or target_support and self.mating and allow_support
                     or not active and (background or installed_contact))
            penetration=max(0.,-float(contact.dist))
            if not allowed or penetration>self.max_penetration:
                raise FailureEvent(FailureCode.PLANNED_CONTACT if planned else FailureCode.UNEXPECTED_CONTACT,
                    "Disallowed CAD contact or excessive penetration.",
                    violations=[{"geoms":[self.model.geom(a).name,self.model.geom(b).name],
                                 "distance_m":float(contact.dist),"allowed_pair":bool(allowed),
                                 "maximum_penetration_m":self.max_penetration}])
            values["penetration_m"]=max(values["penetration_m"],penetration)
            force=np.zeros(6)
            if not planned:
                mujoco.mj_contactForce(self.model,data,index,force)
            if force[0]>.005 and contact.dist<=0:
                if active and (source_support or target_support):
                    values["supported"]=True
                    values["target_supported"] |= target_support
                    values["support_force_n"]+=float(force[0])
                if grasp:
                    side="left" if "left" in self.pads[pad] else "right"
                    values[side+"_contact"]=True
                    values[side+"_force_n"]+=float(force[0])
        return values

    def sample(self,data,reference,*,allow_support,allow_grasp):
        speed,tracking=self.physical_state(data,reference)
        contacts=self.contacts(data,allow_support=allow_support,allow_grasp=allow_grasp)
        body=data.body(self.part_body)
        rotation=body.xmat.reshape(3,3)
        velocity=data.joint(body_name(self.pid)+"_free").qvel
        if self.reference_other is not None:
            for pid,(p,r) in self.reference_other.items():
                b=data.body(body_name(pid))
                if np.linalg.norm(b.xpos-p)>.0015 or rotation_error(b.xmat.reshape(3,3),r)>math.radians(2):
                    raise FailureEvent(FailureCode.PLACEMENT_NOT_CONFIRMED,"Previously placed or waiting part moved.",
                                       violations=[{"part_id":pid,"drift_m":float(np.linalg.norm(b.xpos-p))}])
        return {"simulation_time_s":float(data.time),"part_id":self.pid,"part_position_m":body.xpos.copy(),
                "part_rotation_world":rotation.copy(),"tcp_position_m":read_tcp_pose(data)[0],
                "part_bottom_clearance_m":float(body.xpos[2]-np.abs(rotation[2])@self.half_size-self.config["table_top_z_m"]),
                "part_speed_m_s":float(np.linalg.norm(velocity[:3])),
                "part_angular_speed_rad_s":float(np.linalg.norm(velocity[3:])),
                "arm_speed_rad_s":speed,"tracking_error_rad":tracking,
                "robot_speed_rad_s":float(np.max(np.abs(data.qvel[:14]))),
                "target_position_error_m":float(np.linalg.norm(body.xpos-self.target_position)),
                "target_orientation_error_rad":rotation_error(rotation,self.target_rotation),
                "pad_gap_mm":float(min(pad_gaps_mm(self.model,data))),**contacts}

    def verify_installation(self,rows):
        settings=self.config["verification"]
        checks={"target_support":all(r["target_supported"] for r in rows),
                "fingers_released":all(not r["left_contact"] and not r["right_contact"] and r["pad_gap_mm"]>80 for r in rows),
                "position":max(r["target_position_error_m"] for r in rows)<=settings["position_tolerance_m"],
                "angle":max(r["target_orientation_error_rad"] for r in rows)<=math.radians(settings["angle_tolerance_deg"]),
                "linear_stability":max(r["part_speed_m_s"] for r in rows)<.001,
                "angular_stability":max(r["part_angular_speed_rad_s"] for r in rows)<.01}
        if not all(checks.values()):
            raise FailureEvent(FailureCode.PLACEMENT_NOT_CONFIRMED,"Release/stability verification failed.",
                               violations=[checks])
        return checks


class BatteryExecution:
    def __init__(self,model,data,registry,config,on_sample,on_stage):
        self.model,self.data,self.registry,self.config=model,data,registry,config
        self.on_sample,self.on_stage=on_sample,on_stage
        self.installed=set()
        self.completed=[]
        self.verifier=None
        self.motion=None

    def stage(self,name):
        self.motion.stage=name
        self.on_stage(name)

    def install(self,pid):
        self.verifier=BatteryVerifier(self.model,self.registry,self.config,pid,self.installed)
        self.motion=WorkcellMotion(self.model,self.data,self.verifier,self.on_sample)
        self.stage("settle")
        self.motion.hold(2.,allow_support=True,allow_grasp=False)
        self.verifier.reference_other={p:(self.data.body(body_name(p)).xpos.copy(),
            self.data.body(body_name(p)).xmat.reshape(3,3).copy()) for p in self.registry if p not in ("0",pid)}
        position=self.data.body(body_name(pid)).xpos.copy()
        rotation=Rotation.from_euler("z",self.config["grasp_yaw_deg"],degrees=True).as_matrix()@DOWNWARD_TCP
        grasp=position+[0,0,self.config["grasp_tcp_above_body_center_m"]]
        self.stage("pick_hover")
        self.motion.move(grasp+[0,0,.12],rotation)
        self.stage("pick_lower")
        self.motion.move(grasp,rotation)
        self.stage("pick_close")
        count=0
        for i in range(1,math.ceil(4/self.motion.dt)+1):
            u=i*self.motion.dt/4
            self.motion.gripper_command=255*(3*u*u-2*u*u*u)
            row=self.motion.step(allow_support=True,allow_grasp=True)
            count=count+1 if row["left_contact"] and row["right_contact"] else 0
            if count*self.motion.dt>=.02:
                break
        else:
            raise FailureEvent(FailureCode.GRASP_NOT_CONFIRMED,"No bilateral contact.")
        self.motion.hold(1.,allow_support=True,allow_grasp=True,
                         gripper_target=min(255,float(self.data.ten_length[0])*255/.8+3))
        rows=self.motion.hold(.5,allow_support=True,allow_grasp=True)
        if not all(r["left_contact"] and r["right_contact"] for r in rows):
            raise FailureEvent(FailureCode.GRASP_NOT_CONFIRMED,"Unstable bilateral contact.")
        self.motion.held_pose=self.verifier.relative_part_pose(self.data)
        self.stage("pick_lift")
        p,r=read_tcp_pose(self.data)
        rows=self.motion.move(p+[0,0,.12],r,allow_support=True,allow_grasp=True)
        if any(v["supported"] or v["part_bottom_clearance_m"]<.05 for v in rows):
            raise FailureEvent(FailureCode.GRASP_NOT_CONFIRMED,"Lift did not separate part from support.")
        held_position,held_rotation=self.motion.held_pose
        target_rotation=self.verifier.target_rotation@held_rotation.T
        target_position=self.verifier.target_position-target_rotation@held_position
        self.stage("transfer")
        self.motion.move(target_position+[0,0,.12],target_rotation,allow_support=False,allow_grasp=True)
        self.stage("install_approach")
        self.motion.move(target_position+[0,0,.025],target_rotation,allow_support=False,allow_grasp=True)
        self.verifier.mating=True
        self.stage("install_lower")
        self.motion.move(target_position+[0,0,self.config["release_height_above_goal_m"]],target_rotation,
                         allow_support=True,allow_grasp=True)
        self.stage("release")
        self.motion.held_pose=None
        self.motion.hold(3.,allow_support=True,allow_grasp=True,gripper_target=0.)
        self.stage("retreat")
        p,r=read_tcp_pose(self.data)
        self.motion.move(p+[0,0,.12],r,allow_support=True,allow_grasp=False)
        self.stage("verify_stability")
        rows=self.motion.hold(self.config["verification"]["settle_seconds"],allow_support=True,allow_grasp=False)
        checks=self.verifier.verify_installation(rows)
        self.installed.add(pid)
        result={"part_id":pid,"checks":checks,
                "max_position_error_m":max(r["target_position_error_m"] for r in rows),
                "max_angle_error_rad":max(r["target_orientation_error_rad"] for r in rows),
                "on_declared_backing_jig":True}
        self.completed.append(result)
        return result
