"""Mesh-derived collision assets with bounded decomposition and quality evidence."""
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys
import numpy as np
import trimesh
from assembly_v12.contracts import InputError,digest,mesh,read,sha,write


def load_mesh(path,scale=1.):
    v,f=mesh(path)
    m=trimesh.Trimesh(v*scale,f,process=True,validate=True)
    if not m.is_watertight or not m.is_winding_consistent or not np.isfinite(m.vertices).all():
        raise InputError('mesh_invalid','Closed, consistently oriented rigid surface required; no silent hole filling.')
    if m.volume<0:m.invert()
    if m.volume<=1e-15 or not np.isfinite(m.moment_inertia).all():
        raise InputError('mesh_invalid','Cannot derive positive volume and inertia.')
    return m

def sample(m,count,seed):
    pts,_=trimesh.sample.sample_surface(m,count,seed=seed)
    vertices=m.vertices
    if len(vertices)>count:vertices=vertices[np.linspace(0,len(vertices)-1,count,dtype=int)]
    return np.concatenate((pts,vertices))

def surface_distance(a,b,count=2048,seed=0):
    dist=closest_distance(b,sample(a,count,seed))
    return float(np.max(dist))

def closest_distance(mesh,points):
    # Trimesh's absolute barycentric tolerances are poorly conditioned for
    # sub-mm CAD triangles in metre coordinates. Compute proximity in mm and
    # convert measured distances back; this does not change any acceptance limit.
    scaled=mesh.copy();scaled.apply_scale(1000.)
    _,distance,_=trimesh.proximity.closest_point(scaled,np.asarray(points)*1000.)
    return distance/1000.

def geometry_matches(source,native,tolerance=2e-6):
    # Identical indexed triangles prove the same entire surface (including
    # tiny details); avoid redundant sampled proximity tests after OBJ export.
    if (source.vertices.shape == native.vertices.shape and
        np.array_equal(source.faces, native.faces) and
        np.max(np.abs(source.vertices-native.vertices), initial=0.) <= 1e-12):
        return True
    # Different topology/order still requires the original surface/volume checks.
    return (np.max(np.abs(source.bounds-native.bounds))<=tolerance and
            abs(source.volume-native.volume)<=max(1e-12,source.volume*1e-4) and
            max(surface_distance(source,native,1024),surface_distance(native,source,1024,1))<=tolerance)

def winding_inside(mesh,points):
    """Solid-angle winding avoids ambiguous ray hits along shared triangulation edges."""
    result=[];tri=mesh.triangles
    for start in range(0,len(points),128):
        p=points[start:start+128,None,:];a=tri[None,:,0,:]-p;b=tri[None,:,1,:]-p;c=tri[None,:,2,:]-p
        la=np.linalg.norm(a,axis=2);lb=np.linalg.norm(b,axis=2);lc=np.linalg.norm(c,axis=2)
        numerator=np.einsum('ijk,ijk->ij',a,np.cross(b,c))
        denominator=la*lb*lc+np.einsum('ijk,ijk->ij',a,b)*lc+np.einsum('ijk,ijk->ij',b,c)*la+np.einsum('ijk,ijk->ij',c,a)*lb
        angle=np.sum(2*np.arctan2(numerator,denominator),axis=1);result.extend(abs(angle)>2*np.pi)
    return np.asarray(result,bool)

def quality(original,hulls,settings):
    union=trimesh.boolean.union(hulls,engine='manifold',check_volume=True) if len(hulls)>1 else hulls[0]
    if isinstance(union,trimesh.Scene):union=union.to_mesh()
    count=settings['quality_samples'];seed=settings['seed'];tol=settings['maximum_surface_error_m']
    forward=surface_distance(original,union,count,seed)
    collision_points=sample(union,count,seed+1)
    collision_distance=closest_distance(original,collision_points)
    # Decomposition has internal interfaces; they are not an outer collision
    # envelope error. Only samples outside the original closed solid count.
    outside=~winding_inside(original,collision_points)
    reverse=float(collision_distance[outside].max()) if outside.any() else 0.
    volume_error=abs(union.volume-original.volume)/original.volume
    rng=np.random.default_rng(seed)
    lo,hi=original.bounds
    probes=rng.uniform(lo,hi,(count,3))
    # Grid lines through CAD axes also probe central holes that random sampling may miss.
    grid=[]
    for axis in range(3):
        points=np.tile((lo+hi)/2,(65,1));points[:,axis]=np.linspace(lo[axis],hi[axis],65);grid.extend(points)
    probes=np.concatenate((probes,grid))
    distance=closest_distance(original,probes)
    forbidden=probes[(distance>tol)&~winding_inside(original,probes)]
    from scipy.spatial import ConvexHull
    covered=np.zeros(len(forbidden),bool)
    for hull in hulls:
        eq=ConvexHull(hull.vertices).equations
        covered|=np.all(forbidden@eq[:,:3].T+eq[:,3]<=1e-9,axis=1)
    filled=int(covered.sum())
    status='passed' if max(forward,reverse)<=tol and volume_error<=settings['volume_relative_error_max'] and filled==0 else 'failed'
    return {'status':status,'source_to_collision_max_m':forward,'collision_to_source_max_m':reverse,
            'volume_relative_error':float(volume_error),'original_volume_m3':float(original.volume),
            'collision_union_volume_m3':float(union.volume),'empty_space_probes':len(forbidden),'filled_empty_probes':filled,
            'surface_samples_each':count,'limit_m':tol,
            'method':'original_surface_coverage_exterior_union_error_winding_void_probes/v2',
            'limits':'Finite geometric sampling, not a continuous Hausdorff proof; actual assembled and path contact tests are also required.'}

