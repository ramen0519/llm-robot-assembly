"""Runtime inventory, immutable replay bundles and cross-linked evidence auditing."""
from pathlib import Path
from html import escape
import csv
import importlib.metadata
import importlib.util
import json
import shutil
import numpy as np
from assembly_v12.contracts import read,write,digest,sha,relative
from .completion import valid_evidence

ROOT=Path(__file__).resolve().parents[2]

def inventory(prepared=None):
    files={}
    for name in ('pyproject.toml','uv.lock'):
        if (ROOT/name).is_file():files[name]=sha(ROOT/name)
    for directory in ('scripts','config','models','scenes','third_party'):
        for path in sorted((ROOT/directory).rglob('*')):
            if path.is_file() and '__pycache__' not in path.parts and path.suffix.lower() in ('.py','.xml','.toml','.json','.obj','.stl','.ply','.yaml','.txt','.npz'):
                if path.name.startswith('local.') or path.name.startswith('.'):continue
                files[path.relative_to(ROOT).as_posix()]=sha(path)
    packages={}
    for name in ('mujoco','mink','daqp','qpsolvers','numpy','scipy','trimesh','coacd','manifold3d','langgraph','openai',
                 'torch','transformers','pillow','safetensors','networkx','rtree','imageio-ffmpeg','flask'):
        try:packages[name]=importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:packages[name]='not_installed'
    result={'schema':'runtime_inventory/v2.0','files':files,'packages':packages,'seed':0}
    encoder=ROOT/'runtime/assets/dinov2-small'
    result['encoder_assets']={p.name:sha(p) for p in sorted(encoder.glob('*')) if p.is_file()}
    native={}
    for name in ('mujoco','mink','coacd','daqp'):
        spec=importlib.util.find_spec(name)
        if spec and spec.origin:
            path=Path(spec.origin);paths=path.parent.glob('*.pyd') if path.suffix=='.py' else [path]
            native.update({str(p):sha(p) for p in paths})
            if path.suffix=='.py':native.update({str(p):sha(p) for p in path.parent.glob('*.dll')})
    result['native_runtime']=native
    if prepared:
        result['prepared_sha256']=digest(prepared)
        result['product_files']={str(p):sha(p) for row in prepared['parts'].values() for p in
               [Path(row['mesh']),*(Path(row['asset']['folder'])/a['path'] for a in [row['asset']['visual'],*row['asset']['hulls']])]}
        result['input_export_sha256']=sha(prepared['input_export']) if prepared.get('input_export') else None
        source=prepared.get('input_hint') or prepared.get('input_export')
        if source:
            input_root=Path(source).parent
            key='order_hint_bundle' if prepared.get('input_hint') else 'native_input_bundle'
            result[key]={p.relative_to(input_root).as_posix():sha(p) for p in sorted(input_root.rglob('*')) if p.is_file()}
            if prepared.get('input_hint'):result['order_hint_sha256']=sha(source)
    result['id']=digest(result);return result

def events(folder):
    p=Path(folder)/'events.jsonl'
    return [json.loads(line) for line in p.read_text(encoding='utf8').splitlines()] if p.is_file() else []

def counters(rows):
    attempts={};rejected=0;alignment=replan=0;generated=0;submitted=0;lift_proposals=lift_actions=0
    for e in rows:
        action=e['proposal']['skill'];pid=e['part_id']
        lift_proposals+=action=='lift';lift_actions+=int(action=='lift' and bool(e.get('executed')))
        generated+=e.get('geometry_candidates_generated',0)
        if e.get('candidate_started'):
            attempts[pid]=attempts.get(pid,0)+1;submitted+=int(e['policy_source']=='astra')
            rejected+=int(not e.get('preflight',{}).get('accepted',False))
        if e.get('executed'):alignment+=action=='recover_alignment';replan+=action=='replan_insert'
    return {'python_candidates_generated':generated,'astra_submitted_candidates':submitted,
            'candidate_attempts':sum(attempts.values()),'rejected_candidates':rejected,
            'retries':sum(max(0,n-1) for n in attempts.values()),'alignment_actions':alignment,'replan_actions':replan,
            'lift_recovery_proposals':lift_proposals,'lift_recovery_actions':lift_actions,
            'physical_recovery_actions':alignment+replan+lift_actions}


