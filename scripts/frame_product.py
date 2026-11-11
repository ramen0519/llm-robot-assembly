"""Parametric development frames; all geometry is expressed in metres.

The base is pre-fixtured. Each post, sleeve and cover is a separate product part.
CAD is generated from the same disjoint boxes used for contact geometry,
so square holes never become filled convex mesh collision shapes.
"""
import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path

import manifold3d as m3d
import numpy as np


FRAME_LAYOUTS = {
    'four_post': ((-1, -1), (1, -1), (-1, 1), (1, 1)),
    'triangle_three_post': ((-1, -1), (1, -1), (0, 1)),
}
TRIANGLE_CANDIDATE_ID = 'triangle_three_post_v1'


@dataclass(frozen=True)
class FrameDimensions:
    base_width: float = .120
    base_height: float = .020
    socket_floor: float = .004
    post_spacing: float = .060
    hole_width: float = .024
    post_width: float = .020
    post_height: float = .060
    sleeve_width: float = .036
    sleeve_height: float = .030
    cover_height: float = .008

    def validate(self):
        values = list(vars(self).values())
        if not all(type(v) in (int, float) and np.isfinite(v) and v > 0 for v in values):
            raise ValueError('Dimensions must be finite positive metre values.')
        if not (self.socket_floor < self.base_height < self.post_height
                and self.post_width < self.hole_width < self.sleeve_width < self.post_spacing
                and self.post_spacing + self.hole_width < self.base_width
                and self.base_height + self.sleeve_height + self.cover_height < self.socket_floor + self.post_height):
            raise ValueError('Invalid mating geometry or insufficient post engagement.')


def perforated_plate(width, height, hole_width, centers, floor=0.):
    """Non-overlapping box cells in a plate-local frame; origin at bottom centre."""
    xs = sorted(set([-width / 2, width / 2] + [x + s * hole_width / 2 for x, _ in centers for s in (-1, 1)]))
    ys = sorted(set([-width / 2, width / 2] + [y + s * hole_width / 2 for _, y in centers for s in (-1, 1)]))
    boxes = []
    if floor:
        boxes.append({'name': 'floor', 'center': [0., 0., floor / 2], 'size': [width, width, floor]})
    # Merge consecutive solid cells along X. The solid volume and open holes
    # stay identical, while rings use four bars instead of eight small boxes.
    for j, (c, e) in enumerate(zip(ys, ys[1:])):
        y = (c + e) / 2
        run_start = None
        for i in range(len(xs)):
            solid_cell = False
            if i < len(xs) - 1:
                x = (xs[i] + xs[i + 1]) / 2
                solid_cell = not any(abs(x - hx) < hole_width / 2 - 1e-10 and abs(y - hy) < hole_width / 2 - 1e-10
                                     for hx, hy in centers)
            if solid_cell and run_start is None:
                run_start = i
            if not solid_cell and run_start is not None:
                a, b = xs[run_start], xs[i]
                boxes.append({'name': f'wall_{run_start}_{j}', 'center': [(a + b) / 2, y, (height + floor) / 2],
                              'size': [b - a, e - c, height - floor]})
                run_start = None
    return boxes


def frame_hole_centers(d, layout='four_post'):
    if layout not in FRAME_LAYOUTS:
        raise ValueError(f'Unknown frame layout: {layout}')
    return [(x * d.post_spacing / 2, y * d.post_spacing / 2) for x, y in FRAME_LAYOUTS[layout]]


