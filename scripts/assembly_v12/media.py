"""Recorded integration-state replay, never re-execution of motor commands."""
from pathlib import Path
import struct
import subprocess
import time
import zlib

import mujoco
import numpy as np

from .contracts import append, digest, read, sha, write
from failure_report import json_safe

STATE = mujoco.mjtState.mjSTATE_INTEGRATION
CAMERA_VERSION = 'assembly_target_top_orthographic/v2_active_orange'


def png(path, rgb):
    rgb = np.asarray(rgb, dtype=np.uint8)
    h, w = rgb.shape[:2]
    def chunk(tag, value):
        return struct.pack('!I', len(value))+tag+value+struct.pack('!I', zlib.crc32(tag+value)&0xffffffff)
    raw = b''.join(b'\x00'+row.tobytes() for row in rgb)
    Path(path).write_bytes(b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('!2I5B',w,h,8,2,0,0,0))+
                          chunk(b'IDAT',zlib.compress(raw))+chunk(b'IEND',b''))


def state(model, data):
    a = np.empty(mujoco.mj_stateSize(model, STATE))
    mujoco.mj_getState(model, data, a, STATE)
    return a


class Recorder:
    def __init__(self, folder, model, data, prepared, fps=20):
        self.folder, self.model, self.data, self.prepared = Path(folder), model, data, prepared
        self.folder.mkdir(parents=True, exist_ok=True)
        (self.folder/'failures').mkdir()
        (self.folder/'media').mkdir()
        mujoco.mj_saveModel(model, str(self.folder/'model.mjb'), None)
        self.model_hash = sha(self.folder/'model.mjb')
        self.fps, self.last_time = fps, -1.
        self.states, self.frames = [], []
        self.action_id = self.check_id = self.part_id = None
        self.last_failure = None
        self.failures = 0
        self.errors = []
        self.deadline = float('inf')

    def sample(self, row=None, force=False):
        if time.monotonic() > self.deadline:
            raise TimeoutError('run_wall_time_budget')
        if force or self.data.time-self.last_time >= 1/self.fps-1e-9:
            self.states.append(state(self.model,self.data))
            self.frames.append({'index':len(self.frames), 'simulation_time_s':float(self.data.time),
                                'source':'live_simulation', 'action_id':self.action_id, 'check_id':None,
                                'phase':row.get('stage') if row else 'boundary'})
            if row is not None:
                append(self.folder/'skill_trace.jsonl',{'action_id':self.action_id,'part_id':self.part_id,
                       'source':'live_simulation','simulation_time_s':float(self.data.time),'sample':json_safe(row)})
            self.last_time = float(self.data.time)

    def render(self, data, path, *, target=None, elevation=-35, goal=False, label=None, search=False):
        camera = mujoco.MjvCamera()
        p = self.prepared
        camera.lookat[:] = target if target is not None else [0, -.03, p['profile']['tabletop_z_m']+.10]
        camera.distance = .42 if target is not None else .95
        camera.azimuth, camera.elevation = 90, elevation
        if elevation == -90:
            camera.orthographic = 1
        option = mujoco.MjvOption()
        if goal:
            option.geomgroup[:] = 0
            option.geomgroup[2] = 1
        with mujoco.Renderer(self.model, height=480, width=480) as renderer:
            renderer.update_scene(data, camera=camera, scene_option=option)
            if search:
                active=self.model.body('workcell_part_'+self.part_id).id
                for geom in renderer.scene.geoms[:renderer.scene.ngeom]:
                    if geom.objtype!=mujoco.mjtObj.mjOBJ_GEOM or geom.objid<0: continue
                    bid=self.model.geom_bodyid[geom.objid]
                    name=self.model.geom(geom.objid).name
                    geom.rgba[:]=([1.,.5,.05,1.] if bid==active else
                                  [.5,.5,.5,1.] if 'gripper' in name else [.45,.60,.75,1.])
            pixels = renderer.render().copy()
        # Search images remain unlabeled. Report provenance is in frame manifests.
        png(path, pixels)
        return pixels

    def failure_snapshot(self, data, source, phase, error, parent_scene=None):
        self.failures += 1
        name = f'failure_{self.failures:04d}'
        row = {'status':'exact_detector_snapshot', 'source':source, 'phase':phase,
               'parent_scene_source':parent_scene,
               'action_id':self.action_id, 'parent_check_id':self.check_id,
               'check_id':(self.check_id or self.action_id or 'preparation')+':detector:'+str(self.failures),
               'simulation_time_s':float(data.time), 'state':f'failures/{name}.npy',
               'model_sha256':self.model_hash, 'camera_version':CAMERA_VERSION,
               'failure_code':error.code.value, 'png':None,'_state_value':state(self.model,data),
               '_target':data.body('workcell_part_'+self.part_id).xpos.copy()}
        # Candidate planners may reject thousands of samples internally. Capture
        # the state immediately, but render only the rejection that reaches the
        # action result. No reconstructed last-valid pose substitutes for it.
        self.last_failure=row
        return row

    def materialize(self,snapshot):
        if not snapshot:
            return {'status':'unavailable','reason':'No detector-time snapshot for this exception.'}
        if '_state_value' not in snapshot: return snapshot
        row={k:v for k,v in snapshot.items() if not k.startswith('_')}
        target=snapshot['_target']; value=snapshot['_state_value']
        name=Path(row['state']).stem
        np.save(self.folder/row['state'],value,allow_pickle=False)
        row['state_sha256']=sha(self.folder/row['state'])
        try:
            scratch=mujoco.MjData(self.model)
            mujoco.mj_setState(self.model,scratch,value,STATE);mujoco.mj_forward(self.model,scratch)
            image = self.folder/'failures'/(name+'.png')
            self.render(scratch,image,target=target,elevation=-90,search=True)
            row.update(png=str(image.relative_to(self.folder)),png_sha256=sha(image))
        except Exception as exc:
            row['render_error'] = type(exc).__name__+': '+str(exc)[:200]
        write(self.folder/'failures'/(name+'.json'),row)
        return row

    def goal_images(self):
        scratch = mujoco.MjData(self.model)
        mujoco.mj_setState(self.model,scratch,state(self.model,self.data),STATE)
        for pid in self.prepared['order']:
            q = scratch.joint('workcell_part_'+pid+'_free').qpos
            q[:3] = self.prepared['parts'][pid]['target_world_m']; q[3:] = [1,0,0,0]
        mujoco.mj_forward(self.model,scratch)
        base = self.prepared['parts'][self.prepared['base_id']]['target_world_m']
        paths = []
        for name, elevation in [('goal_top',-90),('goal_oblique',-35)]:
            path = self.folder/'media'/(name+'.png')
            self.render(scratch,path,target=base,elevation=elevation,goal=True)
            paths.append(path)
        write(self.folder/'media/goal_images.json',{'source':'configured_goal_render_only', 'physics_steps':0,
                                                  'images':{p.name:sha(p) for p in paths}})
        return paths

    def close(self):
        self.sample(force=True)
        np.savez_compressed(self.folder/'replay_states.npz', states=np.asarray(self.states))
        write(self.folder/'replay_frames.json', {'state_spec':'mjSTATE_INTEGRATION','mujoco_version':mujoco.__version__,
              'model_sha256':self.model_hash,'fps':self.fps,'frames':self.frames,'source':'live_simulation',
              'states_sha256':sha(self.folder/'replay_states.npz'),
              'replay_not_new_physics':True,'interpolation':False})
        return self.render_video()

    def render_video(self, *, output_name='execution'):
        if output_name not in ('execution','replay'):raise ValueError('Invalid replay output name.')
        started = time.monotonic()
        side=320
        result = {'status':'not_run','source':'live_simulation','interpolation':False,'frames':len(self.states),
                  'width':side,'height':side,'fps':self.fps}
        process = None
        try:
            command = ['ffmpeg','-nostdin','-v','error','-y','-f','rawvideo','-pix_fmt','rgb24','-s',f'{side}x{side}',
                       '-r',str(self.fps),'-i','-','-an','-c:v','libx264','-threads','1','-pix_fmt','yuv420p',
                       '-movflags','+faststart',str(self.folder/'media'/(output_name+'.mp4'))]
            process = subprocess.Popen(command,stdin=subprocess.PIPE,stderr=subprocess.PIPE)
            scratch = mujoco.MjData(self.model)
            camera = mujoco.MjvCamera(); camera.lookat[:] = [0,-.02,self.prepared['profile']['tabletop_z_m']+.12]
            camera.distance=.95; camera.azimuth=135; camera.elevation=-35
            with mujoco.Renderer(self.model,height=side,width=side) as renderer:
                for i, value in enumerate(self.states):
                    if time.monotonic()-started>600:
                        raise TimeoutError('Video rendering exceeded the separate 600-second postprocessing limit; states are preserved.')
                    mujoco.mj_setState(self.model,scratch,value,STATE)
                    mujoco.mj_forward(self.model,scratch)
                    renderer.update_scene(scratch,camera=camera)
                    rgb = renderer.render()
                    process.stdin.write(rgb.tobytes())
                    if i in (0,len(self.states)-1):
                        prefix='' if output_name=='execution' else 'replay_'
                        png(self.folder/'media'/(prefix+('initial.png' if i==0 else 'final.png')),rgb)
            process.stdin.close()
            error = process.stderr.read()
            if process.wait():
                raise RuntimeError(error.decode(errors='replace')[:200])
            video='media/'+output_name+'.mp4'
            result.update(status='recorded_states_rendered',video=video,sha256=sha(self.folder/video))
        except Exception as exc:
            result.update(status='media_error',error=type(exc).__name__+': '+str(exc)[:300])
        finally:
            if process and process.poll() is None:
                process.kill(); process.wait()
        result['render_seconds'] = time.monotonic()-started
        write(self.folder/('media_result.json' if output_name=='execution' else 'replay_media_result.json'),result)
        return result
