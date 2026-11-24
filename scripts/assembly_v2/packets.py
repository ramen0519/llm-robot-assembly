"""Loss-aware model views; original observations/evidence stay on disk intact."""
from copy import deepcopy

def display_numbers(value):
    if isinstance(value,float):return float(format(value,'.6g'))
    if isinstance(value,list):return [display_numbers(v) for v in value]
    if isinstance(value,dict):return {k:display_numbers(v) for k,v in value.items()}
    return value

def observation_packet(context):
    """Keep every presented candidate and its checks, with shared column labels.

    Display rounding is explicit. Candidate IDs resolve against unrounded current
    geometry. No past message is sliced or summarized when the context fills.
    """
    packet=deepcopy(context);pool=packet.pop('grasp_candidates',[])
    columns=['candidate_id','position_mm','quaternion_xyzw','opening_width_mm','approach_family',
             'surface_contacts_part_mm','surface_normals_part','surface_spacing_mm','checks']
    checks=pool[0]['checks'] if pool and all(c['checks']==pool[0]['checks'] for c in pool) else None
    rows=[]
    for c in pool:
        g=c['geometry']
        rows.append([c['candidate_id'],c['position_mm'],c['quaternion_xyzw'],c['opening_width_mm'],c['approach_family'],
            [[x*1000 for x in pt] for pt in g['contact_points_part_m']],g['contact_normals_part'],
            g['contact_spacing_m']*1000,'common_checks' if checks is not None else c['checks']])
    packet['grasp_candidates']={'columns':columns,'rows':display_numbers(rows),'common_checks':checks,
       'frame':'part_local','display_precision':'6 significant digits; ID resolves original full precision geometry',
       'candidate_ids_are_current_revision_only':True}
    return packet

def guidance_packet(incident):
    """Exact applied values and event linkage, without duplicated trajectory logs."""
    output={k:deepcopy(v) for k,v in incident.items() if k!='events'};output['events']=[]
    for e in incident['events']:
        row={k:deepcopy(e.get(k)) for k in ('id','action_id','observation_revision','part_id','active_role',
            'policy_source','source','executed','installation_verified','proposal','failure','scope')}
        if e.get('resolved_grasp'):
            g=e['resolved_grasp'];row['applied_grasp']={k:deepcopy(g.get(k)) for k in
                ('position_mm','quaternion_xyzw','opening_width_mm','source','parent_candidate_id')}
        if e.get('preflight'):
            row['preflight']={k:deepcopy(e['preflight'].get(k)) for k in
                ('check_id','source','grasp_feasible','installation_feasible_with_grasp','accepted','main_state_unchanged')}
        if e.get('verification'):row['verification']=deepcopy(e['verification'])
        output['events'].append(row)
    output['evidence_view']='Original event IDs and exact applied values; complete events retained in incidents.json and replay bundle.'
    return output
