"""Observed states, exact failure snapshots and bounded off-line video rendering."""
from pathlib import Path
import subprocess
import sys
import time
import mujoco
import numpy as np
from assembly_v12.media import Recorder as PreviousRecorder,STATE,state,png
from assembly_v12.contracts import read,write,sha
from .transforms import wxyz
from .scene import body_name
from .budget import Budget

CAMERA_VERSION='assembly_mesh_failure_top_orthographic_480/v2.0'

class Recorder(PreviousRecorder):
    def failure_snapshot(self,*args,**kwargs):
        value=super().failure_snapshot(*args,**kwargs);value['camera_version']=CAMERA_VERSION;return value

    def materialize(self,snapshot):
        value=super().materialize(snapshot)
        # Portable artifact names use '/' on both Windows and Linux. Keep the
        # materialized event and its sidecar identical, including camera version.
        if value.get('state'):
            for field in ('state','png'):
                if value.get(field):value[field]=Path(value[field]).as_posix()
            if not value.get('oblique_png'):
                try:
                    scratch=mujoco.MjData(self.model)
                    mujoco.mj_setState(self.model,scratch,np.load(self.folder/value['state'],allow_pickle=False),STATE)
                    mujoco.mj_forward(self.model,scratch)
                    image=self.folder/'failures'/(Path(value['state']).stem+'_oblique.png')
                    target=scratch.body(body_name(self.part_id,self.prepared['base_id'])).xpos.copy()
                    self.render(scratch,image,target=target,elevation=-20)
                    value.update(oblique_png=image.relative_to(self.folder).as_posix(),oblique_png_sha256=sha(image),
                        oblique_source=value['source'],oblique_same_saved_state=True)
                except Exception as exc:value['oblique_render_error']=type(exc).__name__+': '+str(exc)[:200]
            write(self.folder/'failures'/(Path(value['state']).stem+'.json'),value)
        return value

    def goal_images(self):
        scratch=mujoco.MjData(self.model)
        mujoco.mj_setState(self.model,scratch,state(self.model,self.data),STATE)
        for pid in self.prepared['order']:
            t=np.asarray(self.prepared['parts'][pid]['target'])
            q=scratch.joint(body_name(pid,self.prepared['base_id'])+'_free').qpos
            q[:3]=t[:3,3];q[3:]=wxyz(t)
        mujoco.mj_forward(self.model,scratch)
        base=np.asarray(self.prepared['parts'][self.prepared['base_id']]['target'])[:3,3]
        paths=[]
        for name,elevation in [('goal_top',-90),('goal_oblique',-35)]:
            path=self.folder/'media'/(name+'.png');self.render(scratch,path,target=base,elevation=elevation,goal=True);paths.append(path)
        write(self.folder/'media/goal_images.json',{'source':'configured_goal_render_only','physics_steps':0,
             'original_full_SE3':True,'images':{p.name:sha(p) for p in paths}})
        return paths

    def close(self):
        # Postprocessing has its own finite budget; the action deadline must not
        # prevent saving the last measured state after a timeout/failure.
        old=self.deadline;self.deadline=float('inf')
        self.sample(force=True);self.deadline=old
        np.savez_compressed(self.folder/'replay_states.npz',states=np.asarray(self.states))
        write(self.folder/'replay_frames.json',{'state_spec':'mjSTATE_INTEGRATION','mujoco_version':mujoco.__version__,
              'model_sha256':self.model_hash,'fps':self.fps,'frames':self.frames,'source':'live_simulation',
              'states_sha256':sha(self.folder/'replay_states.npz'),'replay_not_new_physics':True,'interpolation':False})
        return self.render_video()

    def render_video(self,*,output_name='execution'):
        return render_bounded(self.folder,self.prepared['profile']['resources']['postprocess_seconds'],output_name)

