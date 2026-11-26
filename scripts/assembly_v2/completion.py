"""The common completion profile, shared by physics, memory, audits and UI."""
from copy import deepcopy
from pathlib import Path
import math
import numpy as np
from assembly_v12.contracts import read,digest

ROOT=Path(__file__).resolve().parents[2]
PROFILE_PATH=ROOT/'config/completion.v1.2.json'

def load_profile(path=PROFILE_PATH):
    p=read(path)
    g=p['global_completion']
    if (p['schema']!='assembly_completion_profile/v1.2' or p['physics_timestep_s']!=.002 or
        p['gravity_m_s2']!=[0,0,-9.81] or g['position_error_max_xyz_m']!=[.005]*3 or
        g['position_error_frame']!='fixed_base' or g['position_error_metric']!='componentwise_abs' or
        not math.isclose(g['orientation_error_max_rad'],math.radians(3),abs_tol=1e-14) or
        g['linear_speed_max_m_s']!=.001 or g['angular_speed_max_rad_s']!=.01 or
        g['final_hard_contact_penetration_max_m']!=.0005 or
        g['gripper_product_min_clearance_m']!=.005 or g['robot_product_contact_allowed'] is not False or
        p['execution_hard_contact_penetration_max_m']!=.001 or
        any(p[k]!=1 for k in ('reference_settle_s','reference_observation_s','post_release_settle_s','post_release_observation_s'))):
        raise ValueError('Completion profile differs from the approved v1.2 contract.')
    return p

def profile_for_timestep(timestep=.002):
    """Explicit development variants preserve every geometric/physical threshold."""
    if timestep not in (.002,.004,.010):raise ValueError('Supported physics intervals: .002/.004/.010 seconds.')
    baseline=load_profile()
    if timestep==.002:return baseline
    profile=deepcopy(baseline)
    profile['profile_id']+='__experimental_'+str(round(timestep*1000))+'ms'
    profile['physics_timestep_s']=float(timestep)
    profile['numerical_variant']={'status':'experimental_development_only',
        'baseline_profile_hash':digest(baseline),'baseline_timestep_s':.002,
        'observation_samples':round(1./timestep)+1,
        'not_baseline_501_sample_qualification':True}
    return profile


def observation_samples(profile):
    dt=float(profile['physics_timestep_s'])
    if dt not in (.002,.004,.010):raise ValueError('Unrecognized observation interval.')
    return round(1./dt)+1


def with_timestep(prepared,timestep):
    """Derive new initialization data; never edit source CAD or a saved live state."""
    old=prepared['completion_profile']
    if old!=profile_for_timestep(old['physics_timestep_s']) or prepared['completion_profile_hash']!=digest(old):
        raise ValueError('Cannot derive a timestep variant from altered completion criteria.')
    profile=profile_for_timestep(timestep)
    out=deepcopy(prepared)
    if profile!=old:
        if prepared.get('continuation_contract') or prepared.get('initial_integration_state'):
            raise ValueError('Timestep variants require a fresh scene, not live continuation.')
        out['numerical_parent']={'prepared_sha256':digest(prepared),'completion_profile_hash':digest(old),
            'fresh_scene_required':True}
    out['completion_profile']=profile;out['completion_profile_hash']=digest(profile)
    return out


def operation_checks(operation,measurement,profile,reference=False):
    if operation['kind']=='generic_goal_placement':
        reason=operation.get('not_applicable_reason')
        if not reason: return {'insertion_definition':{'status':'unknown','required':True}}
        return {'insertion_depth':{'status':'not_applicable','required':False,'reason':reason},
                'insertion_prealignment':{'status':'not_applicable','required':False,'reason':reason}}
    if operation['kind']!='linear_insertion' or not operation.get('geometry_evidence'):
        return {'operation_definition':{'status':'unknown','required':True}}
    limits=profile['operation_checks']['linear_insertion']
    checks={}
    for key,limit in (('insertion_depth_error_m',limits['depth_error_max_m']),
                      ('prealignment_perpendicular_error_m',limits['prealignment_perpendicular_error_max_m']),
                      ('prealignment_orientation_error_rad',limits['prealignment_orientation_error_max_rad'])):
        v=measurement.get(key)
        if reference and key.startswith('prealignment_'):
            checks[key]={'status':'not_applicable','required':False,'reason':'Separate reference scene has no robot approach; actual installation requires prealignment.'}
            continue
        checks[key]={'status':'unknown' if v is None else 'passed' if math.isfinite(v) and v<limit else 'failed',
                     'required':True,'value':v,'limit':limit,'comparison':'lt'}
    return checks