def metric_issues(result, rows):
    expected = counters(rows)
    recorded = result.get('metrics')
    # Old immutable runs predate the optional motion-plan/recovery counters.
    # Only omit new fields when absent from an old record; still verify every
    # historical counter and reject missing fields in current run records.
    if 'motion_plan_mode' not in result and isinstance(recorded, dict):
        for key in ('lift_recovery_proposals', 'lift_recovery_actions', 'physical_recovery_actions'):
            if key not in recorded:
                expected.pop(key)
    return [] if recorded == expected else ['event_result_counter_mismatch']

def audit(folder):
    folder=Path(folder);result=read(folder/'result.json');issues=[];rows=events(folder)
    success=result.get('status')=='physical_success'
    final=read(folder/'final_verification.json') if (folder/'final_verification.json').is_file() else None
    p=read(folder/'prepared.json') if (folder/'prepared.json').exists() else None
    if success and (not p or not valid_evidence(final,p['completion_profile_hash'],required_parts=p['order'])):
        issues.append('missing_or_invalid_common_final_window')
    issues.extend(metric_issues(result,rows))
    if result.get('main_action_policy_source',result.get('policy_source'))=='astra' and result.get('policy_decision_evaluated') and not (folder/'llm_events.jsonl').is_file():issues.append('missing_actual_model_journal')
    model_hash=sha(folder/'model.mjb') if (folder/'model.mjb').exists() else None
    for e in rows:
        if e.get('policy_source')=='development_failure_probe' and (e.get('executed') or result.get('split')!='development' or not result.get('development_failure_probe')):
            issues.append('invalid_development_probe_provenance:'+e['id'])
        if e.get('tool_result',{}).get('executed')!=e.get('executed'):issues.append('event_tool_executed_mismatch:'+e['id'])
        if e.get('tool_result',{}).get('source')!=e.get('source'):issues.append('event_tool_source_mismatch:'+e['id'])
        if e.get('installation_verified') and (not e['executed'] or e['source']!='live_simulation' or
                not valid_evidence(e.get('verification'),p['completion_profile_hash'])):issues.append('invalid_installation:'+e['id'])
        f=e.get('failure') or {};snap=f.get('failure_snapshot',{})
        if snap.get('status')!='exact_detector_snapshot':continue
        if snap.get('action_id')!=e['action_id'] or snap.get('model_sha256')!=model_hash or snap.get('source')!=f.get('source'):
            issues.append('failure_event_identity_mismatch:'+e['id'])
        state_path=snap.get('state');sidecar=folder/'failures'/(Path(state_path).stem+'.json') if state_path else None
        if not sidecar or not sidecar.is_file() or read(sidecar)!=snap:issues.append('failure_sidecar_mismatch:'+e['id'])
        for field,hash_key in [('state','state_sha256'),('png','png_sha256')]+([('oblique_png','oblique_png_sha256')] if snap.get('oblique_png') else []):
            try:
                valid=bool(snap.get(field)) and relative(folder,snap[field]).is_file() and sha(relative(folder,snap[field]))==snap.get(hash_key)
            except (ValueError,OSError):valid=False
            if not valid:issues.append('missing_or_changed_failure_'+field+':'+e['id'])
        if e.get('preflight') and snap.get('parent_scene_source')=='mujoco_preflight_copy' and snap.get('parent_check_id')!=e['preflight']['check_id']:issues.append('copy_check_link_mismatch:'+e['id'])
    for e in rows:
        if e.get('installation_verified') and e['verification'].get('model_sha256')!=model_hash:issues.append('completion_model_mismatch')
    if success and (not final or final.get('model_sha256')!=model_hash):issues.append('final_model_mismatch')
    for evidence in [final,*(e.get('verification') for e in rows if e.get('installation_verified'))]:
        if evidence and p:
            for pid,row in evidence.get('parts',{}).items():
                goal=row.get('goal_pose')
                if pid not in p['parts'] or np.asarray(goal).shape!=(4,4) or not np.allclose(goal,p['parts'][pid]['target'],atol=1e-10):
                    issues.append('completion_goal_changed:'+pid)
    if result.get('resume_context'):
        try:
            from .resume import validate_boundary
            source=folder/'continuation_source'
            checkpoint=read(source/'checkpoint.json');parent=read(source/'parent_result.json')
            inherited=validate_boundary(checkpoint,parent,p,read(source/'installation_proofs.json'))
            restored=read(folder/'restored_boundary_verification.json')
            if not valid_evidence(restored,p['completion_profile_hash'],required_parts=inherited):issues.append('unverified_restored_boundary')
            if model_hash!=checkpoint['model_sha256'] or sha(source/'integration_state.npy')!=checkpoint['state_sha256']:issues.append('restored_boundary_identity_mismatch')
            actual={e['part_id'] for e in rows if e.get('installation_verified')}
            if set(result['installed'])!=inherited|actual:issues.append('unproven_continuation_installation')
            if result.get('main_action_policy_source')!='scripted' or result.get('split')!='development':issues.append('continuation_is_development_only')
        except (ValueError,KeyError,OSError,TypeError) as exc:issues.append('invalid_continuation_proof:'+str(exc)[:200])
    value={'schema':'assembly_audit/v2.0','status':'failed' if issues else 'passed','issues':issues,
           'physical_status':result['status'],'scope':'record consistency; does not convert a failure/not_run to physical success'}
    write(folder/'audit.json',value);return value

