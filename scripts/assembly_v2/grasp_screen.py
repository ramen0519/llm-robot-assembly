"""Nominal tool-to-scene goal clearance, advisory to the policy only.

Uses calibrated empty-jaw configurations in a disposable kinematic copy. It
does not claim a grasp, path, insertion or physical installation succeeded.
"""
from copy import copy
import time
import mujoco
import numpy as np
from view_tcp import read_tcp_pose
from .transforms import pose


def choose_joint_opening(compatible):
    """Prefer short closure travel among sampled approach/closure/goal fits.

    Retain the existing 3 mm roomy preference. When no opening meets it,
    choose the smallest jointly clear opening, not the widest-clearance one.
    This is a geometry recommendation; it does not authorize physical success.
    """
    roomy=[r for r in compatible if r['minimum_distance_m']>=.003]
    return min(roomy or compatible,key=lambda r:r['requested_opening_mm']) if compatible else None


class GoalToolScreen:
    def __init__(self,skills):
        self.skills=skills;self.model=skills.model;self.data=copy(skills.data)
        self.goal=np.asarray(skills.prepared['parts'][skills.active]['target'])
        v=skills.verifier
        self.tool=np.array(sorted(v.gripper_geoms),dtype=int)
        self.obstacles=np.array([g for g in range(self.model.ngeom)
            if (self.model.geom_contype[g] or self.model.geom_conaffinity[g]) and
            g not in v.robot_geoms and v.geom_part.get(g)!=skills.active],dtype=int)
        self.radii=self.model.geom_rbound[self.tool,None]+self.model.geom_rbound[self.obstacles]
        self.cal=skills.prepared['profile'].get('grasp_pad_center_calibration',{}).get('samples',[])
        self.started=time.monotonic();self.local={}

    def __call__(self,candidate):
        self.skills.budget.check('nominal_goal_tool_screen')
        base={'source':'nominal_tool_projection','physical_success':False,'robot_ik':'not_run',
              'actual_grasp_transform':'unknown_until_physical_lift','other_parts':'current_scene_except_active',
              'meaning':'advisory goal clearance; not a grasp/path/installation test'}
        if time.monotonic()-self.started>self.skills.prepared['profile']['resources'].get('candidate_seconds',20):
            return {**base,'status':'not_run','reason':'candidate_screen_time_limit'}
        width=candidate['geometry']['contact_spacing_m']*1000
        usable=[r for r in self.cal if 'qpos' in r and len(r['qpos'])==8]
        if not usable or width<min(r['gap_mm'] for r in usable)-1 or width>max(r['gap_mm'] for r in usable)+1:
            return {**base,'status':'not_run','reason':'no_measured_jaw_configuration_for_width'}
        configs=[]
        for extra in (0.,1.6):
            row=min(usable,key=lambda r:abs(r['gap_mm']-(width+extra)))
            if row not in configs:configs.append(row)
        t=self.goal@pose(np.asarray(candidate['position_mm'])/1000,candidate['quaternion_xyzw'])
        outcomes=[]
        for row in configs:
            key=tuple(row['qpos'])
            if key not in self.local:
                self.data.qpos[:]=self.skills.data.qpos
                self.data.qpos[6:14]=row['qpos']
                mujoco.mj_kinematics(self.model,self.data)
                tp,tr=read_tcp_pose(self.data)
                positions=(self.data.geom_xpos[self.tool]-tp)@tr
                rotations=tr.T@self.data.geom_xmat[self.tool].reshape(-1,3,3)
                self.local[key]=(positions.copy(),rotations.copy())
            positions,rotations=self.local[key]
            self.data.geom_xpos[self.tool]=positions@t[:3,:3].T+t[:3,3]
            self.data.geom_xmat[self.tool]=(t[:3,:3]@rotations).reshape(-1,9)
            difference=self.data.geom_xpos[self.tool,None,:]-self.data.geom_xpos[self.obstacles][None,:,:]
            lower=np.linalg.norm(difference,axis=2)-self.radii
            distance=.025;pair=None
            for i,j in np.argwhere(lower<distance+1e-12):
                if lower[i,j]>=max(0.,distance)+1e-12:continue
                a,b=int(self.tool[i]),int(self.obstacles[j])
                value=float(mujoco.mj_geomDistance(self.model,self.data,a,b,max(.00001,distance),None))
                if value<distance:
                    distance=value
                    pair=[self.model.geom(g).name or 'body:'+self.model.body(int(self.model.geom_bodyid[g])).name+'/geom:'+str(g) for g in (a,b)]
            outcomes.append({'calibrated_gap_mm':row['gap_mm'],'minimum_distance_m':distance,'closest_geoms':pair})
        best=max(outcomes,key=lambda r:r['minimum_distance_m'])
        return {**base,'status':'clear_nominal' if best['minimum_distance_m']>1e-6 else 'blocked_nominal',
                'best_clearance_mm':best['minimum_distance_m']*1000,'gap_trials':outcomes,
                'actual_grasp_and_installation_still_required':True}


    def source_openings(self,candidate):
        """Sample the open tool along approach, including the grasped body's bulk.

        The target contact spacing can be smaller than the material below it.
        These are advisory kinematic samples, not robot/closure/force proofs.
        """
        from .transforms import body_pose
        from .scene import body_name
        common={'source':'sampled_open_tool_projection','physical_success':False,
                'samples_per_opening':17,'continuous_sweep':'not_proved','robot_ik':'not_run',
                'closing_and_lift':'not_run','clearance_ranking_margin_mm':3.,
                'meaning':'current-scene approach only; actual full preflight remains required'}
        usable=[r for r in self.cal if 'qpos' in r and len(r['qpos'])==8]
        if not usable:return {**common,'status':'not_run','reason':'no_measured_jaw_configurations','trials':[]}
        current=body_pose(self.skills.data,body_name(self.skills.active,self.skills.prepared['base_id']))
        target=current@pose(np.asarray(candidate['position_mm'])/1000,candidate['quaternion_xyzw'])
        obstacles=np.array([g for g in range(self.model.ngeom)
            if (self.model.geom_contype[g] or self.model.geom_conaffinity[g]) and g not in self.skills.verifier.robot_geoms],dtype=int)
        radii=self.model.geom_rbound[self.tool,None]+self.model.geom_rbound[obstacles]
        openings=sorted(set(min(85.,candidate['opening_width_mm']+extra) for extra in (0,8,16,24,32,85)))
        trials=[]
        for opening in openings:
            self.skills.budget.check('nominal_source_opening')
            if time.monotonic()-self.started>self.skills.prepared['profile']['resources'].get('candidate_seconds',20):
                return {**common,'status':'not_run','reason':'candidate_screen_time_limit','trials':trials}
            row=min(usable,key=lambda r:abs(r['gap_mm']-opening))
            if abs(row['gap_mm']-opening)>1.:
                trials.append({'requested_opening_mm':opening,'status':'not_run','reason':'uncalibrated_opening'})
                continue
            key=tuple(row['qpos'])
            if key not in self.local:
                self.data.qpos[:]=self.skills.data.qpos;self.data.qpos[6:14]=row['qpos']
                mujoco.mj_kinematics(self.model,self.data);tp,tr=read_tcp_pose(self.data)
                self.local[key]=(((self.data.geom_xpos[self.tool]-tp)@tr).copy(),
                                 (tr.T@self.data.geom_xmat[self.tool].reshape(-1,3,3)).copy())
            positions,rotations=self.local[key];distance=.025;pair=None;at_offset=None
            for offset in np.linspace(.08,0.,17):
                self.data.geom_xpos[self.tool]=positions@target[:3,:3].T+target[:3,3]-target[:3,2]*offset
                self.data.geom_xmat[self.tool]=(target[:3,:3]@rotations).reshape(-1,9)
                lower=np.linalg.norm(self.data.geom_xpos[self.tool,None]-self.data.geom_xpos[obstacles][None],axis=2)-radii
                for i,j in np.argwhere(lower<max(0.,distance)+1e-12):
                    a,b=int(self.tool[i]),int(obstacles[j])
                    value=float(mujoco.mj_geomDistance(self.model,self.data,a,b,max(.00001,distance),None))
                    if value<distance:
                        distance=value;at_offset=float(offset)
                        pair=[self.model.geom(g).name or 'body:'+self.model.body(int(self.model.geom_bodyid[g])).name+'/geom:'+str(g) for g in (a,b)]
            trials.append({'requested_opening_mm':opening,'calibrated_gap_mm':row['gap_mm'],
                'status':'clear_sampled_nominal' if distance>1e-6 else 'blocked_sampled_nominal',
                'minimum_distance_m':distance,'closest_geoms':pair,'distance_before_grasp_m':at_offset})
        clear=[r for r in trials if r['status']=='clear_sampled_nominal']
        roomy=[r for r in clear if r['minimum_distance_m']>=.003]
        chosen=min(roomy,key=lambda r:r['requested_opening_mm']) if roomy else max(clear,key=lambda r:r['minimum_distance_m']) if clear else None
        return {**common,'status':'clear_sampled_nominal' if chosen else 'no_clear_sampled_opening',
                'recommended_opening_mm':chosen['requested_opening_mm'] if chosen else None,'trials':trials}


    def _project_tool(self, row, target):
        key=tuple(row['qpos'])
        if key not in self.local:
            self.data.qpos[:]=self.skills.data.qpos
            self.data.qpos[6:14]=row['qpos']
            mujoco.mj_kinematics(self.model,self.data)
            tp,tr=read_tcp_pose(self.data)
            self.local[key]=(((self.data.geom_xpos[self.tool]-tp)@tr).copy(),
                             (tr.T@self.data.geom_xmat[self.tool].reshape(-1,3,3)).copy())
        positions,rotations=self.local[key]
        self.data.geom_xpos[self.tool]=positions@target[:3,:3].T+target[:3,3]
        self.data.geom_xmat[self.tool]=(target[:3,:3]@rotations).reshape(-1,9)

    def _projected_distance(self, obstacles):
        if not len(obstacles):return {'minimum_distance_m':.025,'closest_geoms':None,'tool_geom_id':None}
        radii=self.model.geom_rbound[self.tool,None]+self.model.geom_rbound[obstacles]
        lower=np.linalg.norm(self.data.geom_xpos[self.tool,None]-self.data.geom_xpos[obstacles][None],axis=2)-radii
        distance=.025;pair=None;tool_id=None
        for i,j in np.argwhere(lower<distance+1e-12):
            if lower[i,j]>=max(0.,distance)+1e-12:continue
            a,b=int(self.tool[i]),int(obstacles[j])
            value=float(mujoco.mj_geomDistance(self.model,self.data,a,b,max(.00001,distance),None))
            if value<distance:
                distance=value;tool_id=a
                pair=[self.model.geom(g).name or 'geom:'+str(g) for g in (a,b)]
        return {'minimum_distance_m':distance,'closest_geoms':pair,'tool_geom_id':tool_id}

    def closure_preview(self,candidate):
        """First geometric contact of the full tool, not point-pair width alone.

        Empty-jaw calibration samples are projected into a disposable copy.
        They bracket first contact; compliant closure, bilateral forces, slipping
        and actual installation still require the unmodified dynamic preflight.
        """
        from .transforms import body_pose
        from .scene import body_name
        common={'source':'sampled_full_tool_closure','physical_success':False,
                'closure_dynamics':'not_run','bilateral_grasp':'not_proved',
                'continuous_sweep':'not_proved','robot_ik':'not_run',
                'candidate_surface_width_mm':candidate['geometry']['contact_spacing_m']*1000,
                'starting_opening_mm':candidate['opening_width_mm']}
        def available():
            self.skills.budget.check('nominal_tool_closure')
            return time.monotonic()-self.started<=self.skills.prepared['profile']['resources'].get('candidate_seconds',20)
        if not available():return {**common,'status':'not_run','reason':'candidate_screen_time_limit'}
        rows=sorted((r for r in self.cal if len(r.get('qpos',[]))==8),key=lambda r:-r['gap_mm'])
        opening=candidate['opening_width_mm']
        if not rows or min(abs(r['gap_mm']-opening) for r in rows)>1.:
            return {**common,'status':'not_run','reason':'uncalibrated_opening'}
        first=min(rows,key=lambda r:abs(r['gap_mm']-opening))
        rows=rows[rows.index(first):]
        # Calibration is bounded by calibrated_pad_center to <=256 samples.
        if len(rows)>256:return {**common,'status':'not_run','reason':'unbounded_calibration'}
        local=pose(np.asarray(candidate['position_mm'])/1000,candidate['quaternion_xyzw'])
        current=body_pose(self.skills.data,body_name(self.skills.active,self.skills.prepared['base_id']))
        target=current@local
        active=np.array([g for g,p in self.skills.verifier.geom_part.items() if p==self.skills.active],dtype=int)
        if not len(active):return {**common,'status':'not_run','reason':'no_active_collision_geometry'}
        previous=None;trace=[]
        for row in rows:
            if not available():return {**common,'status':'not_run','reason':'candidate_screen_time_limit','sample_count':len(trace),'contact_boundary_samples':trace[-2:]}
            self._project_tool(row,target);hit=self._projected_distance(active)
            trace.append({'gap_mm':row['gap_mm'],**hit})
            if hit['minimum_distance_m']<=0:
                kind='pad' if hit['tool_geom_id'] in self.skills.verifier.pads else 'non_pad'
                if previous is None:
                    return {**common,'status':'blocked_at_opening','contact_kind':kind,'sample_count':len(trace),'contact_boundary_samples':trace[-2:]}
                trials=[]
                for endpoint in (row,previous):
                    self._project_tool(endpoint,self.goal@local)
                    trials.append({'calibrated_gap_mm':endpoint['gap_mm'],**self._projected_distance(self.obstacles)})
                worst=min(t['minimum_distance_m'] for t in trials)
                lo,hi=row['gap_mm'],previous['gap_mm']
                return {**common,'status':'first_contact_bracketed','contact_kind':kind,
                    'contact_gap_bracket_mm':[lo,hi],
                    'surface_width_underestimates_first_contact':lo>common['candidate_surface_width_mm']+1.,
                    'goal_with_first_contact':{'status':'clear_sampled_nominal' if worst>1e-6 else 'blocked_sampled_nominal',
                        'worst_clearance_mm':worst*1000,'gap_trials':trials,'physical_success':False},
                    'sample_count':len(trace),'contact_boundary_samples':trace[-2:],'actual_closure_and_installation_required':True}
            previous=row
        return {**common,'status':'no_contact_in_calibrated_range','sample_count':len(trace),'contact_boundary_samples':trace[-2:]}


    def closure_repairs(self,candidate,closure):
        from .grasp_refinement import feature_pose_alternatives
        return feature_pose_alternatives(self,candidate,closure)

    def opening_and_closure(self,candidate):
        """Choose an opening only after checking the complete closing interval.

        A very wide approach can be clear yet sweep the finger backing across
        an intervening wall while closing. Clearance at the start is not enough.
        """
        result=self.source_openings(candidate)
        result['selection_basis']='joint_sampled_approach_closure_goal_not_maximum_open_clearance'
        result['approach_only_recommendation_mm']=result.get('recommended_opening_mm')
        if result.get('status')!='clear_sampled_nominal':return result
        reviewed=[];compatible=[]
        for row in result['trials']:
            if row['status']!='clear_sampled_nominal':continue
            trial={**candidate,'opening_width_mm':row['requested_opening_mm']}
            close=self.closure_preview(trial)
            goal=close.get('goal_with_first_contact',{})
            check={'opening_mm':row['requested_opening_mm'],'closure_status':close['status'],
                'first_contact_kind':close.get('contact_kind'),'contact_gap_bracket_mm':close.get('contact_gap_bracket_mm'),
                'goal_status':goal.get('status'),'goal_clearance_mm':goal.get('worst_clearance_mm'),
                'physical_success':False}
            reviewed.append(check)
            if close.get('contact_kind')=='pad' and goal.get('status')=='clear_sampled_nominal':
                compatible.append(row)
        chosen=choose_joint_opening(compatible)
        result['opening_selection_tradeoff']='3 mm roomy preference, then minimum jointly clear opening; shorter closure travel, not a dynamics guarantee'
        result.update(closure_by_opening=reviewed,recommended_opening_mm=chosen['requested_opening_mm'] if chosen else None,
                      status='clear_sampled_nominal' if chosen else 'no_jointly_clear_sampled_opening')
        return result
