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
    # Topology/vertex order need not match, but both placed surfaces must match.
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

def extrusion_hulls(m):
    """Exact cap-triangle prisms when the surface proves a constant extrusion.

    This is a geometric optimization, not a cylinder/box/product classifier.
    The union is still subjected to all original-surface/hole quality checks.
    """
    axes=[]
    for normal in m.face_normals:
        if not any(abs(float(np.dot(normal,a)))>1-1e-8 for a in axes):axes.append(normal)
    for axis in axes:
        heights=m.vertices@axis;low,high=heights.min(),heights.max()
        if high-low<1e-6 or np.any(np.minimum(abs(heights-low),abs(heights-high))>1e-8):continue
        faces=m.faces[np.all(abs(heights[m.faces]-high)<1e-8,axis=1)]
        if not len(faces):continue
        result=[]
        for face in faces:
            top=m.vertices[face];bottom=top-axis*(high-low)
            result.append(trimesh.convex.convex_hull(np.concatenate([top,bottom])))
        if abs(sum(h.volume for h in result)-m.volume)<=max(1e-12,m.volume*1e-6):return result
    raise ValueError('No verified constant extrusion.')

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
            cells=solid_cells(m.vertices,m.faces,settings['max_geometry_cells'])
            hulls=[]
            for row in cells['boxes']:
                b=trimesh.creation.box(extents=row['size']);b.apply_translation(row['center']);hulls.append(b)
            algorithm='exact_orthogonal_cells'
        except (InputError,ValueError,np.linalg.LinAlgError):
            try:
                hulls=extrusion_hulls(m);algorithm='verified_cap_triangle_prisms'
            except ValueError:
                import coacd
                coacd.set_log_level('error')
                pieces=coacd.run_coacd(coacd.Mesh(m.vertices,m.faces),threshold=settings['coacd_threshold_m'],
                         real_metric=True,preprocess_mode='off',resolution=2000,mcts_iterations=20,mcts_nodes=10,
                         max_convex_hull=settings['max_hulls_per_body'],seed=settings['seed'],merge=True)
                hulls=[trimesh.Trimesh(v,f,process=True).convex_hull for v,f in pieces]
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
