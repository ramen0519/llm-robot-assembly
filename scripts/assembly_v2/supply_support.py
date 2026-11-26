"""Check the actual lowest mesh surface against a finite rectangular support."""
import numpy as np
from scipy.spatial import ConvexHull,QhullError


def clip_rectangle(polygon,low,high):
    """Clip a planar triangle; vertices alone miss support inside a large face."""
    result=[np.asarray(p,float) for p in polygon]
    for axis,limit,sign in ((0,low[0],1),(0,high[0],-1),(1,low[1],1),(1,high[1],-1)):
        output=[]
        if not result:break
        previous=result[-1];prev_in=sign*(previous[axis]-limit)>=-1e-12
        for current in result:
            cur_in=sign*(current[axis]-limit)>=-1e-12
            if cur_in!=prev_in:
                delta=current[axis]-previous[axis]
                if abs(delta)>1e-15:output.append(previous+(current-previous)*((limit-previous[axis])/delta))
            if cur_in:output.append(current)
            previous,prev_in=current,cur_in
        result=output
    return result


def pedestal_footprint(mesh,rotation,preferred_width):
    """Use a narrow stand only when it touches a statically supporting surface.

    A hollow underside can stand on a table while a narrow pedestal passes
    through its opening. Try the full footprint next; otherwise leave the part
    on the table. This does not certify dynamic settling or robot access.
    """
    vertices=np.asarray(mesh.vertices)@np.asarray(rotation).T
    low,high=vertices.min(0),vertices.max(0);center=(low[:2]+high[:2])/2
    com=np.asarray(mesh.center_mass)@np.asarray(rotation).T
    triangles=vertices[np.asarray(mesh.faces)]
    bottom_faces=triangles[np.max(triangles[:,:,2],axis=1)<=low[2]+1e-6,:,:2]
    attempts=[]
    for label,width in (('preferred',np.asarray(preferred_width,float)),('full_footprint',high[:2]-low[:2])):
        points=[];area=0.
        for triangle in bottom_faces:
            polygon=clip_rectangle(triangle,center-width/2,center+width/2)
            if len(polygon)<3:continue
            xy=np.asarray(polygon)
            a=abs(float(np.dot(xy[:,0],np.roll(xy[:,1],-1))-np.dot(xy[:,1],np.roll(xy[:,0],-1))))/2
            if a>1e-14:points.extend(polygon);area+=a
        margin=0.
        if len(points)>=3:
            try:
                hull=ConvexHull(np.asarray(points))
                margin=float(np.min(-(hull.equations[:,:2]@com[:2]+hull.equations[:,2])))
            except QhullError:pass
        accepted=bool(area>1e-12 and margin>1e-5)
        attempts.append({'candidate':label,'size_xy_m':width.tolist(),'contact_area_m2':area,
                         'com_support_margin_m':margin,'accepted':accepted})
        if accepted:return width,{'source':'lowest_mesh_triangles_clipped_to_support_rectangle',
            'selection':label,'attempts':attempts,'scope':'static geometry only; settling and robot access must be tested'}
    return None,{'source':'lowest_mesh_triangles_clipped_to_support_rectangle','selection':'table_fallback',
        'attempts':attempts,'scope':'no verified finite pedestal support; use original table stable pose and test settling'}