def extrusion_hulls(m, max_hulls=None):
    """Build cap prisms when two planar caps and parallel side walls prove an extrusion."""
    normals = m.face_normals
    first = normals[int(np.argmax(m.area_faces))]
    crosses = np.cross(normals, first)
    lengths = np.linalg.norm(crosses, axis=1)
    j = int(np.argmax(lengths))
    if lengths[j] < 1e-8:
        raise ValueError('No non-parallel faces on the closed solid.')
    # Any extrusion axis is parallel to one of two non-parallel face normals,
    # or perpendicular to both if both faces are on the sides.
    candidates = []
    for axis in (first, normals[j], crosses[j]/lengths[j]):
        alignment = np.abs(normals @ axis)
        if np.any(np.minimum(alignment, 1-alignment) > 1e-7):
            continue
        heights = m.vertices @ axis
        low, high = float(heights.min()), float(heights.max())
        if high-low < 1e-6:
            continue
        surfaces = heights[m.faces]
        lower = np.max(np.abs(surfaces-low),axis=1) < 1e-8
        upper = np.max(np.abs(surfaces-high),axis=1) < 1e-8
        caps = lower | upper
        if (not lower.any() or not upper.any() or np.any(alignment[caps] < 1-1e-8)
                or np.any(alignment[~caps] > 1e-7)):
            continue
        tolerance = max(1e-12, m.volume*1e-6)
        for mask, shift in ((upper, (low-high)*axis), (lower, (high-low)*axis)):
            size = int(mask.sum())
            if max_hulls is not None and size > max_hulls:
                continue
            if abs(m.area_faces[mask].sum()*(high-low) - m.volume) <= tolerance:
                candidates.append((size, mask, shift))
    for _, mask, shift in sorted(candidates, key=lambda row:row[0]):
        hulls = []
        for cap in m.triangles[mask]:
            hulls.append(decomposition_hull(np.concatenate((cap, cap+shift))))
        if abs(sum(h.volume for h in hulls)-m.volume) <= max(1e-12,m.volume*1e-6):
            return hulls
    raise ValueError('No verified constant extrusion within the collision geometry budget.')

def merge_occupied_cells(cells):
    """Partition occupied grid cells into disjoint cuboids without bridging empty cells."""
    axes=cells['axes']
    remaining=cells['occupied'].copy()
    boxes=[]
    for start in np.argwhere(remaining):
        point=tuple(start)
        if not remaining[point]:
            continue
        end=start.copy()+1
        for axis in (0, 1, 2):
            while end[axis] < remaining.shape[axis]:
                face=[slice(int(start[i]),int(end[i])) for i in range(3)]
                face[axis]=int(end[axis])
                if not np.all(remaining[tuple(face)]):
                    break
                end[axis]+=1
        block=tuple(slice(int(start[i]),int(end[i])) for i in range(3))
        remaining[block]=False
        lo=np.array([axes[i][start[i]] for i in range(3)])
        hi=np.array([axes[i][end[i]] for i in range(3)])
        boxes.append({'size':hi-lo, 'center':(lo+hi)/2})
    return boxes

def decomposition_hull(vertices, faces=None):
    from scipy.spatial import ConvexHull, QhullError

    try:
        hull = (trimesh.convex.convex_hull(vertices) if faces is None else
                trimesh.Trimesh(vertices, faces, process=True).convex_hull)
        if hull.is_volume:
            return hull
    except (ValueError, QhullError):
        pass

    # Work on the original CoACD vertices. Absolute triangle tolerances can
    # drop a small boundary face when the same convex solid is expressed in m.
    points = np.asarray(vertices, dtype=float)
    center = points.mean(axis=0)
    span = float(np.ptp(points, axis=0).max())
    if not np.isfinite(span) or span <= 0:
        raise InputError('collision_geometry_invalid', 'Degenerate decomposition piece.')
    local = (points - center) / span
    reference = ConvexHull(local)
    hull = trimesh.convex.convex_hull(local)
    hull.apply_scale(span)
    hull.apply_translation(center)
    if not hull.is_volume or not np.isclose(hull.volume, reference.volume * span**3, rtol=1e-8, atol=0):
        raise InputError('collision_geometry_invalid', 'Could not retain the closed decomposition piece.')
    return hull


