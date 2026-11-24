"""One product-to-results entry point. No product Python/registry is executed."""
import argparse
from datetime import datetime,timezone
from pathlib import Path
import os
import subprocess
import sys
import time
import uuid
import zipfile
from assembly_v12.contracts import InputError,location,read,write,import_export,digest
from .budget import Budget
from .preparation import prepare,geometric_priority,PROFILE
from .artifacts import report,audit,counters

ROOT=Path(__file__).resolve().parents[2]

def linux_path(path):
    p=location(path)
    if not p.drive or p.drive.upper()!='C:':raise ValueError('This configured WSL bridge accepts C: source/output paths only.')
    return '/mnt/c/'+p.as_posix()[3:]

def bridge(source,folder,config_path,budget,*,unit=None,assembled=False,base=None,profile=None):
    config=read(config_path);windows=os.name=='nt' and config['python'].startswith('/')
    translate=linux_path if windows else lambda x:str(location(x))
    command=(['wsl.exe','-d',config.get('distribution','ASAP-Ubuntu'),'--'] if windows else [])
    if windows:
        # Fail quickly for an unavailable guest instead of hanging preparation.
        budget.process(command+['/bin/echo','assembly_bridge_ready'],timeout=10,capture_output=True)
    elif not Path(config['python']).is_file():raise InputError('bridge_unavailable','Configured native Python is missing.')
    common=[config['python'],'-B',config['repository']+'/tools/run_disassembly.py','--input',translate(source),
        '--assembly',folder.name,'--family',folder.name,'--asap-root',config['asap_root'],
        '--store',translate(folder/'disassembly_memory'),'--memory-off','--max-attempts',str(config['max_attempts']),
        '--max-path',str(config['max_path']),'--max-stability',str(config['max_stability']),'--max-poses',str(config['max_poses']),
        '--seconds',str(min(config['seconds'],int(budget.remaining()))),'--max-calls','0','--max-usd','0']
    if unit:common+=['--unit',unit]
    if assembled:common+=['--assembled-obj']
    if base:common+=['--base',base]
    started=time.monotonic()
    with (folder/'geometry_process.log').open('w',encoding='utf8') as log:
        proc=budget.process(command+common+['--run',translate(folder/'disassembly_geometry'),'--mode','prepare'],timeout=60,stdout=log,stderr=subprocess.STDOUT)
    if proc.returncode:raise InputError('input_invalid','Native CAD normalization failed; inspect geometry_process.log.')
    priority=geometric_priority(folder/'disassembly_geometry/input',profile,base)
    write(folder/'disassembly_priority.json',priority)
    args=command+common+['--run',translate(folder/'disassembly'),'--mode','fixture','--fixture-order',*priority['ranking']]
    if not base:args+=['--base',priority['fixed_base']]
    with (folder/'disassembly_process.log').open('w',encoding='utf8') as log:
        proc=budget.process(args,timeout=config['seconds']+15,stdout=log,stderr=subprocess.STDOUT)
    write(folder/'disassembly_cost.json',{'policy':'native_fixture_physics_not_Astra','api_calls':0,'wall_seconds':time.monotonic()-started,'returncode':proc.returncode})
    export=folder/'disassembly/export_v1.json'
    if proc.returncode or not export.is_file():raise InputError('disassembly_failed','No complete native verified chain export.')
    return export

def pipeline(args):
    out=location(args.output) if args.output else ROOT/'runs/assembly_v2'/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')+'_'+uuid.uuid4().hex[:6])
    out.mkdir(parents=True,exist_ok=False)
    budget=Budget(args.seconds,cancelled=lambda:(out/'cancel.request').exists());prepared=None;started=time.monotonic()
    try:
        source=location(args.input);export=source/'export_v1.json' if source.is_dir() and (source/'export_v1.json').exists() else source
        schema=read(export).get('schema') if export.is_file() and export.suffix.lower()=='.json' else None
        if schema=='prepared_assembly/v2.0':
            prepared=read(export)
            # Recheck original imported native proof and all source/current mesh semantics.
            from .validation import check_prepared
            check_prepared(prepared,budget)
        else:
            native_zip=False
            if export.is_file() and export.suffix.lower()=='.zip':
                with zipfile.ZipFile(export) as zipped:native_zip=any(Path(name).name=='export_v1.json' for name in zipped.namelist())
            if schema!='disassembly_export/v1' and not native_zip:
                export=bridge(source,out,args.bridge_config,budget,unit=args.unit,assembled=args.assembled_obj,base=args.base,profile=args.profile)
            import_export(export,out/'input');prepared=prepare(out/'input',out,args.profile,budget)
        if args.command=='prepare':
            result={'schema':'assembly_result/v2.0','status':'prepared_geometry','policy_source':'not_run','required':prepared['order'],
                    'installed':[],'physics':'not_run','reference_status':'not_run','api_calls':0,'metrics':counters([]),
                    'error':None,'manual_product_adjustments':False}
            write(out/'prepared.json',prepared);write(out/'result.json',result);report(out,result)
        else:
            from .execution import run
            snapshot=read(args.snapshot) if args.snapshot else None;plan=None
            if args.plan:
                from .trials import checked_plan
                doc=checked_plan(args.plan);matches=[r for r in doc['cases'] if r['lineage_id']==args.lineage and r['condition']==args.condition and r['prepared_sha256']==digest(prepared)]
                if len(matches)!=1:raise ValueError('Run must match exactly one frozen trial case.')
                plan=matches[0]
            result=run(prepared,out,condition=args.condition,policy_kind=args.policy,llm_config=args.llm_config,
               memory_root=args.memory_root,snapshot=snapshot,encoder_path=args.encoder,seconds=args.seconds,budget=budget,
               split=args.split,lineage_id=args.lineage,plan=plan,first_grasp=read(args.first_grasp) if args.first_grasp else None,approach=args.approach)
    except Exception as exc:
        import traceback
        result={'schema':'assembly_result/v2.0','status':getattr(exc,'status','not_run' if isinstance(exc,subprocess.TimeoutExpired) else 'preparation_failed'),
                'policy_source':'not_run','required':prepared['order'] if prepared else [],'installed':[],
                'api_calls':0,'metrics':counters([]),'error':type(exc).__name__+': '+str(exc)[:1400]}
        if (out/'result.json').is_file():
            previous=read(out/'result.json');previous.update(status='failed',error=result['error']);result=previous
        elif (out/'llm_events.jsonl').is_file():
            result.update(api_calls=None,usage_status='unknown_inspect_model_journal')
        write(out/'exception.json',{'traceback':traceback.format_exc()});write(out/'result.json',result);report(out,result)
    result['pipeline_seconds']=time.monotonic()-started;write(out/'result.json',result)
    print(str(out/'report.html'),result['status'],flush=True)
    return result,out

