"""Bounded geometry/IK proposals around a small surface feature, never motions."""
from copy import deepcopy
import time
import numpy as np
from scipy.spatial.transform import Rotation
from view_tcp import read_tcp_pose
from .grasp_transport_hint import nominal_transport_hint,refinement_trials
from .transforms import pose,xyzw


def translation_only(screen,candidate,common):
    """Legacy/small diagnostic inputs have no calibrated rotation pivot."""
    original=pose(np.asarray(candidate['position_mm'])/1000,candidate['quaternion_xyzw'])
    common={**common,'search_scope':'translation_only_no_calibrated_rotation_pivot'}
    for offset in (.5,1.,1.5,2.,2.5):
        screen.skills.budget.check('nominal_closure_repair')
        if time.monotonic()-screen.started>screen.skills.prepared['profile']['resources'].get('candidate_seconds',20):
            return {**common,'status':'partial_time_limit'}
        t=original.copy();t[:3,3]-=t[:3,2]*offset/1000
        trial=deepcopy(candidate);trial.update(position_mm=(t[:3,3]*1000).tolist(),quaternion_xyzw=xyzw(t))
        check=screen.closure_preview(trial);goal=check.get('goal_with_first_contact',{})
        if check.get('contact_kind')=='pad' and not check.get('surface_width_underestimates_first_contact',True) and goal.get('status')=='clear_sampled_nominal':
            common['alternatives'].append({'mode':'pose','frame':'part_local','position_mm':trial['position_mm'],
                'quaternion_xyzw':trial['quaternion_xyzw'],'opening_width_mm':trial['opening_width_mm'],
                'parent_candidate_id':candidate['candidate_id'],'retraction_mm':offset,
                'geometry':{'contact_gap_bracket_mm':check['contact_gap_bracket_mm'],
                    'goal_clearance_mm':goal['worst_clearance_mm'],'original_surface_pair_is_seed_only':True,
                    'goal_ik':{'status':'not_run','reason':'no_calibrated_rotation_pivot'}},
                'requires':['current_scene_approach','physical_closure_lift','measured_held_installation','release_completion_window']})
    return {**common,'status':'alternatives_found' if common['alternatives'] else 'no_alternative_in_bounded_scan'}


def feature_pose_alternatives(screen,candidate,closure):
    common={'source':'bounded_geometry_pose_alternatives','physical_success':False,
        'offset_axis':'negative_revised_TCP_z_in_part_frame','original_candidate_preserved':True,
        'not_an_executed_recovery':True,'alternatives':[],
        'search_scope':'closing-direction yaw and approach tilt around the measured surface-feature centre',
        'max_trials':168,'max_presented_alternatives':6}
    if closure.get('status')!='first_contact_bracketed' or not closure.get('surface_width_underestimates_first_contact'):
        return {**common,'status':'not_needed_for_this_geometry'}
    if 'pad_center_tcp_m' not in candidate.get('geometry',{}):
        return translation_only(screen,candidate,common)
    original=pose(np.asarray(candidate['position_mm'])/1000,candidate['quaternion_xyzw'])
    pad=np.asarray(candidate['geometry']['pad_center_tcp_m'])
    centre=original[:3,3]+original[:3,:3]@pad
    skills=screen.skills;resources=skills.prepared['profile']['resources']
    current_rotation=read_tcp_pose(skills.data)[1]
    current_part_rotation=np.asarray(skills.data.xmat[skills.model.body('workcell_part_'+skills.active).id]).reshape(3,3)
    tested=0;converged=0
    for yaw,tilt,offset in refinement_trials():
        rotation=original[:3,:3]@Rotation.from_euler('z',yaw,degrees=True).as_matrix()@Rotation.from_euler('y',tilt,degrees=True).as_matrix()
        skills.budget.check('nominal_feature_pose_refinement')
        left=resources.get('candidate_seconds',20)-(time.monotonic()-screen.started)
        if left<=0 or tested>=common['max_trials']:
            return {**common,'status':'partial_time_limit' if left<=0 else 'bounded_scan_complete','tested':tested}
        tested+=1;t=original.copy();t[:3,:3]=rotation
        t[:3,3]=centre-rotation@pad-rotation[:,2]*offset/1000
        trial=deepcopy(candidate);trial.update(position_mm=(t[:3,3]*1000).tolist(),quaternion_xyzw=xyzw(t))
        check=screen.closure_preview(trial);goal=check.get('goal_with_first_contact',{})
        if check.get('contact_kind')!='pad' or check.get('surface_width_underestimates_first_contact',True) or goal.get('status')!='clear_sampled_nominal':continue
        target=screen.goal@t;attempts=[]
        left=resources.get('candidate_seconds',20)-(time.monotonic()-screen.started)
        if left<=0:return {**common,'status':'partial_time_limit','tested':tested}
        ik=nominal_transport_hint(screen,t,min(4.,left))
        hint={'goal_top_approach_deviation_rad':float(np.arccos(np.clip(-target[2,2],-1.,1.))),
            'tcp_orientation_change_rad':float(Rotation.from_matrix(current_rotation.T@current_part_rotation@rotation).magnitude()),
            'not_a_feasibility_check':True}
        if 'source_goal_max_joint_distance_rad' in ik:
            hint['carried_joint_span_rad']=ik['source_goal_max_joint_distance_rad']
        common['alternatives'].append({'mode':'pose','frame':'part_local','position_mm':trial['position_mm'],
            'quaternion_xyzw':trial['quaternion_xyzw'],'opening_width_mm':trial['opening_width_mm'],
            'parent_candidate_id':candidate['candidate_id'],'retraction_mm':offset,
            'closing_axis_yaw_deg':yaw,'approach_tilt_deg':tilt,'motion_hint':hint,
            'geometry':{'contact_gap_bracket_mm':check['contact_gap_bracket_mm'],
                'goal_clearance_mm':goal['worst_clearance_mm'],'goal_ik':ik,'original_surface_pair_is_seed_only':True},
            'requires':['current_scene_approach','physical_closure_lift','measured_held_installation','release_completion_window']})
        converged+=ik['status']=='converged'
        if converged>=4 or len(common['alternatives'])>=common['max_presented_alternatives']:
            return {**common,'status':'alternatives_found','tested':tested}
    return {**common,'status':'alternatives_found' if common['alternatives'] else 'no_alternative_in_bounded_scan','tested':tested}
