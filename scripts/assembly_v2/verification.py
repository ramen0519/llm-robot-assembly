"""General contact roles, original-frame velocities and common completion evidence."""
import math
import mujoco
import numpy as np
from failure_report import FailureCode,FailureEvent
from move_gripper import pad_gaps_mm
from view_tcp import read_tcp_pose
from workcell_verification import WorkcellVerifier,PAD_NAMES,rotation_error
from workcell_scene import TABLE_GEOM
from .geometry import load_mesh
from .scene import body_name
from .transforms import body_pose,errors
from .completion import ObservationWindow

class MeshVerifier(WorkcellVerifier):
    def __init__(self,model,prepared,active,installed,settled):
        self.model,self.prepared,self.active,self.installed,self.settled=model,prepared,active,installed,settled
        self.base=prepared['base_id'];self.part_body=model.body(body_name(active,self.base)).id
        self.pads={model.geom(n).id:n for n in PAD_NAMES}
        self.bodies={pid:model.body(body_name(pid,self.base)).id for pid in prepared['parts']}
        self.body_part={v:k for k,v in self.bodies.items()}
        self.geom_part={g:self.body_part[int(model.geom_bodyid[g])] for g in range(model.ngeom)
                        if int(model.geom_bodyid[g]) in self.body_part and (model.geom_contype[g] or model.geom_conaffinity[g])}
        self.table_geom=model.geom(TABLE_GEOM).id
        self.robot_geoms={g for g in range(model.ngeom) if g not in self.geom_part and
                          (model.geom_contype[g] or model.geom_conaffinity[g]) and
                          model.geom(g).name not in ('floor',TABLE_GEOM) and model.geom_bodyid[g]!=0}
        self.gripper_geoms={g for g in self.robot_geoms if model.geom(g).name.startswith('gripper_')}
        self.goal_pairs={frozenset(pair) for pair in prepared['allowed_goal_contact_pairs']}
        self.mating=False;self.reference_mode=False;self.last_contacts={};self.failure_observer=None;self.stage='initialise'
        self.target_position=np.asarray(prepared['parts'][active]['target'])[:3,3]
        self.target_rotation=np.asarray(prepared['parts'][active]['target'])[:3,:3]
        self.base_rotation=np.asarray(prepared['fixed_base_initial_pose'])[:3,:3]
        self.max_hard_history=0.;self.hard_violation=None
        self.vertices=load_mesh(prepared['parts'][active]['mesh']).vertices

    def near_goal(self,data,pid):
        if pid==self.base:return True
        t=body_pose(data,body_name(pid,self.base));goal=np.asarray(self.prepared['parts'][pid]['target'])
        xyz,angle=errors(goal,t,self.base_rotation)
        if pid!=self.active or not self.mating:return bool(np.all(xyz<=.005+1e-12) and angle<=math.radians(3)+1e-12)
        op=self.prepared['parts'][pid]['operation']
        if op['kind']=='linear_insertion':
            delta=t[:3,3]-goal[:3,3];axis=np.asarray(op['axis_world'])
            return np.linalg.norm(delta-axis*np.dot(delta,axis))<=.004 and angle<=math.radians(3)
        return np.linalg.norm(t[:3,3]-goal[:3,3])<=.015 and angle<=math.radians(3)

    def contacts(self,data,*,allow_support,allow_grasp,planned=False):
        sides={'left':False,'right':False};support={p:0. for p in self.bodies};rows=[];hard=0.;pad_pen=0.;robot_contacts=set()
        for index,c in enumerate(data.contact):
            a,b=int(c.geom1),int(c.geom2);pa,pb=self.geom_part.get(a),self.geom_part.get(b)
            if c.dist>1e-7:continue
            active_geom= a if pa==self.active else b if pb==self.active else None
            other= b if pa==self.active else a if pb==self.active else None
            grasp=active_geom is not None and other in self.pads
            roles=[('product:'+p if p is not None else 'pad' if g in self.pads else 'gripper' if g in self.gripper_geoms else 'robot' if g in self.robot_geoms else 'table' if g==self.table_geom else 'environment') for g,p in ((a,pa),(b,pb))]
            facts={'geoms':[self.model.geom(a).name,self.model.geom(b).name],'parts':[pa,pb],
                   'roles':roles,'signed_distance_m':float(c.dist),'position_world_m':c.pos.tolist()}
            allowed=False
            if pa is not None and pb is not None:
                pair=frozenset((pa,pb))
                allowed=pair in self.goal_pairs and self.near_goal(data,pa) and self.near_goal(data,pb) and (
                    self.reference_mode or {pa,pb}<=(self.installed|{self.base}) or self.mating)
            elif pa is not None or pb is not None:
                pid=pa if pa is not None else pb;counter=b if pa is not None else a
                if counter==self.table_geom:
                    allowed=(pid==self.base or pid not in self.installed and pid!=self.active or
                             pid==self.active and allow_support or pid in self.installed and self.near_goal(data,pid))
                elif counter in self.robot_geoms:
                    robot_contacts.add(pid)
                    allowed=allow_grasp and grasp
            # Robot/environment and self contacts remain forbidden by the existing model.
            if not allowed:
                raise FailureEvent(FailureCode.PLANNED_CONTACT if planned else FailureCode.UNEXPECTED_CONTACT,
                                   'Unpermitted measured contact.',violations=[facts],phase=self.stage)
            penetration=max(0.,-float(c.dist))
            if grasp:pad_pen=max(pad_pen,penetration)
            elif pa is not None or pb is not None:hard=max(hard,penetration)
            force=np.zeros(6)
            if not planned:mujoco.mj_contactForce(self.model,data,index,force)
            world=c.frame.reshape(3,3).T@force[:3]
            # Fingers carrying a part are not fixture/table support. Contact
            # normal ordering is independent of the symmetric allowed pair.
            if pa is not None and (pb is not None or b==self.table_geom):support[pa]+=max(0.,-float(world[2]))
            if pb is not None and (pa is not None or a==self.table_geom):support[pb]+=max(0.,float(world[2]))
            if grasp and force[0]>.01:sides['left' if 'left' in self.pads[other] else 'right']=True
            if self.mating and not grasp and active_geom is not None and force[0]>8.:
                raise FailureEvent(FailureCode.INSERTION_CONTACT_LIMIT,'Measured contact-point normal force >8 N.',violations=[{**facts,'normal_force_n':float(force[0])}],phase=self.stage)
            rows.append(facts)
        limit=self.prepared['completion_profile']['execution_hard_contact_penetration_max_m']
        if not planned:self.max_hard_history=max(self.max_hard_history,hard)
        if hard>limit+1e-12 or pad_pen>.003:
            if not planned:self.hard_violation={'time_s':float(data.time),'hard_penetration_m':hard,'contacts':rows}
            raise FailureEvent(FailureCode.PLANNED_CONTACT if planned else FailureCode.UNEXPECTED_CONTACT,
                               'Hard penetration >1 mm or compliant-pad penetration >3 mm.',
                               violations=[{'hard_penetration_m':hard,'pad_penetration_m':pad_pen,'contacts':rows}],phase=self.stage)
        self.last_contacts={'rows':rows,'robot_contact_parts':sorted(robot_contacts),'support_force_n':support,'hard_penetration_m':hard}
        return {'supported':support[self.active]>.01,'left_contact':sides['left'],'right_contact':sides['right'],
                'penetration_m':max(hard,pad_pen),'hard_penetration_m':hard}

    def velocity(self,data,pid):
        velocity=np.zeros(6)
        # XBODY uses the original body-frame origin, not the mass center.
        mujoco.mj_objectVelocity(self.model,data,mujoco.mjtObj.mjOBJ_XBODY,self.bodies[pid],velocity,0)
        return float(np.linalg.norm(velocity[3:])),float(np.linalg.norm(velocity[:3]))

    def preserve(self,data):
        for pid,original in self.settled.items():
            if pid==self.active:continue
            current=body_pose(data,body_name(pid,self.base))
            if pid in self.installed:
                xyz,angle=errors(self.prepared['parts'][pid]['target'],current,self.base_rotation)
                bad=np.any(xyz>.005+1e-12) or angle>math.radians(3)+1e-12
            else:
                xyz,angle=errors(original,current,np.eye(3));bad=np.linalg.norm(xyz)>.001 or angle>math.radians(1)
            if bad:raise FailureEvent(FailureCode.PLACEMENT_NOT_CONFIRMED,'A different part moved beyond its preservation policy.',
                  violations=[{'part_id':pid,'installed':pid in self.installed,'position_error_xyz_m':xyz.tolist(),'rotation_error_rad':angle}],phase=self.stage)

    def sample(self,data,reference,*,allow_support,allow_grasp):
        speed,tracking=self.physical_state(data,reference)
        contacts=self.contacts(data,allow_support=allow_support,allow_grasp=allow_grasp)
        self.preserve(data)
        t=body_pose(data,body_name(self.active,self.base));tcp,_=read_tcp_pose(data)
        ls,ws=self.velocity(data,self.active)
        vertices=self.vertices
        bottom=float((vertices@t[:3,:3].T+t[:3,3])[:,2].min())
        return {'simulation_time_s':float(data.time),'stage':self.stage,'part_pose':t.tolist(),
                'tcp_world_m':tcp.tolist(),'part_speed_m_s':ls,'part_angular_speed_rad_s':ws,
                'part_bottom_clearance_m':bottom-self.prepared['profile']['tabletop_z_m'],
                'pad_gap_mm':float(min(pad_gaps_mm(self.model,data))),
                'robot_speed_rad_s':float(np.max(np.abs(data.qvel[:14]))),'arm_speed_rad_s':speed,
                'tracking_error_rad':tracking,**contacts}

    def relative_part_pose(self,data):
        p,r=read_tcp_pose(data);body=data.body(self.part_body)
        return r.T@(body.xpos-p),r.T@body.xmat.reshape(3,3)

    def clearance(self,data,pid):
        distance=.10
        for pg,part in self.geom_part.items():
            if part!=pid:continue
            for g in self.gripper_geoms:
                # Broad-phase only skips pairs whose bounding spheres prove clearance.
                lower=np.linalg.norm(data.geom_xpos[g]-data.geom_xpos[pg])-self.model.geom_rbound[g]-self.model.geom_rbound[pg]
                if lower>=distance:continue
                distance=min(distance,float(mujoco.mj_geomDistance(self.model,data,g,pg,distance,None)))
        return distance

    def measurements(self,data,ids,*,released,retreat_complete,prealignment):
        output={};profile=self.prepared['completion_profile']
        for pid in ids:
            part=self.prepared['parts'][pid];t=body_pose(data,body_name(pid,self.base));goal=np.asarray(part['target'])
            xyz,angle=errors(goal,t,self.base_rotation);linear,angular=self.velocity(data,pid)
            m={'goal_pose':goal.tolist(),'current_pose':t.tolist(),'position_error_base_xyz_m':xyz.tolist(),
               'orientation_error_rad':angle,'linear_speed_m_s':linear,'angular_speed_rad_s':angular,
               'hard_penetration_m':self.last_contacts['hard_penetration_m'],
               'gripper_clearance_m':self.clearance(data,pid),'released':bool(released),'retreat_complete':bool(retreat_complete),
               'robot_product_noncontact':pid not in self.last_contacts['robot_contact_parts']}
            op=part['operation']
            if op['kind']=='linear_insertion':
                m['insertion_depth_error_m']=abs(float(np.dot(t[:3,3]-goal[:3,3],op['axis_world'])))
                m.update(prealignment.get(pid,{}))
            output[pid]=m
        return output

