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
