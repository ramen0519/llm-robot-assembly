"""Objective evidence and advisory guidance; frozen condition-first image retrieval."""
from copy import deepcopy
from pathlib import Path
import json
import shutil
import sys
import tempfile
import uuid
import numpy as np
from assembly_v12.contracts import read,write,sha,digest,append,relative,location
from assembly_v12.memory import normalized
from .completion import valid_evidence
from .artifacts import events,verify_bundle
from .budget import Budget
from .media import CAMERA_VERSION

ROOT=Path(__file__).resolve().parents[2]
DEFAULT_MEMORY=ROOT/'runtime/memory/assembly_v2'
DOMAIN='general_mesh_assembly/v2.0'

def source_identity(prepared):
    return {'source_run_hash':prepared['source_run_hash'],'source_geometry_hash':prepared['source_geometry_hash'],
            'source_mesh_set':digest(sorted(p['source_mesh_sha256'] for p in prepared['parts'].values()))}

def phase_family(phase):
    phase=phase.split('/')[0]
    for family,words in [('approach',('approach','grasp_open')),('grasp',('grasp_close','pick_lift')),
                         ('install',('transport','insert','verified_part_path')),('release',('release','retreat','completion'))]:
        if any(phase.startswith(w) for w in words):return family
    return phase

def scope_for(failure,grasp,action,profile_hash):
    pairs=[]
    def walk(value):
        if isinstance(value,dict):
            if 'roles' in value:
                pair=sorted('product' if x.startswith('product:') else x for x in value['roles'])
                if pair not in pairs:pairs.append(pair)
            for v in value.values():walk(v)
        elif isinstance(value,list):
            for v in value:walk(v)
    walk(failure.get('violations',[]))
    t=np.asarray(grasp['tcp_in_part']) if grasp else None
    return {'domain':DOMAIN,'robot_gripper':'UR3e/Robotiq2F85','profile_hash':profile_hash,
       'failure_code':failure['failure_code'],'source':failure['source'],'phase':phase_family(failure['phase']),
       'action':action,'contact_roles':sorted(pairs),'contact_roles_observed':bool(pairs),
       'opening_width_mm':grasp['opening_width_mm'] if grasp else None,
       'approach_axis_world':grasp.get('approach_axis_world') if grasp else None}

def compatible(saved,query):
    required=('domain','robot_gripper','profile_hash','failure_code','source','phase','action')
    if any(saved.get(k)!=query.get(k) or saved.get(k) is None for k in required):return False,'problem_context_mismatch'
    contact=saved['failure_code'] in ('PLANNED_CONTACT','UNEXPECTED_CONTACT','INSERTION_CONTACT_LIMIT')
    if contact and (not saved.get('contact_roles_observed') or not query.get('contact_roles_observed')):return False,'contact_roles_unknown'
    if saved.get('contact_roles')!=query.get('contact_roles'):return False,'contact_roles_mismatch'
    if saved.get('opening_width_mm') is None or query.get('opening_width_mm') is None:return False,'gripper_geometry_unknown'
    if abs(saved['opening_width_mm']-query['opening_width_mm'])>5:return False,'width_outside_observed_similarity_band'
    if saved.get('approach_axis_world') is None or query.get('approach_axis_world') is None:return False,'approach_unknown'
    if np.dot(saved['approach_axis_world'],query['approach_axis_world'])<np.cos(np.deg2rad(15)):return False,'approach_mismatch'
    return True,'condition_filter_passed; advisory_only; current_scene_revalidation_required'

def incidents_from(rows,run_id,prepared,split,lineage_id,policy_source,final):
    output=[]
    for pid in prepared['order']:
        relevant=[e for e in rows if e['part_id']==pid]
        failed=next((i for i,e in enumerate(relevant) if e.get('failure')),None)
        if failed is None:continue  # Nominal successes remain objective run logs, not solved-failure cards.
        chain=relevant[failed:];solved=next((e for e in chain if e.get('installation_verified') and e['executed'] and
                    valid_evidence(e.get('verification'),prepared['completion_profile_hash'])),None)
        preserved=final and pid in final.get('part_ids',[]) and valid_evidence(final,prepared['completion_profile_hash'])
        e={'id':digest([run_id,pid]),'run_id':run_id,'part_id':pid,'split':split,'lineage_id':lineage_id,
           'domain':DOMAIN,'policy_source':policy_source,'source_identity':source_identity(prepared),
           'events':chain,'state':'solved_local' if solved else 'unresolved','installation_evidence':solved['id'] if solved else None,
           'end_preserved':bool(preserved),'registration':'eligible' if solved and preserved else 'pending',
           'scope':chain[0]['scope'],'profile_hash':prepared['completion_profile_hash']}
        output.append(e)
    return output

