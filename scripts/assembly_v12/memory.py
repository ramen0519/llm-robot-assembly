"""Objective incidents, validated guidance, immutable snapshots and conditional retrieval."""
from copy import deepcopy
import json
from pathlib import Path
import re
import shutil
import time

import numpy as np

from .contracts import append, digest, read, sha, write, relative

DEFAULT_MEMORY = Path(__file__).resolve().parents[2]/'runtime/memory/assembly_v12'
DOMAIN = 'assembly_simulation/v1.2'
FIELDS = {'applies_when','strategy','checks','evidence_refs'}
INSTALL_CHECKS = {'released','target_position_under_1mm','target_orientation_under_1deg',
                  'insertion_depth_reached','linear_speed_under_1mm_s','angular_speed_under_0_01rad_s',
                  'penetration_under_1mm','declared_mating_support'}


def verified_installation(event):
    checks=event.get('verification',{}).get('checks',{})
    return (event.get('installation_verified') is True and event.get('source')=='live_simulation'
            and event.get('executed') is True and event.get('proposal',{}).get('skill') in ('insert','replan_insert')
            and INSTALL_CHECKS<=set(checks) and all(value is True for value in checks.values()))


def validate_guidance(card, incident):
    if not isinstance(card,dict) or set(card)!=FIELDS:
        raise ValueError('Wrong guidance schema.')
    if all(card[k] is None for k in FIELDS-{'evidence_refs'}) and card['evidence_refs']==[]:
        return False
    if any(not isinstance(card[k],str) or not 1<=len(card[k])<=768 for k in FIELDS-{'evidence_refs'}):
        raise ValueError('Guidance must be concise prose or an all-null deferral.')
    facts = {e['id']:e for e in incident['events']}
    if not card['evidence_refs'] or not set(card['evidence_refs'])<=set(facts):
        raise ValueError('Guidance cites missing evidence.')
    if incident.get('installation_evidence') and (incident['installation_evidence'] not in card['evidence_refs'] or
            not any(facts[k].get('failure') for k in card['evidence_refs'])):
        raise ValueError('Solved guidance must cite both the problem and original installation verification.')
    text = ' '.join(card[k] for k in FIELDS-{'evidence_refs'})
    measured={}
    def add(unit,value):
        for v in np.asarray(value).reshape(-1):
            if isinstance(v,(int,float,np.number)) and np.isfinite(v):measured.setdefault(unit,[]).append(float(v))
    def collect(value):
        if isinstance(value,list):
            for v in value:collect(v)
        elif isinstance(value,dict):
            for key,v in value.items():
                if isinstance(v,(int,float,list)) and not isinstance(v,bool):
                    try:
                        if key.endswith('_m_s'):add('m/s',v);add('mm/s',np.asarray(v)*1000)
                        elif key.endswith('_rad_s'):add('rad/s',v)
                        elif key.endswith('_mm'):add('mm',v)
                        elif key.endswith('_m'):add('m',v);add('mm',np.asarray(v)*1000)
                        elif key.endswith('_deg'):add('deg',v)
                        elif key.endswith('_rad'):add('rad',v);add('deg',np.rad2deg(v))
                        elif key.endswith('_n'):add('N',v)
                        elif key.endswith('_s'):add('s',v)
                    except (TypeError,ValueError):pass
                collect(v)
    for key in card['evidence_refs']:collect(facts[key])
    claims=re.findall(r'(?<!\w)(-?\d+(?:\.\d+)?)\s*(mm/s|m/s|rad/s|mm|m|deg|°|도|rad|N|s|초|회|개)(?![A-Za-z])',text)
    for number,unit in claims:
        unit={'°':'deg','도':'deg','초':'s'}.get(unit,unit)
        if not any(np.isclose(float(number),v,rtol=1e-3,atol=1e-6) for v in measured.get(unit,[])):
            raise ValueError('Unrecorded numeric value/unit claim in guidance.')
    stripped=re.sub(r'(?<!\w)-?\d+(?:\.\d+)?\s*(mm/s|m/s|rad/s|mm|m|deg|°|도|rad|N|s|초|회|개)(?![A-Za-z])','',text)
    if re.search(r'(?<!\w)-?\d+(?:\.\d+)?(?!\w)',stripped):
        raise ValueError('Numeric guidance must identify a recorded unit; IDs are not measurements.')
    if re.search(r'```|\b(?:exec|eval|import|def|teleport|weld|regrasp)\b',text):
        raise ValueError('Guidance cannot introduce code or unsupported actions.')
    return True


