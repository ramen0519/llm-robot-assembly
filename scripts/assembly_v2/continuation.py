"""Cooperative motion slices within the unchanged episode budget.

Only a local time slice may be continued. Physical failures, cancellation and
the global deadline propagate. Re-observation and a new checked plan are mandatory.
"""
import math
from copy import deepcopy
from assembly_v12.contracts import append


class SliceExpired(TimeoutError):
    pass


class MotionSlice:
    def __init__(self,parent,seconds):
        if not math.isfinite(seconds) or seconds<=0:raise ValueError('Positive finite motion slice required.')
        self.parent=parent;self.clock=parent.clock
        self.deadline=min(parent.deadline,self.clock()+seconds)
    def check(self,phase='operation'):
        self.parent.check(phase)  # Global stop/cancel is never converted to a resumable slice.
        if self.clock()>=self.deadline:raise SliceExpired('motion_slice:'+phase)
    def remaining(self,limit=None):
        self.check();value=self.deadline-self.clock()
        return max(.001,min(value,limit)) if limit is not None else max(.001,value)


def options(prepared):
    resources=prepared.get('profile',{}).get('resources',{})
    seconds=float(resources.get('motion_slice_seconds',60.))
    limit=int(resources.get('motion_continuations_per_move',2))
    if not 1<=seconds<=300 or not 0<=limit<=3:raise ValueError('Invalid bounded motion continuation settings.')
    return seconds,limit


def record_continuation(skills,**values):
    row={'source':skills.source,'part_id':skills.active,'phase':skills.motion.stage,
         'simulation_time_s':float(skills.data.time),'policy_source':'host_bounded_continuation',
         'global_deadline_unchanged':True,**values}
    skills.continuation_events.append(deepcopy(row))
    if skills.recorder:append(skills.recorder.folder/'continuations.jsonl',row)
    return row


def finite_plan_execution_budget(parent,seconds,duration_s,timestep_s):
    """An explicitly unbounded development run still has finite servo steps.

    Do not restart a valid slow simulated move just because the workstation
    needed a minute of wall time. Cancellation and per-step physical guards
    stay active. Paid/bounded episodes retain their motion slices unchanged.
    """
    from .budget import DevelopmentBudget
    if not math.isfinite(duration_s) or duration_s<0 or not math.isfinite(timestep_s) or timestep_s<=0:
        raise ValueError('Finite simulated motion duration and positive timestep required.')
    return parent if isinstance(parent,DevelopmentBudget) else MotionSlice(parent,seconds)
