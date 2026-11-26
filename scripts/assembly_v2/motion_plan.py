"""Small declarative motion recipe; no Python, new skills or verifier changes.

The policy proposes parameters/waypoints. The serial executor resolves the
recipe and performs the existing geometric, path and physical verification.
"""
from copy import deepcopy
import math
from assembly_v12.contracts import digest

VERSION='bounded_motion_plan/v1'
DEFAULT={'approach_distance_mm':80.,'lift_height_mm':None,
         'transport_via_goal_offsets_mm':[], 'preinstall_axis_world':None,
         'preinstall_extra_mm':0.,'release_mode':'configured'}

def nullable_number(low,high):return {'type':['number','null'],'minimum':low,'maximum':high}
PROPERTIES={
    'approach_distance_mm':nullable_number(40,120),
    'lift_height_mm':nullable_number(65,120),
    'transport_via_goal_offsets_mm':{'type':['array','null'],'maxItems':2,
        'items':{'type':'array','minItems':3,'maxItems':3,'items':{'type':'number','minimum':-250,'maximum':250}}},
    'preinstall_axis_world':{'type':['array','null'],'minItems':3,'maxItems':3,
                            'items':{'type':'number','minimum':-1,'maximum':1}},
    'preinstall_extra_mm':nullable_number(0,40),
    'release_mode':{'type':['string','null'],'enum':[None,'configured','limited_open_then_retreat','full_open']}}
SCHEMA={'type':['object','null'],'properties':PROPERTIES,'required':list(PROPERTIES),'additionalProperties':False}

def number(value,low,high):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not low<=value<=high:
        raise ValueError('Motion-plan value is not a finite in-range number.')

def validate(value,*,mode='bounded',operation=None):
    if mode not in ('fixed','bounded'):raise ValueError('Unknown motion plan mode.')
    if value is None:return
    if mode=='fixed':raise ValueError('Fixed-policy comparison requires motion_plan=null.')
    if not isinstance(value,dict) or set(value)!=set(DEFAULT):raise ValueError('Unexpected motion-plan fields.')
    for key,low,high in (('approach_distance_mm',40,120),('lift_height_mm',65,120),('preinstall_extra_mm',0,40)):
        if value[key] is not None:number(value[key],low,high)
    waypoints=value['transport_via_goal_offsets_mm']
    if waypoints is not None:
        if not isinstance(waypoints,list) or len(waypoints)>2:raise ValueError('At most two transport waypoints.')
        for point in waypoints:
            if not isinstance(point,list) or len(point)!=3:raise ValueError('Three world-axis offsets required.')
            for x in point:number(x,-250,250)
            if math.sqrt(sum(x*x for x in point))>300:raise ValueError('Transport offset exceeds 300 mm.')
    axis=value['preinstall_axis_world']
    if axis is not None:
        if not isinstance(axis,list) or len(axis)!=3:raise ValueError('Three axis components required.')
        for x in axis:number(x,-1,1)
        if abs(math.sqrt(sum(x*x for x in axis))-1)>1e-6:raise ValueError('Unit approach axis required.')
        if operation and operation['kind']=='linear_insertion':
            if any(abs(a-b)>1e-8 for a,b in zip(axis,operation['axis_world'])):
                raise ValueError('A motion plan cannot change a defined insertion axis.')
    if value['release_mode'] not in (None,'configured','limited_open_then_retreat','full_open'):
        raise ValueError('Unsupported release procedure.')

def resolve(value,*,current=None,mode='bounded',operation=None):
    validate(value,mode=mode,operation=operation)
    result=deepcopy(DEFAULT if current is None else current)
    if value is not None:
        for key,item in value.items():
            if item is not None:result[key]=deepcopy(item)
    validate(result,operation=operation)
    return result

def contract(mode,current=None):
    return {'schema':VERSION,'mode':mode,'current':deepcopy(current or DEFAULT),
        'null_plan':'Keep current plan; a new pick starts from common defaults.',
        'null_field':'Keep that field from the current/default plan.',
        'approach_distance_mm':'40..120 along the negative grasp TCP Z axis; full orientation is selected in grasp.',
        'lift_height_mm':'65..120 vertical; null uses the common checked 100/80/65 mm fallback.',
        'transport_via_goal_offsets_mm':'0..2 offsets in WORLD axes from original CAD part-goal origin; each axis <=250 mm, norm <=300 mm. Goal part orientation is retained. Each edge is independently planned and physically checked.',
        'preinstall_axis_world':'Unit direction from goal to approach. A defined insertion axis cannot be changed.',
        'preinstall_extra_mm':'0..40 added to the geometry-derived safe approach envelope; cannot reduce its minimum.',
        'release_mode':'configured, limited_open_then_retreat, full_open. Limited release opens until measured separation and support, at most 2 mm extra, then checks withdrawal before full opening. All contact checks still apply.',
        'host_only':['IK','trajectory','servo','contact force limits','completion thresholds','physical success'],
        'unsupported':['arbitrary_code','regrasp','fastening','teleportation','budget_changes'],
        'current_sha256':digest(current or DEFAULT)}
