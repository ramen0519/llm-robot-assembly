"""Explicit, opt-in rigid fingertip concepts; never a stock hardware claim."""
import math
import mujoco
import numpy as np

def validate_fingertips(value):
    if value is None:return None
    if not isinstance(value,dict) or not {'kind','extension_m','width_m'}<=set(value) or set(value)-{'kind','extension_m','width_m','contact_style'}:
        raise ValueError('Fingertip concept requires kind, extension_m, width_m.')
    if value['kind']!='rigid_extension_concept':raise ValueError('Unknown fingertip concept.')
    if value.get('contact_style','centered') not in ('centered','end_contact'):
        raise ValueError('Unknown contact style.')
    e,w=value['extension_m'],value['width_m']
    if any(type(v) not in (int,float) or not math.isfinite(v) for v in (e,w)):
        raise ValueError('Finite fingertip dimensions required.')
    if not 0<=e<=.075 or not .006<=w<=.022:
        raise ValueError('Concept extension 0..75 mm, width 6..22 mm; not manufacturing approval.')
    return dict(value)

def apply_fingertips(spec,value):
    """Replace removable pads only; preserve housing, follower and all linkages.

    Aluminum backing and rubber contact pads have explicit shape-derived mass
    and inertia. Rigid simulation does not validate bending, fastening or
    allowable force/moment in fabricated hardware.
    """
    v=validate_fingertips(value)
    if v is None:return 0.
    extension,width=v['extension_m'],v['width_m']
    for side in ('left','right'):
        body=spec.body(side+'_pad')
        for child in list(body.bodies):spec.delete(child)
        for g in list(body.geoms):spec.delete(g)
        # Exact box inertias including their different centers of mass.
        end_contact=v.get('contact_style')=='end_contact'
        length=extension+(.025 if end_contact else .02375)
        half_height=.001 if end_contact else .0025
        center_z=length-.002 if end_contact else extension+.01875
        boxes=[(np.array([width,.003,length]),np.array([0.,-.0031,length/2]),2700.,'backing')]
        for i,z in enumerate((center_z+half_height,center_z-half_height),1):
            boxes.append((np.array([width,.002,2*half_height]),np.array([0.,-.0056,z]),1100.,f'pad{i}'))
        masses=np.array([float(np.prod(size))*density for size,pos,density,label in boxes])
        com=sum(m*row[1] for m,row in zip(masses,boxes))/masses.sum()
        inertia=np.zeros((3,3))
        for mass,(size,pos,density,label) in zip(masses,boxes):
            x,y,z=size;inertia+=np.diag(mass/12*np.array([y*y+z*z,x*x+z*z,x*x+y*y]))
            d=pos-com;inertia+=mass*((d@d)*np.eye(3)-np.outer(d,d))
            pad=label.startswith('pad')
            g=body.add_geom(name=f'{side}_{label}',type=mujoco.mjtGeom.mjGEOM_BOX,
                pos=pos,size=size/2,mass=0,group=3 if pad else 0,
                rgba=[.18,.18,.18,1] if pad else [.65,.68,.72,1],
                friction=[.7 if label=='pad1' else .6 if pad else .7,.005,.0001])
            if pad:
                g.solimp=[.95,.99,.001,.5,2];g.solref=[.004,1];g.priority=1
                body.add_geom(name=f'{side}_{label}_visual',type=mujoco.mjtGeom.mjGEOM_BOX,
                    pos=pos,size=size/2,mass=0,group=2,contype=0,conaffinity=0,rgba=[.18,.18,.18,1])
        body.mass=float(masses.sum());body.ipos=com;body.iquat=[1,0,0,0]
        principal,axes=np.linalg.eigh(inertia)
        if np.linalg.det(axes)<0:axes[:,0]*=-1
        q=np.empty(4);mujoco.mju_mat2Quat(q,axes.ravel())
        body.inertia=principal;body.iquat=q;body.fullinertia=[float('nan'),0,0,0,0,0]
        body.explicitinertial=True
    spec.site('pinch').pos=[0,0,.145+extension]
    return extension


def calibrated_pad_center(profile,spacing_m,fallback):
    from assembly_v12.contracts import digest,sha
    from pathlib import Path
    cal=profile.get('grasp_pad_center_calibration')
    if cal is None:return np.asarray(fallback,float)
    if cal.get('source')!='empty_gripper_simulation' or cal.get('tool_definition_hash')!=digest(profile.get('fingertip_concept')):
        raise ValueError('Contact calibration does not match the explicitly selected tool.')
    if cal.get('tool_builder_sha256')!=sha(Path(__file__)):
        raise ValueError('Tool construction changed after contact calibration.')
    samples=cal.get('samples',[])
    if not 1<=len(samples)<=256:raise ValueError('Invalid bounded contact calibration.')
    row=min(samples,key=lambda r:abs(r['gap_mm']-spacing_m*1000))
    center=np.asarray(row['pad_center_tcp_m'],float)
    if center.shape!=(3,) or not np.isfinite(center).all() or np.linalg.norm(center)>.04:
        raise ValueError('Invalid calibrated contact center.')
    return center
