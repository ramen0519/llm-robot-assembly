"""Loss-aware model views; original observations/evidence stay on disk intact."""
from copy import deepcopy

def display_numbers(value):
    if isinstance(value,float):return float(format(value,'.6g'))
    if isinstance(value,list):return [display_numbers(v) for v in value]
    if isinstance(value,dict):return {k:display_numbers(v) for k,v in value.items()}
    return value

def _same_value(first, other):
    if type(first) is not type(other):
        return False
    if isinstance(first,dict):
        return first.keys()==other.keys() and all(_same_value(value,other[key]) for key,value in first.items())
    if isinstance(first,list):
        return len(first)==len(other) and all(_same_value(a,b) for a,b in zip(first,other))
    return first==other


def _common_fields(records):
    """Lossless recursive factoring, including explicit unknown/failed flags."""
    if not records:
        return {}
    common={}
    for key, first in records[0].items():
        if not all(key in row for row in records):
            continue
        values=[row[key] for row in records]
        if all(_same_value(first,value) for value in values[1:]):
            common[key]=deepcopy(first)
        elif all(isinstance(value,dict) for value in values):
            nested=_common_fields(values)
            if nested:
                common[key]=nested
    return common


def _without_common(value, common):
    result={}
    for key, item in value.items():
        if key not in common:
            result[key]=deepcopy(item)
        elif not _same_value(item,common[key]):
            result[key]=(_without_common(item,common[key])
                         if isinstance(item,dict) and isinstance(common[key],dict) else deepcopy(item))
    return result


def record_list_packet(value):
    """Lossless tables for repeated collision/opening trial records, not summaries."""
    if isinstance(value,dict):return {k:record_list_packet(v) for k,v in value.items()}
    if not isinstance(value,list):return deepcopy(value)
    if len(value)>=2 and all(isinstance(v,dict) and v.keys()==value[0].keys() for v in value):
        common=_common_fields(value);diffs=[_without_common(v,common) for v in value]
        columns=list(diffs[0])
        if all(d.keys()==diffs[0].keys() for d in diffs):
            return {'schema':'factored_record_list/v1','common':record_list_packet(common),
                    'columns':columns,'rows':[[record_list_packet(d[k]) for k in columns] for d in diffs]}
    return [record_list_packet(v) for v in value]


def observation_packet(context):
    """All candidates survive; common annotations are sent only once.

    Exact raw candidates remain in the observation/event artifacts. The model
    gets the same six-significant-digit geometry view as before. Factoring does
    not promote unknown geometry checks to physical successes, filter candidates,
    change live coordinates, or truncate previous conversation messages.
    """
    packet=deepcopy(context);pool=packet.pop('grasp_candidates',[])
    columns=['candidate_id','position_mm','quaternion_xyzw','opening_width_mm','approach_family',
             'surface_contacts_part_mm','surface_normals_part','surface_spacing_mm','checks','motion_hint']
    checks=[display_numbers(c['checks']) for c in pool]
    hints=[display_numbers(c.get('motion_hint',{'status':'not_measured'})) for c in pool]
    common_checks=_common_fields(checks);common_hints=_common_fields(hints)
    has_alternatives=any(c.get('geometry_pose_alternatives') for c in pool)
    alternatives=[display_numbers(c.get('geometry_pose_alternatives',[])) for c in pool]
    common_alternatives=(_common_fields(alternatives) if alternatives and all(isinstance(a,dict) for a in alternatives) else {})
    if has_alternatives:
        columns.append('geometry_pose_alternatives')
    rows=[]
    for c,check,hint,alternative in zip(pool,checks,hints,alternatives):
        g=c['geometry'];check_diff=_without_common(check,common_checks)
        row=[c['candidate_id'],c['position_mm'],c['quaternion_xyzw'],c['opening_width_mm'],c['approach_family'],
             [[x*1000 for x in pt] for pt in g['contact_points_part_m']],g['contact_normals_part'],
             g['contact_spacing_m']*1000,record_list_packet(check_diff) if check_diff else 'common_checks',_without_common(hint,common_hints)]
        if has_alternatives:
            row.append(_without_common(alternative,common_alternatives) if common_alternatives else alternative)
        rows.append(row)
    packet['grasp_candidates']={'schema':'factored_grasp_candidates/v1','columns':columns,
       'rows':display_numbers(rows),'common_checks':record_list_packet(common_checks),'common_motion_hint':common_hints,
       'common_geometry_pose_alternatives':common_alternatives,
       'read_rule':'Recursively merge common_checks with each row checks dictionary; common_checks string means no overrides. Merge common_motion_hint and common_geometry_pose_alternatives with their row dictionaries likewise. For factored_record_list/v1, each row is a dictionary from columns plus recursively merged common fields, in original list order. All fields, statuses and reasons are retained; no omitted field means passed.',
       'frame':'part_local','display_precision':'6 significant digits; ID resolves original full precision geometry',
       'candidate_ids_are_current_revision_only':True,
       'geometry_pose_alternatives_contract':'Optional geometry proposals retain source; choose via full pose action, then actual preflight.'}
    return packet


