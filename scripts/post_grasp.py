"""First upright post. Policy numbers describe the nominal TCP in the part frame."""
from functools import lru_cache
import math

import numpy as np

from workcell_scene import yaw_rotation
from view_tcp import read_tcp_pose


GRASP_FIELDS = ('x_mm', 'y_mm', 'z_mm', 'jaw_yaw_deg')
MAX_PROPOSALS = 3
GRASP_SCHEMA = {'type': 'object', 'properties': {k: {'type': 'number'} for k in GRASP_FIELDS},
                'required': list(GRASP_FIELDS), 'additionalProperties': False}


def numeric_grasp(value):
    return (isinstance(value, dict) and set(value) == set(GRASP_FIELDS)
            and all(type(v) is int or (type(v) is float and math.isfinite(v)) for v in value.values()))


def grasp_geometry(skills):
    size = np.asarray(skills.config['part']['size_m']) * 1000
    tcp_p, tcp_r = read_tcp_pose(skills.data)
    part_r = skills.data.body(skills.verifier.part_body).xmat.reshape(3, 3)
    jaw = part_r.T @ -tcp_r[:, 1]
    pads = []
    for level in (1, 2):
        name = f'gripper_left_pad{level}'
        pad = skills.data.geom(name)
        extent = np.abs(pad.xmat.reshape(3, 3).T @ tcp_r[:, 2]) @ skills.model.geom(name).size
        center = (pad.xpos - tcp_p) @ tcp_r[:, 2]
        pads.append([round(float((center-extent)*1000), 2), round(float((center+extent)*1000), 2)])
    return {'shape': 'upright rectangular post', 'part_size_xyz_mm': size.tolist(),
            'frame': 'origin at geometric center of post; axes follow the post; +z is up',
            'position': 'x_mm,y_mm,z_mm locate nominal TCP inside the part bounding box (millimetres)',
            'orientation': 'top-down approach; jaw_yaw_deg is closing-axis azimuth from part +X toward +Y in [-180,180]',
            'current_jaw_azimuth_deg': round(math.degrees(math.atan2(jaw[1], jaw[0])), 2),
            'open_pad_spans_along_tcp_positive_z_mm': pads,
            'pad_axis_note': 'Positive TCP z points down at grasp. Open pad spans change during closure.',
            'gripper_max_open_mm': 85,
            'socket_insertion_depth_mm': skills.verifier.joint['nominal_insertion_depth_m'] * 1000}


@lru_cache(maxsize=1)
def unloaded_pad_reach_profile():
    """Sample the same 2F-85 linkage closing in the robot-only scene, in its TCP frame."""
    import mujoco
    from move_gripper import command_at, pad_gaps_mm
    from ur3e_with_gripper import load_robot_with_gripper

    model, data = load_robot_with_gripper()
    pads = [model.geom(f'gripper_{side}_pad{level}').id for side in ('left', 'right') for level in (1, 2)]
    samples = []
    for step in range(round(6 / model.opt.timestep)):
        data.actuator('gripper_fingers_actuator').ctrl = command_at(data.time)[0]
        mujoco.mj_step(model, data)
        if step % 10:
            continue
        mujoco.mj_forward(model, data)
        gap = min(pad_gaps_mm(model, data))
        if gap <= 80 - 10*len(samples):
            p, r = read_tcp_pose(data)
            axis = r[:, 2]
            reach = max((data.geom(i).xpos - p) @ axis
                        + np.abs(data.geom(i).xmat.reshape(3, 3).T @ axis) @ model.geom_size[i] for i in pads)
            samples.append((round(gap, 2), round(float(reach*1000), 2)))
            if len(samples) == 8:
                return tuple(samples)
    raise ValueError('Gripper-only closure calibration failed to span the required jaw openings.')


def lowest_pad_above_part_bottom(skills):
    """Project both fingers on the held part's local Z axis, in metres."""
    body = skills.data.body(skills.verifier.part_body)
    axis = body.xmat.reshape(3, 3)[:, 2]
    half_height = skills.config['part']['size_m'][2] / 2
    return min((skills.data.geom(i).xpos - body.xpos) @ axis + half_height
               - np.abs(skills.data.geom(i).xmat.reshape(3, 3).T @ axis) @ skills.model.geom_size[i]
               for i in skills.verifier.pads)


def pad_clearance_at_seating(skills):
    lowest = lowest_pad_above_part_bottom(skills)
    depth = skills.verifier.joint['nominal_insertion_depth_m']
    return {'lowest_pad_above_post_bottom_mm': round(float(lowest * 1000), 2),
            'socket_rim_above_seated_post_bottom_mm': round(depth * 1000, 2),
            'predicted_lowest_pad_height_relative_to_rim_mm': round(float((lowest-depth) * 1000), 2)}


def bounds_issue(grasp, size_m):
    if not numeric_grasp(grasp):
        return 'Expected four finite numeric grasp parameters.'
    for key, limit in zip(GRASP_FIELDS, [*(np.asarray(size_m) * 500), 180]):
        if abs(grasp[key]) > limit:
            return f'{key} outside [{-limit:g}, {limit:g}].'
    return None


def world_grasp(center, part_rotation, grasp, downward):
    offset = np.array([grasp[key] for key in GRASP_FIELDS[:3]]) / 1000.
    rotation = part_rotation @ yaw_rotation(grasp['jaw_yaw_deg']) @ downward
    return center + part_rotation @ offset, rotation
