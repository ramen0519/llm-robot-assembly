"""Two-to-four free posts present from episode start; one pre-fixtured product base."""
from copy import deepcopy
import mujoco
import numpy as np
from frame_product import FrameDimensions, product_parts
from insertion_scene import BASE_NAME, BASE_XY_TABLE, insertion_config
from workcell_scene import build_workcell_spec, compile_workcell, part_name, table_to_world, yaw_quat_wxyz, yaw_rotation

# Wider development variant provides room to open the gripper beside installed posts.
MULTI_DIMENSIONS = FrameDimensions(base_width=.160, post_spacing=.080)
SPAWNS = [( .13, -.10), (.26, -.10), (.13, .04), (.26, .04)]


def build_multi_post_scene(count=2):
    if type(count) is not int or count not in (2, 3, 4):
        raise ValueError('This milestone supports two to four posts.')
    d = MULTI_DIMENSIONS
    config = insertion_config(d)
    spec = build_workcell_spec(config)
    base = spec.worldbody.add_body(name=BASE_NAME, pos=table_to_world(config, [*BASE_XY_TABLE, 0.]),
                                  quat=yaw_quat_wxyz(config['table']['yaw_deg']))
    for box in product_parts(d)[0]['boxes']:
        base.add_geom(name=BASE_NAME + '_' + box['name'], type=mujoco.mjtGeom.mjGEOM_BOX,
                      pos=box['center'], size=np.array(box['size']) / 2,
                      rgba=[.18, .42, .70, 1.], friction=config['contact']['friction'])
    registry = {}
    posts = [p for p in product_parts(d) if p['kind'] == 'post'][:count]
    for i, part in enumerate(posts):
        c = deepcopy(config)
        c['part'].update(id=part['id'], spawn_xy_table_m=list(SPAWNS[i]))
        xy = np.array(BASE_XY_TABLE) + np.array(part['position_product_m'][:2])
        c['target'].update(id=f'socket_{i + 1}', xy_table_m=xy.tolist())
        if i:
            body = spec.worldbody.add_body(name=part_name(c), pos=table_to_world(c, [*SPAWNS[i], d.post_height / 2 + .005]),
                                          quat=yaw_quat_wxyz(c['table']['yaw_deg']))
            body.add_freejoint(name=part_name(c) + '_free')
            body.add_geom(name=part_name(c) + '_geom', type=mujoco.mjtGeom.mjGEOM_BOX,
                          size=[d.post_width / 2, d.post_width / 2, d.post_height / 2], mass=.040,
                          rgba=[.95, .44 + i * .1, .08, 1.], friction=c['contact']['friction'])
        joint = {'product_candidate': 'four_post_frame_wide_v1', 'dimensions_m': vars(d),
                 'active_product_parts': count + 1, 'eventual_product_parts': 10,
                 'fixed_product_parts': ['base'], 'free_product_parts': [p['id'] for p in posts],
                 'target_part_center_world_m': table_to_world(c, [*xy, d.socket_floor + d.post_height / 2]),
                 'target_part_rotation_world': yaw_rotation(c['table']['yaw_deg']),
                 'socket_floor_z_world_m': c['table']['top_center_world_m'][2] + d.socket_floor,
                 'socket_rim_z_world_m': c['table']['top_center_world_m'][2] + d.base_height,
                 'nominal_insertion_depth_m': d.base_height - d.socket_floor,
                 'nominal_side_clearance_m': (d.hole_width - d.post_width) / 2,
                 'source': 'sim_ground_truth', 'fixture_status': 'base rigidly pre-fixtured'}
        registry[part['id']] = {'config': c, 'joint': joint,
                              'spawn_center_world_m': table_to_world(c, [*SPAWNS[i], d.post_height / 2])}
    model, data = compile_workcell(spec, free_part_count=count)
    return model, data, registry