def guidance_schema():
    parameter={'type':'object','properties':{'name':{'type':'string','enum':['position_mm','quaternion_xyzw','opening_width_mm']},
       'unit':{'type':'string','enum':['mm','xyzw']},'value':{'type':'array','items':{'type':'number'},'minItems':1,'maxItems':4},
       'event_id':{'type':'string'}},'required':['name','unit','value','event_id'],'additionalProperties':False}
    properties={'summary':{'type':['string','null'],'maxLength':700},'action':{'type':['string','null'],'enum':['pick','insert','recover_alignment','replan_insert',None]},
       'parameters':{'type':'array','items':parameter},'evidence_refs':{'type':'array','items':{'type':'string'}},
       'checks':{'type':'array','items':{'type':'string','enum':['current_geometry','grasp_copy','installation_copy','main_completion_window']}}}
    return {'type':'object','properties':properties,'required':list(properties),'additionalProperties':False}

def validate_guidance(card,incident):
    if not isinstance(card,dict) or set(card)!=set(guidance_schema()['properties']):raise ValueError('Invalid machine guidance schema.')
    if card['summary'] is None and card['action'] is None and not card['parameters'] and not card['evidence_refs']:return False
    if not isinstance(card['summary'],str) or not 1<=len(card['summary'])<=700:raise ValueError('Invalid concise guidance.')
    facts={e['id']:e for e in incident['events']};refs=card['evidence_refs']
    if not refs or not set(refs)<=set(facts) or incident['installation_evidence'] not in refs or not any(facts[k].get('failure') for k in refs):
        raise ValueError('Guidance requires original failure and main installation evidence.')
    action=card['action']
    successful=[e for e in facts.values() if e['executed'] and not e.get('failure') and e['proposal']['skill']==action]
    if action not in ('pick','insert','recover_alignment','replan_insert') or not successful:raise ValueError('Guidance action was not actually executed successfully.')
    if not {'current_geometry','grasp_copy','installation_copy','main_completion_window'}<=set(card['checks']):raise ValueError('Required revalidation missing.')
    for p in card['parameters']:
        if set(p)!={'name','unit','value','event_id'} or p['event_id'] not in refs:raise ValueError('Unlinked parameter evidence.')
        event=facts[p['event_id']]
        if event not in successful:raise ValueError('Parameter cites an unexecuted or unsuccessful action.')
        actual=event.get('resolved_grasp',{}).get(p['name'])
        unit='xyzw' if p['name']=='quaternion_xyzw' else 'mm'
        if actual is None or p['unit']!=unit or not np.array_equal(np.atleast_1d(actual),p['value']):raise ValueError('Unobserved parameter/value/unit.')
    if action=='pick' and {p['name'] for p in card['parameters']}!={'position_mm','quaternion_xyzw','opening_width_mm'}:
        raise ValueError('A grasp guidance example must cite its full applied pose and opening.')
    return True

class Encoder:
    def __init__(self,folder,seconds=60):self.folder=Path(folder).absolute();self.seconds=seconds;self.version=None;self.meta=None
    def encode(self,image):
        with tempfile.TemporaryDirectory(prefix='assembly_encoder_') as temp:
            output=Path(temp)/'encoded.json'
            done=Budget(self.seconds).process([sys.executable,'-B','-m','assembly_v2.memory','encode',str(self.folder),str(Path(image).absolute()),str(output)],
                 cwd=Path(__file__).resolve().parents[1],capture_output=True)
            if done.returncode or not output.is_file():raise RuntimeError('Bounded local DINO encoder failed: '+done.stderr.decode(errors='replace')[-500:])
            value=read(output);self.meta=value['meta'];self.version=value['version'];return normalized(value['vector'])

