"""Small, explicit denominators; scripted and live decisions never share a score."""
import csv
import json
from pathlib import Path

from .contracts import read,write


def records(path):
    return [json.loads(x) for x in Path(path).read_text(encoding='utf8').splitlines()] if Path(path).is_file() else []


def summarize(result,folder):
    folder=Path(folder); events=records(folder/'events.jsonl'); retrieval=records(folder/'retrieval.jsonl')
    failures=[e for e in events if e.get('failure')]
    usage=result.get('usage',{})
    timings=result.get('timings_seconds',{})
    return {'run_id':result.get('run_id',folder.name),'condition':result.get('condition'),
            'status':result['status'],'policy_source':result.get('policy_source','not_run'),
            'input_valid':result.get('input_valid',False),'task_supported':result.get('task_supported',False),
            'preparation_ready':result.get('preparation_ready',False),'success':result['status']=='success',
            'required_parts':len(result.get('required',[])),'installed_parts':len(result.get('installed',[])),
            'initial_installed_parts':len(result.get('initial_installed',[])),
            'control_decisions':result.get('control_decisions',0),'failed_actions':len(failures),
            'grasp_candidates':sum('preflight' in e for e in events),
            'grasp_rejections':sum('preflight' in e and not e['preflight']['accepted'] for e in events),
            'retries':sum('preflight' in e and not e['preflight']['accepted'] for e in events)+result.get('alignment_attempts',0)+result.get('replan_attempts',0),
            'alignment_attempts':result.get('alignment_attempts',0),'replan_attempts':result.get('replan_attempts',0),
            'retrieval_requests':len(retrieval),'retrieval_hits':sum(bool(e['returned']) for e in retrieval),
            'retrieval_seconds':sum(e['seconds'] for e in retrieval),
            'incidents':result.get('incidents',0),'solved_local':result.get('solved_local',0),
            'api_calls':result.get('api_calls',0),'input_tokens':usage.get('input_tokens',0),
            'input_count_requests':usage.get('input_count_requests',0),'http_requests':usage.get('http_requests',0),
            'cached_input_tokens':usage.get('cached_input_tokens',0),'output_tokens':usage.get('output_tokens',0),
            'reasoning_tokens':usage.get('reasoning_tokens',0),'estimated_cost_usd':usage.get('api_cost',0),
            'known_cost_usd':usage.get('known_api_cost_estimate_usd',0),
            'unknown_usage_requests':usage.get('unknown_usage_requests',0),
            'unresolved_cost_reserve_usd':usage.get('unresolved_cost_reserve_usd',0),
            'physical_wall_seconds':result.get('physical_wall_seconds'),
            'execution_wall_seconds':result.get('execution_wall_seconds',result.get('physical_wall_seconds')),
            'policy_seconds':timings.get('policy'), 'guidance_seconds':timings.get('guidance'),
            'preflight_seconds':timings.get('preflight'),'main_tool_seconds':timings.get('main_tool'),
            'scene_setup_seconds':timings.get('scene_setup'),'stabilization_seconds':timings.get('stabilization'),
            'video_render_seconds':result.get('media',{}).get('render_seconds'),
            'upstream_seconds':result.get('upstream',{}).get('seconds'),
            'simulation_seconds':result.get('simulation_seconds'),
            'pipeline_seconds':result.get('pipeline_seconds',result.get('elapsed_seconds')),
            'media_status':result.get('media',{}).get('status','not_run')}


def write_metrics(result,folder):
    row=summarize(result,folder)
    write(Path(folder)/'metrics.json',row)
    table(Path(folder)/'metrics.csv',[row])
    return row


def table(path,rows):
    if not rows: return
    with Path(path).open('w',newline='',encoding='utf-8-sig') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
