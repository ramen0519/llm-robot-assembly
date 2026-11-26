"""Bounded access to the existing native planner; never restart a guest.

The opt-in namespace transport uses the same distro, Python and source files.
It is a transport recovery, not a replacement for native physical validation.
"""
from pathlib import Path, PurePosixPath
import json
import os
import signal
import subprocess
import time
from assembly_v12.contracts import InputError, location, read, sha, write

MARKER = 'ASSEMBLY_BRIDGE_READY '
GUARDED_NAMESPACE = '''pid="$1"; shift
test "$(cat /proc/$pid/comm)" = systemd || exit 73
exec /usr/bin/nsenter --target "$pid" --mount --pid -- "$@"
'''
READINESS = '''import hashlib, json, pathlib, sys
entry=pathlib.Path(sys.argv[1]); root=pathlib.Path(sys.argv[2])
assert entry.is_file(), 'native entry missing'
assert root.is_dir(), 'ASAP root missing'
import redmax_py
print('ASSEMBLY_BRIDGE_READY '+json.dumps({'python':sys.executable,
 'python_version':sys.version.split()[0], 'redmax_module':redmax_py.__file__,
 'entry_sha256':hashlib.sha256(entry.read_bytes()).hexdigest(),
 'asap_root':str(root), 'api_calls':0}),flush=True)
'''


def linux_path(value):
    path=location(value)
    if not path.drive or path.drive.upper() not in ('C:', 'F:'):
        raise InputError('input_invalid', 'WSL bridge accepts C: and F: paths only.')
    return '/mnt/'+path.drive[0].lower()+'/'+path.as_posix()[3:]


def _stop_owned(process):
    if process.poll() is not None:
        return
    if os.name == 'nt':
        subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],
                       capture_output=True,timeout=5)
    else:
        os.killpg(process.pid,signal.SIGTERM)
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill(); process.wait(timeout=3)


def invoke(args, budget, *, timeout, **kwargs):
    """Cancellation stops only this invocation, never the distro/other jobs."""
    budget.check('bridge_process_start')
    deadline=time.monotonic()+budget.remaining(timeout)
    options={'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {'start_new_session':True}
    process=subprocess.Popen(args,**options,**kwargs)
    try:
        while True:
            budget.check('bridge_process')
            left=deadline-time.monotonic()
            if left<=0:
                raise subprocess.TimeoutExpired(args,timeout)
            try:
                stdout,stderr=process.communicate(timeout=min(.2,left))
                budget.check('bridge_process_end')
                return subprocess.CompletedProcess(args,process.returncode,stdout,stderr)
            except subprocess.TimeoutExpired:
                continue
    except BaseException:
        _stop_owned(process)
        raise


def _safe_output(value):
    if isinstance(value,bytes):
        value=value.decode('utf-16-le' if b'\x00' in value else 'utf-8',errors='replace')
    return (value or '')[-3000:]


def _probe(name,prefix,command,budget,timeout):
    started=time.monotonic()
    try:
        result=invoke(prefix+command,budget,timeout=timeout,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        return {'name':name,'status':'passed' if result.returncode==0 else 'failed',
                'returncode':result.returncode,'stdout':_safe_output(result.stdout),
                'stderr':_safe_output(result.stderr),'seconds':time.monotonic()-started}
    except (subprocess.TimeoutExpired,OSError) as exc:
        return {'name':name,'status':'timeout' if isinstance(exc,subprocess.TimeoutExpired) else 'unavailable',
                'error_type':type(exc).__name__,'seconds':time.monotonic()-started}


def connect(config_path,folder,budget):
    config=read(config_path);folder=location(folder)
    windows=os.name=='nt' and config['python'].startswith('/')
    evidence={'schema':'native_bridge_health/v1','status':'checking','probes':[],
              'api_calls':0,'guest_restarted':False,'other_jobs_stopped':False,
              'native_physics':'not_run','selected_transport':None}
    def save():write(folder/'bridge_health.json',evidence)
    save()
    try:
        for key in ('python','repository','asap_root'):
            value=config[key]
            if windows and (not PurePosixPath(value).is_absolute() or '..' in PurePosixPath(value).parts or
                            value.lower().startswith('/mnt/e')):
                raise InputError('bridge_unavailable','Invalid native '+key+' path.')
        if windows:
            distro=config.get('distribution','ASAP-Ubuntu')
            prefix=['wsl.exe','--distribution',distro,'--cd','/','--exec']
            direct=_probe('direct_wsl',prefix,['/bin/echo','assembly_bridge_ready'],budget,5)
            evidence['probes'].append(direct);save()
            if direct['status']=='passed' and 'assembly_bridge_ready' in direct.get('stdout',''):
                transport='direct_wsl'
            else:
                fallback=config.get('namespace_fallback',{})
                if fallback.get('enabled') is not True:
                    raise InputError('bridge_unavailable','WSL did not respond; namespace fallback is not enabled.')
                pid=fallback.get('systemd_pid')
                if type(pid) is not int or pid<1:
                    raise InputError('bridge_unavailable','A positive verified systemd PID is required.')
                prefix=['wsl.exe','--distribution',distro,'--system','--user','root','--exec',
                        '/bin/sh','-c',GUARDED_NAMESPACE,'assembly-bridge',str(pid)]
                transport='existing_distro_systemd_namespace'
            translated=lambda p:str(p)
        else:
            prefix=[];transport='local_native_python';translated=lambda p:str(location(p))
        entry=config['repository'].rstrip('/')+'/tools/run_disassembly.py'
        result=_probe('native_python_and_redmax',prefix,
                      [config['python'],'-B','-c',READINESS,translated(entry),translated(config['asap_root'])],budget,10)
        evidence['probes'].append(result);save()
        if result['status']!='passed':
            raise InputError('bridge_unavailable','Native Python/RedMax is unavailable; inspect bridge_health.json.')
        lines=[line[len(MARKER):] for line in result['stdout'].splitlines() if line.startswith(MARKER)]
        if len(lines)!=1:
            raise InputError('bridge_unavailable','Native readiness reply is missing or ambiguous.')
        runtime=json.loads(lines[0])
        if windows:
            repository=config['repository']
            if repository.startswith(('/mnt/c/','/mnt/f/')):
                host=location(repository[5].upper()+':/'+repository[7:]+'/tools/run_disassembly.py')
                if runtime['entry_sha256']!=sha(host):
                    raise InputError('bridge_unavailable','Native and host planner source hashes differ.')
        evidence.update(status='ready',selected_transport=transport,runtime=runtime)
        save()
        return config,prefix,linux_path if windows else lambda p:str(location(p))
    except Exception as exc:
        evidence.update(status='cancelled' if str(exc).startswith('cancelled:') else 'unavailable',
                        error=type(exc).__name__+': '+str(exc)[:1000],
                        next_step='Inspect bridge_health.json; do not restart a guest with other running work.')
        save();raise


def native_process(prefix,command,budget,*,timeout,**kwargs):
    # Linux timeout also bounds the native process if the Windows relay exits.
    seconds=max(.1,budget.remaining(timeout)-1)
    linux=bool(prefix) or (command and str(command[0]).startswith('/'))
    args=(prefix+['/usr/bin/timeout','--signal=TERM','--kill-after=3s',str(seconds)+'s']+command
          if linux else command)
    return invoke(args,budget,timeout=timeout,**kwargs)
