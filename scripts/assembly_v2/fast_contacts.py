"""Vectorized classification of one immutable MuJoCo contact frame.

Only model topology is cached. Forces, poses, contact order and all decisions
are recomputed on every call. Non-elliptic models retain the scalar verifier.
"""
import numpy as np
import mujoco
from failure_report import FailureCode, FailureEvent


def topology(v):
    cached=getattr(v,'_contact_topology',None)
    if cached is not None:return cached
    ids=list(v.bodies);lookup={p:i for i,p in enumerate(ids)};n=v.model.ngeom
    product=np.full(n,-1,dtype=int);fixture=np.full(n,-1,dtype=int)
    for g,p in v.geom_part.items():product[g]=lookup[p]
    for g,p in v.fixture_owner.items():fixture[g]=lookup[p]
    robot=np.zeros(n,bool);robot[list(v.robot_geoms)]=True
    pad=np.zeros(n,bool);pad[list(v.pads)]=True
    left=np.zeros(n,bool)
    for g,name in v.pads.items():left[g]='left' in name
    pairs=np.zeros((len(ids),len(ids)),bool)
    for i,a in enumerate(ids):
        for j,b in enumerate(ids):pairs[i,j]=frozenset((a,b)) in v.goal_pairs
    names=[];roles=[]
    for g in range(n):
        name=v.model.geom(g).name
        if not name:name='body:'+v.model.body(int(v.model.geom_bodyid[g])).name+'/geom:'+str(g)
        names.append(name)
        roles.append('product:'+ids[product[g]] if product[g]>=0 else 'pad' if pad[g] else
                     'gripper' if g in v.gripper_geoms else 'robot' if robot[g] else
                     'table' if g==v.table_geom else 'supply_fixture' if fixture[g]>=0 else 'environment')
    cached=(ids,lookup,product,fixture,robot,pad,left,pairs,names,roles)
    v._contact_topology=cached
    return cached