def product_parts(dim=None, layout='four_post'):
    d = dim or FrameDimensions()
    d.validate()
    centers = frame_hole_centers(d, layout)
    parts = [{'id': 'base', 'kind': 'base', 'fixed': True, 'position_product_m': [0., 0., 0.],
              'requires': [], 'boxes': perforated_plate(d.base_width, d.base_height, d.hole_width, centers, d.socket_floor)}]
    for i, (x, y) in enumerate(centers, 1):
        parts.append({'id': f'post_{i}', 'kind': 'post', 'fixed': False,
                      'position_product_m': [x, y, d.socket_floor + d.post_height / 2], 'requires': ['base'],
                      'boxes': [{'name': 'solid', 'center': [0., 0., 0.], 'size': [d.post_width, d.post_width, d.post_height]}]})
    for i, (x, y) in enumerate(centers, 1):
        parts.append({'id': f'sleeve_{i}', 'kind': 'sleeve', 'fixed': False,
                      'position_product_m': [x, y, d.base_height], 'requires': ['base', f'post_{i}'],
                      'boxes': perforated_plate(d.sleeve_width, d.sleeve_height, d.hole_width, [(0., 0.)])})
    # Integrated raised tab is part of the cover, not an eleventh product part.
    cover = perforated_plate(d.base_width, d.cover_height, d.hole_width, centers)
    cover.append({'name': 'grip_tab', 'center': [0., 0., d.cover_height + .015],
                  'size': [.030, .025, .030]})
    parts.append({'id': 'cover', 'kind': 'cover', 'fixed': False,
                  'position_product_m': [0., 0., d.base_height + d.sleeve_height],
                  'requires': [f'{kind}_{i}' for kind in ('sleeve', 'post') for i in range(1, len(centers) + 1)],
                  'boxes': cover})
    return parts


def solid_for(part):
    solid = m3d.Manifold()
    for box in part['boxes']:
        # Build booleans in millimetres to make shared decimal faces coincide.
        size_mm = np.round(np.asarray(box['size']) * 1000, 8)
        center_mm = np.round(np.asarray(box['center']) * 1000, 8)
        solid = solid + m3d.Manifold.cube(size_mm, center=True).translate(center_mm)
    if solid.status() != m3d.Error.NoError or solid.is_empty() or len(solid.decompose()) != 1:
        raise ValueError('CAD must be one connected valid solid per product part.')
    return solid.scale((.001, .001, .001))


def export_product(output_dir, dim=None, layout='four_post'):
    d = dim or FrameDimensions()
    parts = product_parts(d, layout)
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    # Never silently replace an experiment's CAD.
    if any(output.iterdir()):
        raise ValueError('CAD output directory must be empty.')
    manifest = {'schema_version': 1, 'candidate_id': 'four_post_frame_v2_center_grip' if layout == 'four_post' else TRIANGLE_CANDIDATE_ID,
                'units': 'metre', 'frame': 'product_base_bottom_center', 'product_parts': len(parts), 'robot_installations': len(parts) - 1,
                'status': 'development_candidate', 'robot_execution_validated': False,
                'fixture': 'base rigidly pre-fixtured; fixture engineering not validated',
                'dimensions_m': vars(d), 'parts': []}
    for part in parts:
        solid = solid_for(part)
        mesh = solid.to_mesh()
        vertices, faces = np.asarray(mesh.vert_properties[:, :3]), np.asarray(mesh.tri_verts)
        path = output / (part['id'] + '.obj')
        with path.open('w', encoding='utf-8', newline='\n') as stream:
            stream.write('# metre units; local part frame; assembly transform in manifest.json\n')
            for v in vertices:
                stream.write('v ' + ' '.join(f'{x:.12g}' for x in v) + '\n')
            for f in faces:
                stream.write('f ' + ' '.join(str(int(x) + 1) for x in f) + '\n')
        pose = np.eye(4)
        pose[:3, 3] = part['position_product_m']
        manifest['parts'].append({k: v for k, v in part.items() if k != 'boxes'} | {
            'assembled_pose_product': pose.tolist(), 'obj': path.name,
            'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'volume_m3': solid.volume(),
            'triangles': len(faces), 'convex_collision_components': len(part['boxes'])})
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2, ensure_ascii=False, allow_nan=False), encoding='utf-8')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--layout', choices=FRAME_LAYOUTS, default='four_post')
    args = parser.parse_args()
    result = export_product(args.output, layout=args.layout)
    print(json.dumps({'status': result['status'], 'parts': len(result['parts']), 'output': str(args.output)}))
