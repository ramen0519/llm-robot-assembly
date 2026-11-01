"""One free post and the fixed four-hole base; no automatic assembly attachment."""
import copy
import mujoco
import numpy as np

from frame_product import FrameDimensions, product_parts
from workcell_scene import (build_workcell_spec, compile_workcell, load_config, table_to_world,
                            yaw_quat_wxyz, yaw_rotation, validate_config)

BASE_NAME = 'assembly_base'
BASE_XY_TABLE = [-.12, -.12]


def insertion_config(dim=None):
    d = dim or FrameDimensions()
    d.validate()
    config = copy.deepcopy(load_config())
    config['profile_name'] = 'four_post_frame_first_joint_example'
    config['part'].update(id='post_1', size_m=[d.post_width, d.post_width, d.post_height], mass_kg=.040)
    config['target'].update(id='socket_1', xy_table_m=[v - d.post_spacing / 2 for v in BASE_XY_TABLE])
    validate_config(config)
    return config


def add_transfer_test_obstacle(spec, config, *, center_table_m=None):
    """Fixed development fixture, present from scene initialization."""
    center = np.asarray([-.010, -.125, .115] if center_table_m is None else center_table_m, dtype=float)
    if center.shape != (3,) or not np.isfinite(center).all():
        raise ValueError('Test obstacle needs a finite table-frame XYZ center.')
    obstacle = {'name': 'assembly_transfer_obstacle', 'kind': 'fixed_test_sphere',
                'center_table_m': center.tolist(), 'radius_m': .025, 'margin_m': .006}
    center = table_to_world(config, obstacle['center_table_m'])
    obstacle['center_world_m'] = center.tolist()
    fixture = spec.worldbody.add_body(name=obstacle['name'], pos=center)
    fixture.add_geom(name=obstacle['name'] + '_geom', type=mujoco.mjtGeom.mjGEOM_SPHERE,
                     size=[obstacle['radius_m'], 0., 0.], margin=obstacle['margin_m'],
                     rgba=[.9, .15, .12, 1.])
    return obstacle


def build_insertion_scene(config=None, dim=None, *, transfer_obstacle=False, obstacle_center_table_m=None):
    if type(transfer_obstacle) is not bool:
        raise ValueError('transfer_obstacle must be a boolean test-scene option.')
    if obstacle_center_table_m is not None and not transfer_obstacle:
        raise ValueError('Obstacle coordinates require a declared obstacle.')
    d = dim or FrameDimensions()
    d.validate()
    config = config or insertion_config(d)
    if not np.allclose(config['part']['size_m'], [d.post_width, d.post_width, d.post_height]):
        raise ValueError('Post size must match product dimensions.')
    expected_xy = np.array(BASE_XY_TABLE) - d.post_spacing / 2
    if not np.allclose(config['target']['xy_table_m'], expected_xy) or config['target']['yaw_deg'] != 0:
        raise ValueError('This scene supports the first fixed product socket only.')
    spec = build_workcell_spec(config)
    body = spec.worldbody.add_body(name=BASE_NAME, pos=table_to_world(config, [*BASE_XY_TABLE, 0.]),
                                  quat=yaw_quat_wxyz(config['table']['yaw_deg']))
    for box in product_parts(d)[0]['boxes']:
        body.add_geom(name=BASE_NAME + '_' + box['name'], type=mujoco.mjtGeom.mjGEOM_BOX,
                      pos=box['center'], size=np.array(box['size']) / 2,
                      rgba=[.18, .42, .70, 1.], friction=config['contact']['friction'])
    obstacle = add_transfer_test_obstacle(spec, config, center_table_m=obstacle_center_table_m) if transfer_obstacle else None
    model, data = compile_workcell(spec)
    target = table_to_world(config, [*config['target']['xy_table_m'], d.socket_floor + d.post_height / 2])
    return model, data, config, {
        'product_candidate': 'four_post_frame_v1', 'active_product_parts': 2, 'eventual_product_parts': 10,
        'fixed_product_parts': ['base'], 'free_product_parts': [config['part']['id']],
        'target_part_center_world_m': target, 'target_part_rotation_world': yaw_rotation(config['table']['yaw_deg']),
        'socket_floor_z_world_m': config['table']['top_center_world_m'][2] + d.socket_floor,
        'socket_rim_z_world_m': config['table']['top_center_world_m'][2] + d.base_height,
        'nominal_insertion_depth_m': d.base_height - d.socket_floor,
        'nominal_side_clearance_m': (d.hole_width - d.post_width) / 2,
        'source': 'sim_ground_truth', 'fixture_status': 'base rigidly pre-fixtured',
        **({'transport_test_obstacle': obstacle} if obstacle else {}),
    }
