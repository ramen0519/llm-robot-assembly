"""Data-driven scene using the unchanged robot, actuators and FrameSkills checks."""
from copy import copy, deepcopy
from pathlib import Path

import mujoco
import numpy as np

from frame_assembly_skills import FrameSkills
from failure_report import FailureEvent
from ur3e_with_gripper import build_robot_spec
from workcell_scene import TABLE_GEOM, compile_workcell, load_config, part_name


def build_scene(prepared):
    p = prepared['profile']
    spec = build_robot_spec()
    spec.body('base').pos = p['robot_base_world_m']
    center, size = prepared['table_center_world_m'], prepared['table_size_xy_m']
    table = spec.worldbody.add_body(name='workcell_table', pos=center)
    table.add_geom(name=TABLE_GEOM, type=mujoco.mjtGeom.mjGEOM_BOX,
                   pos=[0, 0, -.02], size=[size[0]/2, size[1]/2, .02],
                   friction=p['friction'], rgba=[.53, .59, .64, 1])
    base = prepared['base_id']
    registry = {}
    for pid, part in prepared['parts'].items():
        fixed = pid == base
        name = 'assembly_base' if fixed else 'workcell_part_'+pid
        pos = part['target_world_m'] if fixed else part['initial_world_m']
        body = spec.worldbody.add_body(name=name, pos=pos)
        if not fixed:
            body.add_freejoint(name=name+'_free')
            body.add_geom(name=name+'_geom', type=mujoco.mjtGeom.mjGEOM_BOX,
                          size=np.array(part['size'])/2, mass=0, contype=0, conaffinity=0, rgba=[0,0,0,0], group=5)
        mass = p['assumed_cover_mass_kg'] if part['kind'] == 'cover' else p['assumed_mass_kg']
        colors = {'base':[.18,.42,.70,1], 'post':[.95,.55,.08,1], 'sleeve':[.30,.72,.38,1], 'cover':[.75,.35,.65,1]}
        geoms = []
        for box in part['boxes']:
            g = name+'_'+box['name']
            geoms.append(g)
            body.add_geom(name=g, type=mujoco.mjtGeom.mjGEOM_BOX, pos=box['center'], size=np.array(box['size'])/2,
                          mass=0 if fixed else mass*np.prod(box['size'])/part['volume_m3'],
                          friction=p['friction'], rgba=colors[part['kind']], group=2)
        if fixed:
            continue
        c = deepcopy(load_config())
        c['profile_name'] = 'generated_from_verified_cad/v1.2'
        c['table'].update(top_center_world_m=center, size_xy_m=size, yaw_deg=0.)
        c['robot']['base_xy_table_m'] = (np.array(p['robot_base_world_m'][:2])-center[:2]).tolist()
        c['part'].update(id=pid, size_m=part['size'], mass_kg=mass,
                         spawn_xy_table_m=(np.array(pos[:2])-center[:2]).tolist(), yaw_deg=0.)
        c['target'].update(id='mate_'+pid, xy_table_m=(np.array(part['target_world_m'][:2])-center[:2]).tolist(), yaw_deg=0.)
        c['contact']['friction'] = p['friction']
        dims = {'hole_width':part['hole_width'], 'post_width':part['post_width'],
                'cover_height':part['feature'].get('plate_top_z', part['low'][2])-part['low'][2]}
        joint = {'product_candidate':prepared['source_run_hash'], 'dimensions_m':dims,
                 'target_part_center_world_m':part['target_world_m'], 'target_part_rotation_world':np.eye(3).tolist(),
                 'socket_floor_z_world_m':part['floor_world_m'], 'socket_rim_z_world_m':part['rim_world_m'],
                 'nominal_insertion_depth_m':part['nominal_depth_m'],
                 'nominal_side_clearance_m':(part['hole_width']-part['post_width'])/2,
                 'source':'CAD_goal_not_current_observation', 'fixture_status':p['fixture']}
        offset = [0., 0., .006]
        if part['kind'] == 'cover':
            offset = (np.asarray(part['feature']['tab']['center'])-part['center']+[0,0,.006]).tolist()
        registry[pid] = {'config':c, 'joint':joint, 'kind':part['kind'], 'requires':part['requires'],
                         'fixed_base_id':base, 'support_ids':part['support_ids'], 'mating_ids':part['mating_ids'],
                         'collision_geom_names':geoms, 'grasp_offset_part_m':offset,
                         'spawn_center_world_m':part['settled_reference_world_m'],
                         'body_origin_in_cad_m':part['center'], 'body_rotation_in_cad':np.eye(3).tolist()}
        if part['kind'] == 'cover':
            registry[pid]['bore_centers_part_m'] = part['bore_centers_part_m']
    model, data = compile_workcell(spec, free_part_count=len(registry))
    return model, data, registry


class ObservedSkills(FrameSkills):
    """Capture detector-time copy/candidate state before the check copy is lost."""
    def __init__(self, *args, observer=None, source='live_simulation', **kwargs):
        super().__init__(*args, **kwargs)
        self.observer, self.source = observer, source
        if observer:
            for method in ('contacts', 'verify_alignment', 'check_held', 'physical_state', 'verify_other_parts'):
                original = getattr(self.verifier, method)
                def wrapped(data, *a, _original=original, **kw):
                    try:
                        return _original(data, *a, **kw)
                    except FailureEvent as exc:
                        source = 'planner_candidate' if kw.get('planned') else self.source
                        if not hasattr(exc,'v12snapshot'):
                            exc.v12snapshot=observer.failure_snapshot(data, source, self.motion.stage, exc,parent_scene=self.source)
                        raise
                setattr(self.verifier, method, wrapped)

    def _grasp_trial_copy(self):
        return ObservedSkills(self.model, copy(self.data), self.active, self.registry, set(self.installed),
                              transport_strategy=self.transport_strategy, empty_approach_strategy=self.empty_approach_strategy,
                              observer=self.observer, source='mujoco_preflight_copy')

    def check_grasp(self, grasp):
        if self.observer:
            self.observer.last_failure = None
        result = super().check_grasp(grasp)
        if self.observer:
            snap=self.observer.last_failure
            matches=(snap and not result['accepted'] and snap['failure_code']==result.get('failure_code')
                     and result.get('phase','').startswith(snap['phase']))
            result['failure_snapshot'] = self.observer.materialize(snap) if matches else {'status':'unavailable', 'reason':'No matching detector-time state captured'}
        return result
