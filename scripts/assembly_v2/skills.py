"""Serial mesh skills: computed plans, servo commands and measured rigid dynamics."""
from copy import copy,deepcopy
import math
import numpy as np
from scipy.spatial.transform import Rotation
from failure_report import FailureCode,FailureEvent,json_safe
from view_tcp import read_tcp_pose
from workcell_motion import WorkcellMotion
from joint_path_planner import plan_tcp_path
from .verification import MeshVerifier,observe_window
from .scene import body_name
from .transforms import body_pose,pose,inv,errors
from .completion import valid_evidence
from assembly_v12.contracts import digest
from assembly_v12.media import state

def simplify_part_path(poses,position_tolerance=1e-5,angle_tolerance=1e-4):
    """Remove collinear SE3 samples, not collision checks on robot motion."""
    from scipy.spatial.transform import Slerp
    poses=np.asarray(poses);keep={0,len(poses)-1};stack=[(0,len(poses)-1)]
    while stack:
        left,right=stack.pop()
        if right-left<=1:continue
        a,b=poses[left],poses[right];segment=b[:3,3]-a[:3,3];length2=float(segment@segment)
        fractions=np.clip((poses[left+1:right,:3,3]-a[:3,3])@segment/length2,0,1) if length2>1e-16 else np.linspace(0,1,right-left+1)[1:-1]
        pe=np.linalg.norm(poses[left+1:right,:3,3]-(a[:3,3]+fractions[:,None]*segment),axis=1)
        expected=Slerp([0,1],Rotation.from_matrix([a[:3,:3],b[:3,:3]]))(fractions)
        angles=(expected.inv()*Rotation.from_matrix(poses[left+1:right,:3,:3])).magnitude()
        score=np.maximum(pe/position_tolerance,angles/angle_tolerance);offset=int(np.argmax(score))
        if score[offset]>1:
            index=left+1+offset;keep.add(index);stack.extend([(left,index),(index,right)])
    return poses[sorted(keep)]