def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__);sub=ap.add_subparsers(dest='command',required=True)
    for name in ('prepare','run'):
        p=sub.add_parser(name);p.add_argument('--input',required=True);p.add_argument('--output')
        p.add_argument('--profile',default=str(PROFILE));p.add_argument('--bridge-config',default=str(ROOT/'config/disassembly_bridge.wsl.example.json'))
        p.add_argument('--unit',choices=['m','cm','mm','inch']);p.add_argument('--assembled-obj',action='store_true');p.add_argument('--base')
        p.add_argument('--seconds',type=float,default=300)
        if name=='run':
            p.add_argument('--policy',choices=['astra','scripted'],default='astra');p.add_argument('--condition',choices=['S0','S1','R0','R1'],default='R0')
            p.add_argument('--llm-config',default=str(ROOT/'config/llm.astra-high.toml'));p.add_argument('--memory-root',default=str(ROOT/'runtime/memory/assembly_v2'))
            p.add_argument('--encoder',default=str(ROOT/'runtime/assets/dinov2-small'));p.add_argument('--snapshot');p.add_argument('--plan')
            p.add_argument('--split',choices=['development','collection','evaluation'],default='development');p.add_argument('--lineage')
            p.add_argument('--first-grasp');p.add_argument('--approach',choices=['top','side','oblique'])
    p=sub.add_parser('ui');p.add_argument('--port',type=int,default=8765)
    p=sub.add_parser('status')
    for name in ('audit','report','replay'):
        p=sub.add_parser(name);p.add_argument('--run',required=True)
    p=sub.add_parser('freeze');p.add_argument('--root',default=str(ROOT/'runtime/memory/assembly_v2'));p.add_argument('--output',required=True)
    p=sub.add_parser('plan-trials');p.add_argument('--manifest',required=True);p.add_argument('--snapshot');p.add_argument('--output',required=True)
    for name in ('collect','evaluate'):
        p=sub.add_parser(name);p.add_argument('--manifest',required=True);p.add_argument('--output',required=True)
        p.add_argument('--max-runs',type=int,default=5);p.add_argument('--paid-budget');p.add_argument('--continue-main',action='store_true')
    a=ap.parse_args(argv)
    if a.command in ('prepare','run'):
        if not 1<=a.seconds<=1800:ap.error('Use a declared 1–1800 second run limit.')
        result,_=pipeline(a);return 0 if result['status'] in ('physical_success','no_work','prepared_geometry') else 2
    if a.command=='ui':
        from .ui import serve
        serve(a.port);return 0
    if a.command=='status':
        from .ui import environment_status
        print(__import__('json').dumps(environment_status(),ensure_ascii=False,indent=2));return 0
    if a.command=='audit':return 0 if audit(location(a.run))['status']=='passed' else 2
    if a.command=='report':report(location(a.run),read(location(a.run)/'result.json'));return 0
    if a.command=='replay':
        from .artifacts import verify_bundle
        from .media import render_bounded
        path=location(a.run);path=path/'replay_bundle' if (path/'replay_bundle').exists() else path
        verify_bundle(path);(path/'media').mkdir(exist_ok=True)
        return 0 if render_bounded(path)['status']=='recorded_states_rendered' else 2
    if a.command=='freeze':
        from .memory import Library
        write(a.output,Library(a.root,split='evaluation').snapshot);return 0
    from .trials import plan_trials,execute_trials
    if a.command=='plan-trials':plan_trials(a.manifest,a.output,read(a.snapshot) if a.snapshot else None);return 0
    return execute_trials(a.manifest,a.output,a.max_runs,split='collection' if a.command=='collect' else 'evaluation',paid_budget=a.paid_budget,continue_main=a.continue_main)

if __name__=='__main__':raise SystemExit(main())
