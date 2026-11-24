"""One product-to-results CLI, plus frozen collection/evaluation manifests."""
import argparse
from copy import deepcopy
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import time
import uuid

from .contracts import InputError, check_export, digest, import_export, location, read, sha, write
from .preparation import prepare, native_priority
from .metrics import write_metrics,table
from .artifacts import report

ROOT=Path(__file__).resolve().parents[2]
CONDITIONS={'S0':('single',False),'S1':('single',True),'R0':('roles_shared_history',False),'R1':('roles_shared_history',True)}


def bridge(source, folder, config_path, *, unit=None, assembled=False, base=None, profile=None):
    """Fixed zero-API native planner command. No shell or executable from input CAD."""
    config=read(config_path)
    # A trusted common profile may name a virtualenv's Python symlink. Product
    # artifacts remain link-free; only this configured interpreter is resolved.
    interpreter=Path(config['python']); location(interpreter.parent)
    python=location(interpreter.resolve()); repository=location(config['repository']); asap=location(config['asap_root'])
    if not python.is_file() or not (repository/'tools/run_disassembly.py').is_file():
        raise InputError('bridge_unavailable','Configure the disassembly interpreter and repository once in the common bridge profile.')
    if source.is_file() and source.suffix.lower()=='.json':
        manifest=read(source)
        base=base or manifest.get('fixed_base')
    args=[str(interpreter),'-B',str(repository/'tools/run_disassembly.py'),'--input',str(source),
          '--run',str(folder/'disassembly'),'--assembly',folder.name,'--family',folder.name,'--mode','fixture',
          '--asap-root',str(asap),'--store',str(folder/'disassembly_memory'),'--memory-off',
          '--max-attempts',str(config['max_attempts']),'--max-path',str(config['max_path']),
          '--max-stability',str(config['max_stability']),'--max-poses',str(config['max_poses']),
          '--seconds',str(config['seconds']),'--max-calls','0','--max-usd','0']
    if unit: args+=['--unit',unit]
    if assembled: args+=['--assembled-obj']
    if base: args+=['--base',str(base)]
    started=time.monotonic()
    # First normalize input using the upstream STEP/OBJ reader. This geometry
    # stage does not claim any removal success and does not call a model.
    geometry_args=list(args)
    geometry_args[geometry_args.index('--mode')+1]='prepare'
    geometry_args[geometry_args.index('--run')+1]=str(folder/'disassembly_geometry')
    with (folder/'geometry_process.log').open('w',encoding='utf8') as log:
        completed=subprocess.run(geometry_args,cwd=repository,stdout=log,stderr=subprocess.STDOUT,timeout=60)
    if completed.returncode:
        raise InputError('input_invalid','Upstream geometry normalization failed; see geometry_process.log.')
    priority=native_priority(folder/'disassembly_geometry/input',profile,base)
    write(folder/'disassembly_priority.json',priority)
    if '--base' in args: args[args.index('--base')+1]=priority['fixed_base']
    else: args+=['--base',priority['fixed_base']]
    args+=['--fixture-order',*priority['ranking']]
    with (folder/'disassembly_process.log').open('w',encoding='utf8') as log:
        try:
            completed=subprocess.run(args,cwd=repository,stdout=log,stderr=subprocess.STDOUT,timeout=config['seconds']+30)
        except subprocess.TimeoutExpired:
            write(folder/'disassembly_cost.json',{'api_calls':0,'seconds':time.monotonic()-started,'status':'timeout'})
            raise InputError('disassembly_failed','Native planner deadline; see disassembly_process.log.')
    write(folder/'disassembly_cost.json',{'api_calls':0,'seconds':time.monotonic()-started,'returncode':completed.returncode,
                                        'policy_source':'fixture_native_physics','command':args})
    export=folder/'disassembly/export_v1.json'
    if completed.returncode or not export.is_file():
        raise InputError('disassembly_failed','Disassembly did not produce a complete export; see native result/log.')
    return export


