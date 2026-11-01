"""Declared initial scene variations; no runtime state edits and no held-out claims."""
from copy import deepcopy
from insertion_scene import build_insertion_scene, insertion_config
from workcell_scene import validate_config

SCENE_CASES = {
    'nominal': {'tray_delta_xy_m': [0., 0.], 'obstacle_center_table_m': None},
    'tray_x_plus_10mm': {'tray_delta_xy_m': [.010, 0.], 'obstacle_center_table_m': None},
    'tray_y_plus_10mm': {'tray_delta_xy_m': [0., .010], 'obstacle_center_table_m': None},
    'obstacle_x_plus_10mm': {'tray_delta_xy_m': [0., 0.], 'obstacle_center_table_m': [0., -.125, .115]},
}


def build_case(case_id='nominal', *, transfer_obstacle=False):
    if case_id not in SCENE_CASES:
        raise ValueError('Unknown declared development scene.')
    case = deepcopy(SCENE_CASES[case_id])
    if case['obstacle_center_table_m'] is not None and not transfer_obstacle:
        raise ValueError('Moved obstacle case requires --transfer-obstacle.')
    config = insertion_config()
    config['part']['spawn_xy_table_m'] = [x + d for x, d in zip(config['part']['spawn_xy_table_m'], case['tray_delta_xy_m'])]
    validate_config(config)
    return build_insertion_scene(config=config, transfer_obstacle=transfer_obstacle,
                                 obstacle_center_table_m=case['obstacle_center_table_m'])
