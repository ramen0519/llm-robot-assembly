"""Parametric ten-part development candidate; all geometry is expressed in metres.

The base is pre-fixtured. Nine parts still require robot installation.
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
    for i, (a, b) in enumerate(zip(xs, xs[1:])):
        for j, (c, d) in enumerate(zip(ys, ys[1:])):
            x, y = (a + b) / 2, (c + d) / 2
            if any(abs(x - hx) < hole_width / 2 - 1e-10 and abs(y - hy) < hole_width / 2 - 1e-10 for hx, hy in centers):
                continue
            boxes.append({'name': f'wall_{i}_{j}', 'center': [x, y, (height + floor) / 2],
                          'size': [b - a, d - c, height - floor]})
    return boxes


def product_parts(dim=None):
    d = dim or FrameDimensions()
    d.validate()
    centers = [(x * d.post_spacing / 2, y * d.post_spacing / 2) for x, y in ((-1, -1), (1, -1), (-1, 1), (1, 1))]
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
    cover.append({'name': 'grip_tab', 'center': [0., -d.base_width / 2 + .015, d.cover_height + .015],
                  'size': [.030, .025, .030]})
    parts.append({'id': 'cover', 'kind': 'cover', 'fixed': False,
                  'position_product_m': [0., 0., d.base_height + d.sleeve_height],
                  'requires': [f'sleeve_{i}' for i in range(1, 5)] + [f'post_{i}' for i in range(1, 5)],
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


def export_product(output_dir, dim=None):
    d = dim or FrameDimensions()
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    # Never silently replace an experiment's CAD.
    if any(output.iterdir()):
        raise ValueError('CAD output directory must be empty.')
    manifest = {'schema_version': 1, 'candidate_id': 'four_post_frame_v1', 'units': 'metre',
                'frame': 'product_base_bottom_center', 'product_parts': 10, 'robot_installations': 9,
                'status': 'development_candidate', 'robot_execution_validated': False,
                'fixture': 'base rigidly pre-fixtured; fixture engineering not validated',
                'dimensions_m': vars(d), 'parts': []}
    for part in product_parts(d):
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
    args = parser.parse_args()
    result = export_product(args.output)
    print(json.dumps({'status': result['status'], 'parts': len(result['parts']), 'output': str(args.output)}))
