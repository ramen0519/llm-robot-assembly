"""Local, inspectable run provenance and a report that opens without a server."""
from html import escape
from pathlib import Path
import importlib.metadata
import json
import subprocess

from .contracts import read,sha,write


def implementation_versions():
    root=Path(__file__).resolve().parents[2]
    paths=sorted((root/'scripts').glob('*.py'))+sorted((root/'scripts/assembly_v12').glob('*.py'))
    try:
        head=subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True).strip()
    except (OSError,subprocess.CalledProcessError):head=None
    packages={}
    for name in ('mujoco','mink','numpy','scipy','langgraph','openai','torch','transformers','pillow','safetensors'):
        try:packages[name]=importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:packages[name]='not_installed'
    return {'base_git_head':head,'source_files':{str(p.relative_to(root)):sha(p) for p in paths},
            'packages':packages,'source_hashes_include_uncommitted_changes':True}


def versions(folder):
    write(Path(folder)/'code_versions.json',implementation_versions())


def report(result,folder):
    folder=Path(folder)
    def h(value):return escape(str(value))
    rows=[('상태',result['status']),('판단 출처',result.get('policy_source','미실행')),
          ('조건',result.get('condition',result.get('mode','미실행'))),
          ('실제 설치',str(result.get('installed',[]))+' / '+str(result.get('required',[]))),
          ('API 생성 요청',result.get('api_calls',0)),('실행 오류',result.get('error')),
          ('영상 상태',result.get('media',{}).get('status','미실행'))]
    text=['<!doctype html><meta charset="utf-8"><title>조립 실행 결과</title>',
          '<style>body{font:16px system-ui;max-width:1100px;margin:32px auto;padding:0 20px;color:#202020}td,th{border:1px solid #ddd;padding:8px;text-align:left}table{border-collapse:collapse;width:100%}figure{display:inline-block;max-width:31%;vertical-align:top}img,video{max-width:100%}code{overflow-wrap:anywhere}</style>',
          '<h1>조립 실행 결과</h1><p><code>'+h(folder.name)+'</code></p>',
          '<p>규칙 정책(scripted)은 Astra 실행이 아닙니다. 복사 검사 통과는 주 실행 설치 성공이 아닙니다.</p>',
          '<table>'+''.join('<tr><th>'+h(k)+'</th><td>'+h(v)+'</td></tr>' for k,v in rows)+'</table>']
    for name,label in [('goal_top','완성 목표 · 렌더 전용'),('initial','주 실행 · 초기'),('final','주 실행 · 최종')]:
        if (folder/'media'/(name+'.png')).is_file():
            text.append(f'<figure><img src="media/{name}.png"><figcaption>{h(label)}</figcaption></figure>')
    if result.get('media',{}).get('status')=='recorded_states_rendered':
        text.append('<h2>기록 상태 재생</h2><p>주 실행의 저장 상태만 사용하며 동작을 다시 실행하지 않습니다. 경계 프레임 때문에 영상 길이와 시뮬레이션 시각에 소폭 차이가 있습니다.</p><video controls preload="metadata" src="media/execution.mp4"></video>')
    failures=sorted((folder/'failures').glob('*.json')) if (folder/'failures').exists() else []
    if failures:
        text.append('<h2>검출된 실패 장면</h2>')
        for path in failures:
            f=read(path)
            if f.get('png'):
                text.append('<figure><img src="'+h(f['png'])+'"><figcaption>'+h(f['source'])+' · '+h(f['phase'])+' · '+h(f['failure_code'])+'</figcaption></figure>')
    if (folder/'events.jsonl').is_file():
        text.append('<h2>판단과 실행</h2><table><tr><th>부품</th><th>역할</th><th>행동</th><th>실제 이동</th><th>실패/설치 검증</th></tr>')
        for line in (folder/'events.jsonl').read_text(encoding='utf8').splitlines():
            e=json.loads(line)
            text.append('<tr>'+''.join('<td>'+h(v)+'</td>' for v in (e['part_id'],e['active_role'],e['proposal']['skill'],e['executed'],(e.get('failure') or {}).get('failure_code') or e.get('installation_verified')))+'</tr>')
        text.append('</table>')
    text.append('<h2>원본 자료</h2><ul>')
    for name in ('result.json','metrics.csv','prepared.json','code_versions.json','events.jsonl','observations.jsonl',
                 'incidents.json','retrieval.jsonl','memory_snapshot.json','llm_events.jsonl','final_verification.json','replay_frames.json','input/export_v1.json'):
        if (folder/name).exists():text.append('<li><a href="'+h(name)+'">'+h(name)+'</a></li>')
    text.append('</ul>')
    (folder/'report.html').write_text('\n'.join(text),encoding='utf8')