def seal_bundle(folder,prepared):
    folder=Path(folder);bundle=folder/'replay_bundle';bundle.mkdir(exist_ok=False)
    names=['model.mjb','replay_states.npz','replay_frames.json','prepared.json','resolved_config.json','code_versions.json',
           'events.jsonl','observations.jsonl','candidates.jsonl','final_verification.json','reference_verification.json',
           'result.json','audit.json','llm_events.jsonl','conversation.jsonl',
           'reference_model.mjb','reference_boundary_states.npz','reference_diagnostics.json','failure_summary.json','phases.jsonl','planned_motions.jsonl','restored_boundary_verification.json','continuation_contract.json','workcell.json','continuations.jsonl','incidents.json','guidance_candidates.json','memory_use.json','resume_context.json']
    for name in names:
        if (folder/name).is_file():shutil.copy2(folder/name,bundle/name)
    for directory in ('failures','input','assets','reference_media','checkpoints','continuation_source'):
        if (folder/directory).exists():shutil.copytree(folder/directory,bundle/directory)
    # Prepared execution may reference a shared prepared run. Copy its original
    # input and collision assets too, while retaining the immutable source paths.
    if prepared:
        source=prepared.get('input_hint') or prepared.get('input_export')
        if source and not (bundle/'input').exists():shutil.copytree(Path(source).parent,bundle/'input')
        if not (bundle/'assets').exists():
            for pid,part in prepared['parts'].items():
                dest=bundle/'assets'/pid;dest.mkdir(parents=True)
                shutil.copy2(part['mesh'],dest/'source_metric.obj');shutil.copytree(part['asset']['folder'],dest/'collision')
    version=read(folder/'code_versions.json')
    for name,checksum in version['files'].items():
        source=ROOT/name
        if sha(source)!=checksum:raise ValueError('Runtime source changed before archive sealing.')
        dest=bundle/'implementation'/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,dest)
    files={f.relative_to(bundle).as_posix():sha(f) for f in sorted(bundle.rglob('*')) if f.is_file()}
    required={'model.mjb','replay_states.npz','replay_frames.json','prepared.json','code_versions.json'}
    manifest={'schema':'assembly_replay_bundle/v2.0','files':files,'replayable':required<=set(files),
              'source_run':folder.name,'replay_mode':'saved states; no new physics and no model calls'}
    manifest['id']=digest(manifest);write(bundle/'bundle.json',manifest);return manifest