class Incidents:
    def __init__(self, run_id, split, family, lineage_id=None):
        self.run_id,self.split,self.family = run_id,split,family
        self.lineage_id=lineage_id or run_id
        self.incidents = {}
        self.events = []

    def event(self, part, target, event):
        event = deepcopy(event)
        event['id'] = f'{self.run_id}:event:{len(self.events)+1}'
        self.events.append(event)
        key = (part,target)
        if event.get('failure') and key not in self.incidents:
            self.incidents[key] = {'id':digest([self.run_id,part,target]),'run_id':self.run_id,'part_id':part,
                                  'target_id':target,'domain':DOMAIN,'split':self.split,'family':self.family,
                                  'lineage_id':self.lineage_id,
                                  'state':'open','events':[e for e in self.events[:-1] if e.get('part_id')==part]}
        if key in self.incidents:
            incident = self.incidents[key]
            incident['events'].append(event)
            if verified_installation(event):
                incident['state'] = 'solved_local'
                incident['installation_evidence'] = event['id']
        return event

    def finalize(self, run_success):
        for incident in self.incidents.values():
            incident['run_success'] = bool(run_success)
            incident['registration'] = ('eligible' if run_success and incident['state']=='solved_local' else 'pending')
            incident['registration_reason'] = ('complete_run_preserved' if run_success else
                                               'whole_run_not_successful; separate local preservation adapter unavailable')
            if incident['state']=='open': incident['state']='unresolved'
        return list(self.incidents.values())


class Encoder:
    """The disassembly DINOv2 weights/preprocess contract, with assembly camera ID."""
    def __init__(self, folder):
        self.folder = Path(folder); self.model = None

    def load(self):
        if self.model is not None: return
        import torch
        import transformers
        from transformers import Dinov2Model
        import transformers.models.dinov2.modeling_dinov2 as implementation
        from .media import CAMERA_VERSION
        checksum = sha(self.folder/'model.safetensors')
        if checksum!='ae1e99fcefd534ed978cdeb8326f08030c96e28b7a81ffcbc98a857c84d14be1':
            raise ValueError('DINO weight pin mismatch.')
        processor = read(self.folder/'preprocessor_config.json')
        torch.set_num_threads(2)
        self.meta = {'model':'facebook/dinov2-small','revision':'ed25f3a31f01632728cabb09d1542f84ab7b0056',
                     'weights':checksum,'torch':torch.__version__,'transformers':transformers.__version__,
                     'code_sha256':sha(implementation.__file__),'processor_sha256':sha(self.folder/'preprocessor_config.json'),
                     'config_sha256':sha(self.folder/'config.json'),'resize':'square RGB bicubic 224 full image',
                     'mean':processor['image_mean'],'std':processor['image_std'],'features':'CLS last_hidden_state L2',
                     'camera':CAMERA_VERSION,'device':'cpu','torch_num_threads':2}
        self.version = digest(self.meta)
        self.model = Dinov2Model.from_pretrained(str(self.folder),local_files_only=True,use_safetensors=True).eval()

    def encode(self, path):
        self.load()
        import torch
        from PIL import Image
        with Image.open(path) as im:
            if im.format!='PNG' or im.width!=im.height: raise ValueError('Expected square PNG.')
            rgb = np.asarray(im.convert('RGB').resize((224,224),Image.Resampling.BICUBIC),np.float32)/255.
        rgb=(rgb-np.asarray(self.meta['mean'],np.float32))/np.asarray(self.meta['std'],np.float32)
        with torch.no_grad():
            v=self.model(pixel_values=torch.from_numpy(np.ascontiguousarray(rgb.transpose(2,0,1))).unsqueeze(0)).last_hidden_state[0,0].cpu().numpy()
        return normalized(v)


def normalized(vector):
    v=np.asarray(vector,np.float32)
    if v.shape!=(384,) or not np.isfinite(v).all() or np.linalg.norm(v)<1e-8:
        raise ValueError('Invalid DINO vector.')
    return v/np.linalg.norm(v)


def scope_for(failure, geometry, allowed, contract):
    # Machine facts only. IDs are intentionally absent. No parsing of LLM causes.
    return {'domain':DOMAIN,'source':failure['source'],'phase':failure['phase'],'failure_code':failure['failure_code'],
            'contract':contract,'shape':geometry.get('shape'),'first_response':next((a for a in allowed if a!='stop'),None),
            'geometry':{k:geometry[k] for k in ('square_bore_width_mm','bore_width_mm','socket_insertion_depth_mm') if k in geometry}}


