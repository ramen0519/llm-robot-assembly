"""Compare each archived first sleeve grasp with a memory-assisted first decision."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re

from assembly_graph import run_assembly_graph
from export_grasp_experience import ROOT, digest
from failure_report import json_safe
from frame_assembly_scene import build_frame_scene
from frame_assembly_skills import FrameSkills
from generate_grasp_lesson import _new_run_dir, _write_json
from grasp_candidate_preflight import source_state
from llm_runtime import ApiLedger, create_openai_client, load_llm_config
from openai_policy import OpenAIAssemblyPolicy, compact_context
from run_frame_assembly import episode_boundary, restore_episode_data
from sleeve_grasp_memory import SOURCE_FILES, load_experience

TARGETS = ('sleeve_2', 'sleeve_4')
OUTPUT = ROOT / 'runs/step22q_sleeve_first_grasp'


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def rows(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8-sig').splitlines() if line]


def table_contact(trial):
    if not trial or trial.get('accepted') is not False or trial.get('failure_code') != 'UNEXPECTED_CONTACT' or trial.get('phase') != 'pick_close':
        return False
    for violation in trial.get('violations', []):
        pair = violation.get('geoms', [])
        if len(pair) == 2 and 'workcell_tabletop' in pair and any(re.fullmatch(r'gripper_(left|right)_pad[12]', name) for name in pair):
            return True
    return False


def archived_case(part, archive, events, journal):
    """Join the first saved assembly decision to its prepared and returned model records."""
    candidates = []
    for i, row in enumerate(journal):
        if row['event'] != 'request_prepared' or row.get('agent_role') != 'assembly':
            continue
        request = row['request']
        packet = json.loads(request['input'])
        if packet.get('part_id') != part or packet['observation_revision'] != 1:
            continue
        chain = journal[i:i+6]
        require([e['event'] for e in chain] == ['request_prepared', 'input_count_started', 'input_count_completed',
                'model_request_started', 'model_response_received', 'proposal_validated']
                and all(e.get('agent_role') == 'assembly' for e in chain), 'Incomplete source model call.')
        req_hash = hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()
        returned, validated = chain[-2:]
        require(row['request_hash'] == chain[1]['request_hash'] == chain[3]['request_hash'] == req_hash
                and returned['response_status'] == 'completed' and returned['response_id'] == validated['response_id']
                and returned['response_id'] and returned.get('usage'), 'Source response identity or usage mismatch.')
        candidates.append((packet, validated['proposal'], req_hash, returned['response_id']))
    require(len(candidates) == 1, 'Expected exactly one source first model proposal.')
    packet, action, req_hash, response_id = candidates[0]
    first = [e for e in events if e['active_part_id'] == part and e['observation_revision'] == 1]
    decide, check = ([e for e in first if e['node'] == node] for node in ('decide', 'check_grasp'))
    require(len(decide) == len(check) == 1 and decide[0]['action'] == check[0]['action'] == action
            and decide[0]['active_role'] == 'assembly' and decide[0]['experience_ids'] == []
            and action['skill'] == 'pick' and action['part_id'] == part and action['observation_revision'] == 1
            and action['target_id'] == packet['target_id'] and packet['experiences'] == []
            and packet['completed'] == [] and packet['grasp_task']['previous_trials'] == []
            and packet['recipes'] == [] and packet['active_role'] == 'assembly', 'Archived control mismatch.')
    trial = check[0]['grasp_trial']
    require(trial == archive['graph_states'][part]['grasp_trials'][0] and trial['grasp'] == action['grasp']
            and trial['source'] == 'mujoco_preflight_copy' and trial['observation_revision'] == 1
            and trial['live_start_s'] == packet['observation']['simulation_time_s'] and table_contact(trial),
            'Archived baseline does not document the declared contact.')
    return {'input': packet, 'action': action, 'request_hash': req_hash, 'response_id': response_id,
            'source_trial': trial, 'start': archive['part_episodes'][part]['start']}


class ComparedPolicy:
    def __init__(self, case, folder, actual=None):
        self.case, self.folder, self.actual = case, folder, actual
        self.calls = 0
        self.searched = False
        self.experience_ids = []

    def check_input(self, context):
        packet = compact_context(context)
        control = deepcopy(packet)
        control['experiences'] = []
        require(control == self.case['input'], 'Fresh first-decision context differs from the archived control.')
        return packet

    def search_before_grasp(self, context):
        packet = self.check_input(context)
        require(self.actual is not None and not self.searched and packet['experiences'] == [], 'Unexpected search.')
        self.searched = True
        _write_json(self.folder / 'search_input.json', packet)
        return self.actual.search_before_grasp(context)

    def propose(self, context):
        packet = self.check_input(context)
        require(self.calls == 0, 'Second grasp decision is outside this experiment.')
        self.calls += 1
        self.experience_ids = [v['record_id'] for v in packet['experiences']]
        _write_json(self.folder / 'decision_input.json', packet)
        if self.actual is None:
            require(not self.experience_ids, 'Archived control received memory.')
            action = deepcopy(self.case['action'])
        else:
            action = self.actual.propose(context)
        _write_json(self.folder / 'decision.json', action)
        return action


class NoRecovery:
    def propose(self, context):
        raise ValueError('Recovery reached during a first-grasp-only experiment.')


def boundary_summary(state):
    return {k: state[k] for k in ('model_sha256','integration_sha256','host_sha256','simulation_time_s')}


def run_arm(part, case, config, *, folder, registry, model, actual=None, memory=None):
    from sleeve_memory_policy import FirstGraspMemory
    folder.mkdir(parents=True, exist_ok=False)
    start = case['start']
    installed = set(start['installed'])
    data = restore_episode_data(model, start)
    require(episode_boundary(model,data,registry,installed) == start, 'Full saved and restored boundaries differ.')
    _write_json(folder / 'restored_start.json', start)
    skills = FrameSkills(model,data,part,registry,installed,transport_strategy=config['transport_strategy'],
                         empty_approach_strategy=config['empty_approach_strategy'])
    policy = ComparedPolicy(case, folder, actual)
    before = None
    def event(row):
        nonlocal before
        if row['node'] == 'settle' and row['status'] == 'running':
            before = boundary_summary(source_state(skills))
            _write_json(folder / 'settled_state.json', before)
        with (folder / 'graph_events.jsonl').open('a',encoding='utf-8') as stream:
            stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False)+'\n')
        if row['node'] == 'execute' and row['action']['skill'] == 'stop':
            return
        require(row['node'] not in ('execute','verify','recover_grasp'), 'Unexpected original-scene action.')
    def fail(exc):
        return {'status':'failed', 'phase':getattr(exc,'phase',None), 'message':str(exc),
                'failure_code':getattr(getattr(exc,'code',None),'value',type(exc).__name__),
                'violations':json_safe(getattr(exc,'violations',[]))}
    entry = {'part_id':part, 'policy_source':'archived_22P_control' if actual is None else 'new_assembly_model',
             'status':'error', 'trial':None, 'action':None, 'experience_ids':[], 'searched':False,
             'target_contact':None, 'original_state_unchanged':None}
    try:
        memory_hook = FirstGraspMemory(memory, folder/'memory', folder.parent.parent.name+'-'+part) if actual is not None else None
        packet = case['input']
        state = run_assembly_graph(skills, failure_handler=fail, mode='roles', policies={'assembly':policy,'recovery':NoRecovery()},
            recovery_budget=packet['recovery_budget_remaining'], transport_replan_budget=packet['transport_replan_budget_remaining'],
            scenario='nominal', on_event=event, task_id=packet['task_id'], insert_result_skill='insert_sleeve/v1',
            propose_grasp=True, finish_on_verified_insert=True, first_grasp_only=True, first_grasp_memory=memory_hook)
        _write_json(folder/'graph_state.json',state)
        trials = state['grasp_trials']
        require(not state['completed'] and len(trials)<=1 and len(skills.results)==0, 'Unexpected original pick, insert or retry.')
        action = state.get('action')
        entry.update(action=action, searched=policy.searched, experience_ids=policy.experience_ids,
                     status=state['status'], error=state.get('error'), settled=before)
        if state['status']=='first_grasp_checked':
            require(len(trials)==1 and action and trials[0]['grasp']==action['grasp']
                    and trials[0]['observation_revision']==action['observation_revision']==1
                    and trials[0]['source']=='mujoco_preflight_copy' and type(trials[0]['accepted']) is bool,
                    'First copy preflight does not match the decision.')
            trial=trials[0]
            _write_json(folder/'first_trial.json',trial)
            entry.update(trial=trial,target_contact=table_contact(trial))
        elif trials:
            entry['trial']=trials[0]
        return entry
    finally:
        after=boundary_summary(source_state(skills))
        entry['terminal_state']=after
        entry['original_state_unchanged']=before is not None and before==after
        if not entry['original_state_unchanged']:
            entry.update(status='error', error='Source scene or executor changed after settling.')
        _write_json(folder/'outcome.json',entry)


def counts(entries):
    checked=[v for v in entries if v['status']=='first_grasp_checked' and v.get('trial') is not None and v['original_state_unchanged']]
    return {'targets_started':len(entries), 'first_candidates_checked':len(checked),
            'preflight_accepted':sum(t['trial']['accepted'] for t in checked),
            'target_pad_table_contacts':sum(t['target_contact'] is True for t in checked),
            'other_preflight_rejections':sum(not t['trial']['accepted'] and not t['target_contact'] for t in checked),
            'memory_hits':sum(bool(t['experience_ids']) for t in entries),
            'memory_search_misses':sum(t['searched'] and not t['experience_ids'] for t in entries),
            'stopped':sum(t['status']=='stopped' for t in entries),
            'failed_or_incomplete':sum(t['status'] not in ('first_grasp_checked','stopped') for t in entries)}


def run_trial(source_run, memory_path=None, llm_config=None, *, baseline_only=False, output_root=OUTPUT):
    """Evaluate fresh first grasp proposals; the archived target decisions go only to the control."""
    source=Path(source_run).resolve()
    require(not Path(output_root).resolve().is_relative_to(source), 'Experiment output must be outside the archive.')
    run=_new_run_dir(output_root)
    result={'schema':'sleeve_first_grasp_comparison/v1','status':'partial','run_dir':str(run),
            'source_run':str(source),'targets':list(TARGETS),'baseline_only':baseline_only,
            'baseline_method':'fresh_copy_physics_archived_decision',
            'baseline':{},'treatment':{},'API_calls':0,'original_scene_assembly_executed':False}
    ledger=client=None
    try:
        hashes={name:hashlib.sha256((source/name).read_bytes()).hexdigest() for name in SOURCE_FILES}
        archive, cfg = read(source/'result.json'),read(source/'resolved_config.json')
        graph_file=source/'graph_events.jsonl'
        result['archive_sha256']={**hashes,'graph_events.jsonl':hashlib.sha256(graph_file.read_bytes()).hexdigest()}
        graph,journal=rows(graph_file),rows(source/'llm_events.jsonl')
        cases={p:archived_case(p,archive,graph,journal) for p in TARGETS}
        model,_,registry=build_frame_scene(product_count=10,transfer_obstacle=cfg['transfer_obstacle'])
        require(json_safe(registry)==cfg['registry'],'Restored registry differs from saved scene.')
        for p,case in cases.items():
            folder=run/'baseline'/p
            entry=run_arm(p,case,cfg,folder=folder,registry=registry,model=model)
            result['baseline'][p]=entry
            require(entry['status']=='first_grasp_checked' and entry['target_contact'] and entry['original_state_unchanged']
                    and entry['trial']==case['source_trial'],'Archived first rejection did not reproduce exactly.')
            _write_json(folder/'archive_receipt.json', {k:case[k] for k in ('request_hash','response_id','action','source_trial')})
        if baseline_only:
            result['status']='baseline_complete'
            return result
        record=load_experience(memory_path,source_run=source)
        config=load_llm_config(llm_config).validate(live=True)
        require(config.model==cfg['llm_config']['model'] and config.reasoning_effort==cfg['llm_config']['reasoning_effort'],
                'Live trial must use the same model and reasoning effort as the control.')
        _write_json(run/'config_public.json',config.public())
        _write_json(run/'experience_identity.json',{'experience_id':record['experience_id'],'source_part':record['source_part']})
        ledger=ApiLedger(config,run/'llm_events.jsonl')
        client=create_openai_client(config)
        for p,case in cases.items():
            entry=run_arm(p,case,cfg,folder=run/'treatment'/p,registry=registry,model=model,
                          actual=OpenAIAssemblyPolicy(config,client,ledger,role='assembly'),memory=record)
            result['treatment'][p]=entry
            require(entry.get('settled')==result['baseline'][p]['settled'] and entry['original_state_unchanged'],
                    'Paired settle state or terminal source state differs.')
            if ledger.halted or entry['status'] not in ('first_grasp_checked','stopped'):
                break
        result['status']='complete' if len(result['treatment'])==len(TARGETS) and all(
            v['status']=='first_grasp_checked' for v in result['treatment'].values()) else 'partial'
    except (Exception, KeyboardInterrupt) as exc:
        result['error']={'type':type(exc).__name__,'message':str(exc)[:600]}
    finally:
        if client is not None:
            client.close()
        result['counts']={name:counts(list(result[name].values())) for name in ('baseline','treatment')}
        result['ledger']=ledger.summary() if ledger else None
        result['API_calls']=ledger.model_requests if ledger else 0
        _write_json(run/'result.json',result)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-run',type=Path,required=True)
    parser.add_argument('--memory',type=Path)
    parser.add_argument('--llm-config',type=Path)
    parser.add_argument('--baseline-only',action='store_true')
    parser.add_argument('--output-root',type=Path,default=OUTPUT)
    args=parser.parse_args()
    if not args.baseline_only and (not args.memory or not args.llm_config):
        parser.error('The live arm requires --memory and --llm-config.')
    result=run_trial(args.source_run,args.memory,args.llm_config,baseline_only=args.baseline_only,
                     output_root=args.output_root)
    print(json.dumps({k:result.get(k) for k in ('run_dir','status','counts','API_calls','error')},ensure_ascii=False),flush=True)
    return 0 if result['status'] in ('baseline_complete','complete') else 1


if __name__=='__main__':
    raise SystemExit(main())