def guidance_packet(incident):
    """Exact applied values and event linkage, without duplicated trajectory logs."""
    output={k:deepcopy(v) for k,v in incident.items() if k!='events'};output['events']=[]
    for e in incident['events']:
        row={k:deepcopy(e.get(k)) for k in ('id','action_id','observation_revision','part_id','active_role',
            'policy_source','source','executed','installation_verified','proposal','failure','scope','resolved_motion_plan','motion_plan_source')}
        if e.get('resolved_grasp'):
            g=e['resolved_grasp'];row['applied_grasp']={k:deepcopy(g.get(k)) for k in
                ('position_mm','quaternion_xyzw','opening_width_mm','source','parent_candidate_id')}
        if e.get('preflight'):
            row['preflight']={k:deepcopy(e['preflight'].get(k)) for k in
                ('check_id','source','grasp_feasible','installation_feasible_with_grasp','accepted','main_state_unchanged')}
        if e.get('verification'):row['verification']=deepcopy(e['verification'])
        output['events'].append(row)
    output['evidence_view']='Original event IDs and exact applied values; complete events retained in incidents.json and replay bundle.'
    output['evidence_refs_contract']={'kind':'event IDs only, not action/check/detector IDs',
         'allowed_event_ids':[e['id'] for e in incident['events']],
         'required_failure_event_ids':[e['id'] for e in incident['events'] if e.get('failure')],
         'required_main_installation_event_id':incident['installation_evidence']}
    return output

def tool_result_packet(result, event_id):
    """Exclude simulator restart internals from a newly created model message.

    This is not history truncation: all previous messages remain append-only.
    Full tool results, contacts and checkpoint controller state remain in the
    immutable event log and checkpoint files. Failures and success evidence are
    not shortened, rounded, or reclassified by this view.
    """
    from assembly_v12.contracts import digest
    packet=deepcopy(result)
    excluded=[];completion_views=[]
    def visit(value, path):
        if isinstance(value,list):
            return [visit(v,path+'/'+str(i)) for i,v in enumerate(value)]
        if not isinstance(value,dict):return value
        if value.get('schema')=='completion_evidence/v1.2':
            from .completion import valid_evidence
            if valid_evidence(value,value.get('profile_hash'),source=value.get('source'),required_parts=value.get('part_ids')):
                fields=['position_base_axis5mm','relative_orientation_3deg','origin_linear_speed','angular_speed',
                        'final_hard_penetration','gripper_clearance','insertion_depth_error_m',
                        'prealignment_perpendicular_error_m','prealignment_orientation_error_rad']
                rows=[]
                for pid,part in value['parts'].items():
                    rows.append([pid,part['operation']['kind'],[deepcopy(part['worst'].get(k)) for k in fields],
                        {k:c['reason'] for k,c in part['checks'].items() if c.get('status')=='not_applicable'}])
                completion_views.append(path)
                return {'schema':'verified_completion_model_view/v1','status':'passed','source':value['source'],
                    'check_id':value['check_id'],'profile_hash':value['profile_hash'],'model_sha256':value['model_sha256'],
                    'asset_hash':value['asset_hash'],'samples':value['samples'],'duration_s':value['duration_s'],
                    'physics_timestep_s':value.get('physics_timestep_s',.002),'required_samples':value.get('required_samples',501),
                    'complete':True,'all_required_checks_verified_by_python':True,'part_ids':value['part_ids'],
                    'columns':['part_id','operation_kind','worst_values','not_applicable_reasons'],
                    'worst_value_columns':fields,'rows':rows,'full_completion_sha256':digest(value),
                    'evidence_location':{'events_file':'events.jsonl','event_id':event_id,'path':path},
                    'note':'Only a verified passed window is tabulated. Original limits, all checks and measured poses remain in the full event. Failed/unknown windows are never compacted.'}
        if value.get('schema')=='physical_action_checkpoint/v1':
            view={k:deepcopy(v) for k,v in value.items() if k not in ('settled','controller')}
            view['checkpoint_payload_sha256']=digest(value)
            view['omitted_restart_fields']=['settled','controller']
            view['held_pose']=deepcopy(value.get('controller',{}).get('held_pose'))
            excluded.append(path)
            return view
        return {k:visit(v,path+'/'+k) for k,v in value.items()}
    packet=visit(packet,'tool_result')
    if excluded or completion_views:
        packet['model_evidence_view']={
            'schema':'tool_result_view/v1','events_file':'events.jsonl',
            'event_id':event_id,'full_tool_result_sha256':digest(result),
            'checkpoint_views':excluded,'completion_views':completion_views,
            'note':'Append-only model view: restart internals omitted and independently verified passed windows tabulated. Exact worst values, contacts, failures and provenance retained. Full original results remain on disk; failed/unknown windows are unchanged.'}
    return packet


