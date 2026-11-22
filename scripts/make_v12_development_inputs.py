"""Generate declared development CAD fixtures; does NOT fabricate planner results."""
import argparse
from pathlib import Path
import numpy as np
from frame_product import FrameDimensions, product_parts, solid_for
from assembly_v12.contracts import write


def generate(folder, variant):
    folder=Path(folder); folder.mkdir(parents=True,exist_ok=False)
    dim=FrameDimensions(base_width=.160,post_spacing=.080)
    if variant=='new_cell':
        dim=FrameDimensions(base_width=.140,post_spacing=.070,post_width=.022,hole_width=.026,
                            sleeve_width=.038,post_height=.064)
    parts=product_parts(dim)
    if variant in ('cell','new_cell'): parts=[parts[0],parts[1]]
    elif variant=='post_sleeve': parts=[parts[0],parts[1],next(p for p in parts if p['id']=='sleeve_1')]
    elif variant!='full_frame': raise ValueError('Unknown development variant.')
    rows=[]
    for index,part in enumerate(parts):
        pid='0' if index==0 else f'component_{chr(75+index)}'
        m=solid_for(part).to_mesh()
        vertices=np.asarray(m.vert_properties[:,:3]); faces=np.asarray(m.tri_verts)
        name=pid+'.obj'
        with (folder/name).open('w',encoding='ascii') as stream:
            np.savetxt(stream,vertices,fmt='v %.12g %.12g %.12g')
            np.savetxt(stream,faces+1,fmt='f %d %d %d')
        transform=np.eye(4); transform[:3,3]=np.array(part['position_product_m'])*1000
        rows.append({'part_id':pid,'mesh':name,'assembly_matrix_mm':transform.tolist(),
                     'mesh_coordinate_frame':'part_local'})
    manifest={'schema':'development_goal_CAD/v1','unit':'m','fixed_base':'0','parts':rows,
              'development_only':True,'geometry_variant':variant,'manual_robot_registry':False}
    write(folder/'product.json',manifest)
    return folder/'product.json'


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('output'); p.add_argument('--variant',choices=['cell','new_cell','post_sleeve','full_frame'],default='cell')
    a=p.parse_args(); print(generate(a.output,a.variant))