def contacts(v,data,*,allow_support,allow_grasp,planned=False):
    ids,lookup,product,fixture,robot,pad,left,pairs,names,roles=topology(v)
    c=data.contact;indices=np.flatnonzero(c.dist<=1e-7)
    geoms=c.geom[indices];dist=c.dist[indices].copy();positions=c.pos[indices].copy()
    a,b=geoms[:,0],geoms[:,1];pa,pb=product[a],product[b]
    both=(pa>=0)&(pb>=0);one=(pa>=0)^(pb>=0)
    pid=np.maximum(pa,pb);counter=np.where(pa>=0,b,a)
    active=lookup[v.active];base=lookup[v.base]
    grasp=one&(pid==active)&pad[counter]
    installed=np.array([p in v.installed for p in ids],bool)
    table=one&(counter==v.table_geom);fixtures=one&(fixture[counter]>=0)
    robot_part=one&robot[counter]
    # Same-frame pose predicates; no reuse by simulation timestamp.
    needed=np.unique(np.r_[pa[both],pb[both],pid[table&installed[np.maximum(pid,0)]]])
    near=np.zeros(len(ids),bool)
    statuses=v.near_goal_many(data,[ids[i] for i in needed])
    for i in needed:near[i]=statuses[ids[i]]
    declared=np.zeros(len(indices),bool);declared[both]=pairs[pa[both],pb[both]]
    close=np.zeros(len(indices),bool);close[both]=near[pa[both]]&near[pb[both]]
    supported_phase=installed.copy();supported_phase[base]=True
    phase=np.zeros(len(indices),bool)
    phase[both]=v.reference_mode|v.mating|(supported_phase[pa[both]]&supported_phase[pb[both]])
    allowed=both&declared&close&phase
    positive_pid=np.maximum(pid,0)
    allowed|=table&((pid==base)|(~installed[positive_pid]&(pid!=active))|
                    ((pid==active)&allow_support)|(installed[positive_pid]&near[positive_pid]))
    allowed|=fixtures&(fixture[counter]==pid)&~installed[positive_pid]&((pid!=active)|allow_support)
    allowed|=robot_part&grasp&allow_grasp

    # mj_contactForce on elliptic contacts is the contact's contiguous slice of
    # efc_force (zero for excluded constraints). Pyramidal models use the scalar
    # implementation. Keep all six dimensions, including torsion/rolling.
    force=np.zeros((len(indices),6))
    if not planned and len(indices):
        adr=c.efc_address[indices];dim=c.dim[indices];component=np.arange(6)[None,:]
        valid=(adr[:,None]>=0)&(component<dim[:,None])
        addresses=adr[:,None]+component
        force[valid]=data.efc_force[addresses[valid]]
    force_bad=v.mating&~grasp&((pa==active)|(pb==active))&(force[:,0]>8.)
    bad=np.flatnonzero(~allowed|force_bad)

    def fact(i):
        ga,gb=int(a[i]),int(b[i]);ia,ib=int(pa[i]),int(pb[i])
        out={'geoms':[names[ga],names[gb]],'parts':[ids[ia] if ia>=0 else None,ids[ib] if ib>=0 else None],
             'roles':[roles[ga],roles[gb]],'signed_distance_m':float(dist[i]),'position_world_m':positions[i].tolist()}
        if both[i]:out.update(goal_pair_declared=bool(declared[i]),near_goal={ids[ia]:bool(near[ia]),ids[ib]:bool(near[ib])})
        return out
    if len(bad):
        i=int(bad[0]);row=fact(i)
        if not allowed[i]:
            reason='robot_environment_or_self_contact'
            if both[i]:reason='goal_contact_pair_not_declared' if not declared[i] else 'part_outside_goal_tolerance' if not close[i] else 'contact_not_allowed_in_this_phase'
            elif table[i]:reason='table_contact_outside_goal' if installed[pid[i]] and not near[pid[i]] else 'support_not_allowed_in_this_phase'
            elif fixtures[i]:reason='foreign_or_inactive_supply_fixture_contact'
            elif robot_part[i]:reason='robot_product_contact_not_allowed'
            row['rejection_reason']=reason
            raise FailureEvent(FailureCode.PLANNED_CONTACT if planned else FailureCode.UNEXPECTED_CONTACT,
                               'Unpermitted measured contact.',violations=[row],phase=v.stage)
        row['normal_force_n']=float(force[i,0])
        raise FailureEvent(FailureCode.INSERTION_CONTACT_LIMIT,'Measured contact-point normal force >8 N.',violations=[row],phase=v.stage)
    pen=np.maximum(0.,-dist)
    hard=float(np.max(pen[~grasp&((pa>=0)|(pb>=0))],initial=0.))
    pad_pen=float(np.max(pen[grasp],initial=0.))
    if not planned:v.max_hard_history=max(v.max_hard_history,hard)
    pad_limit=.0005 if v.prepared.get('profile',{}).get('fingertip_concept') else .003
    limit=v.prepared['completion_profile']['execution_hard_contact_penetration_max_m']
    if hard>limit+1e-12 or pad_pen>pad_limit:
        rows=[fact(i) for i in range(len(indices))]
        if not planned:v.hard_violation={'time_s':float(data.time),'hard_penetration_m':hard,'contacts':rows}
        raise FailureEvent(FailureCode.PLANNED_CONTACT if planned else FailureCode.UNEXPECTED_CONTACT,
                           'Hard or compliant-pad penetration exceeded the configured limit.',
                           violations=[{'hard_penetration_m':hard,'pad_penetration_m':pad_pen,'pad_limit_m':pad_limit,'contacts':rows}],phase=v.stage)
    # Batched matmul retains the same three-component dot product. add.at uses
    # the original interleaved contact order, preserving support-force sums.
    world=(c.frame[indices].reshape(-1,3,3).transpose(0,2,1)@force[:,:3,None])[:,:,0]
    support_a=(pa>=0)&((pb>=0)|(b==v.table_geom)|(fixture[b]>=0))
    support_b=(pb>=0)&((pa>=0)|(a==v.table_geom)|(fixture[a]>=0))
    targets=np.column_stack((pa,pb)).ravel();mask=np.column_stack((support_a,support_b)).ravel()
    amounts=np.column_stack((np.maximum(0.,-world[:,2]),np.maximum(0.,world[:,2]))).ravel()
    support=np.zeros(len(ids));np.add.at(support,targets[mask],amounts[mask])
    loaded=grasp&(force[:,0]>.01)
    v.last_contacts={'robot_contact_parts':sorted(ids[i] for i in np.unique(pid[robot_part])),
                     'support_force_n':{p:float(support[i]) for i,p in enumerate(ids)},'hard_penetration_m':hard}
    # fact owns copied arrays; later physical frames cannot alter this evidence.
    v._contact_evidence_builder=lambda:[fact(i) for i in range(len(indices))]
    return {'supported':bool(support[active]>.01),'left_contact':bool(np.any(loaded&left[counter])),
            'right_contact':bool(np.any(loaded&~left[counter])),
            'penetration_m':max(hard,pad_pen),'hard_penetration_m':hard}
