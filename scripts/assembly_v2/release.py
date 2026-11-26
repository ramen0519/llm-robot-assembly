"""Measured contact release before withdrawal; no commanded gap proves release."""
import math
import numpy as np
from failure_report import FailureCode,FailureEvent
from .transforms import errors

RELEASE_VERSION='measured_supported_release/v1'
MIN_SEPARATION_M=.0001
MAX_EXTRA_OPENING_MM=2.
MAX_OPENING_SECONDS=6.
HOLD_SECONDS=1.


def release_observation(skills,row):
    """Use this exact physics frame, including zero-force geometric contacts."""
    goal=np.asarray(skills.prepared['parts'][skills.active]['target'])
    xyz,angle=errors(goal,np.asarray(row['part_pose']),skills.verifier.base_rotation)
    near=bool(np.all(xyz<=.005+1e-12) and angle<=math.radians(3)+1e-12)
    # A zero-force pad contact must not be mistaken for detached fingers.
    contact=skills.active in skills.verifier._last_contacts['robot_contact_parts']
    clearance=skills.verifier.clearance(skills.data,skills.active) if not contact else None
    return {'simulation_time_s':row['simulation_time_s'],'pad_gap_mm':row['pad_gap_mm'],
        'supported':bool(row['supported']),'robot_contact':contact,
        'part_clearance_m':clearance,'within_original_goal':near,
        'position_error_base_xyz_m':xyz.tolist(),'orientation_error_rad':float(angle),
        'detached':bool(not contact and clearance is not None and clearance>=MIN_SEPARATION_M),
        'part_speed_m_s':row['part_speed_m_s'],'part_angular_speed_rad_s':row['part_angular_speed_rad_s']}


def open_until_clear(skills,context):
    """Open only until actual clearance and physical support are observed.

    Two millimetres is an opening ceiling, not a mandatory target. Full existing
    contact/force/joint checks run at every physics step. A failed movement is
    never rolled back/replayed. Post-retreat completion remains separate.
    """
    dt=skills.motion.dt
    if not math.isfinite(dt) or dt<=0:raise ValueError('Invalid release timestep.')
    maximum=context['held_gap_mm']+MAX_EXTRA_OPENING_MM
    evidence={'schema':RELEASE_VERSION,'source':skills.source,'initial_gap_mm':context['held_gap_mm'],
        'maximum_gap_mm':maximum,'minimum_separation_m':MIN_SEPARATION_M,
        'requested_extra_opening_is_ceiling':True,'status':'running','samples':0,
        'trace':[],'physical_installation_success':False}
    context['opening_evidence']=evidence
    try:
        for index in range(math.ceil(MAX_OPENING_SECONDS/dt)+1):
            row=skills.motion.sample(allow_support=True,allow_grasp=True)
            observed=release_observation(skills,row);evidence['samples']+=1;evidence['last']=observed
            if index==0 or index%max(1,round(.1/dt))==0:evidence['trace'].append(observed)
            if not observed['within_original_goal']:
                raise FailureEvent(FailureCode.PLACEMENT_NOT_CONFIRMED,
                    'Part moved outside original CAD goal during release.',violations=[observed])
            if observed['detached']:
                # Stop opening now, even if support is still settling. Existing
                # gravity/contact dynamics and no-grasp checks must establish it.
                evidence['stop_command']=float(skills.motion.gripper_command)
                evidence['gap_at_first_clear_mm']=row['pad_gap_mm']
                for hold_index in range(math.ceil(HOLD_SECONDS/dt)):
                    after=skills.motion.step(allow_support=True,allow_grasp=False)
                    verified=release_observation(skills,after)
                    evidence['settle_samples']=hold_index+1;evidence['settled']=verified
                    if (not verified['detached'] or not verified['within_original_goal']
                            or after['pad_gap_mm']>maximum+1e-9):
                        raise FailureEvent(FailureCode.PLACEMENT_NOT_CONFIRMED,
                            'Released part recontacted the tool, left its goal or exceeded bounded opening.',violations=[verified])
                if (not verified['detached'] or not verified['supported'] or not verified['within_original_goal']
                        or verified['part_speed_m_s']>.001 or verified['part_angular_speed_rad_s']>.01):
                    raise FailureEvent(FailureCode.PLACEMENT_NOT_CONFIRMED,
                        'Release lacks measured separation, stable support or original goal preservation.',violations=[verified])
                evidence.update(status='released_supported',actual_extra_opening_mm=after['pad_gap_mm']-context['held_gap_mm'],
                    physical_installation_success=False)
                return evidence
            if row['pad_gap_mm']>=maximum or index==math.ceil(MAX_OPENING_SECONDS/dt):
                raise FailureEvent(FailureCode.PLACEMENT_NOT_CONFIRMED,
                    'No measured contact-free release within bounded opening.',violations=[observed,
                    {'maximum_gap_mm':maximum,'maximum_seconds':MAX_OPENING_SECONDS}])
            skills.motion.gripper_command=max(0.,skills.motion.gripper_command-5.*dt)
            skills.motion.step(allow_support=True,allow_grasp=True)
    except Exception:
        evidence['status']='failed';raise
    finally:
        skills.path_attempts.append({'phase':'measured_release',**evidence})