class Library:
    def __init__(self,root=DEFAULT_MEMORY,*,snapshot=None,encoder=None,split='development',exclude_sources=None):
        self.root=location(root);self.root.mkdir(parents=True,exist_ok=True);self.encoder=encoder;self.split=split
        if 'onedrive' in str(self.root).lower():raise ValueError('Live memory must be outside OneDrive.')
        if snapshot is None:
            cases=[]
            for path in sorted(self.root.glob('*/archive.json')):
                a=read(path)
                if a.get('domain')==DOMAIN and (split=='development' or a['experience']['split']=='collection'):
                    cases.append({'case_id':path.parent.name,'archive_sha256':sha(path)})
            self.snapshot={'schema':'memory_snapshot/v2.0','cases':cases};self.snapshot['id']=digest(self.snapshot)
        else:self.snapshot=deepcopy(snapshot)
        if self.snapshot['id']!=digest({k:v for k,v in self.snapshot.items() if k!='id'}):raise ValueError('Snapshot changed.')
        for row in self.snapshot['cases']:
            a=self.archive(row);e=a['experience']
            if split!='development' and (e['split']!='collection' or e['policy_source']!='astra'):raise ValueError('Development/scripted experience cannot enter research memory.')
            if exclude_sources and any(e['source_identity'].get(k)==v for k,v in exclude_sources.items()):raise ValueError('Source leakage even if lineage/product ID was renamed.')

    def archive(self,row):
        path=relative(self.root,row['case_id']+'/archive.json')
        if sha(path)!=row['archive_sha256']:raise ValueError('Archive changed after snapshot.')
        a=read(path);e=a['experience']
        if a.get('domain')!=DOMAIN or e['registration']!='eligible' or not e['end_preserved'] or not validate_guidance(a['guidance'],e):raise ValueError('Ineligible solved case.')
        bundle=relative(path.parent,a['bundle']);verify_bundle(bundle)
        raw={x['id']:x for x in events(bundle)}
        if any(raw.get(x['id'])!=x for x in e['events']):raise ValueError('Experience differs from preserved execution.')
        final=read(bundle/'final_verification.json')
        if not valid_evidence(final,e['profile_hash']) or e['part_id'] not in final['part_ids']:raise ValueError('Lost final preservation evidence.')
        return a

    def register(self,incident,card,run_folder,source):
        if incident['registration']!='eligible' or not validate_guidance(card,incident):return {'status':'pending','reason':'insufficient_incident_or_guidance'}
        if incident['split']!='development' and (incident['policy_source']!='astra' or source!='astra'):return {'status':'pending','reason':'research_requires_actual_astra'}
        run_folder=Path(run_folder);manifest=verify_bundle(run_folder/'replay_bundle')
        if not manifest['replayable']:return {'status':'pending','reason':'bundle_not_replayable'}
        cid=digest([incident['run_id'],incident['id']]);dest=self.root/cid
        if dest.exists():
            if not (dest/'archive.json').is_file():raise ValueError('Incomplete old registration; preserve it for inspection.')
            self.archive({'case_id':cid,'archive_sha256':sha(dest/'archive.json')})
            return {'status':'duplicate','case_id':cid}
        pending=self.root/('.pending-'+cid+'-'+uuid.uuid4().hex[:8])
        # Each archive contains its own relative evidence bundle reference. No dependency on the original run directory.
        pending.mkdir();shutil.copytree(run_folder/'replay_bundle',pending/'evidence')
        failures=[]
        for event in incident['events']:
            if not event.get('failure'):continue
            snap=event['failure'].get('failure_snapshot',{});f={'event_id':event['id'],'scope':event['scope'],'snapshot':snap,'embedding_status':'not_run'}
            if self.encoder and snap.get('png'):
                try:
                    image=relative(pending/'evidence',snap['png']);vector=self.encoder.encode(image)
                    f.update(embedding_status='encoded',embedding=vector.tolist(),encoder_version=self.encoder.version,encoder_meta=self.encoder.meta)
                except Exception as exc:f.update(embedding_status='failed',encoder_error=str(exc)[:500])
            failures.append(f)
        write(pending/'archive.json',{'domain':DOMAIN,'experience':incident,'guidance':card,'guidance_source':source,
              'bundle':'evidence','bundle_id':manifest['id'],'replayable':True,'failures':failures})
        pending.rename(dest)
        return {'status':'registered','case_id':cid,'available_from':'next_snapshot','embedding_count':sum(f['embedding_status']=='encoded' for f in failures)}

    def retrieve(self,query,*,image=None,refs=None,log=None):
        matches=[];rejected=[]
        for row in self.snapshot['cases']:
            a=self.archive(row)
            for f in a['failures']:
                ok,reason=compatible(f['scope'],query)
                if ok:matches.append((row,a,f))
                else:rejected.append({'case_id':row['case_id'],'event_id':f['event_id'],'reason':reason})
        v=None;encoder_error=None
        if matches and image and self.encoder:
            try:v=self.encoder.encode(image)
            except Exception as exc:encoder_error=str(exc)[:400]
        ranked=[]
        for row,a,f in matches:
            score=float(np.clip(np.dot(v,normalized(f['embedding'])),-1.,1.)) if v is not None and f.get('encoder_version')==self.encoder.version and f['snapshot'].get('camera_version')==CAMERA_VERSION else None
            ranked.append({'record_id':row['case_id'],'failure_event_id':f['event_id'],'guidance':a['guidance'],
                           'score':score,'ranking':'DINO_after_condition_filter' if score is not None else 'condition_only',
                           'must_revalidate':True,'not_an_executable_success':True})
        ranked.sort(key=lambda r:(r['score'] is not None,r['score'] if r['score'] is not None else -1,r['record_id']),reverse=True)
        selected=ranked[:3]
        if log:append(log,{'snapshot_id':self.snapshot['id'],'query':query,'refs':refs,'selected':selected,
                           'rejected':rejected,'encoder_error':encoder_error,'raw_images_sent_to_llm':False})
        return selected

if __name__=='__main__':
    if sys.argv[1]!='encode':raise ValueError('Unknown encoder operation.')
    from assembly_v12.memory import Encoder as NativeEncoder
    encoder=NativeEncoder(sys.argv[2]);vector=encoder.encode(sys.argv[3]);encoder.meta['camera']=CAMERA_VERSION
    import importlib.metadata
    encoder.meta['pillow']=importlib.metadata.version('pillow');encoder.meta['numpy']=np.__version__
    encoder.meta['adapter']='bounded_subprocess/v2.0'
    write(sys.argv[4],{'vector':vector.tolist(),'meta':encoder.meta,'version':digest(encoder.meta)})