class Library:
    def __init__(self, root, *, snapshot=None, encoder=None, allowed_splits=('collection',), exclude_runs=(), exclude_families=()):
        self.root=Path(root); self.root.mkdir(parents=True,exist_ok=True)
        self.encoder=encoder
        if snapshot is None:
            rows=[]
            for path in sorted(self.root.glob('*/archive.json')):
                a=read(path)
                if a['experience']['split'] in allowed_splits and a['experience']['run_id'] not in exclude_runs and a['experience']['family'] not in exclude_families:
                    rows.append({'case_id':path.parent.name,'archive_sha256':sha(path)})
            self.snapshot={'schema':'assembly_memory_snapshot/v1.2','cases':rows}
            self.snapshot['id']=digest(self.snapshot)
        else:
            self.snapshot=deepcopy(snapshot)
            if self.snapshot['id']!=digest({k:v for k,v in self.snapshot.items() if k!='id'}):
                raise ValueError('Memory snapshot hash mismatch.')
        self.entries=[]
        for row in self.snapshot['cases']:
            a=self._archive(row)
            if a['experience']['split'] not in allowed_splits or a['experience']['run_id'] in exclude_runs or a['experience']['family'] in exclude_families:
                raise ValueError('Frozen memory leaks a prohibited split/run/family.')
            self.entries.append(row)

    def _archive(self,row):
        if not re.fullmatch('[0-9a-f]{64}',row['case_id']): raise ValueError('Invalid case ID.')
        path=relative(self.root,row['case_id']+'/archive.json')
        if sha(path)!=row['archive_sha256']: raise ValueError('Archive changed after memory freeze.')
        a=read(path)
        e=a['experience']
        if e.get('registration')!='eligible' or e.get('state')!='solved_local' or not e.get('run_success'):
            raise ValueError('Unresolved/unpreserved case in solved index.')
        if row['case_id']!=digest([e['run_id'],e['id']]): raise ValueError('Case identity mismatch.')
        installed=[v for v in e['events'] if v['id']==e.get('installation_evidence')]
        if len(installed)!=1 or not verified_installation(installed[0]):
            raise ValueError('Solved flag lacks the original live installation verification.')
        for name,checksum in a.get('evidence_files',{}).items():
            if sha(relative(path.parent,name))!=checksum: raise ValueError('Archived physical evidence changed.')
        if not a.get('evidence_files'):
            raise ValueError('Missing physical evidence inventory.')
        raw={v['id']:v for v in [json.loads(line) for line in (path.parent/'events.jsonl').read_text(encoding='utf8').splitlines()]}
        if any(raw.get(v['id'])!=v for v in e['events']):raise ValueError('Archive experience differs from original copied events.')
        first=next(v for v in e['events'] if v.get('failure'))
        if a['scope']!=first['scope']:raise ValueError('Archive applicability differs from its original failure.')
        if not validate_guidance(a['guidance'],e): raise ValueError('Pending guidance in index.')
        for f in a.get('failures',[]):
            if f['event_id'] not in raw or f['scope']!=raw[f['event_id']]['scope']:
                raise ValueError('Failure-image scope differs from its specific original event.')
            if f.get('png') and sha(relative(path.parent,f['png']))!=f['png_sha256']: raise ValueError('Failure image archive changed.')
        return a

    def register(self, incident, card, scope, run_folder, *, guidance_source):
        if incident.get('registration')!='eligible' or not validate_guidance(card,incident):
            return {'status':'pending','reason':'incident_or_guidance_not_eligible'}
        if scope!=next(v for v in incident['events'] if v.get('failure'))['scope']:
            raise ValueError('Supplied applicability differs from the original failure scope.')
        installed=[v for v in incident['events'] if v['id']==incident.get('installation_evidence')]
        if len(installed)!=1 or not verified_installation(installed[0]):
            return {'status':'pending','reason':'missing_live_installation_evidence'}
        run_folder=Path(run_folder)
        final=read(run_folder/'final_verification.json')
        preserved=[r for r in final if r['part_id']==incident['part_id']]
        if len(preserved)!=1 or not INSTALL_CHECKS<=set(preserved[0]['checks']) or not all(v is True for v in preserved[0]['checks'].values()):
            return {'status':'pending','reason':'missing_final_preservation_evidence'}
        raw=[json.loads(line) for line in (run_folder/'events.jsonl').read_text(encoding='utf8').splitlines()]
        raw_events={v['id']:v for v in raw}
        if any(raw_events.get(v['id'])!=v for v in incident['events']):
            raise ValueError('Incident events differ from original execution log.')
        cid=digest([incident['run_id'],incident['id']]); destination=self.root/cid
        if destination.exists():
            self._archive({'case_id':cid,'archive_sha256':sha(destination/'archive.json')})
            return {'status':'duplicate','case_id':cid}
        import uuid
        folder=self.root/'.pending'/(cid+'_'+uuid.uuid4().hex[:8]);folder.mkdir(parents=True)
        evidence_files={}
        for name in ('events.jsonl','final_verification.json','resolved_config.json','prepared.json'):
            dest=folder/name; shutil.copyfile(run_folder/name,dest); evidence_files[name]=sha(dest)
        failures=[]
        for event in incident['events']:
            failure=event.get('failure')
            if not failure: continue
            item={'event_id':event['id'],'scope':event.get('scope',scope),'png':None}
            snap=failure.get('failure_snapshot',{})
            if snap.get('status')=='exact_detector_snapshot' and snap.get('png'):
                source=relative(Path(run_folder),snap['png']); dest=folder/(digest(event['id'])+'.png')
                if sha(source)!=snap['png_sha256']: raise ValueError('Failure snapshot modified before registration.')
                shutil.copyfile(source,dest)
                item.update(png=dest.name,png_sha256=sha(dest),camera_version=snap['camera_version'])
                if self.encoder:
                    try:
                        vector=self.encoder.encode(dest)
                        if self.encoder.meta.get('camera')!=snap['camera_version']:
                            item['encoder_error']='camera_version_mismatch; image retained without a visual embedding'
                        else:
                            item.update(embedding=vector.tolist(),encoder_version=self.encoder.version,encoder_meta=self.encoder.meta)
                    except Exception as exc: item['encoder_error']=type(exc).__name__+': '+str(exc)[:150]
            failures.append(item)
        archive={'experience':deepcopy(incident),'guidance':deepcopy(card),'scope':deepcopy(scope),
                 'failures':failures,'guidance_source':guidance_source,'evidence_files':evidence_files}
        write(folder/'archive.json',archive)
        folder.rename(destination)
        # This instance's snapshot is intentionally unchanged until another run.
        return {'status':'registered','case_id':cid,'available_from':'next_run'}

    def retrieve(self, query, *, image=None, image_camera=None, log=None, refs=None):
        started=time.monotonic(); matches=[]; rejected=[]; errors=[]
        for row in self.entries:
            a=self._archive(row)
            for f in a['failures']:
                if f['scope']==query and query.get('first_response'):
                    matches.append((row['case_id'],a,f))
                else: rejected.append({'case_id':row['case_id'],'reason':'required_scope_not_observed'})
        vector=None
        if matches and image and self.encoder:
            try:
                vector=self.encoder.encode(image)
                if self.encoder.meta.get('camera')!=image_camera or image_camera is None:
                    vector=None
                    errors.append({'kind':'camera_version_mismatch','detail':'Query camera must match the frozen encoder camera contract.'})
            except Exception as exc: errors.append({'kind':'encoder_error','detail':str(exc)[:160]})
        scored=[]
        if vector is not None:
            for cid,a,f in matches:
                if f.get('encoder_version')==self.encoder.version and f.get('camera_version')==image_camera and f.get('embedding') is not None:
                    scored.append((float(np.dot(vector,normalized(f['embedding']))),cid,a,f))
        mode='visual' if scored else ('metadata_only' if matches else 'none')
        ranked=sorted(scored,key=lambda r:(-r[0],r[1])) if scored else [(None,c,a,f) for c,a,f in sorted(matches,key=lambda r:r[0])]
        cards=[]; seen=set()
        for score,cid,a,f in ranked:
            if cid in seen: continue
            seen.add(cid)
            cards.append({'record_id':cid,**a['guidance'],'retrieval_mode':mode,'score':score,'matched_failure_id':f['event_id']})
            if len(cards)>=(3 if mode=='visual' else 1): break
        receipt={'query':query,'snapshot_id':self.snapshot['id'],'mode':mode,'returned':cards,
                 'rejected':rejected,'errors':errors,'seconds':time.monotonic()-started,
                 'refs':refs,'image_sha256':sha(image) if image else None,
                 'image_camera_version':image_camera,
                 'encoder_version':getattr(self.encoder,'version',None),
                 'visual_unavailable_reason':None if scored else ('no_applicable_cases' if not matches else
                     'query_image_unavailable' if not image else 'encoder_not_configured' if not self.encoder else
                     'encoder_error' if errors else 'no_matching_encoder_version')}
        if log: append(log,receipt)
        return cards,receipt
