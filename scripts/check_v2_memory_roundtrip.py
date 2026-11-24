"""Actual scripted evidence -> explicitly non-LLM development card -> next snapshot.

The objective incident must come from real completed simulator events. This
exercise is not actual Astra guidance generation or research collection.
"""
import argparse
from pathlib import Path
import numpy as np
from assembly_v12.contracts import read,write,relative
from assembly_v2.memory import Library,Encoder,validate_guidance
from assembly_v2.artifacts import verify_bundle

def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--run',required=True);ap.add_argument('--output',required=True)
    a=ap.parse_args();run=Path(a.run).absolute();out=Path(a.output).absolute();out.mkdir(parents=True,exist_ok=False)
    result={'scope':'development_storage_retrieval_contract; guidance supplied by test, NOT Astra','api_calls':0,'status':'not_run'}
    try:
        incidents=read(run/'incidents.json');incident=next(e for e in incidents if e['registration']=='eligible')
        if incident['split']!='development' or incident['policy_source']!='scripted':raise ValueError('This adapter is only for explicitly scripted development.')
        picked=next(e for e in incident['events'] if e['proposal']['skill']=='pick' and e['executed'] and not e.get('failure'))
        failed=next(e for e in incident['events'] if e.get('failure'));grasp=picked['resolved_grasp']
        card={'summary':'개발 검사용 지침: 실제 성공한 전체 파지 자세를 참고하되 현재 물체의 표면·간섭·설치를 다시 확인한다.',
          'action':'pick','parameters':[{'name':k,'unit':'xyzw' if k=='quaternion_xyzw' else 'mm',
             'value':np.atleast_1d(grasp[k]).tolist(),'event_id':picked['id']} for k in ('position_mm','quaternion_xyzw','opening_width_mm')],
          'evidence_refs':[failed['id'],picked['id'],incident['installation_evidence']],
          'checks':['current_geometry','grasp_copy','installation_copy','main_completion_window']}
        validate_guidance(card,incident)
        weights=Path(__file__).resolve().parents[1]/'runtime/assets/dinov2-small'
        encoder=Encoder(weights);library=Library(out/'isolated_development_memory',encoder=encoder)
        result['registration']=library.register(incident,card,run,'development_test_card_not_LLM')
        result['same_run_snapshot_cases']=len(library.snapshot['cases'])
        next_library=Library(out/'isolated_development_memory',encoder=encoder)
        snap=failed['failure']['failure_snapshot'];image=relative(run,snap['png'])
        result['next_snapshot']=next_library.snapshot
        result['retrieved']=next_library.retrieve(failed['scope'],image=image,refs={'validation':'next_snapshot_reuse'},log=out/'retrieval.jsonl')
        case=out/'isolated_development_memory'/result['registration']['case_id'];result['independent_replay_bundle']=verify_bundle(case/'evidence')['replayable']
        result['status']='passed' if result['registration']['status']=='registered' and result['same_run_snapshot_cases']==0 and result['retrieved'] and result['independent_replay_bundle'] else 'failed'
        result['actual_Astra_guidance']='not_run'
    except Exception as exc:result.update(status='failed',error=type(exc).__name__+': '+str(exc))
    write(out/'memory_roundtrip.json',result);print(out/'memory_roundtrip.json',flush=True)

if __name__=='__main__':main()