def render_bounded(folder,seconds=180,output_name='replay'):
    folder=Path(folder).absolute()
    if output_name not in ('execution','replay'):raise ValueError('Invalid video role.')
    started=time.monotonic();result={'status':'not_run','source':'recorded_live_states','output':output_name}
    try:
        with (folder/(output_name+'_render.log')).open('w',encoding='utf8') as log:
            done=Budget(seconds).process([sys.executable,'-B','-m','assembly_v2.media',str(folder),output_name,str(seconds)],
                  cwd=Path(__file__).resolve().parents[1],stdout=log,stderr=subprocess.STDOUT)
        path=folder/(output_name+'_media_result.json')
        if done.returncode or not path.is_file():raise RuntimeError('Renderer failed; inspect render log.')
        result=read(path)
    except Exception as exc:result.update(status='media_error',error=type(exc).__name__+': '+str(exc)[:400])
    result['render_seconds']=time.monotonic()-started
    write(folder/('media_result.json' if output_name=='execution' else 'replay_media_result.json'),result)
    return result

def render_worker(folder,output_name,seconds):
    import imageio_ffmpeg
    folder=Path(folder);budget=Budget(float(seconds));meta=read(folder/'replay_frames.json')
    if sha(folder/'model.mjb')!=meta['model_sha256'] or sha(folder/'replay_states.npz')!=meta['states_sha256']:
        raise ValueError('Replay evidence hash mismatch.')
    model=mujoco.MjModel.from_binary_path(str(folder/'model.mjb'));data=mujoco.MjData(model)
    values=np.load(folder/'replay_states.npz',allow_pickle=False)['states'];prepared=read(folder/'prepared.json')
    if len(values)!=len(meta['frames']):raise ValueError('Frame/state count mismatch.')
    frames=folder/'media'/('frames_'+output_name);frames.mkdir(exist_ok=True)
    camera=mujoco.MjvCamera();camera.lookat[:]=[0,-.02,prepared['profile']['tabletop_z_m']+.12]
    camera.distance=.95;camera.azimuth=135;camera.elevation=-35
    # Keep all 20 Hz recorded states, but render a declared 5 Hz subset. Physics
    # still ran at 500 Hz; no interpolated/AI-generated state enters the video.
    selected=[0];last=meta['frames'][0]['simulation_time_s']
    for index,frame in enumerate(meta['frames'][1:],1):
        if frame['simulation_time_s']-last>=.2-1e-8:
            selected.append(index);last=frame['simulation_time_s']
    if selected[-1]!=len(values)-1:selected.append(len(values)-1)
    with mujoco.Renderer(model,height=320,width=320) as renderer:
        for output_index,index in enumerate(selected):
            value=values[index]
            budget.check('frame_render');mujoco.mj_setState(model,data,value,STATE)
            # Recorded-state visualization needs positions, not another force
            # solve. Dynamics, force measurements and verification use the full
            # physical pipeline elsewhere. Source pixels were compared exactly.
            mujoco.mj_fwdPosition(model,data)
            renderer.update_scene(data,camera=camera);pixels=renderer.render().copy()
            png(frames/f'{output_index:06d}.png',pixels)
            if index in (0,len(values)-1):png(folder/'media'/('initial.png' if index==0 else 'final.png'),pixels)
    video=folder/'media'/(output_name+'.mp4')
    write(folder/'media'/(output_name+'_selected_frames.json'),{'source':'recorded_live_states','source_frame_indices':selected,
          'actual_times_s':[meta['frames'][i]['simulation_time_s'] for i in selected],'video_fps':5,'interpolation':False})
    done=budget.process([imageio_ffmpeg.get_ffmpeg_exe(),'-nostdin','-v','error','-y','-framerate','5',
         '-i',str(frames/'%06d.png'),'-c:v','libx264','-threads','1','-pix_fmt','yuv420p','-movflags','+faststart',str(video)],capture_output=True)
    if done.returncode:raise RuntimeError(done.stderr.decode(errors='replace')[:500])
    write(folder/(output_name+'_media_result.json'),{'status':'recorded_states_rendered','source':'live_simulation',
          'video':video.relative_to(folder).as_posix(),'sha256':sha(video),'frames':len(selected),'recorded_states':len(values),'fps':5,
          'simulation_time_s':meta['frames'][-1]['simulation_time_s']-meta['frames'][0]['simulation_time_s'],
          'replay_not_new_physics':True,'interpolation':False})

if __name__=='__main__':render_worker(*sys.argv[1:])