def tool_configuration_packet(profile):
    """Measured tool metadata, never an archived product-specific grasp answer."""
    from assembly_v12.contracts import digest
    from .tooling import validate_fingertips,calibrated_pad_center
    finger=validate_fingertips(profile.get('fingertip_concept'))
    result={'base_gripper':profile['gripper'],'opening_command_unit':'mm',
        'action_opening_range_mm':[1,85],
        'release_strategy':profile.get('release_strategy','full_open'),
        'placement_approach':profile.get('placement_approach','actual_held_tool_and_installed_geometry'),
        'tool_choice_not_a_grasp_or_assembly_success':True}
    if finger is None:
        result['finger_kind']='stock';result['candidate_contact_center_tcp_mm']=[0,0,6]
        return result
    cal=profile.get('grasp_pad_center_calibration',{})
    samples=cal.get('samples',[])
    if not samples:raise ValueError('Experimental tool has no measured contact calibration.')
    # Reject stale/incompatible calibration through the same candidate contract.
    calibrated_pad_center(profile,samples[0]['gap_mm']/1000,[0,0,.006])
    end=finger.get('contact_style')=='end_contact'
    result.update(finger_kind='unfabricated_rigid_fingertip_concept',definition=finger,
        tcp_extension_mm=finger['extension_m']*1000,
        backing_dimensions_mm=[finger['width_m']*1000,3,(finger['extension_m']+(.025 if end else .02375))*1000],
        contact_pad_total_height_mm=4 if end else 10,
        contact_calibration={'source':cal['source'],'sha256':digest(cal),
            'columns':['measured_gap_mm','contact_center_tcp_mm'],
            'rows':display_numbers([[r['gap_mm'],[v*1000 for v in r['pad_center_tcp_m']]] for r in samples]),
            'selection':'nearest measured gap; physical closing still determines contact and actual grasp',
            'coverage_gap_mm':[min(r['gap_mm'] for r in samples),max(r['gap_mm'] for r in samples)]},
        limitations=['Rigid simulated fingers; bending, mounting and real allowable force are unverified.',
            'Housing and original linkages remain present and checked for collision.',
            'Point contact spacing is not a proof that finite pads fit; require physical preflight.'])
    return result


def candidate_packet(candidate):
    """Keep optional geometry pose alternatives in the live policy observation."""
    keys=('candidate_id','position_mm','quaternion_xyzw','opening_width_mm',
          'approach_family','geometry','checks','motion_hint','geometry_pose_alternatives')
    return {key:candidate[key] for key in keys if key in candidate}


def shared_candidate_packet(packet,cache):
    """Reference only exactly identical row data already present in shared history.

    Geometry checks are freshly computed by the executor. This reduces wire
    repetition, not physics work, and never imports an earlier success label.
    Original full-row references are kept so repeated references cannot chain.
    """
    from assembly_v12.contracts import digest
    result=deepcopy(packet);table=result.get('grasp_candidates',{})
    if table.get('schema')!='factored_grasp_candidates/v1':return result
    header={k:v for k,v in table.items() if k!='rows'}
    used=0;rows=[]
    for row in table['rows']:
        key=digest({'part_id':packet['part_id'],'target_id':packet['target_id'],
                    'header':header,'values_except_candidate_id':row[1:]})
        if key in cache:
            rows.append({'candidate_id':row[0],'reuse_full_row_from_shared_history':deepcopy(cache[key]),
                         'same_geometry_and_checks_sha256':key});used+=1
        else:
            rows.append(row)
            cache[key]={'part_id':packet['part_id'],'observation_revision':packet['observation_revision'],
                        'candidate_id':row[0]}
    if used:
        table.update(schema='shared_history_grasp_candidates/v1',rows=rows,
            history_reference_rule='A row dictionary reuses ALL values after candidate_id from the named earlier FULL row and its identical common fields. Only candidate_id is replaced with the current ID. That full row remains in shared history. Fresh checks matched exactly, including failed/unknown flags. Use current IDs for actions; this is not reuse of physical success.',
            referenced_rows=used)
    return result