def asset_worker(source,folder,settings):
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    m=load_mesh(source);algorithm='convex_exact'
    if m.is_convex:
        hulls=[m.convex_hull]
    else:
        # Exact orthogonal decomposition is an optional geometric optimization,
        # never a required shape label or a product-name branch.
        try:
            from assembly_v12.preparation import solid_cells
            cells=solid_cells(m.vertices,m.faces,settings['max_geometry_cells'],surface_planes_only=True)
            boxes=cells['boxes']
            algorithm='exact_orthogonal_cells'
            if len(boxes)>settings['max_hulls_per_body']:
                boxes=merge_occupied_cells(cells)
                algorithm='exact_orthogonal_merged_cells'
            hulls=[]
            for row in boxes:
                b=trimesh.creation.box(extents=row['size']);b.apply_translation(row['center']);hulls.append(b)
        except (InputError,ValueError,np.linalg.LinAlgError):
            try:
                hulls=extrusion_hulls(m,settings['max_hulls_per_body']);algorithm='verified_cap_triangle_prisms'
            except ValueError:
                import coacd
                coacd.set_log_level('error')
                pieces=coacd.run_coacd(coacd.Mesh(m.vertices,m.faces),threshold=settings['coacd_threshold_m'],
                         real_metric=True,preprocess_mode='off',resolution=2000,mcts_iterations=20,mcts_nodes=10,
                         max_convex_hull=settings['max_hulls_per_body'],seed=settings['seed'],merge=True)
                hulls=[decomposition_hull(v,f) for v,f in pieces]
                algorithm='coacd_real_metric'
    if not hulls or len(hulls)>settings['max_hulls_per_body']:
        raise InputError('collision_geometry_invalid','Collision hull resource limit exhausted.')
    q=quality(m,hulls,settings)
    paths=[]
    for i,hull in enumerate(hulls):
        path=folder/f'hull_{i:04d}.obj';hull.export(path);paths.append({'path':path.name,'sha256':sha(path)})
    visual=folder/'visual.obj';m.export(visual)
    result={'schema':'collision_asset/v2.0','source_mesh_sha256':sha(source),'visual':{'path':visual.name,'sha256':sha(visual)},
            'hulls':paths,'algorithm':algorithm,'settings':settings,'quality':q,
            'versions':{k:importlib.metadata.version(k) for k in ('trimesh','coacd','manifold3d','numpy')},
            'volume_m3':float(m.volume),'center_mass_m':m.center_mass.tolist(),
            'inertia_per_kg':(m.moment_inertia/m.mass).tolist(),'bounds_m':m.bounds.tolist()}
    stable,probabilities=m.compute_stable_poses(sigma=0,n_samples=1,threshold=.001)
    result['stable_poses']=[{'transform':t.tolist(),'probability':float(prob)} for t,prob in zip(stable,probabilities)]
    result['asset_hash']=digest(result);write(folder/'asset.json',result)
    if q['status']!='passed':raise InputError('collision_geometry_invalid','Collision quality failed; inspect asset.json. No physical success may use this approximation.')
    return result

def prepare_asset(source,folder,profile,budget):
    from .asset_cache import reuse
    cached = reuse(source,folder,profile,budget)
    if cached is not None:
        return cached
    settings={**profile['collision'],**{k:profile['resources'][k] for k in ('max_hulls_per_body','max_geometry_cells')}}
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    write(folder/'settings.json',settings)
    with (folder/'asset_process.log').open('w',encoding='utf8') as log:
        environment=dict(os.environ,OMP_NUM_THREADS='2',OPENBLAS_NUM_THREADS='2')
        result=budget.process([sys.executable,'-B','-m','assembly_v2.geometry',str(source),str(folder)],
                  timeout=profile['resources']['decomposition_seconds_per_part'],
                  cwd=Path(__file__).resolve().parents[1],stdout=log,stderr=subprocess.STDOUT,env=environment)
    if result.returncode or not (folder/'asset.json').exists():
        raise InputError('collision_geometry_invalid','Bounded asset preparation failed; see '+str(folder/'asset_process.log'))
    asset=read(folder/'asset.json')
    if asset['quality']['status']!='passed':raise InputError('collision_geometry_invalid','Unverified collision geometry.')
    asset['folder']=str(folder.absolute())
    return asset

if __name__=='__main__':
    asset_worker(sys.argv[1],sys.argv[2],read(Path(sys.argv[2])/'settings.json'))