def sample_checks(m,operation,profile,reference=False):
    g=profile['global_completion']
    specifications={
        'position_base_axis5mm':('position_error_base_xyz_m',g['position_error_max_xyz_m'],'le'),
        'relative_orientation_3deg':('orientation_error_rad',g['orientation_error_max_rad'],'le'),
        'origin_linear_speed':('linear_speed_m_s',g['linear_speed_max_m_s'],'le'),
        'angular_speed':('angular_speed_rad_s',g['angular_speed_max_rad_s'],'le'),
        'final_hard_penetration':('hard_penetration_m',g['final_hard_contact_penetration_max_m'],'le'),
        'gripper_clearance':('gripper_clearance_m',g['gripper_product_min_clearance_m'],'ge')}
    result={}
    for label,(key,limit,comparison) in specifications.items():
        value=m.get(key)
        if value is None:status='unknown'
        else:
            v=np.asarray(value,float)
            ok=np.isfinite(v).all() and (np.all(v<=np.asarray(limit)+1e-12) if comparison=='le' else np.all(v>=limit-1e-12))
            status='passed' if ok else 'failed'
        result[label]={'status':status,'required':True,'value':value,'limit':limit,'comparison':comparison}
    for key in ('released','retreat_complete','robot_product_noncontact'):
        result[key]={'status':'passed' if m.get(key) is True else 'unknown' if key not in m else 'failed','required':True}
    result.update(operation_checks(operation,m,profile,reference))
    return result

def checks_pass(checks):
    return bool(checks) and all(v['status']=='passed' if v.get('required') else
                               v['status'] in ('passed','not_applicable') and (v['status']!='not_applicable' or v.get('reason'))
                               for v in checks.values())

class ObservationWindow:
    def __init__(self,parts,profile,*,check_id,source,model_sha256,asset_hash,operations):
        self.profile=profile;self.parts=list(parts);self.operations=operations
        self.result={'schema':'completion_evidence/v1.2','profile_id':profile['profile_id'],'profile_hash':digest(profile),
                     'check_id':check_id,'source':source,'model_sha256':model_sha256,'asset_hash':asset_hash,
                     'part_ids':list(parts),'status':'not_run','complete':False,'samples':0,'start_time_s':None,
                     'end_time_s':None,'duration_s':0.,'parts':{},'violations':[],
                     'frame':'fixed_base_at_initialization','reference_point':'declared_part_frame_origin',
                     'physics_timestep_s':profile['physics_timestep_s'],'required_samples':observation_samples(profile)}
    def add(self,t,measurements):
        r=self.result
        if set(measurements)!=set(self.parts):raise ValueError('Every target must be measured in the same physics step.')
        if r['start_time_s'] is None:r['start_time_s']=float(t)
        elif not math.isclose(t-r['end_time_s'],self.profile['physics_timestep_s'],abs_tol=1e-8):
            raise ValueError('Missing/duplicated observation step.')
        r['end_time_s']=float(t);r['samples']+=1;r['duration_s']=float(t)-r['start_time_s']
        for pid,m in measurements.items():
            checks=sample_checks(m,self.operations[pid],self.profile,reference=r['source']=='reference_scene')
            row=r['parts'].setdefault(pid,{'checks':deepcopy(checks),'worst':{},'goal_pose':m.get('goal_pose'),
                                          'last_pose':None,'operation':deepcopy(self.operations[pid])})
            row['last_pose']=m.get('current_pose')
            for key,c in checks.items():
                if c['status'] not in ('passed','not_applicable'):
                    if row['checks'][key]['status'] in ('passed','not_applicable') or r['samples']==1:
                        r['violations'].append({'part_id':pid,'check':key,'time_s':float(t),'evidence':deepcopy(c)})
                    row['checks'][key]=deepcopy(c)
                if c.get('value') is not None:
                    v=np.asarray(c['value'],float);old=row['worst'].get(key)
                    if old is None:worst=v
                    elif c.get('comparison')=='ge':worst=np.minimum(old,v)
                    else:worst=np.maximum(old,v)
                    row['worst'][key]=worst.tolist()
        r['status']='running'
    def finish(self,reason=None):
        r=self.result
        r['complete']=r['samples']>=observation_samples(self.profile) and r['duration_s']>=1.-1e-8 and reason is None
        r['status']='passed' if r['complete'] and all(checks_pass(x['checks']) for x in r['parts'].values()) else 'failed' if r['violations'] else 'unknown'
        if not r['complete']:r['incomplete_reason']=reason or 'observation_window_incomplete'
        return deepcopy(r)