def verify_bundle(folder):
    folder=Path(folder);b=read(folder/'bundle.json')
    if b['id']!=digest({k:v for k,v in b.items() if k!='id'}):raise ValueError('Bundle identity mismatch.')
    for name,checksum in b['files'].items():
        if sha(relative(folder,name))!=checksum:raise ValueError('Immutable bundle changed: '+name)
    return b

def failure_summary(rows):
    failures=[]
    for e in rows:
        f=e.get('failure')
        if not f:continue
        contacts=[]
        for v in f.get('violations',[]):
            if isinstance(v,dict) and 'geoms' in v:contacts.append(v)
        failures.append({'event_id':e['id'],'part_id':e['part_id'],'code':f.get('failure_code'),
            'phase':f.get('phase'),'source':f.get('source'),'contacts':contacts,
            'grasp_feasible':e.get('preflight',{}).get('grasp_feasible','not_run'),
            'installation_feasible_with_grasp':e.get('preflight',{}).get('installation_feasible_with_grasp','not_run'),
            'main_executed':e.get('executed',False),'snapshot':f.get('failure_snapshot',{}),
            'interpretation':'sampled_pose_or_path_failure_not_proof_all_grasps_impossible'})
    return {'schema':'assembly_failure_summary/v1','failures':failures,
        'scope':'detector evidence only; no invented successful experience or physical feasibility claim'}