def observe_window(skills,ids,*,check_id,source,model_hash,prealignment,reference=False):
    profile=skills.prepared['completion_profile'];motion=skills.motion
    window=ObservationWindow(ids,profile,check_id=check_id,source=source,model_sha256=model_hash,
                             asset_hash=skills.prepared['asset_hash'],operations={p:skills.prepared['parts'][p]['operation'] for p in ids})
    try:
        motion.stage='reference_settle' if reference else 'post_release_settle';skills.verifier.stage=motion.stage
        motion.hold(profile['reference_settle_s'] if reference else profile['post_release_settle_s'],allow_support=True,allow_grasp=False)
        motion.stage='completion_observation';skills.verifier.stage=motion.stage
        for i in range(501):
            if i:motion.step(allow_support=True,allow_grasp=False)
            else:motion.sample(allow_support=True,allow_grasp=False)
            window.add(float(motion.data.time),skills.verifier.measurements(motion.data,ids,
                       released=motion.held_pose is None and motion.gripper_command<1.,
                       retreat_complete=skills.retreat_complete,prealignment=prealignment))
        return window.finish()
    except Exception as exc:
        result=window.finish(type(exc).__name__+': '+str(exc))
        if isinstance(exc,FailureEvent):result['execution_failure']={'code':exc.code.value,'violations':exc.violations}
        return result