def valid_evidence(e,profile_hash=None,*,source='live_simulation',required_parts=None):
    if not isinstance(e,dict):return False
    try:profile=profile_for_timestep(e.get('physics_timestep_s',.002))
    except (ValueError,TypeError):return False
    valid=(isinstance(e,dict) and e.get('schema')=='completion_evidence/v1.2' and e.get('status')=='passed' and
            e.get('complete') is True and e.get('source')==source and e.get('duration_s',0)>=1.-1e-8 and
            e.get('samples',0)>=observation_samples(profile) and bool(e.get('model_sha256')) and bool(e.get('asset_hash')) and
            (profile_hash is None or e.get('profile_hash')==profile_hash) and
            (required_parts is None or set(e.get('part_ids',[]))==set(required_parts)) and
            set(e.get('parts',{}))==set(e.get('part_ids',[])) and bool(e.get('parts')) and
            not e.get('violations') and all(checks_pass(p.get('checks',{})) for p in e['parts'].values()))
    if not valid:return False
    if e.get('profile_hash')!=digest(profile):return False
    if e.get('required_samples',observation_samples(profile))!=observation_samples(profile):return False
    common={'position_base_axis5mm','relative_orientation_3deg','origin_linear_speed','angular_speed',
            'final_hard_penetration','gripper_clearance','released','retreat_complete','robot_product_noncontact'}
    for row in e['parts'].values():
        if not common<=set(row['checks']):return False
        op=row.get('operation',{})
        required={'insertion_depth_error_m','prealignment_perpendicular_error_m','prealignment_orientation_error_rad'} if op.get('kind')=='linear_insertion' else {'insertion_depth','insertion_prealignment'}
        if not required<=set(row['checks']):return False
        if source=='live_simulation' and op.get('kind')=='linear_insertion' and any(row['checks'][k].get('required') is not True for k in required):return False
        mapping={'position_base_axis5mm':'position_error_base_xyz_m','relative_orientation_3deg':'orientation_error_rad',
                 'origin_linear_speed':'linear_speed_m_s','angular_speed':'angular_speed_rad_s',
                 'final_hard_penetration':'hard_penetration_m','gripper_clearance':'gripper_clearance_m'}
        measured={name:row.get('worst',{}).get(label) for label,name in mapping.items()}
        measured.update({k:row['checks'][k].get('status')=='passed' for k in ('released','retreat_complete','robot_product_noncontact')})
        if op.get('kind')=='linear_insertion':
            measured.update({k:row.get('worst',{}).get(k) for k in required})
        expected=sample_checks(measured,op,profile,reference=source=='reference_scene')
        if not checks_pass(expected) or set(expected)!=set(row['checks']):return False
        for key,c in expected.items():
            stored=row['checks'][key]
            if any(stored.get(field)!=c.get(field) for field in ('limit','comparison','required')):return False
        for k in common:
            c=row['checks'][k]
            if c.get('required') is not True:return False
            if k in row.get('worst',{}):
                v=np.asarray(row['worst'][k],float);limit=np.asarray(c['limit'])
                if not np.isfinite(v).all() or (np.any(v<limit-1e-12) if c['comparison']=='ge' else np.any(v>limit+1e-12)):return False
    return True
