"""Compound collision geometry and explicit mating relations for a ten-part frame."""
from copy import deepcopy
import mujoco
import numpy as np
from frame_product import product_parts
from insertion_scene import BASE_NAME, BASE_XY_TABLE, insertion_config, add_transfer_test_obstacle
from multi_post_scene import MULTI_DIMENSIONS, SPAWNS
from workcell_scene import build_workcell_spec, compile_workcell, part_name, table_to_world, yaw_quat_wxyz, yaw_rotation, validate_config

# Front tray avoids the measured arm/gripper self-collision of the old rear-left slot.
PART_SPAWNS = [*SPAWNS, (-.28, .05), (.26, -.20), (.13, -.25), (.04, -.25), (.27, .17)]


def build_frame_scene(product_count=6, *, cover_grip='center', transfer_obstacle=False):
    if type(transfer_obstacle) is not bool:
        raise ValueError('The transfer-obstacle option must be boolean.')
    # Scene construction also supports isolated compound-component probes.
    # The full-frame runner controls its separately validated execution scope.
    if cover_grip not in ('center', 'edge'):
        raise ValueError('Unknown cover grip variant')
    if type(product_count) is not int or not 2 <= product_count <= 10:
        raise ValueError('Product count must include base plus one to nine free components.')
    d = MULTI_DIMENSIONS
    config = insertion_config(d)
    spec = build_workcell_spec(config)
    base = spec.worldbody.add_body(name=BASE_NAME, pos=table_to_world(config, [*BASE_XY_TABLE, 0.]),
                                  quat=yaw_quat_wxyz(config['table']['yaw_deg']))
    product = product_parts(d)
    if cover_grip == 'edge':
        next(b for b in product[-1]['boxes'] if b['name'] == 'grip_tab')['center'][1] = -d.base_width / 2 + .015
    for box in product[0]['boxes']:
        base.add_geom(name=BASE_NAME + '_' + box['name'], type=mujoco.mjtGeom.mjGEOM_BOX,
                      pos=box['center'], size=np.array(box['size']) / 2, rgba=[.18, .42, .70, 1.],
                      friction=config['contact']['friction'])
    # This fixture blocks transport while leaving the second socket's descent clear.
    obstacle = add_transfer_test_obstacle(spec, config, center_table_m=[.040, -.150, .115]) if transfer_obstacle else None
    registry = {}
    for i, part in enumerate(product[1:product_count]):
        kind = part['kind']
        boxes = deepcopy(part['boxes'])
        low = np.min([np.array(b['center']) - np.array(b['size']) / 2 for b in boxes], axis=0)
        high = np.max([np.array(b['center']) + np.array(b['size']) / 2 for b in boxes], axis=0)
        origin, size = (low + high) / 2, high - low
        c = deepcopy(config)
        c['profile_name'] = 'compound_frame_front_tray_v2'
        c['part'].update(id=part['id'], size_m=size.tolist(), spawn_xy_table_m=list(PART_SPAWNS[i]),
                         mass_kg=.080 if kind == 'cover' else .040)
        target = np.array([*BASE_XY_TABLE, 0.]) + part['position_product_m'] + origin
        c['target'].update(id='mate_' + part['id'], xy_table_m=target[:2].tolist(), size_xy_m=[.20, .20])
        validate_config(c)
        collision_names = []
        if i == 0:
            collision_names = [part_name(c) + '_geom']
        else:
            body = spec.worldbody.add_body(name=part_name(c), pos=table_to_world(c, [*PART_SPAWNS[i], size[2] / 2 + .005]),
                                          quat=yaw_quat_wxyz(c['table']['yaw_deg']))
            body.add_freejoint(name=part_name(c) + '_free')
            # Named reference only: zero collision bits/mass. Actual holes use convex boxes below.
            body.add_geom(name=part_name(c) + '_geom', type=mujoco.mjtGeom.mjGEOM_BOX,
                          size=size / 2, contype=0, conaffinity=0, mass=0., rgba=[0., 0., 0., 0.])
            volumes = np.array([np.prod(b['size']) for b in boxes])
            for index, box in enumerate(boxes):
                name = part_name(c) + '_' + box['name']
                collision_names.append(name)
                body.add_geom(name=name, type=mujoco.mjtGeom.mjGEOM_BOX, pos=np.array(box['center']) - origin,
                              size=np.array(box['size']) / 2, mass=c['part']['mass_kg'] * volumes[index] / volumes.sum(),
                              rgba=[.95, .55, .08, 1.] if kind == 'post' else ([.30, .72, .38, 1.] if kind == 'sleeve' else [.75, .35, .65, 1.]),
                              friction=c['contact']['friction'])
        if kind == 'post':
            support_ids, mating_ids, rim_z = ['base_floor'], ['base'], d.base_height
            grasp = np.array([0., 0., .006])
        elif kind == 'sleeve':
            support_ids, mating_ids = ['base_top'], ['base', 'post_' + part['id'].split('_')[-1]]
            rim_z = d.socket_floor + d.post_height
            grasp = np.array([0., 0., .006])
        else:
            support_ids = [f'sleeve_{k}' for k in range(1, 5)]
            mating_ids = support_ids + [f'post_{k}' for k in range(1, 5)]
            rim_z = d.socket_floor + d.post_height
            tab = next(b for b in boxes if b['name'] == 'grip_tab')
            grasp = np.array(tab['center']) - origin + [0., 0., .006]
        bottom_target = target[2] - size[2] / 2
        joint = {'product_candidate': 'four_post_frame_wide_v2_center_grip' if cover_grip == 'center' else 'four_post_frame_edge_grip_probe', 'dimensions_m': vars(d),
                 'active_product_parts': product_count, 'eventual_product_parts': 10,
                 'target_part_center_world_m': table_to_world(c, target),
                 'target_part_rotation_world': yaw_rotation(c['table']['yaw_deg']),
                 'socket_floor_z_world_m': c['table']['top_center_world_m'][2] + bottom_target,
                 'socket_rim_z_world_m': c['table']['top_center_world_m'][2] + rim_z,
                 'nominal_insertion_depth_m': rim_z - bottom_target,
                 'nominal_side_clearance_m': (d.hole_width - d.post_width) / 2,
                 'source': 'sim_ground_truth', 'fixture_status': 'base rigidly pre-fixtured',
                 **({'transport_test_obstacle': deepcopy(obstacle)} if obstacle else {})}
        registry[part['id']] = {'config': c, 'joint': joint, 'kind': kind, 'requires': part['requires'],
                              'geometry_representation': 'compound_boxes', 'body_origin_in_cad_m': origin.tolist(),
                              'body_rotation_in_cad': np.eye(3).tolist(), 'collision_geom_names': collision_names,
                              'support_ids': support_ids, 'mating_ids': mating_ids,
                              'grasp_offset_part_m': grasp.tolist(),
                              'spawn_center_world_m': table_to_world(c, [*PART_SPAWNS[i], size[2] / 2])}
    model, data = compile_workcell(spec, free_part_count=product_count - 1)
    return model, data, registry
