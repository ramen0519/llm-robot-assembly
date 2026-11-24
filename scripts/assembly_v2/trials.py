"""Memory-off collection, immutable evaluation and one aggregate paid cap."""
from pathlib import Path
from decimal import Decimal
import os
import time
from assembly_v12.contracts import read,write,sha,digest,location
from llm_runtime import load_llm_config
from .execution import run,CONDITIONS
from .artifacts import inventory
from .memory import Library,source_identity

def plan_trials(manifest,output,snapshot=None):
    source=read(manifest);cfg=source['shared_run_config'];rows=[];lineages=set();splits={}
    if cfg['policy']!='astra':raise ValueError('Research trials require actual Astra; scripted runs are development only.')
    config=load_llm_config(cfg['llm_config']).validate(live=True)
    if config.model!='gpt-6-astra' or config.reasoning_effort!='high':raise ValueError('Approved model/effort required.')
    if not 1<=cfg['seconds']<=1800:raise ValueError('Bounded run time required.')
    for index,case in enumerate(source['cases']):
        if case['split'] not in ('collection','evaluation') or case['lineage_id'] in lineages:raise ValueError('Unique lineage and explicit research split required.')
        lineages.add(case['lineage_id']);p=read(case['prepared']);identity=source_identity(p)
        for k,v in identity.items():
            token=(k,v)
            if token in splits and splits[token]!=case['split']:raise ValueError('Renamed original input cannot leak across collection/evaluation.')
            splits[token]=case['split']
        if case['split']=='evaluation':
            if snapshot is None:raise ValueError('Collect and register first, then freeze memory before evaluation.')
            Library(source['memory_root'],snapshot=snapshot,split='evaluation',exclude_sources=identity)
            conditions=list(CONDITIONS);conditions=conditions[index%4:]+conditions[:index%4]
        else:conditions=[case.get('condition','R0')]
        for condition in conditions:
            if condition not in CONDITIONS or case['split']=='collection' and CONDITIONS[condition][1]:raise ValueError('Collection retrieval must be off.')
            rows.append({**case,'condition':condition,'prepared_sha256':digest(p),'prepared_file_sha256':sha(case['prepared']),
               'source_identity':identity,'snapshot_id':snapshot['id'] if snapshot else None,'runtime':inventory(p)})
    result={'schema':'assembly_trials/v2.0','cases':rows,'memory_snapshot':snapshot,'shared_run_config':cfg,
       'memory_root':source['memory_root'],'llm_config_sha256':sha(cfg['llm_config']),
       'implementation':inventory(),'split_policy':'distinct source run/geometry/mesh-set; reuse demonstration is development, not independent evaluation',
       'denominator':'All started trials; retain preparation failures, physical failures, system errors, media failures separately.'}
    result['id']=digest(result);write(output,result);return result

def checked_plan(path):
    p=read(path)
    if p['id']!=digest({k:v for k,v in p.items() if k!='id'}):raise ValueError('Frozen plan altered.')
    if p['implementation']!=inventory():raise ValueError('Runtime code/XML/meshes/config/dependencies changed after planning.')
    if sha(p['shared_run_config']['llm_config'])!=p['llm_config_sha256']:raise ValueError('Model/budget settings changed.')
    for row in p['cases']:
        if sha(row['prepared'])!=row['prepared_file_sha256'] or inventory(read(row['prepared']))!=row['runtime']:
            raise ValueError('Input, collision asset, native path or preparation changed after planning.')
    return p

