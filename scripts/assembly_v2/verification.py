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
from .transforms import body_pose,errors,errors_many
from .completion import ObservationWindow,observation_samples

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
        self.fixture_owner={model.geom(f['geom_name']).id:f['part_id'] for f in prepared.get('layout',{}).get('supply_fixtures',[])}
        self.robot_geoms={g for g in range(model.ngeom) if g not in self.geom_part and g not in self.fixture_owner and
                          (model.geom_contype[g] or model.geom_conaffinity[g]) and
                          model.geom(g).name not in ('floor',TABLE_GEOM) and model.geom_bodyid[g]!=0}
        self.gripper_geoms={g for g in self.robot_geoms if model.geom(g).name.startswith('gripper_') or
                            model.body(int(model.geom_bodyid[g])).name.startswith('gripper_')}
        self.goal_pairs={frozenset(pair) for pair in prepared['allowed_goal_contact_pairs']}
        self.mating=False;self.reference_mode=False;self.last_contacts={};self.failure_observer=None;self.stage='initialise'
        self.target_position=np.asarray(prepared['parts'][active]['target'])[:3,3]
        self.target_rotation=np.asarray(prepared['parts'][active]['target'])[:3,:3]
        self.base_rotation=np.asarray(prepared['fixed_base_initial_pose'])[:3,:3]
        self.max_hard_history=0.;self.hard_violation=None
        self.vertices=load_mesh(prepared['parts'][active]['mesh']).vertices

    @property
    def last_contacts(self):
        # Detailed evidence is available in the same format on request. The
        # builder owns copies of this frame's positions/distances and decisions.
        # Ordinary control needs the checked summary, not thousands of dicts.
        builder=getattr(self,'_contact_evidence_builder',None)
        if builder is not None:
            self._last_contacts['rows']=builder();self._contact_evidence_builder=None
        return self._last_contacts

    @last_contacts.setter
    def last_contacts(self,value):
        self._last_contacts=value;self._contact_evidence_builder=None

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

    def near_goal_many(self,data,ids):
        """Batch only the requested poses in this immutable contact traversal."""
        if getattr(self.near_goal,'__func__',None) is not MeshVerifier.near_goal:
            return {pid:self.near_goal(data,pid) for pid in ids}
        output={self.base:True};moving=[pid for pid in ids if pid!=self.base]
        if not moving:return {pid:output[pid] for pid in ids}
        goals=[self.prepared['parts'][pid]['target'] for pid in moving]
        current=[body_pose(data,body_name(pid,self.base)) for pid in moving]
        positions,angles=errors_many(goals,current,self.base_rotation)
        for pid,t,goal,xyz,angle in zip(moving,current,goals,positions,angles):
            if pid!=self.active or not self.mating:
                output[pid]=bool(np.all(xyz<=.005+1e-12) and angle<=math.radians(3)+1e-12)
            else:
                op=self.prepared['parts'][pid]['operation'];delta=t[:3,3]-np.asarray(goal)[:3,3]
                if op['kind']=='linear_insertion':
                    axis=np.asarray(op['axis_world'])
                    output[pid]=bool(np.linalg.norm(delta-axis*np.dot(delta,axis))<=.004 and angle<=math.radians(3))
                else:output[pid]=bool(np.linalg.norm(delta)<=.015 and angle<=math.radians(3))
        return {pid:output[pid] for pid in ids}

    def contacts(self,data,*,allow_support,allow_grasp,planned=False):
        if isinstance(data,mujoco.MjData) and self.model.opt.cone==mujoco.mjtCone.mjCONE_ELLIPTIC:
            from .fast_contacts import contacts
            return contacts(self,data,allow_support=allow_support,allow_grasp=allow_grasp,planned=planned)
        return self._scalar_contacts(data,allow_support=allow_support,allow_grasp=allow_grasp,planned=planned)

    def _scalar_contacts(self,data,*,allow_support,allow_grasp,planned=False):
        sides={'left':False,'right':False};support={p:0. for p in self.bodies};records=[];hard=0.;pad_pen=0.;robot_contacts=set()
        # No time-based or persistent cache: different planning copies, changed
        # qpos at equal time, and a new physics step must all recompute. During
        # this synchronous contact traversal the data and policy are unchanged.
        goal_status={};fixture_owner=getattr(self,'fixture_owner',{})
        def near_goal(pid):
            if pid not in goal_status:goal_status[pid]=self.near_goal(data,pid)
            return goal_status[pid]
        # MuJoCo exposes contiguous contact views. Reading each contact through
        # individual pybind objects is expensive on finely decomposed CAD.
        # Views exist only within this synchronous call; no state/decision cache
        # crosses a physics frame. Static labels alone may be reused.
        contacts=data.contact
        if isinstance(data,mujoco.MjData):
            geoms,distances,positions,frames=contacts.geom,contacts.dist,contacts.pos,contacts.frame
            points=((int(i),int(geoms[i,0]),int(geoms[i,1]),float(distances[i]),positions[i],frames[i])
                    for i in np.flatnonzero(distances<=1e-7))
        else:
            points=((i,int(c.geom1),int(c.geom2),float(c.dist),c.pos,c.frame)
                    for i,c in enumerate(contacts) if c.dist<=1e-7)
        static=getattr(self,'_contact_static_labels',None)
        if static is None:static={};self._contact_static_labels=static
        def facts(a,b,distance,position,extra):
            key=(a,b)
            if key not in static:
                pa,pb=self.geom_part.get(a),self.geom_part.get(b)
                roles=[('product:'+p if p is not None else 'pad' if g in self.pads else 'gripper' if g in self.gripper_geoms else 'robot' if g in self.robot_geoms else 'table' if g==self.table_geom else 'supply_fixture' if g in fixture_owner else 'environment') for g,p in ((a,pa),(b,pb))]
                names=[]
                for g in (a,b):
                    name=self.model.geom(g).name
                    if not name and isinstance(self.model,mujoco.MjModel):
                        name='body:'+self.model.body(int(self.model.geom_bodyid[g])).name+'/geom:'+str(g)
                    names.append(name)
                static[key]=(names,[pa,pb],roles)
            names,part_labels,roles=static[key]
            return {'geoms':names.copy(),'parts':part_labels.copy(),'roles':roles.copy(),
                   'signed_distance_m':distance,'position_world_m':position.tolist(),**extra}
        force=np.zeros(6)
        for index,a,b,distance,position,frame in points:
            pa,pb=self.geom_part.get(a),self.geom_part.get(b)
            active_geom= a if pa==self.active else b if pb==self.active else None
            other= b if pa==self.active else a if pb==self.active else None
            grasp=active_geom is not None and other in self.pads
            extra={}
            allowed=False;reason='robot_environment_or_self_contact'
            if pa is not None and pb is not None:
                pair=frozenset((pa,pb))
                near={p:near_goal(p) for p in (pa,pb)}
                allowed=pair in self.goal_pairs and all(near.values()) and (
                    self.reference_mode or {pa,pb}<=(self.installed|{self.base}) or self.mating)
                reason=('goal_contact_pair_not_declared' if pair not in self.goal_pairs else
                        'part_outside_goal_tolerance' if not all(near.values()) else 'contact_not_allowed_in_this_phase')
                extra.update(goal_pair_declared=pair in self.goal_pairs,near_goal=near)
            elif pa is not None or pb is not None:
                pid=pa if pa is not None else pb;counter=b if pa is not None else a
                if counter==self.table_geom:
                    allowed=(pid==self.base or pid not in self.installed and pid!=self.active or
                             pid==self.active and allow_support or pid in self.installed and near_goal(pid))
                    reason='table_contact_outside_goal' if pid in self.installed and not near_goal(pid) else 'support_not_allowed_in_this_phase'
                elif counter in fixture_owner:
                    allowed=(fixture_owner[counter]==pid and pid not in self.installed and
                             (pid!=self.active or allow_support))
                    reason='foreign_or_inactive_supply_fixture_contact'
                elif counter in self.robot_geoms:
                    robot_contacts.add(pid)
                    allowed=allow_grasp and grasp
                    reason='robot_product_contact_not_allowed'
            # Robot/environment and self contacts remain forbidden by the existing model.
            if not allowed:
                rejected=facts(a,b,distance,position,extra);rejected['rejection_reason']=reason
                raise FailureEvent(FailureCode.PLANNED_CONTACT if planned else FailureCode.UNEXPECTED_CONTACT,
                                   'Unpermitted measured contact.',violations=[rejected],phase=self.stage)
            penetration=max(0.,-distance)
            if grasp:pad_pen=max(pad_pen,penetration)
            elif pa is not None or pb is not None:hard=max(hard,penetration)
            force.fill(0.)
            if not planned:mujoco.mj_contactForce(self.model,data,index,force)
            world=frame.reshape(3,3).T@force[:3]
            # Fingers carrying a part are not fixture/table support. Contact
            # normal ordering is independent of the symmetric allowed pair.
            if pa is not None and (pb is not None or b==self.table_geom or b in fixture_owner):support[pa]+=max(0.,-float(world[2]))
            if pb is not None and (pa is not None or a==self.table_geom or a in fixture_owner):support[pb]+=max(0.,float(world[2]))
            if grasp and force[0]>.01:sides['left' if 'left' in self.pads[other] else 'right']=True
            if self.mating and not grasp and active_geom is not None and force[0]>8.:
                raise FailureEvent(FailureCode.INSERTION_CONTACT_LIMIT,'Measured contact-point normal force >8 N.',violations=[{**facts(a,b,distance,position,extra),'normal_force_n':float(force[0])}],phase=self.stage)
            records.append((a,b,distance,position.copy(),extra))
        limit=self.prepared['completion_profile']['execution_hard_contact_penetration_max_m']
        if not planned:self.max_hard_history=max(self.max_hard_history,hard)
        pad_limit=.0005 if self.prepared.get('profile',{}).get('fingertip_concept') else .003
        if hard>limit+1e-12 or pad_pen>pad_limit:
            rows=[facts(*r) for r in records]
            if not planned:self.hard_violation={'time_s':float(data.time),'hard_penetration_m':hard,'contacts':rows}
            raise FailureEvent(FailureCode.PLANNED_CONTACT if planned else FailureCode.UNEXPECTED_CONTACT,
                               'Hard or compliant-pad penetration exceeded the configured limit.',
                               violations=[{'hard_penetration_m':hard,'pad_penetration_m':pad_pen,'pad_limit_m':pad_limit,'contacts':rows}],phase=self.stage)
        self.last_contacts={'robot_contact_parts':sorted(robot_contacts),'support_force_n':support,'hard_penetration_m':hard}
        self._contact_evidence_builder=lambda:[facts(*r) for r in records]
        return {'supported':support[self.active]>.01,'left_contact':sides['left'],'right_contact':sides['right'],
                'penetration_m':max(hard,pad_pen),'hard_penetration_m':hard}

    def velocity(self,data,pid):
        velocity=np.zeros(6)
        # XBODY uses the original body-frame origin, not the mass center.
        mujoco.mj_objectVelocity(self.model,data,mujoco.mjtObj.mjOBJ_XBODY,self.bodies[pid],velocity,0)
        return float(np.linalg.norm(velocity[3:])),float(np.linalg.norm(velocity[:3]))

    def preserve(self,data):
        ids=[pid for pid in self.settled if pid!=self.active]
        if not ids:return
        goals=[self.prepared['parts'][pid]['target'] if pid in self.installed else self.settled[pid] for pid in ids]
        current=[body_pose(data,body_name(pid,self.base)) for pid in ids]
        bases=[self.base_rotation if pid in self.installed else np.eye(3) for pid in ids]
        positions,angles=errors_many(goals,current,bases)
        # Keep original part ordering and exact predicates/first-failure evidence.
        for pid,xyz,angle in zip(ids,positions,angles):
            bad=(np.any(xyz>.005+1e-12) or angle>math.radians(3)+1e-12) if pid in self.installed else (np.linalg.norm(xyz)>.001 or angle>math.radians(1))
            if bad:raise FailureEvent(FailureCode.PLACEMENT_NOT_CONFIRMED,'A different part moved beyond its preservation policy.',
                  violations=[{'part_id':pid,'installed':pid in self.installed,'position_error_xyz_m':xyz.tolist(),'rotation_error_rad':float(angle)}],phase=self.stage)

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
        # Cache only immutable geom IDs/radii, never positions or distances.
        groups=getattr(self,'_clearance_groups',None)
        if groups is None:
            groups={};self._clearance_groups=groups
        if pid not in groups:
            part=np.array([g for g,p in self.geom_part.items() if p==pid],dtype=int)
            gripper=np.array(list(self.gripper_geoms),dtype=int)
            groups[pid]=(part,gripper,self.model.geom_rbound[part,None]+self.model.geom_rbound[gripper])
        part,gripper,radii=groups[pid]
        if not len(part) or not len(gripper):return distance
        difference=data.geom_xpos[gripper][None,:,:]-data.geom_xpos[part][:,None,:]
        lower=np.linalg.norm(difference,axis=2)-radii
        # A conservative roundoff guard can only add exact narrow-phase checks.
        # Retain the old nested pair order and every potentially closer pair.
        for i,j in np.argwhere(lower<distance+1e-12):
            if lower[i,j]>=distance+1e-12:continue
            distance=min(distance,float(mujoco.mj_geomDistance(self.model,data,int(gripper[j]),int(part[i]),distance,None)))
        return distance

    def measurements(self,data,ids,*,released,retreat_complete,prealignment):
        output={};profile=self.prepared['completion_profile']
        for pid in ids:
            part=self.prepared['parts'][pid];t=body_pose(data,body_name(pid,self.base));goal=np.asarray(part['target'])
            xyz,angle=errors(goal,t,self.base_rotation);linear,angular=self.velocity(data,pid)
            m={'goal_pose':goal.tolist(),'current_pose':t.tolist(),'position_error_base_xyz_m':xyz.tolist(),
               'orientation_error_rad':angle,'linear_speed_m_s':linear,'angular_speed_rad_s':angular,
               'hard_penetration_m':self._last_contacts['hard_penetration_m'],
               'gripper_clearance_m':self.clearance(data,pid),'released':bool(released),'retreat_complete':bool(retreat_complete),
               'robot_product_noncontact':pid not in self._last_contacts['robot_contact_parts']}
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
        for i in range(observation_samples(profile)):
            if i:motion.step(allow_support=True,allow_grasp=False)
            else:motion.sample(allow_support=True,allow_grasp=False)
            window.add(float(motion.data.time),skills.verifier.measurements(motion.data,ids,
                       released=motion.held_pose is None and motion.gripper_command<1.,
                       retreat_complete=skills.retreat_complete,prealignment=prealignment))
        return window.finish()
    except Exception as exc:
        result=window.finish(type(exc).__name__+': '+str(exc))
        result['stopped_at_simulation_time_s']=float(skills.data.time)
        result['stopped_phase']=motion.stage
        if not isinstance(exc,FailureEvent):
            result['interruption']={'exception_type':type(exc).__name__,'message':str(exc)}
        if isinstance(exc,FailureEvent):
            # A measured physical violation is a known failure, even when it
            # happened during settling before the first observation sample.
            # Timeout/missing measurements remain unknown; never manufacture a
            # completed observation window or replace the immutable CAD goal.
            result['status']='failed'
            result['execution_failure']={'code':exc.code.value,'message':str(exc),
                                         'phase':motion.stage,'violations':exc.violations}
            result['violations'].append({'check':'execution_safety','time_s':float(skills.data.time),
                                         'code':exc.code.value,'message':str(exc)})
            result['stopped_part_poses']=pose_diagnostics(skills)
        return result


def pose_diagnostics(skills):
    """Measured deviations at the stop, independent of contact classification."""
    rows={};v=skills.verifier;limits=skills.prepared['completion_profile']['global_completion']
    for pid,p in skills.prepared['parts'].items():
        current=body_pose(skills.data,body_name(pid,skills.prepared['base_id']))
        xyz,angle=errors(p['target'],current,v.base_rotation);linear,angular=v.velocity(skills.data,pid)
        rows[pid]={'fixed_base':pid==skills.prepared['base_id'],'target_pose':p['target'],
            'current_pose':current.tolist(),'position_error_base_xyz_m':xyz.tolist(),
            'orientation_error_rad':angle,'linear_speed_m_s':linear,'angular_speed_rad_s':angular,
            'within_goal_pose_limits':bool(np.all(xyz<=np.asarray(limits['position_error_max_xyz_m'])+1e-12)
                                          and angle<=limits['orientation_error_max_rad']+1e-12)}
    return rows
