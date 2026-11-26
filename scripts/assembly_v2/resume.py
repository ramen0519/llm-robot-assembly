"""Resume an authenticated, released development boundary; never reset a part."""
from copy import deepcopy
from pathlib import Path
import shutil

import mujoco
import numpy as np

from assembly_v12.contracts import read, write, sha, digest, relative
from assembly_v12.media import STATE, state
from .completion import valid_evidence


def validate_boundary(checkpoint, result, prepared, proofs):
    """Eligibility only. A fresh physical observation remains mandatory."""
    c = checkpoint
    if c.get('source') != 'live_simulation' or c.get('state_spec') != 'mjSTATE_INTEGRATION':
        raise ValueError('Continuation requires a recorded main integration state.')
    installed = set(c.get('installed', []))
    if not installed or installed != set(result.get('installed', [])) or not installed <= set(prepared['order']):
        raise ValueError('Installed set is missing or inconsistent.')
    if result.get('hard_execution_violation'):
        raise ValueError('A hard physical violation cannot be resumed automatically.')
    ctrl = c.get('controller', {})
    if ctrl.get('held_pose') is not None or ctrl.get('slip_reference') is not None:
        raise ValueError('Continuation requires an empty hand, not an uncertain grasp.')
    command = ctrl.get('gripper_command')
    if command is None or not np.isfinite(command) or not 0 <= command < 1:
        raise ValueError('Continuation requires a fully open gripper.')
    release = c.get('release_context')
    if not c.get('completion_context', {}).get('retreat_complete') or (release and not (release.get('opening_complete') and release.get('retreat_complete'))):
        raise ValueError('The recorded release and retreat must be complete.')
    if set(proofs) != installed:
        raise ValueError('Every inherited installed part needs actual main installation evidence.')
    for pid, proof in proofs.items():
        e = proof['event']
        if e.get('part_id') != pid or not e.get('installation_verified') or not e.get('executed') or e.get('source') != 'live_simulation':
            raise ValueError('Copy/planning evidence cannot prove a main installation.')
        v = e.get('verification')
        if not valid_evidence(v, prepared['completion_profile_hash']) or pid not in v['part_ids']:
            raise ValueError('Missing physical installation evidence for ' + pid)
        if v.get('model_sha256') != c['model_sha256']:
            raise ValueError('Inherited installation used a different physical model.')
        for part_id, row in v['parts'].items():
            if not np.allclose(row['goal_pose'], prepared['parts'][part_id]['target'], atol=1e-10):
                raise ValueError('An inherited CAD goal changed.')
    return installed


def load_parent(parent, prepared, folder, budget):
    from .artifacts import verify_bundle, events
    parent = Path(parent).resolve()
    folder = Path(folder)
    bundle = parent / 'replay_bundle'
    budget.check('authenticate_parent')
    manifest = verify_bundle(bundle)
    r = read(bundle / 'result.json')
    p = read(bundle / 'prepared.json')
    if read(bundle / 'audit.json')['status'] != 'passed' or digest(p) != digest(prepared):
        raise ValueError('Parent audit or prepared input does not match.')
    reference = read(bundle / 'reference_verification.json')
    if not valid_evidence(reference, p['completion_profile_hash'], source='reference_scene', required_parts=p['order']):
        raise ValueError('The original completed CAD reference was not verified.')
    c = r.get('terminal_checkpoint')
    if not c or c.get('prepared_sha256') != digest(p):
        raise ValueError('A prepared-state authenticated terminal checkpoint is required.')
    if sha(bundle / 'model.mjb') != c['model_sha256'] or sha(relative(bundle, c['state'])) != c['state_sha256']:
        raise ValueError('The recorded model or state changed.')
    proofs = {}
    if r.get('resume_context'):
        # Inherited evidence is sealed with the previous continuation, including
        # its own re-observation. No missing installation is inferred from pose.
        proofs = read(bundle / 'continuation_source' / 'installation_proofs.json')
        observation = read(bundle / 'restored_boundary_verification.json')
        if not valid_evidence(observation, p['completion_profile_hash'], required_parts=r['resume_context']['installed_at_start']):
            raise ValueError('The parent continuation did not reverify its inherited boundary.')
    for e in events(bundle):
        if e.get('installation_verified'):
            proofs[e['part_id']] = {'run_id': r['run_id'], 'bundle_id': manifest['id'], 'event': e}
    installed = validate_boundary(c, r, p, proofs)
    model = mujoco.MjModel.from_binary_path(str(bundle / 'model.mjb'))
    data = mujoco.MjData(model)
    saved = np.load(relative(bundle, c['state']), allow_pickle=False)
    if saved.shape != (mujoco.mj_stateSize(model, STATE),) or not np.isfinite(saved).all():
        raise ValueError('Invalid integration-state size or nonfinite values.')
    mujoco.mj_setState(model, data, saved, STATE)
    mujoco.mj_forward(model, data)
    if not np.array_equal(saved, state(model, data)):
        raise ValueError('The complete saved integration state did not restore exactly.')
    dest = folder / 'continuation_source'
    dest.mkdir(exist_ok=False)
    write(dest / 'parent_bundle.json', manifest)
    write(dest / 'parent_result.json', r)
    write(dest / 'installation_proofs.json', proofs)
    write(dest / 'checkpoint.json', c)
    shutil.copy2(relative(bundle, c['state']), dest / 'integration_state.npy')
    shutil.copy2(bundle / 'reference_verification.json', folder / 'reference_verification.json')
    context = {'schema': 'released_development_continuation/v1', 'parent_run': str(parent),
        'parent_run_id': r['run_id'], 'parent_bundle_id': manifest['id'],
        'model_sha256': c['model_sha256'], 'state_sha256': c['state_sha256'],
        'prepared_sha256': digest(p), 'installed_at_start': sorted(installed),
        'integration_state_restored_exactly': True, 'fresh_empty_base_episode': False,
        'no_new_installation_claim_from_restore': True, 'policy_source': 'scripted',
        'physical_reobservation': 'not_run'}
    write(folder / 'resume_context.json', context)
    return model, data, c, context


def restore_controller(skills, checkpoint):
    ctrl = checkpoint['controller']
    skills.motion.reference = np.asarray(ctrl['reference'], float)
    skills.motion.gripper_command = float(ctrl['gripper_command'])
    goal = ctrl['goal']
    skills.motion.goal = None if goal is None else {**goal,
        'position_m': np.asarray(goal['position_m']), 'rotation_matrix': np.asarray(goal['rotation_matrix'])}
    skills.retreat_complete = True
    skills.prealignment = deepcopy(checkpoint['completion_context']['prealignment'])
    skills.stage('restored_released_boundary')
    skills.motion.sample(allow_support=True, allow_grasp=False)


def continue_development(parent, folder, *, seconds=None):
    """Explicit API-free development. Finite physics/planner guards stay active."""
    from .budget import Budget, DevelopmentBudget
    from .execution import run
    folder = Path(folder)
    if folder.exists():
        raise ValueError('Use a fresh continuation output; preserve the parent run.')
    p = read(Path(parent) / 'replay_bundle' / 'prepared.json')
    cancel = lambda: (folder / 'cancel.request').exists()
    budget = DevelopmentBudget(cancelled=cancel) if seconds is None else Budget(seconds, cancelled=cancel)
    return run(p, folder, policy_kind='scripted', condition='R0', split='development',
        budget=budget, seconds=seconds, resume_parent=parent,
        no_wall_time_limit=seconds is None)
