"""Read-only review of offline assembly trial outcomes; never executes a robot."""
from pathlib import Path
import argparse,json
from assembly_v12.contracts import read,write,sha
from assembly_v2.completion import valid_evidence
from assembly_v2.failure_diagnosis import diagnose_failure


def review(root, output):
    root=Path(root).resolve();output=Path(output).resolve()
    if output.exists():raise ValueError('Use a new output directory to preserve prior reviews.')
    rows=[]
    for path in sorted(root.glob('part*/result.json')):
        r=read(path);failure=r.get('failure');completion=r.get('completion')
        prepared=read(path.parent/'prepared.json') if (path.parent/'prepared.json').exists() else None
        verified=False
        if prepared and completion and (path.parent/'model.mjb').is_file():
            verified=(completion.get('model_sha256')==sha(path.parent/'model.mjb')
                and valid_evidence(completion,prepared['completion_profile_hash'],source='live_simulation',
                                   required_parts=r.get('installed',[])))
        rows.append({'run_directory':str(path.parent),'result_sha256':sha(path),
            'run_id':r.get('run_id'),'part_id':r.get('part_id'),
            'policy_source':r.get('policy_source'),'status':r.get('status'),
            'api_calls':r.get('api_calls'),'elapsed_s':r.get('elapsed_s'),
            'verified_parts_in_this_branch':r.get('installed',[]) if verified else [],
            'common_completion_verified':bool(verified),
            'failure':None if not failure else {'failure_code':failure.get('failure_code'),
                'phase':failure.get('phase'),'source':failure.get('source'),'message':failure.get('message'),
                'diagnosis':diagnose_failure(failure)}})
    output.mkdir(parents=True,exist_ok=False)
    result={'schema':'offline_failure_review/v1','source_root':str(root),'rows':rows,
        'read_only':True,'new_simulation_steps':0,'new_api_calls':0,
        'separate_branches_not_combined':True,'automatic_skill_registration':False}
    write(output/'review.json',result)
    lines=['# 조립 실행 실패 검토','',
        '원본 결과는 읽기 전용으로 확인했다. 아래 분류는 원인 확정이나 재시도 명령이 아니다. 각 행은 별도 개발 분기다.','',
        '| 실행 | 부품 | 결과 | 단계 | 분류 | 해당 분기의 검증 부품 |',
        '|---|---|---|---|---|---|']
    for row in rows:
        f=row['failure'] or {};d=f.get('diagnosis',{})
        lines.append('| '+ ' | '.join([Path(row['run_directory']).name,str(row['part_id']),str(row['status']),
            str(f.get('phase','')),str(d.get('failure_layer','')),
            ', '.join(map(str,row['verified_parts_in_this_branch']))])+' |')
    lines+=['','시간 소진: 물리 성공/실패 미확정. IK 탐색 미발견: 그 탐색 한도에서 답을 못 찾았다는 뜻이며 조립 불가능 증명이 아니다.',
            '실제 접촉 위반은 원본 violations와 정확한 검출 순간을 확인한다. 자세·힘·접촉 허용 기준을 변경하지 않는다.','']
    (output/'REVIEW.md').write_text('\n'.join(lines),encoding='utf-8')
    print(json.dumps({'runs':len(rows),'output':str(output),'new_api_calls':0},ensure_ascii=False))
    return result

if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--root',required=True);ap.add_argument('--output',required=True)
    a=ap.parse_args();review(a.root,a.output)
