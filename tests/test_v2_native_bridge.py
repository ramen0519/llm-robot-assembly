"""Native transport failure must not become a physical assembly success."""
from pathlib import Path
from unittest.mock import patch
import json,os,subprocess,sys,tempfile,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from assembly_v12.contracts import write,read,sha,InputError
from assembly_v2.budget import Budget
from assembly_v2 import native_bridge as bridge


class NativeBridge(unittest.TestCase):
    def test_owned_process_times_out_without_resetting_budget(self):
        budget=Budget(3);deadline=budget.deadline
        with self.assertRaises(subprocess.TimeoutExpired):
            bridge.invoke([sys.executable,'-c','import time;time.sleep(30)'],budget,timeout=.15,
                          stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        self.assertEqual(deadline,budget.deadline)
        self.assertGreater(budget.remaining(),.1)

    def test_cancelled_budget_starts_no_process(self):
        with patch.object(bridge.subprocess,'Popen') as spawn:
            with self.assertRaisesRegex(TimeoutError,'cancelled:'):
                bridge.invoke(['unused'],Budget(1,cancelled=lambda:True),timeout=1)
        spawn.assert_not_called()

    def test_native_process_gets_independent_linux_timeout(self):
        with patch.object(bridge,'invoke') as invoke:
            bridge.native_process(['wsl.exe'],['/opt/python','job.py'],Budget(20),timeout=10)
        command=invoke.call_args.args[0]
        self.assertIn('/usr/bin/timeout',command)
        self.assertIn('--kill-after=3s',command)
        self.assertLessEqual(float(command[-3][:-1]),9)

    @unittest.skipUnless(os.name=='nt','Windows transport contract')
    def test_fallback_is_same_runtime_and_requires_hash_and_systemd_guard(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'tools').mkdir();entry=root/'tools/run_disassembly.py';entry.write_text('# native')
            config=root/'config.json';out=root/'out'
            write(config,{'python':'/opt/asap/python','repository':bridge.linux_path(root),
                          'asap_root':'/opt/asap','namespace_fallback':{'enabled':True,'systemd_pid':2}})
            reply={'name':'native','status':'passed','stdout':bridge.MARKER+json.dumps({'entry_sha256':sha(entry)})}
            with patch.object(bridge,'_probe',side_effect=[{'name':'direct','status':'timeout'},reply]) as probe:
                _,prefix,_=bridge.connect(config,out,Budget(20))
            self.assertIn('root',prefix);self.assertIn(bridge.GUARDED_NAMESPACE,prefix)
            health=read(out/'bridge_health.json')
            self.assertEqual(health['selected_transport'],'existing_distro_systemd_namespace')
            self.assertEqual(health['native_physics'],'not_run')
            self.assertFalse(health['guest_restarted']);self.assertEqual(health['api_calls'],0)
            reply['stdout']=bridge.MARKER+json.dumps({'entry_sha256':'wrong'})
            with patch.object(bridge,'_probe',side_effect=[{'name':'direct','status':'timeout'},reply]):
                with self.assertRaisesRegex(InputError,'hashes differ'):bridge.connect(config,out,Budget(20))
            self.assertEqual(read(out/'bridge_health.json')['status'],'unavailable')

    @unittest.skipUnless(os.name=='nt','Windows transport contract')
    def test_disabled_fallback_does_not_attempt_namespace(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);write(root/'config.json',{'python':'/opt/python','repository':'/mnt/c/dev/planner','asap_root':'/opt/asap'})
            with patch.object(bridge,'_probe',return_value={'name':'direct','status':'timeout'}) as probe:
                with self.assertRaisesRegex(InputError,'not enabled'):
                    bridge.connect(root/'config.json',root/'out',Budget(20))
            self.assertEqual(probe.call_count,1)


if __name__=='__main__':unittest.main()