class MeshSkills:
    def __init__(self,model,data,prepared,active,installed,settled,budget,*,recorder=None,source='live_simulation'):
        self.model,self.data,self.prepared,self.active=model,data,prepared,active
        self.installed,self.settled,self.budget=installed,settled,budget
        self.source,self.recorder=source,recorder
        self.verifier=MeshVerifier(model,prepared,active,installed,settled)
        self.motion=WorkcellMotion(model,data,self.verifier,recorder.sample if recorder and source=='live_simulation' else None,budget)
        self.retreat_complete=False;self.prealignment={};self.path_attempts=[]
        self.model_hash=recorder.model_hash if recorder else 'component_check_no_recording'
        self.last_grasp=None
        if recorder:
            for method in ('contacts','physical_state','check_held','preserve'):
                original=getattr(self.verifier,method)
                def observed(data,*args,_original=original,**kw):
                    try:return _original(data,*args,**kw)
                    except FailureEvent as exc:
                        if not hasattr(exc,'v2snapshot'):
                            origin='planner_candidate' if kw.get('planned') else self.source
                            exc.v2snapshot=recorder.failure_snapshot(data,origin,self.motion.stage,exc,parent_scene=self.source)
                        raise
                setattr(self.verifier,method,observed)

    def stage(self,name):
        self.motion.stage=name;self.verifier.stage=name
        if self.recorder and self.source=='live_simulation':self.recorder.sample(force=True)

    def snapshot(self):
        return digest(state(self.model,self.data).tolist())

    def settle(self):
        self.stage('settle_initial_layout')
        self.motion.hold(3.,allow_support=True,allow_grasp=False)
        for pid in self.prepared['order']:
            linear,angular=self.verifier.velocity(self.data,pid)
            if linear>.001 or angular>.01:
                raise FailureEvent(FailureCode.NOT_SETTLED,'An initial supply part is not stable.',violations=[{'part_id':pid,'linear_speed_m_s':linear,'angular_speed_rad_s':angular}])
        # Freeze once, after observed settling. Never refresh this to hide later displacement.
        self.settled.update({p:body_pose(self.data,body_name(p,self.prepared['base_id'])).tolist() for p in self.prepared['order']})
        return deepcopy(self.settled)

    def fresh_hold(self):
        if self.motion.held_pose is None:raise ValueError('No measured held part.')
        row=self.motion.sample(allow_support=True,allow_grasp=True)
        self.verifier.check_held(self.data,row,self.motion.slip_reference)
        current=self.verifier.relative_part_pose(self.data)
        self.motion.held_pose=tuple(v.copy() for v in current)
        return current

    def move(self,t,*,support=True,grasp=False,rrt=False):
        self.budget.check(self.motion.stage)
        if grasp and self.motion.held_pose is not None:self.fresh_hold()
        kw={'allow_support':support,'allow_grasp':grasp}
        try:
            plan=self.motion.plan(t[:3,3],t[:3,:3],**kw)
            meta={'planner':'cartesian_mink_dense_joint_check'}
        except FailureEvent as exc:
            if not rrt or exc.code not in (FailureCode.PLANNED_CONTACT,FailureCode.IK_NOT_CONVERGED,FailureCode.MOTION_PATH_NOT_FOUND):raise
            if self.recorder:self.recorder.last_failure=None
            resources=self.prepared['profile']['resources']
            plan,meta=plan_tcp_path(self.motion,t[:3,3],t[:3,:3],max_ik_iterations=resources['ik_iterations'],
                                   max_time_s=min(resources['rrt_seconds'],self.budget.remaining()),seed=0,**kw)
            meta['fallback_from']={'code':exc.code.value,'phase':exc.phase}
        self.path_attempts.append(json_safe({'phase':self.motion.stage,'target_tcp':t,'source':self.source,**meta}))
        return self.motion.execute(plan,**kw)

    def pick(self,grasp):
        if self.motion.held_pose is not None:raise ValueError('Gripper is already occupied.')
        local=pose(np.asarray(grasp['position_mm'])/1000,grasp['quaternion_xyzw'])
        part=body_pose(self.data,body_name(self.active,self.prepared['base_id']))
        target=part@local;pre=target.copy();pre[:3,3]-=target[:3,2]*.08
        self.last_grasp=deepcopy(grasp);self.retreat_complete=False
        self.stage('grasp_open')
        self.motion.hold(1.,allow_support=True,allow_grasp=False,gripper_target=0.)
        opening=grasp['opening_width_mm']
        # Apply the requested opening by measured pad distance. A proposal is not silently ignored.
        for i in range(1,1501):
            row=self.motion.sample(allow_support=True,allow_grasp=False)
            if row['pad_gap_mm']<=opening+.25:break
            self.motion.gripper_command=min(255.,i/1500*255.)
            self.motion.step(allow_support=True,allow_grasp=False)
        self.motion.hold(.5,allow_support=True,allow_grasp=False)
        self.stage('approach_pregrasp');self.move(pre,rrt=True)
        xyz,angle=errors(part,body_pose(self.data,body_name(self.active,self.prepared['base_id'])),np.eye(3))
        if np.linalg.norm(xyz)>.001 or angle>math.radians(1):raise FailureEvent(FailureCode.INPUT_OR_PRECONDITION_INVALID,'Part moved during approach. Re-observe.')
        self.stage('approach_grasp');self.move(target)
        self.stage('grasp_close');start=self.motion.gripper_command;consecutive=0
        for i in range(1,2001):
            u=i/2000;self.motion.gripper_command=start+(255.-start)*(3*u*u-2*u*u*u)
            row=self.motion.step(allow_support=True,allow_grasp=True)
            consecutive=consecutive+1 if row['left_contact'] and row['right_contact'] else 0
            if consecutive*self.motion.dt>=.02:break
        else:raise FailureEvent(FailureCode.GRASP_NOT_CONFIRMED,'No bilateral pad contact in the bounded close.')
        self.motion.hold(1.,allow_support=True,allow_grasp=True,
            gripper_target=min(255.,float(self.data.ten_length[0])*255./.8+3.))
        rows=self.motion.hold(1.5,allow_support=True,allow_grasp=True)
        if any(not r['left_contact'] or not r['right_contact'] or not .5<r['pad_gap_mm']<85 for r in rows):
            raise FailureEvent(FailureCode.GRASP_NOT_CONFIRMED,'Bilateral grasp did not persist.',violations=[rows[-1]])
        self.motion.held_pose=self.verifier.relative_part_pose(self.data)
        self.motion.slip_reference=tuple(v.copy() for v in self.motion.held_pose)
        self.stage('pick_lift');p,r=read_tcp_pose(self.data);lift=np.eye(4);lift[:3,:3]=r;lift[:3,3]=p+[0,0,.10]
        rows=self.move(lift,grasp=True)
        if any(r['supported'] or r['part_bottom_clearance_m']<.05 for r in rows):
            raise FailureEvent(FailureCode.GRASP_NOT_CONFIRMED,'Part not physically lifted clear of the table.',violations=[rows[-1]])
        return {'status':'grasped','source':self.source,'actual_grasp_transform':self.held_transform().tolist(),
                'minimum_lift_clearance_m':min(r['part_bottom_clearance_m'] for r in rows)}

    def held_transform(self):
        p,r=self.fresh_hold();t=np.eye(4);t[:3,3]=p;t[:3,:3]=r;return t

    def part_to_tcp(self,part_target):return np.asarray(part_target)@inv(self.held_transform())

    def insert(self,check_id,*,replan=False):
        if self.motion.held_pose is None:raise ValueError('Install requires a measured bilateral grasp.')
        part=self.prepared['parts'][self.active];goal=np.asarray(part['target']);op=part['operation']
        self.verifier.mating=True
        native=part.get('part_path',{});used_native=False
        if not replan and native.get('status')=='usable_part_path':
            original=list(reversed(native['world_part_poses']));poses=simplify_part_path(original)
            if len(poses)>200:raise FailureEvent(FailureCode.MOTION_PATH_NOT_FOUND,'Native pose path complexity exceeds bounded execution; replan allowed.')
            self.path_attempts.append({'source':'verified_native_part_path','original_samples':len(original),'retained_samples':len(poses),
                 'position_tolerance_m':1e-5,'angle_tolerance_rad':1e-4,'robot_edges_still_dense_checked':True})
            first=np.asarray(poses[0]);robot=np.asarray(self.prepared['profile']['robot_base_world_m'])
            if np.linalg.norm(first[:3,3]-robot)<.60:
                self.stage('transport_to_part_path');self.move(self.part_to_tcp(first),grasp=True,rrt=True)
                for p in poses[1:-1]:
                    self.stage('verified_part_path_to_tcp');self.move(self.part_to_tcp(p),grasp=True)
                used_native=True
            else:self.path_attempts.append({'phase':'transport','native_path':'retained_but_endpoint_outside_coarse_reach','fallback':'CAD_goal_approach_revalidated'})
        # A native part path never substitutes for arm/gripper collision checking.
        if not used_native:
            pre=goal.copy()
            if op['kind']=='linear_insertion':offset=np.asarray(op['axis_world'])*(op['nominal_depth_m']+.02)
            else:
                # Approach opposite the actually held tool's forward axis. Give
                # side approaches a small upward component to clear the rigid
                # support surface; the full swept geometry is still checked.
                approach=-self.part_to_tcp(goal)[:3,2];approach[2]=max(.25,approach[2])
                offset=.08*approach/np.linalg.norm(approach)
            pre[:3,3]+=offset
            self.path_attempts.append({'phase':'transport_preinstall','part_offset_world_m':offset.tolist(),
                'basis':'defined_insertion_axis' if op['kind']=='linear_insertion' else 'observed_grasp_axis_with_support_clearance',
                'clearance_not_assumed':'requires_arm_gripper_carried_mesh_path_and_physical_checks'})
            self.stage('transport_preinstall');self.move(self.part_to_tcp(pre),grasp=True,rrt=True)
        if op['kind']=='linear_insertion':
            self.stage('insert_prealignment')
            current=body_pose(self.data,body_name(self.active,self.prepared['base_id']));delta=current[:3,3]-goal[:3,3]
            axis=np.asarray(op['axis_world']);perp=float(np.linalg.norm(delta-axis*np.dot(delta,axis)))
            angle=float(Rotation.from_matrix(goal[:3,:3].T@current[:3,:3]).magnitude())
            self.prealignment[self.active]={'prealignment_perpendicular_error_m':perp,'prealignment_orientation_error_rad':angle}
            if perp>=.001 or angle>=math.radians(1):
                raise FailureEvent(FailureCode.ALIGNMENT_OUT_OF_TOLERANCE,'Pre-insertion alignment failed.',violations=[self.prealignment[self.active]])
        self.stage('insert_goal');self.move(self.part_to_tcp(goal),grasp=True)
        self.stage('release');self.motion.held_pose=None;self.motion.slip_reference=None
        self.motion.hold(4.,allow_support=True,allow_grasp=True,gripper_target=0.)
        self.motion.hold(1.,allow_support=True,allow_grasp=True)
        self.stage('retreat');p,r=read_tcp_pose(self.data);retreat=np.eye(4);retreat[:3,:3]=r;retreat[:3,3]=p-r[:,2]*.08
        self.move(retreat,grasp=False,rrt=True);self.retreat_complete=True
        evidence=observe_window(self,sorted(self.installed|{self.active}),check_id=check_id,source=self.source,
                                model_hash=self.model_hash,prealignment=self.prealignment)
        if not valid_evidence(evidence,self.prepared['completion_profile_hash'],source=self.source,required_parts=self.installed|{self.active}):
            exc=FailureEvent(FailureCode.PLACEMENT_NOT_CONFIRMED,'Common completion window failed.',violations=[evidence]);exc.completion_evidence=evidence;raise exc
        return evidence

    def recover_alignment(self):
        if self.motion.held_pose is None:raise ValueError('No held part for alignment recovery.')
        part=self.prepared['parts'][self.active];op=part['operation']
        if op['kind']!='linear_insertion':raise ValueError('No defined insertion axis.')
        self.fresh_hold();self.stage('recover_alignment')
        goal=np.asarray(part['target']);current=body_pose(self.data,body_name(self.active,self.prepared['base_id']))
        axis=np.asarray(op['axis_world']);height=float(np.dot(current[:3,3]-goal[:3,3],axis))
        if height<op['nominal_depth_m']+.02:raise ValueError('Recovery requires an observed free 20 mm approach margin.')
        delta=current[:3,3]-goal[:3,3]
        if np.linalg.norm(delta-axis*np.dot(delta,axis))>.02:raise ValueError('Alignment correction exceeds 20 mm bound.')
        corrected=goal.copy();corrected[:3,3]+=axis*height
        self.move(self.part_to_tcp(corrected),grasp=True)
        return {'status':'aligned','fresh_transform_used':True,'slip_reference_reset':False}

    def preflight(self,grasp,check_id):
        before=self.snapshot();trial=MeshSkills(self.model,copy(self.data),self.prepared,self.active,set(self.installed),deepcopy(self.settled),
                         self.budget,recorder=self.recorder,source='mujoco_preflight_copy')
        trial.prealignment=deepcopy(self.prealignment)
        result={'check_id':check_id,'source':'mujoco_preflight_copy','grasp_feasible':'not_run',
                'installation_feasible_with_grasp':'not_run','accepted':False,'main_executed':False}
        try:
            trial.pick(grasp);result['grasp_feasible']='passed'
            e=trial.insert(check_id+':install');result.update(installation_feasible_with_grasp='passed',accepted=True,completion=e)
        except Exception as exc:
            key='installation_feasible_with_grasp' if result['grasp_feasible']=='passed' else 'grasp_feasible'
            result[key]='failed';result['failure']=self.failure(exc,trial)
        finally:
            result['main_state_unchanged']=before==self.snapshot()
            if not result['main_state_unchanged']:raise RuntimeError('Copy verification changed the live state.')
        result['path_checks']=trial.path_attempts
        return result

    @staticmethod
    def failure(exc,skills):
        record=skills.recorder
        snap=getattr(exc,'v2snapshot',None)
        if record and snap is None and isinstance(exc,FailureEvent):
            snap=record.failure_snapshot(skills.data,skills.source,skills.motion.stage,exc,parent_scene=skills.source)
        return {'failure_code':exc.code.value if isinstance(exc,FailureEvent) else type(exc).__name__,
                'phase':getattr(exc,'phase',None) or skills.motion.stage,'source':snap.get('source',skills.source) if snap else skills.source,
                'message':str(exc)[:1000],'violations':json_safe(getattr(exc,'violations',[])),
                'completion_evidence':getattr(exc,'completion_evidence',None),
                'failure_snapshot':record.materialize(snap) if record and snap else {'status':'unavailable','reason':'no detector snapshot'}}
