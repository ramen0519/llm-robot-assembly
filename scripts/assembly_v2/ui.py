"""Thin localhost UI: the same CLI, state files, validators and reports."""
from pathlib import Path
import importlib.metadata
import json
import os
import secrets
import subprocess
import sys
import threading
import uuid
import hashlib
from flask import Flask,request,jsonify,send_from_directory,session,abort
from assembly_v12.contracts import location,read,write
from llm_runtime import load_llm_config
from .storage import run_root,memory_root,upload_root

ROOT=Path(__file__).resolve().parents[2]
RUNS=run_root()/'assembly_v2_ui'

def environment_status():
    cfg=load_llm_config(ROOT/'config/llm.astra-high.toml')
    packages={}
    for name in ('mujoco','mink','daqp','langgraph','trimesh','coacd','torch','transformers'):
        try:packages[name]=importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:packages[name]='not_installed'
    return {'robot':'MuJoCo / UR3e 1대 / Robotiq 2F-85','model':cfg.model,'effort':cfg.reasoning_effort,
      'API_key_present':bool(os.environ.get(cfg.api_key_env)),'per_run_config_cap_usd':cfg.max_cost_usd,
      'per_run_generation_cap':cfg.max_model_requests,'native_bridge':'requires responsive configured ASAP-Ubuntu; not assumed healthy',
      'encoder_weights_present':(ROOT/'runtime/assets/dinov2-small/model.safetensors').is_file(),'packages':packages,
      'hardware_execution':'disabled; simulator only','output_root':str(run_root()),'memory_root':str(memory_root())}

PAGE='''<!doctype html><html lang="ko"><meta charset="utf-8"><title>조립 프로토타입 v2</title>
<style>body{font:17px system-ui;max-width:1080px;margin:30px auto;padding:16px;color:#222}input,select,button{padding:10px;margin:5px}input[type=text]{width:80%}pre{background:#f5f5f5;padding:14px;white-space:pre-wrap}img{max-width:46%}video{max-width:100%}</style>
<h1>CAD → 로봇 조립</h1><p>주 실행의 물리 성공, 기록 검사, 영상 상태를 따로 확인합니다. scripted는 개발 검사입니다.</p>
<details open><summary>공통 환경</summary><pre id="env"></pre></details>
<form id="form"><label>제품 경로 (STEP / OBJ 조립 자료 / 검증 export / 준비 자료)<br><input name="input" type="text"></label><br>
<label>또는 파일 선택 <input name="file" type="file" accept=".step,.stp,.zip,.json,.obj"></label><br>
<label>작업 <select name="command"><option value="prepare">입력·장면 준비</option><option value="run">준비부터 조립 실행</option></select></label>
<label>정책 <select name="policy"><option value="astra">실제 Astra high</option><option value="scripted">개발용 scripted</option></select></label>
<label>조건 <select name="condition"><option>R0</option><option>R1</option><option>S0</option><option>S1</option></select></label>
<label>OBJ 단위 <select name="unit"><option value="">원본 메타데이터</option><option>mm</option><option>m</option><option>cm</option><option>inch</option></select></label>
<label><input name="assembled" type="checkbox">OBJ에 완성 위치 포함</label>
<label>제한 초 <input name="seconds" type="number" value="300" min="1" max="1800"></label>
<p>R: 조립·복구 역할 분리 / S: 단일 역할 / 0: 검색 끔 / 1: 검색 켬. 이 화면은 개발 실행이며 연구 수집·평가는 동결 manifest CLI를 사용합니다.</p>
<button>시작</button></form><button id="cancel" disabled>중단 요청</button>
<h2>현재 진행</h2><pre id="progress">입력을 기다립니다.</pre><div id="links"></div><div id="images"></div><video id="video" controls hidden></video>
<h2>최근 실행 결과</h2><p>CLI와 UI에서 실행한 실제 기록입니다. 개발 시험과 연구 수집은 별도로 표시됩니다.</p><button id="refresh">목록 새로고침</button><div id="recent"></div>
<script>let job=null,csrf=null;const by=id=>document.getElementById(id);fetch('/api/environment').then(r=>r.json()).then(v=>{csrf=v.csrf;by('env').textContent=JSON.stringify(v.environment,null,2)});
async function recent(){let r=await fetch('/api/recent');let v=await r.json();by('recent').replaceChildren();for(let x of v.runs){let p=document.createElement('p'),a=document.createElement('a');a.href='/results/'+x.token+'/report.html';a.target='_blank';a.textContent=x.run_id+' · '+x.status+' · '+x.policy_source+' · '+x.split;p.appendChild(a);by('recent').appendChild(p)}}by('refresh').onclick=recent;recent();
by('form').onsubmit=async e=>{e.preventDefault();let f=new FormData(e.target);f.append('csrf',csrf);let r=await fetch('/api/start',{method:'POST',body:f});let v=await r.json();if(!r.ok){by('progress').textContent=v.error;return}job=v.job;by('cancel').disabled=false;poll()};
by('cancel').onclick=()=>fetch('/api/cancel/'+job,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({csrf})});
async function poll(){let r=await fetch('/api/jobs/'+job);let v=await r.json();by('progress').textContent=JSON.stringify(v,null,2);by('images').replaceChildren();for(let n of v.images||[]){let i=document.createElement('img');i.src='/files/'+job+'/'+n;i.alt=n;by('images').appendChild(i)}by('links').replaceChildren();if(v.report){let a=document.createElement('a');a.href='/files/'+job+'/report.html';a.textContent='상세 결과·측정값·검색·로그 열기';a.target='_blank';by('links').appendChild(a)}if(v.video){by('video').hidden=false;by('video').src='/files/'+job+'/'+v.video}if(v.running)setTimeout(poll,2500);else by('cancel').disabled=true;}
</script></html>'''