def report(folder,result):
    folder=Path(folder);h=lambda v:escape(str(v))
    values={'상태':result['status'],'판단 출처':result.get('policy_source','not_run'),'설치 완료':result.get('installed',[]),
      '필수 부품':result.get('required',[]),'API 생성':result.get('api_calls',0),'비용 USD':result.get('usage',{}).get('known_api_cost_estimate_usd',0),
      '재시도':result.get('metrics',{}).get('retries',0),'미실행/오류':result.get('error'),'다음 처리':result.get('next_step'),
      '경험 처리':result.get('experience_summary'),'동작 이어가기':result.get('continuation_policy'),
      '물리 계산 간격(초)':result.get('physics_timestep_s'),'검증 조건':result.get('validation_regime')}
    doc=['<!doctype html><html lang="ko"><meta charset="utf-8"><title>조립 v2 결과</title><style>body{font:16px system-ui;max-width:1100px;margin:30px auto;padding:20px}td{border:1px solid #ccc;padding:10px}table{border-collapse:collapse}img{width:320px}video{max-width:800px;width:100%}pre{white-space:pre-wrap}</style>',
      '<h1>조립 실행 결과</h1><p>물리 결과 · 기록 검사 · 영상 생성은 별도 판정입니다. scripted는 Astra 실행이 아니며, 복사 검사는 실제 설치가 아닙니다.</p>',
      '<table>'+''.join('<tr><td>'+h(k)+'</td><td>'+h(v)+'</td></tr>' for k,v in values.items())+'</table>']
    if result.get('resume_context'):
        c=result['resume_context']
        doc.append('<p><strong>검증된 상태를 이어가는 개발 실행:</strong> 이전 실행 '+h(c['parent_run_id'])+'에서 설치한 '+h(c['installed_at_start'])+'를 복원하고 다시 연속 관찰한 뒤 나머지를 실행합니다. 빈 바닥판부터 새로 실행한 Astra 회차가 아닙니다.</p>')
    if result.get('validation_regime','').startswith('experimental_'):
        doc.append('<p><strong>개발 실험 조건:</strong> 계산 간격 '+h(result['physics_timestep_s'])+'초. 위치·각도·접촉 한도와 관찰 1초는 유지하며, 관찰 횟수는 해당 간격을 따릅니다. 기존 0.002초·501회 기준 검증과 구별합니다.</p>')
    if result.get('development_failure_probe'):
        doc.append('<p><strong>개발용 복구 시험:</strong> 첫 실패 후보는 명시적으로 투입한 검사입니다. 그 후보로 주 장면을 움직이지 않습니다. 이후 복구·실행 판단은 실제 Astra이며, 자연 실패율/연구 수집에 포함하지 않습니다.</p>')
    if result.get('status')=='reference_invalid':
        doc.append('<h2>조립 전 완성 목표 유지 검사에서 중단</h2><p>아래는 완성 CAD를 놓고 중력을 켠 별도 기준 장면입니다. 로봇의 집기 실패나 Astra 판단 결과가 아닙니다. 공급 부품의 초기 정착과 로봇 조립은 아직 실행되지 않았습니다.</p>')
        diagnostic=folder/'reference_diagnostics.json'
        if diagnostic.is_file():
            d=read(diagnostic)
            doc.append('<p>고정 받침: '+h(d['fixed_base'])+' / 중단 시각: '+h(d['simulation_time_s'])+'초 / 목표 자세를 벗어난 부품: '+h(d['outside_goal_pose_limits'])+'</p>')
        for name,label in [('initial','중력 검사 전 완성 목표'),('stopped','검사 중단 시 실제 상태')]:
            if (folder/'reference_media'/(name+'.png')).is_file():
                doc.append('<figure><img src="reference_media/'+name+'.png"><figcaption>'+label+' · reference_scene</figcaption></figure>')
    for name in ('goal_top','goal_oblique','initial','final'):
        if (folder/'media'/(name+'.png')).exists():doc.append('<figure><img src="media/'+name+'.png"><figcaption>'+h(name)+'</figcaption></figure>')
    if (folder/'media/execution.mp4').exists():doc.append('<h2>실행 중 저장한 상태의 영상</h2><video controls src="media/execution.mp4"></video>')
    summary=failure_summary(events(folder));write(folder/'failure_summary.json',summary)
    if summary['failures']:
        doc.append('<h2>무엇이 막혔는가</h2><p>집을 수 있는 것과 그 자세로 설치할 수 있는 것은 별도 검사입니다. 아래는 실제 검출한 실패이며, 제한된 후보의 실패를 모든 방법이 불가능하다는 뜻으로 해석하지 않습니다.</p>')
        for f in summary['failures'][-6:]:
            doc.append('<h3>부품 '+h(f['part_id'])+' · '+h(f['phase'])+'</h3><p>출처: '+h(f['source'])+' / 집기: '+h(f['grasp_feasible'])+' / 설치: '+h(f['installation_feasible_with_grasp'])+'</p>')
            for c in f['contacts'][:3]:doc.append('<p>'+h(' ↔ '.join(c['geoms']))+' / 겹침 '+h(round(max(0,-c['signed_distance_m'])*1000,3))+' mm</p>')
            snap=f['snapshot'];image=snap.get('oblique_png') or snap.get('png')
            if image and (folder/image).is_file():doc.append('<figure><img src="'+h(image)+'"><figcaption>검출 당시 저장 상태 · '+h(f['source'])+'</figcaption></figure>')
    for name in ('progress.json','reference_diagnostics.json','reference_verification.json','final_verification.json','metrics.json','audit.json','registrations.json','retrieval.jsonl'):
        if (folder/name).is_file():doc.append('<details><summary>'+h(name)+'</summary><pre>'+h((folder/name).read_text(encoding='utf8'))+'</pre></details>')
    doc.append('<h2>원본 기록</h2><ul>')
    for file in sorted(folder.iterdir()):
        if file.suffix in ('.json','.jsonl','.csv'):doc.append('<li><a href="'+h(file.name)+'">'+h(file.name)+'</a></li>')
    doc.append('</ul></html>');(folder/'report.html').write_text('\n'.join(doc),encoding='utf8')
    m=result.get('metrics',{});write(folder/'metrics.json',m)
    with (folder/'metrics.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=['status','policy_source',*m]);writer.writeheader();writer.writerow({'status':result['status'],'policy_source':result.get('policy_source'),**m})
