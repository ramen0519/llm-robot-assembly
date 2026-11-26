"""Serial mesh skills: computed plans, servo commands and measured rigid dynamics."""
from copy import copy,deepcopy
import math
import time
import re
import numpy as np
from scipy.spatial.transform import Rotation
from failure_report import FailureCode,FailureEvent,json_safe
from view_tcp import read_tcp_pose
from workcell_motion import WorkcellMotion
from joint_path_planner import plan_tcp_path,check_tcp_endpoint
from .verification import MeshVerifier,observe_window
from .scene import body_name
from .transforms import body_pose,pose,inv,errors
from .completion import valid_evidence
from .installation import approach_offset
from .motion_plan import resolve as resolve_motion_plan,VERSION as MOTION_PLAN_VERSION
from assembly_v12.contracts import digest,write,append,sha
from assembly_v12.media import state,STATE

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
        self.last_grasp=None;self.last_grasp_checkpoint=None
        self.continuation_events=[];self.release_context=None
        self.motion_plan=resolve_motion_plan(None);self.motion_plan_source='common_default'
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

    def configure_motion_plan(self,value,*,reset=False,mode='bounded',source='policy_proposal'):
        updated=resolve_motion_plan(value,current=None if reset else self.motion_plan,
            mode=mode,operation=self.prepared['parts'][self.active]['operation'])
        changed=digest(updated)!=digest(self.motion_plan)
        self.motion_plan=updated
        if value is not None:self.motion_plan_source=source
        elif reset:self.motion_plan_source='common_default'
        return changed

    def approach_offset(self,goal,goal_tcp):
        return approach_offset(self,goal,goal_tcp,axis_world=self.motion_plan['preinstall_axis_world'],
                               extra_m=self.motion_plan['preinstall_extra_mm']/1000)

    def stage(self,name):
        self.motion.stage=name;self.verifier.stage=name
        if self.recorder:
            progress={'schema':'assembly_progress/v1','phase':name,'part_id':self.active,
                'source':self.source,'simulation_time_s':float(self.data.time),
                'wall_time_unix_s':time.time(),'assembly_success_claim':False}
            write(self.recorder.folder/'progress.json',progress)
            append(self.recorder.folder/'phases.jsonl',progress)
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
        from .continuation import MotionSlice,SliceExpired,options,record_continuation
        seconds,limit=options(self.prepared)
        for attempt in range(limit+1):
            original_budget=self.motion.budget
            try:
                return self._move_once(t,support=support,grasp=grasp,rrt=rrt,slice_seconds=seconds)
            except SliceExpired as exc:
                # State is kept in place. Nothing is restored or teleported.
                self.motion.budget=original_budget;self.budget.check('continuation_reobserve')
                checkpoint=self.checkpoint('motion_slice_'+str(attempt))
                if attempt>=limit:
                    record_continuation(self,status='limit_reached',checkpoint=checkpoint,reason=str(exc))
                    raise TimeoutError('motion_continuation_limit:'+self.motion.stage) from exc
                observed=self.motion.sample(allow_support=support,allow_grasp=grasp)
                if grasp:self.fresh_hold()
                record_continuation(self,status='reobserved_replan',attempt=attempt+1,
                    target_tcp=np.asarray(t).tolist(),observation=json_safe(observed),checkpoint=checkpoint,
                    original_slip_reference_preserved=True)
            finally:self.motion.budget=original_budget

    def _move_once(self,t,*,support=True,grasp=False,rrt=False,slice_seconds=60.):
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
        if self.recorder:
            append(self.recorder.folder/'planned_motions.jsonl',{'source':self.source,'part_id':self.active,
                'phase':self.motion.stage,'simulation_time_s':float(self.data.time),'planned_duration_s':plan.duration_s,
                'checked_samples':plan.checked_samples,'waypoints':len(plan.arm_knots),'executed_yet':False})
        from .continuation import finite_plan_execution_budget
        self.motion.budget=finite_plan_execution_budget(self.budget,slice_seconds,plan.duration_s,self.motion.dt)
        return self.motion.execute(plan,**kw)

    def set_approach_opening(self,opening):
        """Command the measured empty-tool opening and verify its settled gap."""
        profile=self.prepared['profile'];cal=profile.get('grasp_pad_center_calibration',{})
        samples=[r for r in cal.get('samples',[]) if 'command' in r]
        if len(samples)<2:return None
        from .tooling import calibrated_pad_center
        calibrated_pad_center(profile,opening/1000,[0,0,.006])
        samples=sorted(samples,key=lambda r:r['gap_mm'])
        gaps=np.asarray([r['gap_mm'] for r in samples]);commands=np.asarray([r['command'] for r in samples])
        if not np.isfinite(gaps).all() or not np.isfinite(commands).all() or np.any(np.diff(gaps)<=0) or np.any(np.diff(commands)>0):
            raise ValueError('Invalid empty-jaw command calibration.')
        if not gaps[0]-.25<=opening<=gaps[-1]+.25:raise ValueError('Opening is outside calibrated range.')
        target=float(np.interp(opening,gaps,commands));rounds=[]
        for attempt in range(3):
            duration=max(1.,1.5*abs(target-self.motion.gripper_command)/85.)
            self.motion.hold(duration,allow_support=True,allow_grasp=False,gripper_target=target)
            rows=self.motion.hold(1.,allow_support=True,allow_grasp=False)
            actual=rows[-1]['pad_gap_mm'];error=max(abs(r['pad_gap_mm']-opening) for r in rows)
            rounds.append({'command':target,'actual_gap_mm':actual,'last_half_second_max_error_mm':error})
            if error<=.25:
                return {'mode':'calibrated_command_with_measured_feedback','requested_mm':opening,'rounds':rounds,'status':'passed'}
            correction=float(np.interp(opening,gaps,commands)-np.interp(actual,gaps,commands))
            target=float(np.clip(target+np.clip(correction,-8.,8.),0.,255.))
        raise FailureEvent(FailureCode.INPUT_OR_PRECONDITION_INVALID,'Approach opening did not settle within 0.25 mm.',violations=rounds)

    def move_install_goal(self,goal):
        """Re-observe the held transform near mating contacts, keeping slip origin.

        Small final segments correct observed drift without changing the CAD
        target, contact allowance, force guard or original slip reference.
        """
        current=body_pose(self.data,body_name(self.active,self.prepared['base_id']))
        delta=current[:3,3]-goal[:3,3];distance=float(np.linalg.norm(delta))
        if distance<1e-9:
            self.stage('insert_goal');return self.move(self.part_to_tcp(goal),grasp=True)
        axis=delta/distance
        offsets=[d for d in (.010,.006,.003,.0015,0.) if d<distance-1e-6]
        for offset in offsets:
            self.stage('insert_goal_feedback')
            before=body_pose(self.data,body_name(self.active,self.prepared['base_id']))
            waypoint=goal.copy();waypoint[:3,3]+=axis*offset
            self.path_attempts.append({'phase':'insert_goal_feedback','remaining_offset_m':offset,
                'measured_part_pose':before.tolist(),'original_slip_reference_preserved':True,
                'target_is_original_CAD':offset==0})
            self.move(self.part_to_tcp(waypoint),grasp=True)

    def pick(self,grasp):
        if self.motion.held_pose is not None:raise ValueError('Gripper is already occupied.')
        local=pose(np.asarray(grasp['position_mm'])/1000,grasp['quaternion_xyzw'])
        part=body_pose(self.data,body_name(self.active,self.prepared['base_id']))
        target=part@local;pre=target.copy();pre[:3,3]-=target[:3,2]*(self.motion_plan['approach_distance_mm']/1000)
        self.last_grasp=deepcopy(grasp);self.retreat_complete=False
        self.stage('grasp_open')
        self.motion.hold(1.,allow_support=True,allow_grasp=False,gripper_target=0.)
        opening=grasp['opening_width_mm']
        opening_result=self.set_approach_opening(opening)
        if opening_result is None:
            # Legacy tools without command calibration retain their old path.
            opening_steps=round(3./self.motion.dt)
            for i in range(1,opening_steps+1):
                row=self.motion.sample(allow_support=True,allow_grasp=False)
                if row['pad_gap_mm']<=opening+.25:break
                self.motion.gripper_command=min(255.,i/opening_steps*255.)
                self.motion.step(allow_support=True,allow_grasp=False)
            self.motion.hold(.5,allow_support=True,allow_grasp=False)
            opening_result={'mode':'legacy_uncalibrated','requested_mm':opening,'status':'not_closed_loop_verified'}
        self.path_attempts.append({'phase':'approach_opening_control',**opening_result})
        self.stage('approach_pregrasp');self.move(pre,rrt=True)
        xyz,angle=errors(part,body_pose(self.data,body_name(self.active,self.prepared['base_id'])),np.eye(3))
        if np.linalg.norm(xyz)>.001 or angle>math.radians(1):raise FailureEvent(FailureCode.INPUT_OR_PRECONDITION_INVALID,'Part moved during approach. Re-observe.')
        self.stage('approach_grasp');self.move(target)
        self.stage('grasp_close');start=self.motion.gripper_command;consecutive=0
        closing_steps=round(4./self.motion.dt)
        for i in range(1,closing_steps+1):
            u=i/closing_steps;self.motion.gripper_command=start+(255.-start)*(3*u*u-2*u*u*u)
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
        return self.lift_part()

    def lift_part(self):
        """Try bounded checked lift endpoints without replaying physical failures."""
        if self.motion.held_pose is None:raise ValueError('Lift requires a measured bilateral grasp.')
        self.fresh_hold();self.stage('pick_lift');p,r=read_tcp_pose(self.data)
        last=None;rows=None
        requested=self.motion_plan['lift_height_mm']
        for height in ((requested/1000,) if requested is not None else (.10,.08,.065)):
            before=self.snapshot();lift=np.eye(4);lift[:3,:3]=r;lift[:3,3]=p+[0,0,height]
            try:
                rows=self.move(lift,grasp=True,rrt=True)
                self.path_attempts.append({'phase':'pick_lift_height','requested_vertical_lift_m':height,
                    'minimum_physical_clearance_required_m':.05,'actual_motion_executed':True})
                break
            except FailureEvent as exc:
                if self.snapshot()!=before or exc.code not in (FailureCode.IK_NOT_CONVERGED,FailureCode.PLANNED_CONTACT,FailureCode.MOTION_PATH_NOT_FOUND):raise
                last=exc;self.path_attempts.append({'phase':'pick_lift_height','requested_vertical_lift_m':height,
                    'planning_failure':exc.code.value,'state_unchanged':True,'actual_motion_executed':False})
                if self.recorder:self.recorder.last_failure=None
        if rows is None:raise last
        if any(r['supported'] or r['part_bottom_clearance_m']<.05 for r in rows):
            raise FailureEvent(FailureCode.GRASP_NOT_CONFIRMED,'Part not physically lifted clear of the table.',violations=[rows[-1]])
        self.last_grasp_checkpoint=self.checkpoint('physically_lifted')
        return {'status':'grasped','source':self.source,'checkpoint':self.last_grasp_checkpoint,'actual_grasp_transform':self.held_transform().tolist(),
                'minimum_lift_clearance_m':min(r['part_bottom_clearance_m'] for r in rows)}

    def checkpoint(self,name):
        if not self.recorder:return None
        record=self.recorder;folder=record.folder/'checkpoints';folder.mkdir(exist_ok=True)
        stem=re.sub(r'[^A-Za-z0-9_-]','_',str(record.check_id or record.action_id or 'diagnostic')+'_'+name)
        target=folder/(stem+'.npy');np.save(target,state(self.model,self.data),allow_pickle=False)
        value=json_safe({'schema':'physical_action_checkpoint/v1','source':self.source,'phase':self.motion.stage,
            'part_id':self.active,'check_id':record.check_id,'action_id':record.action_id,
            'model_sha256':self.model_hash,'prepared_sha256':digest(self.prepared),'state_spec':'mjSTATE_INTEGRATION',
            'state':target.relative_to(record.folder).as_posix(),'state_sha256':sha(target),
            'simulation_time_s':float(self.data.time),'installed':sorted(self.installed),'settled':self.settled,
            'controller':{'reference':self.motion.reference,'gripper_command':self.motion.gripper_command,
                'held_pose':self.motion.held_pose,'slip_reference':self.motion.slip_reference,'goal':self.motion.goal},
            'completion_context':{'prealignment':self.prealignment,'retreat_complete':self.retreat_complete},
            'motion_plan':{'schema':MOTION_PLAN_VERSION,'parameters':deepcopy(self.motion_plan),'source':self.motion_plan_source},
            'release_context':deepcopy(self.release_context),'continuation_count':len(self.continuation_events),
            'installation_verified':False})
        write(folder/(stem+'.json'),value);return value

    def held_transform(self):
        p,r=self.fresh_hold();t=np.eye(4);t[:3,3]=p;t[:3,:3]=r;return t

    def part_to_tcp(self,part_target):return np.asarray(part_target)@inv(self.held_transform())

    def insert(self,check_id,*,replan=False):
        if self.motion.held_pose is None:raise ValueError('Install requires a measured bilateral grasp.')
        part=self.prepared['parts'][self.active];goal=np.asarray(part['target']);op=part['operation']
        self.verifier.mating=True
        # A grasp can lift successfully yet put the fingers/palm through the
        # case at the goal. Check that terminal posture using the measured hold
        # before spending time on transport. Passing it is not assembly success.
        self.stage('install_endpoint_check');goal_tcp=self.part_to_tcp(goal)
        endpoint=check_tcp_endpoint(self.motion,goal_tcp[:3,3],goal_tcp[:3,:3],allow_support=True,allow_grasp=True,
            max_ik_iterations=self.prepared['profile']['resources']['ik_iterations'],
            max_time_s=min(self.prepared['profile']['resources']['rrt_seconds'],self.budget.remaining()))
        self.path_attempts.append({'phase':self.motion.stage,**endpoint})
        native=part.get('part_path',{});used_native=False
        custom_transport=bool(self.motion_plan['transport_via_goal_offsets_mm'] or self.motion_plan['preinstall_axis_world'] is not None or self.motion_plan['preinstall_extra_mm'])
        if not replan and not custom_transport and native.get('status')=='usable_part_path':
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
            for index,offset_mm in enumerate(self.motion_plan['transport_via_goal_offsets_mm']):
                waypoint=goal.copy();waypoint[:3,3]+=np.asarray(offset_mm,float)/1000
                self.stage('transport_policy_waypoint')
                self.path_attempts.append({'phase':self.motion.stage,'index':index,'goal_offset_world_mm':offset_mm,
                    'motion_plan_sha256':digest(self.motion_plan),'policy_source':self.motion_plan_source})
                self.move(self.part_to_tcp(waypoint),grasp=True,rrt=True)
            pre=goal.copy()
            offset,clearance=self.approach_offset(goal,goal_tcp)
            pre[:3,3]+=offset
            self.path_attempts.append({'phase':'transport_preinstall','part_offset_world_m':offset.tolist(),**clearance,
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
        self.move_install_goal(goal)
        strategy=self.motion_plan['release_mode']
        if strategy=='configured':strategy=self.prepared['profile'].get('release_strategy','full_open')
        self.begin_release(strategy)
        return self.finish_release(check_id)

    def begin_release(self,strategy):
        if strategy not in ('full_open','limited_open_then_retreat'):raise ValueError('Unsupported release strategy.')
        before=self.motion.sample(allow_support=True,allow_grasp=True)
        self.checkpoint('before_release')
        self.release_context={'strategy':strategy,'desired_gap_mm':before['pad_gap_mm']+2.,
            'opening_complete':False,'retreat_target':None,'retreat_complete':False,
            'held_gap_mm':before['pad_gap_mm'],'goal':deepcopy(self.prepared['parts'][self.active]['target'])}
        self.motion.held_pose=None;self.motion.slip_reference=None

    def finish_release(self,check_id):
        """Finish the observed release state, never pick an already released part."""
        ctx=self.release_context
        if not ctx or self.motion.held_pose is not None:raise ValueError('No authenticated release in progress.')
        goal=np.asarray(self.prepared['parts'][self.active]['target'])
        if not np.array_equal(goal,np.asarray(ctx['goal'])):raise ValueError('Release CAD goal changed.')
        current=body_pose(self.data,body_name(self.active,self.prepared['base_id']))
        xyz,angle=errors(goal,current,self.verifier.base_rotation)
        if np.any(xyz>.005+1e-12) or angle>math.radians(3)+1e-12:
            raise FailureEvent(FailureCode.PLACEMENT_NOT_CONFIRMED,'Released part is outside original goal bounds; no blind retreat recovery.')
        self.verifier.mating=True
        if not ctx['opening_complete']:
            if ctx['strategy']=='limited_open_then_retreat':
                self.stage('release_limited')
                from .release import open_until_clear
                open_until_clear(self,ctx)
            else:
                self.stage('release');self.motion.hold(4.,allow_support=True,allow_grasp=True,gripper_target=0.)
                self.motion.hold(1.,allow_support=True,allow_grasp=True)
            self.motion.sample(allow_support=True,allow_grasp=False);ctx['opening_complete']=True
        self.motion.sample(allow_support=True,allow_grasp=False)
        if ctx['retreat_target'] is None:
            p,r=read_tcp_pose(self.data);target=np.eye(4);target[:3,:3]=r;target[:3,3]=p
            if ctx['strategy']=='limited_open_then_retreat':
                offset,evidence=self.approach_offset(goal,target);target[:3,3]+=offset
                self.path_attempts.append({'phase':'confined_release','held_gap_mm':ctx['held_gap_mm'],
                    'requested_release_gap_mm':ctx['desired_gap_mm'],'retreat_offset_m':offset.tolist(),'geometry':evidence})
            else:target[:3,3]-=r[:,2]*.08
            ctx['retreat_target']=target.tolist()
        if not ctx['retreat_complete']:
            self.stage('retreat_limited_open' if ctx['strategy']=='limited_open_then_retreat' else 'retreat')
            self.move(np.asarray(ctx['retreat_target']),grasp=False,rrt=True)
            ctx['retreat_complete']=True
        if ctx['strategy']=='limited_open_then_retreat':
            self.stage('open_after_retreat');self.motion.hold(4.,allow_support=True,allow_grasp=False,gripper_target=0.)
            self.motion.hold(1.,allow_support=True,allow_grasp=False)
        self.retreat_complete=True
        evidence=observe_window(self,sorted(self.installed|{self.active}),check_id=check_id,source=self.source,
                                model_hash=self.model_hash,prealignment=self.prealignment)
        if not valid_evidence(evidence,self.prepared['completion_profile_hash'],source=self.source,required_parts=self.installed|{self.active}):
            from .failure_diagnosis import completion_error
            raise completion_error(evidence)
        return evidence

    def release_in_confined_space(self,goal):
        # Compatibility for development callers; production uses begin/finish.
        if not np.array_equal(goal,np.asarray(self.prepared['parts'][self.active]['target'])):raise ValueError('CAD goal mismatch.')
        self.begin_release('limited_open_then_retreat')
        return self.finish_release('compatibility_release')

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
        trial.motion_plan=deepcopy(self.motion_plan);trial.motion_plan_source=self.motion_plan_source
        result={'check_id':check_id,'source':'mujoco_preflight_copy','grasp_feasible':'not_run',
                'installation_feasible_with_grasp':'not_run','accepted':False,'main_executed':False,
                'motion_plan_sha256':digest(self.motion_plan),'resolved_motion_plan':deepcopy(self.motion_plan)}
        try:
            trial.pick(grasp);result['grasp_feasible']='passed';result['grasp_checkpoint']=trial.last_grasp_checkpoint
            e=trial.insert(check_id+':install');result.update(installation_feasible_with_grasp='passed',accepted=True,completion=e)
        except Exception as exc:
            key='installation_feasible_with_grasp' if result['grasp_feasible']=='passed' else 'grasp_feasible'
            # A deadline/cancellation/internal error is not measured infeasibility.
            # Physical/IK/contact failures retain their original failure evidence.
            result[key]='failed' if isinstance(exc,FailureEvent) else 'unknown'
            result['failure']=self.failure(exc,trial)
            try:result['stopped_checkpoint']=trial.checkpoint('copy_stopped')
            except Exception as storage:result['checkpoint_error']=type(storage).__name__+': '+str(storage)[:300]
        finally:
            result['main_state_unchanged']=before==self.snapshot()
            if not result['main_state_unchanged']:raise RuntimeError('Copy verification changed the live state.')
        result['path_checks']=trial.path_attempts
        return result

    def preflight_install(self,check_id):
        """A changed held-part recipe is revalidated before changing the main scene."""
        before=self.snapshot()
        trial=MeshSkills(self.model,copy(self.data),self.prepared,self.active,set(self.installed),deepcopy(self.settled),
                         self.budget,recorder=self.recorder,source='mujoco_preflight_copy')
        trial.motion_plan=deepcopy(self.motion_plan);trial.motion_plan_source=self.motion_plan_source
        trial.prealignment=deepcopy(self.prealignment);trial.verifier.mating=self.verifier.mating
        trial.release_context=deepcopy(self.release_context)
        for key in ('reference','gripper_command','held_pose','slip_reference','goal'):
            setattr(trial.motion,key,deepcopy(getattr(self.motion,key)))
        result={'check_id':check_id,'source':'mujoco_preflight_copy','grasp_feasible':'inherited_requires_current_hold_check',
            'installation_feasible_with_grasp':'not_run','accepted':False,'main_executed':False,
            'motion_plan_sha256':digest(self.motion_plan),'resolved_motion_plan':deepcopy(self.motion_plan)}
        try:
            trial.fresh_hold();e=trial.insert(check_id+':install',replan=True)
            result.update(installation_feasible_with_grasp='passed',accepted=True,completion=e,
                          completion_checkpoint=trial.checkpoint('installation_copy_complete'))
        except Exception as exc:
            result['installation_feasible_with_grasp']='failed' if isinstance(exc,FailureEvent) else 'unknown'
            result['failure']=self.failure(exc,trial)
            try:result['stopped_checkpoint']=trial.checkpoint('copy_stopped')
            except Exception as storage:result['checkpoint_error']=type(storage).__name__+': '+str(storage)[:300]
        finally:
            result['main_state_unchanged']=before==self.snapshot()
            if not result['main_state_unchanged']:raise RuntimeError('Copy verification changed live state.')
        result['path_checks']=trial.path_attempts
        return result

    @staticmethod
    def failure(exc,skills):
        record=skills.recorder
        snap=getattr(exc,'v2snapshot',None)
        if record and snap is None and isinstance(exc,FailureEvent):
            snap=record.failure_snapshot(skills.data,skills.source,skills.motion.stage,exc,parent_scene=skills.source)
        value={'failure_code':exc.code.value if isinstance(exc,FailureEvent) else type(exc).__name__,
                'phase':getattr(exc,'phase',None) or skills.motion.stage,'source':snap.get('source',skills.source) if snap else skills.source,
                'message':str(exc)[:1000],'violations':json_safe(getattr(exc,'violations',[])),
                'completion_evidence':getattr(exc,'completion_evidence',None),
                'failure_snapshot':record.materialize(snap) if record and snap else {'status':'unavailable','reason':'no detector snapshot'}}
        from .failure_diagnosis import diagnose_failure
        value['diagnosis']=diagnose_failure(value)
        return value