def create_app(run_root=RUNS,*,result_roots=None):
    app=Flask(__name__);app.secret_key=secrets.token_bytes(32);app.config.update(MAX_CONTENT_LENGTH=100*1024*1024,SESSION_COOKIE_SAMESITE='Strict',SESSION_COOKIE_HTTPONLY=True)
    run_root=location(run_root);run_root.mkdir(parents=True,exist_ok=True);jobs={};lock=threading.Lock()
    roots=[location(p) for p in (result_roots or [run_root])]
    def catalog():
        found={}
        for root in roots:
            if not root.is_dir():continue
            for pattern in ('*/result.json','*/*/result.json','*/*/*/result.json'):
                for path in root.glob(pattern):
                    if any(x in ('replay_bundle','evidence','implementation') for x in path.relative_to(root).parts):continue
                    try:
                        path=location(path);value=read(path)
                        if value.get('schema')!='assembly_result/v2.0' or not (path.parent/'report.html').is_file():continue
                        token=hashlib.sha256(str(path.parent).encode()).hexdigest()[:32]
                        found[token]=(path.parent,value,path.stat().st_mtime)
                    except (OSError,ValueError):continue
        return found
    def guarded():
        token=request.form.get('csrf') if request.form else (request.get_json(silent=True) or {}).get('csrf')
        if not session.get('csrf') or not secrets.compare_digest(token or '',session['csrf']):abort(403)
        if request.headers.get('Origin') and request.headers['Origin'].rstrip('/')!=request.host_url.rstrip('/'):abort(403)
    def folder(job):
        if not len(job)==32 or any(c not in '0123456789abcdef' for c in job):abort(404)
        return run_root/job
    @app.get('/')
    def index():return PAGE
    @app.get('/api/environment')
    def env():
        session['csrf']=session.get('csrf') or secrets.token_hex(24)
        return jsonify(environment=environment_status(),csrf=session['csrf'])
    @app.get('/api/recent')
    def recent():
        rows=sorted(catalog().items(),key=lambda x:x[1][2],reverse=True)[:30]
        return jsonify(runs=[{'token':token,'run_id':value.get('run_id',path.name),'status':value['status'],
            'policy_source':value.get('policy_source','not_run'),'split':value.get('split','development'),
            'cost_usd':value.get('usage',{}).get('known_api_cost_estimate_usd',0)} for token,(path,value,_) in rows])
    @app.get('/results/<token>/<path:name>')
    def previous_result(token,name):
        selected=catalog().get(token)
        if selected is None:abort(404)
        return send_from_directory(selected[0],name)
    @app.post('/api/start')
    def start():
        guarded()
        with lock:
            if any(p.poll() is None for p in jobs.values()):return jsonify(error='현재 실행이 끝난 뒤 시작하세요.'),409
            command=request.form.get('command');policy=request.form.get('policy');condition=request.form.get('condition')
            try:
                seconds=float(request.form.get('seconds','300'))
                if command not in ('prepare','run') or policy not in ('astra','scripted') or condition not in ('S0','S1','R0','R1') or not 1<=seconds<=1800:raise ValueError('설정 범위를 확인하세요.')
                source=request.form.get('input','').strip();upload=request.files.get('file')
                if upload and upload.filename:
                    suffix=Path(upload.filename).suffix.lower()
                    if suffix not in ('.step','.stp','.zip','.json','.obj'):raise ValueError('지원 파일 형식이 아닙니다.')
                    directory=upload_root()/uuid.uuid4().hex;directory.mkdir(parents=True)
                    target=directory/('product'+suffix);upload.save(target);source=str(target)
                if not source or not location(source).exists():raise ValueError('존재하는 입력 경로를 선택하세요.')
                job=uuid.uuid4().hex;out=folder(job)
                args=[sys.executable,'-B',str(ROOT/'scripts/run_assembly.py'),command,'--input',str(location(source)),'--output',str(out),'--seconds',str(seconds)]
                if command=='run':args+=['--policy',policy,'--condition',condition]
                unit=request.form.get('unit')
                if unit:
                    if unit not in ('mm','cm','m','inch'):raise ValueError('단위를 확인하세요.')
                    args+=['--unit',unit]
                if request.form.get('assembled'):args+=['--assembled-obj']
                logs=run_root/(job+'.log');stream=logs.open('w',encoding='utf8')
                try:jobs[job]=subprocess.Popen(args,cwd=ROOT,stdout=stream,stderr=subprocess.STDOUT)
                finally:stream.close()
                return jsonify(job=job)
            except Exception as exc:return jsonify(error=str(exc)),400
    @app.post('/api/cancel/<job>')
    def cancel(job):
        guarded();out=folder(job)
        if not out.exists():return jsonify(error='아직 시작되지 않았습니다.'),409
        (out/'cancel.request').write_text('UI cancellation requested',encoding='utf8');return jsonify(status='requested')
    @app.get('/api/jobs/<job>')
    def progress(job):
        out=folder(job);p=jobs.get(job);v={'running':p.poll() is None if p else False,'exit_code':p.poll() if p else None}
        for name in ('result','audit','registrations','media_result'):
            try:
                if (out/(name+'.json')).exists():v[name]=read(out/(name+'.json'))
            except (ValueError,OSError):v[name]={'status':'writing'}
        if 'result' in v and v['result'].get('replay_bundle'):
            bundle=v['result']['replay_bundle']
            v['result']['replay_bundle']={k:bundle[k] for k in ('id','replayable','replay_mode') if k in bundle}
        for name in ('skill_trace','events','retrieval'):
            path=out/(name+'.jsonl')
            if path.exists():
                try:v['latest_'+name]=json.loads(path.read_text(encoding='utf8').splitlines()[-1])
                except (ValueError,IndexError,OSError):pass
        v['images']=['media/'+n+'.png' for n in ('goal_top','goal_oblique','initial','final') if (out/'media'/(n+'.png')).exists()]
        v['report']=(out/'report.html').exists();v['video']='media/execution.mp4' if (out/'media/execution.mp4').exists() else None
        return jsonify(v)
    @app.get('/files/<job>/<path:name>')
    def files(job,name):return send_from_directory(folder(job),name)
    return app

def serve(port=8765):
    if not 1024<=port<=65535:raise ValueError('Use a valid unprivileged local port.')
    create_app(result_roots=[RUNS.parent,ROOT/'runs']).run(host='127.0.0.1',port=port,debug=False,use_reloader=False)