def pipeline(args):
    source=location(args.input)
    out=location(args.output) if args.output else ROOT/'runs/integrated_assembly'/(
        datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')+'_'+uuid.uuid4().hex[:8])
    out.mkdir(parents=True,exist_ok=False)
    started=time.monotonic()
    valid_input=False
    try:
        export=source/'export_v1.json' if source.is_dir() and (source/'export_v1.json').is_file() else source
        if export.suffix.lower() not in ('.zip',) and not (export.is_file() and export.suffix.lower()=='.json' and read(export).get('schema')=='disassembly_export/v1'):
            if not args.bridge_config:
                raise InputError('bridge_configuration_required','Product CAD requires the shared disassembly bridge profile; no per-product code is needed.')
            export=bridge(source,out,args.bridge_config,unit=args.unit,assembled=args.assembled_obj,base=args.base,profile=args.profile)
        contract,_=import_export(export,out/'input')
        valid_input=True
        prepared=prepare(out/'input',out,args.profile)
        if args.command=='prepare':
            result={'status':'prepared_geometry_and_layout','input_valid':True,'task_supported':True,
                    'preparation_ready':False,'physics':'not_run','api_calls':0,'order':prepared['order']}
        else:
            from .execution import run
            mode,memory=CONDITIONS[args.condition]
            result=run(prepared,out,mode=mode,memory=memory,policy_kind=args.policy,llm_config=args.llm_config,
                       memory_root=args.memory_root,frozen=read(args.snapshot) if args.snapshot else None,
                       encoder_path=args.encoder,seconds=args.seconds,split=args.split,family=args.family,
                       scenario=args.scenario,first_grasp=read(args.first_grasp) if args.first_grasp else None,
                       scripted_guidance=args.scripted_guidance,prompts_path=ROOT/'config/policy_prompts.v12.json')
            result['condition']=args.condition
        result['pipeline_seconds']=time.monotonic()-started
        result['upstream']=read(out/'disassembly_cost.json') if (out/'disassembly_cost.json').is_file() else {'status':'verified_export_reused','new_api_calls':0}
        write(out/'result.json',result)
    except Exception as exc:
        if (out/'result.json').is_file():
            result=read(out/'result.json')
            result.update(pipeline_error=str(exc),pipeline_seconds=time.monotonic()-started)
        else:
            geometry_result=out/'disassembly_geometry/result.json'
            geometry_valid=geometry_result.is_file() and read(geometry_result).get('status')=='PREPARED'
            failure_status=getattr(exc,'status','input_or_preparation_error')
            result={'status':getattr(exc,'status','input_or_preparation_error'),'input_valid':valid_input,
                    'source_geometry_valid':geometry_valid,'selected_chain_valid':valid_input,
                    'task_supported':False if failure_status.startswith('unsupported') else None,'preparation_ready':False,
                    'api_calls':None if (out/'llm_events.jsonl').exists() else 0,
                    'error':str(exc),'pipeline_seconds':time.monotonic()-started}
            if geometry_valid:result['input_valid']=True
        import traceback
        write(out/'exception.json',{'message':str(exc),'traceback':traceback.format_exc()})
        write(out/'result.json',result)
    write_metrics(result,out)
    report(result,out)
    print(json.dumps({'output':str(out),'report':str(out/'report.html'),'status':result['status'],'installed':result.get('installed',[]),'error':result.get('error')},ensure_ascii=False),flush=True)
    return result,out


def plan_trials(input_manifest, output, snapshot=None):
    manifest=read(input_manifest)
    seen=set(); rows=[]; upstream_splits={}
    for index,case in enumerate(manifest['cases']):
        if case['split'] not in ('development','collection','evaluation') or case['lineage_id'] in seen:
            raise ValueError('Duplicate lineage or unknown split in trial manifest.')
        seen.add(case['lineage_id'])
        source_hash=read(case['prepared'])['source_run_hash']
        if source_hash in upstream_splits and upstream_splits[source_hash]!=case['split']:
            raise ValueError('The same source disassembly run cannot appear across collection/evaluation splits.')
        upstream_splits[source_hash]=case['split']
        order=list(CONDITIONS); order=order[index%4:]+order[:index%4]
        if case['split']=='collection': order=[c for c in order if c in ('S0','R0')]
        for condition in order:
            rows.append({**case,'condition':condition,'prepared_sha256':sha(case['prepared']),
                         'source_hash':source_hash,
                         'memory_snapshot_id':snapshot['id'] if snapshot else None})
    if any(r['split']=='evaluation' for r in rows) and snapshot is None:
        raise ValueError('Evaluation requires a fixed collection-memory snapshot.')
    if snapshot:
        for row in snapshot['cases']:
            archive=read(Path(manifest['memory_root'])/row['case_id']/'archive.json')
            e=archive['experience']
            # Family separation is required only for a cross-product claim;
            # the main comparison may use held-out conditions of one family.
            if e['split']!='collection' or e.get('lineage_id',e['run_id']) in {r['lineage_id'] for r in rows if r['split']=='evaluation'}:
                raise ValueError('Evaluation memory includes the evaluation lineage or a noncollection case.')
    doc={'schema':'assembly_trials/v1.2','frozen':True,'cases':rows,'memory_snapshot':snapshot,
         'shared_run_config':manifest['shared_run_config'],'memory_root':manifest['memory_root'],
         'denominator':'all started trials; preparation failures separately retained; no silent infrastructure exclusions'}
    cfg=doc['shared_run_config']
    if cfg['policy'] not in ('scripted','astra') or not 5<=cfg['seconds']<=1800:
        raise ValueError('Explicit bounded shared policy configuration required.')
    if cfg['policy']=='astra':
        from llm_runtime import load_llm_config
        load_llm_config(cfg['llm_config']).validate(live=True)
        doc['llm_config_sha256']=sha(cfg['llm_config'])
    doc['prompt_sha256']=sha(ROOT/'config/policy_prompts.v12.json')
    from .artifacts import implementation_versions
    doc['implementation']=implementation_versions()
    if cfg.get('encoder'):
        from .memory import Encoder
        encoder=Encoder(cfg['encoder']);encoder.load()
        doc['encoder_version']=encoder.version
    doc['id']=digest(doc); write(output,doc); return doc


def execute_trials(manifest_path, output, max_runs, *, split=None, paid_budget=None):
    doc=read(manifest_path)
    if doc['id']!=digest({k:v for k,v in doc.items() if k!='id'}): raise ValueError('Trial manifest modified after freeze.')
    cfg=doc['shared_run_config']
    if sha(ROOT/'config/policy_prompts.v12.json')!=doc['prompt_sha256']:
        raise ValueError('Prompt changed after trial freeze.')
    from .artifacts import implementation_versions
    if doc.get('implementation')!=implementation_versions():
        raise ValueError('Implementation/dependencies changed after trial freeze; freeze a new manifest before starting any run.')
    if cfg.get('encoder'):
        from .memory import Encoder
        encoder=Encoder(cfg['encoder']);encoder.load()
        if doc.get('encoder_version')!=encoder.version:
            raise ValueError('Visual encoder changed after trial freeze.')
    # An aggregate explicit cap is separate from the design's proposed 40 runs.
    if cfg['policy']=='astra':
        if paid_budget is None:
            raise InputError('paid_batch_not_authorized','Astra batches require an explicitly approved aggregate budget file; no call sent.')
        from decimal import Decimal
        from llm_runtime import load_llm_config
        cap=read(paid_budget)
        if (not cap.get('approval_reference') or not 1<=cap['max_runs']<=40 or
                any(cap[k]<=0 for k in ('max_model_requests','max_total_tokens','max_wall_seconds')) or
                not Decimal(str(cap['max_cost_usd'])).is_finite() or Decimal(str(cap['max_cost_usd']))<=0):
            raise ValueError('Invalid aggregate paid budget.')
        if sha(cfg['llm_config'])!=doc['llm_config_sha256']: raise ValueError('LLM config changed after trial freeze.')
        llm=load_llm_config(cfg['llm_config']).validate(live=True)
    from .execution import run
    results=[]; metrics=[]; output=location(output); output.mkdir(parents=True,exist_ok=False)
    spent={'calls':0,'tokens':0,'cost':0.};start=time.monotonic();stop=None
    selected=[r for r in doc['cases'] if split is None or r['split']==split][:max_runs]
    if not selected: raise ValueError('No cases for the requested split.')
    if paid_budget: write(output/'aggregate_budget.json',read(paid_budget))
    for index,row in enumerate(selected):
        if cfg['policy']=='astra':
            if (index>=cap['max_runs'] or spent['calls']+llm.max_model_requests>cap['max_model_requests'] or
                    spent['tokens']+llm.max_total_tokens>cap['max_total_tokens'] or
                    Decimal(str(spent['cost']))+Decimal(llm.max_cost_usd)>Decimal(str(cap['max_cost_usd'])) or
                    time.monotonic()-start+cfg['seconds']+600>cap['max_wall_seconds']):
                stop='aggregate_budget_no_complete_run_reservation';break
        if sha(row['prepared'])!=row['prepared_sha256']: raise ValueError('Prepared scene changed.')
        mode,memory=CONDITIONS[row['condition']]
        folder=output/f'{index:03d}_{row["condition"]}'
        r=run(read(row['prepared']),folder,mode=mode,memory=memory,policy_kind=cfg['policy'],llm_config=cfg.get('llm_config'),
              memory_root=doc['memory_root'],frozen=doc['memory_snapshot'],seconds=cfg['seconds'],split=row['split'],
              family=row['family'],scenario=row.get('scenario','nominal'),
              first_grasp=row.get('first_grasp'),encoder_path=cfg.get('encoder'),
              lineage_id=row['lineage_id'],
              prompts_path=ROOT/'config/policy_prompts.v12.json')
        r.update(condition=row['condition'],lineage_id=row['lineage_id'],trial_manifest_id=doc['id'])
        write(folder/'result.json',r); results.append(r);metrics.append(write_metrics(r,folder));report(r,folder)
        u=r.get('usage',{});spent['calls']+=r.get('api_calls',0)
        if cfg['policy']=='astra':
            spent['tokens']+=u.get('known_input_tokens',0)+u.get('known_output_tokens',0)+u.get('unknown_usage_requests',0)*(llm.max_input_tokens+llm.max_output_tokens)
        spent['cost']+=u.get('known_api_cost_estimate_usd',0)+u.get('unresolved_cost_reserve_usd',0)
        write(output/'progress.json',{'started':len(results),'spent':spent,'last_status':r['status']})
        if u.get('unknown_usage_requests',0):stop='unknown_usage_requires_reconciliation';break
    summary={'started':len(results),'successes':sum(r['status']=='success' for r in results),'all_results':results,
             'success_rate':sum(r['status']=='success' for r in results)/len(results) if results else None,
             'stop':stop,'not_started':len(selected)-len(results),'spent':spent,'policy_source':cfg['policy']}
    write(output/'summary.json',summary)
    table(output/'metrics.csv',metrics)
    return summary


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    for command in ('prepare','run'):
        p=sub.add_parser(command)
        p.add_argument('input'); p.add_argument('--output')
        p.add_argument('--profile',default=str(ROOT/'config/assembly_workcell.v12.json'))
        p.add_argument('--bridge-config'); p.add_argument('--unit',choices=['mm','cm','m','inch'])
        p.add_argument('--assembled-obj',action='store_true'); p.add_argument('--base')
        if command=='run':
            p.add_argument('--condition',choices=CONDITIONS,default='S0')
            p.add_argument('--policy',choices=['scripted','astra'],default='scripted')
            p.add_argument('--llm-config'); p.add_argument('--seconds',type=float,default=300)
            p.add_argument('--memory-root',default=str(ROOT/'runtime/memory/assembly_v12'))
            p.add_argument('--snapshot'); p.add_argument('--encoder')
            p.add_argument('--split',choices=['development','collection','evaluation'],default='development')
            p.add_argument('--family',default='unspecified')
            p.add_argument('--scenario',choices=['nominal','alignment_offset'],default='nominal')
            p.add_argument('--first-grasp'); p.add_argument('--scripted-guidance',action='store_true')
    p=sub.add_parser('freeze-memory'); p.add_argument('root'); p.add_argument('output')
    p=sub.add_parser('memory-status'); p.add_argument('--memory-root',default=str(ROOT/'runtime/memory/assembly_v12'))
    p=sub.add_parser('plan-trials'); p.add_argument('manifest'); p.add_argument('output'); p.add_argument('--snapshot')
    for command in ('collect','evaluate'):
        p=sub.add_parser(command); p.add_argument('manifest'); p.add_argument('output'); p.add_argument('--max-runs',type=int,default=1)
        p.add_argument('--paid-budget',help='An explicitly approved aggregate live budget, never inferred from this design.')
    p=sub.add_parser('replay'); p.add_argument('run')
    p=sub.add_parser('report'); p.add_argument('run')
    args=parser.parse_args(argv)
    if args.command in ('prepare','run'):
        if args.command=='run' and (not 5<=args.seconds<=1800 or args.policy=='astra' and not args.llm_config):
            parser.error('Use 5–1800 seconds and an explicit approved LLM configuration for Astra.')
        result,_=pipeline(args)
        return 0 if result['status'] in ('success','prepared_geometry_and_layout') else 1
    if args.command=='memory-status':
        from .memory import Library
        library=Library(args.memory_root,allowed_splits=('development','collection','evaluation'))
        counts=dict.fromkeys(('development','collection','evaluation'),0)
        for row in library.entries:
            counts[library._archive(row)['experience']['split']]+=1
        print(json.dumps({'memory_root':str(location(args.memory_root)), 'registered_cases':len(library.entries),
                          'guidance_cards':len(library.entries),'by_split':counts,
                          'snapshot_id':library.snapshot['id'],'api_calls':0},ensure_ascii=False))
    elif args.command=='freeze-memory':
        from .memory import Library
        library=Library(args.root); write(args.output,library.snapshot)
    elif args.command=='plan-trials':
        plan_trials(args.manifest,args.output,read(args.snapshot) if args.snapshot else None)
    elif args.command in ('collect','evaluate'):
        if not 1<=args.max_runs<=40: parser.error('Explicit bounded batch size 1–40 required.')
        execute_trials(args.manifest,args.output,args.max_runs,
                       split='collection' if args.command=='collect' else 'evaluation',paid_budget=args.paid_budget)
    elif args.command=='report':
        folder=location(args.run);result=read(folder/'result.json')
        write_metrics(result,folder);report(result,folder)
        write(folder/'report_manifest.json',{'source':'existing_result_and_events; no new simulation or API',
              'result_sha256':sha(folder/'result.json'),
              'events_sha256':sha(folder/'events.jsonl') if (folder/'events.jsonl').exists() else None,
              'report_sha256':sha(folder/'report.html')})
        print(json.dumps({'status':'report_generated','report':str(folder/'report.html'),'api_calls':0}))
    elif args.command=='replay':
        from .media import Recorder
        import mujoco
        import numpy as np
        folder=Path(args.run); index=read(folder/'replay_frames.json')
        if sha(folder/'model.mjb')!=index['model_sha256'] or mujoco.__version__!=index['mujoco_version']:
            raise ValueError('Replay model/version mismatch.')
        if index.get('states_sha256') and sha(folder/'replay_states.npz')!=index['states_sha256']:
            raise ValueError('Replay state archive changed.')
        replay=Recorder.__new__(Recorder)
        replay.folder=folder; replay.model=mujoco.MjModel.from_binary_path(str(folder/'model.mjb'))
        replay.states=np.load(folder/'replay_states.npz',allow_pickle=False)['states']
        replay.prepared=read(folder/'prepared.json'); replay.fps=index['fps']
        print(json.dumps(replay.render_video(output_name='replay')))
    return 0