def execute_trials(manifest,output,max_runs,*,split,paid_budget=None,continue_main=False):
    if not 1<=max_runs<=40:raise ValueError('Explicit batch limit 1–40 required.')
    output=location(output);output.mkdir(parents=True,exist_ok=False);started=time.monotonic()
    batch={'schema':'assembly_batch/v2.0','status':'not_run','started':0,'completed':0,'physical_success':0,'eligible':0,
       'generated':0,'registered':0,'pending':0,'duplicate':0,'api_calls':0,'tokens':0,'cost_usd':0.,'runs':[]}
    try:
        doc=checked_plan(manifest);cfg=doc['shared_run_config'];llm=load_llm_config(cfg['llm_config'])
        batch['prerequisites']={'API_key_present':bool(os.environ.get(llm.api_key_env)),'aggregate_budget_provided':bool(paid_budget)}
        batch['requested_runs']=max_runs;batch['planned_available']=sum(r['split']==split for r in doc['cases'])
        if not paid_budget:raise ValueError('aggregate_paid_budget_not_configured; no API calls authorized by this batch file')
        cap=read(paid_budget)
        if not cap.get('approval_reference') or any(cap.get(k,0)<=0 for k in ('max_runs','max_model_requests','max_total_tokens','max_wall_seconds')) or Decimal(str(cap.get('max_cost_usd',0)))<=0:
            raise ValueError('Invalid explicitly approved aggregate budget.')
        write(output/'aggregate_budget.json',cap)
        if not os.environ.get(llm.api_key_env):raise ValueError('API_key_not_available; five pilot trials and main collection not run')
        rows=[r for r in doc['cases'] if r['split']==split]
        if not rows:raise ValueError('No planned trials for requested split.')
        limit=min(len(rows),cap['max_runs'],len(rows) if continue_main else max_runs)
        pilot=min(5,limit)
        for index,row in enumerate(rows[:limit]):
            if index>=pilot and continue_main and any(r['audit_status']!='passed' or r['api_calls']==0 or r['status'] in ('deadline','not_run') or
                    r['archive_error'] or r['media_status']!='recorded_states_rendered' or r['storage_errors'] for r in batch['runs']):
                batch['stop_reason']='pilot_records_require_review';break
            if (batch['api_calls']+llm.max_model_requests>cap['max_model_requests'] or
                batch['tokens']+llm.max_total_tokens>cap['max_total_tokens'] or
                Decimal(str(batch['cost_usd']))+Decimal(llm.max_cost_usd)>Decimal(str(cap['max_cost_usd'])) or
                time.monotonic()-started+cfg['seconds']+180>cap['max_wall_seconds']):
                batch['stop_reason']='aggregate_cap_cannot_reserve_next_run';break
            folder=output/f'{index:03d}_{row["condition"]}';batch['started']+=1
            result=run(read(row['prepared']),folder,condition=row['condition'],policy_kind='astra',llm_config=cfg['llm_config'],
                 memory_root=doc['memory_root'],snapshot=doc['memory_snapshot'],encoder_path=cfg.get('encoder'),seconds=cfg['seconds'],
                 split=split,lineage_id=row['lineage_id'],plan=row if split=='evaluation' else None)
            usage=result.get('usage',{});batch['api_calls']+=result['api_calls']
            batch['tokens']+=usage.get('known_input_tokens',0)+usage.get('known_output_tokens',0)+usage.get('unknown_usage_requests',0)*(llm.max_input_tokens+llm.max_output_tokens)
            batch['cost_usd']+=usage.get('known_api_cost_estimate_usd',0)+usage.get('unresolved_cost_reserve_usd',0)
            batch['completed']+=1;batch['physical_success']+=result['status']=='physical_success'
            incidents=read(folder/'incidents.json') if (folder/'incidents.json').exists() else []
            guidance=read(folder/'guidance_candidates.json') if (folder/'guidance_candidates.json').exists() else []
            batch['eligible']+=sum(e['registration']=='eligible' for e in incidents);batch['generated']+=sum(g['status']=='generated' for g in guidance)
            batch['pending']+=sum(g['status']=='pending' for g in guidance)
            for r in result['registrations']:
                if r['status'] in ('registered','pending','duplicate'):batch[r['status']]+=1
            batch['runs'].append({'run':str(folder),'run_id':result['run_id'],'status':result['status'],
                  'audit_status':result['audit']['status'],'api_calls':result['api_calls'],'metrics':result['metrics'],
                  'archive_error':result.get('archive_error'),'media_status':result.get('media',{}).get('status'),
                  'storage_errors':sum(r['status'] not in ('registered','duplicate') for r in result['registrations'])})
            write(output/'batch_result.json',batch)
            if usage.get('unknown_usage_requests',0):batch['stop_reason']='unknown_API_usage';break
        batch['status']='completed' if batch['started']==len(rows[:limit]) else 'partial' if batch['started'] else 'not_run'
    except Exception as exc:batch['stop_reason']=type(exc).__name__+': '+str(exc)[:1200]
    batch['wall_seconds']=time.monotonic()-started;write(output/'batch_result.json',batch)
    return 0 if batch['status']=='completed' and batch['started']>0 else 2
