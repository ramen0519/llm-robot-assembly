"""Explicit SE(3), metres and xyzw at external boundaries."""
import numpy as np
from scipy.spatial.transform import Rotation
from assembly_v12.contracts import rigid

def pose(position,quaternion):
    p=np.asarray(position,float);q=np.asarray(quaternion,float)
    if p.shape!=(3,) or q.shape!=(4,) or not np.isfinite(np.r_[p,q]).all() or abs(np.linalg.norm(q)-1)>1e-6:
        raise ValueError('Expected finite position and unit xyzw quaternion.')
    t=np.eye(4);t[:3,:3]=Rotation.from_quat(q).as_matrix();t[:3,3]=p;return t
def inv(t):
    t=rigid(t);out=np.eye(4);out[:3,:3]=t[:3,:3].T;out[:3,3]=-out[:3,:3]@t[:3,3];return out
def body_pose(data,name):
    body=data.body(name);t=np.eye(4);t[:3,3]=body.xpos;t[:3,:3]=body.xmat.reshape(3,3);return t
def xyzw(t):return Rotation.from_matrix(rigid(t)[:3,:3]).as_quat().tolist()
def wxyz(t):return Rotation.from_matrix(rigid(t)[:3,:3]).as_quat()[[3,0,1,2]]
def apply(t,vertices):return np.asarray(vertices)@np.asarray(t)[:3,:3].T+np.asarray(t)[:3,3]
def errors(goal,current,base_rotation):
    goal,current=rigid(goal),rigid(current)
    return np.abs(np.asarray(base_rotation).T@(current[:3,3]-goal[:3,3])),float(Rotation.from_matrix(goal[:3,:3].T@current[:3,:3]).magnitude())
def tcp_goal(part_goal,current_tcp_part):return rigid(part_goal)@inv(current_tcp_part)


def errors_many(goals,currents,base_rotations):
    """Same rigid checks and SO(3) metric, batched within one observed frame.

    No observed pose is cached across frames. Input tolerances are identical to
    contracts.rigid; a non-finite/scaled/reflected transform still raises.
    """
    from assembly_v12.contracts import InputError
    checked=[]
    for value in (goals,currents):
        a=np.asarray(value,dtype=float)
        if a.ndim!=3 or a.shape[1:]!=(4,4):
            raise InputError('input_invalid','Expected a batch of finite rigid transforms.')
        r=a[:,:3,:3]
        if (not np.isfinite(a).all() or not np.allclose(a[:,3],[0,0,0,1],atol=1e-9)
                or not np.allclose(r.transpose(0,2,1)@r,np.eye(3),atol=1e-8)
                or not np.isclose(np.linalg.det(r),1).all()):
            raise InputError('input_invalid','Expected a finite rigid transform, without scale.')
        checked.append(a)
    goal,current=checked
    if goal.shape!=current.shape:raise ValueError('Pose batch size mismatch.')
    if not len(goal):return np.empty((0,3)),np.empty(0)
    base=np.broadcast_to(np.asarray(base_rotations,float),(len(goal),3,3))
    delta=current[:,:3,3]-goal[:,:3,3]
    xyz=np.abs((base.transpose(0,2,1)@delta[:,:,None])[:,:,0])
    angle=Rotation.from_matrix(goal[:,:3,:3].transpose(0,2,1)@current[:,:3,:3]).magnitude()
    return xyz,angle
