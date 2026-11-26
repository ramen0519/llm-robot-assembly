"""Numerical/decision equivalence of same-frame batching; no paid API."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from tempfile import TemporaryDirectory
import sys,unittest
import mujoco,numpy as np
from scipy.spatial.transform import Rotation
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from assembly_v2.transforms import errors,errors_many
from assembly_v2.verification import MeshVerifier
from assembly_v2.skills import MeshSkills
from failure_report import FailureEvent,FailureCode


class SpeedEquivalenceTests(unittest.TestCase):
    def test_prepared_cli_has_one_validation_owner_and_rejects_bad_input(self):
        from assembly_v2 import entry,execution
        from assembly_v2.validation import check_prepared
        from assembly_v12.contracts import write
        # The executor owns validation on run; prepare-only owns it in CLI.
        # A stub downstream executor isolates that contract from robot motion.
        with TemporaryDirectory() as td:
            root=Path(td);path=root/'prepared.json'
            p={'schema':'prepared_assembly/v2.0','order':[]}
            write(path,p)
            args=SimpleNamespace(input=str(path),output=str(root/'run'),seconds=10,command='run',
                snapshot=None,plan=None,condition='R0',policy='scripted',llm_config=None,memory_root=None,
                encoder=None,split='development',lineage=None,first_grasp=None,approach=None,development_failure_probe=None)
            def executor(prepared,folder,**kw):
                from assembly_v2.validation import check_prepared as validate
                validate(prepared,kw['budget'])
                return {'status':'no_work'}
            with patch.object(execution,'run',side_effect=executor),patch('assembly_v2.validation.check_prepared',return_value=True) as checked:
                result,_=entry.pipeline(args)
            self.assertEqual(checked.call_count,1);self.assertEqual(result['status'],'no_work')
            args.output=str(root/'bad_run')
            with patch('assembly_v2.validation.check_prepared',wraps=check_prepared) as checked,patch.object(execution,'check_reference') as reference:
                result,_=entry.pipeline(args)
            self.assertEqual(checked.call_count,1);reference.assert_not_called()
            self.assertEqual(result['status'],'preparation_failed');self.assertEqual(result['api_calls'],0)
            args.command='prepare';args.output=str(root/'bad_prepare')
            with patch('assembly_v2.validation.check_prepared',wraps=check_prepared) as checked:
                result,_=entry.pipeline(args)
            self.assertEqual(checked.call_count,1);self.assertEqual(result['status'],'preparation_failed')

    def test_batch_keeps_scalar_metrics_and_invalid_pose_rejection(self):
        rng=np.random.default_rng(76)
        a=np.tile(np.eye(4),(128,1,1));b=a.copy()
        a[:,:3,:3]=Rotation.random(128,random_state=rng).as_matrix()
        b[:,:3,:3]=Rotation.random(128,random_state=rng).as_matrix()
        a[:,:3,3]=rng.normal(size=(128,3));b[:,:3,3]=rng.normal(size=(128,3))
        bases=Rotation.random(128,random_state=rng).as_matrix()
        actual=errors_many(a,b,bases)
        scalar=[errors(x,y,z) for x,y,z in zip(a,b,bases)]
        np.testing.assert_allclose(actual[0],[r[0] for r in scalar],atol=1e-15,rtol=1e-15)
        np.testing.assert_allclose(actual[1],[r[1] for r in scalar],atol=1e-15,rtol=1e-15)
        for change in ('nan','scale','reflection','bottom'):
            bad=b.copy()
            if change=='nan':bad[3,0,0]=np.nan
            if change=='scale':bad[3,:3,0]*=1.001
            if change=='reflection':bad[3,:3,0]*=-1
            if change=='bottom':bad[3,3,0]=.001
            with self.assertRaises(ValueError):errors_many(a,bad,bases)

    def test_clearance_matches_exact_calls_and_observes_changed_state(self):
        model=mujoco.MjModel.from_xml_string('''<mujoco><worldbody>
          <body name="part" pos="0 0 .3"><freejoint/><geom name="p" type="box" size=".01 .02 .03"/></body>
          <body name="finger" pos=".05 0 .3"><geom name="f1" type="sphere" size=".01"/>
            <geom name="f2" type="sphere" pos="0 .03 0" size=".01"/></body>
        </worldbody></mujoco>''');data=mujoco.MjData(model)
        v=MeshVerifier.__new__(MeshVerifier);v.model=model;v.geom_part={model.geom('p').id:'p'}
        v.gripper_geoms={model.geom('f1').id,model.geom('f2').id}
        def scalar():
            d=.1
            for pg in v.geom_part:
                for g in v.gripper_geoms:
                    lower=np.linalg.norm(data.geom_xpos[g]-data.geom_xpos[pg])-model.geom_rbound[g]-model.geom_rbound[pg]
                    if lower>=d:continue
                    d=min(d,float(mujoco.mj_geomDistance(model,data,g,pg,d,None)))
            return d
        results=[]
        for x in (-.3,0.,.03,.049,.08,.2):
            data.qpos[0]=x;mujoco.mj_forward(model,data)
            self.assertEqual(data.time,0.)
            measured=v.clearance(data,'p');results.append(measured)
            self.assertAlmostEqual(measured,scalar(),places=14)
        self.assertGreater(len(set(results)),2)

    def test_incomplete_copy_does_not_claim_physical_infeasibility(self):
        for when,error,expected in (
            ('pick',TimeoutError('deadline:physics_step'),'unknown'),
            ('insert',TimeoutError('deadline:physics_step'),'unknown'),
            ('pick',RuntimeError('internal diagnostic failure'),'unknown'),
            ('insert',FailureEvent(FailureCode.PLANNED_CONTACT,'actual detected collision'),'failed')):
            with self.subTest(phase=when,error=type(error).__name__):
                def raise_error(*args):raise error
                trial=SimpleNamespace(pick=raise_error if when=='pick' else lambda *a:None,
                    insert=raise_error if when=='insert' else lambda *a:None,
                    last_grasp_checkpoint={'source':'test'},checkpoint=lambda name:{'label':name,'source':'mujoco_preflight_copy'},path_attempts=[],prealignment={},recorder=None,
                    source='mujoco_preflight_copy',motion=SimpleNamespace(stage=when))
                original=SimpleNamespace(model=None,data={},prepared={},active='p',installed=set(),settled={},
                    budget=None,recorder=None,prealignment={},motion_plan={},motion_plan_source='test',snapshot=lambda:'unchanged',failure=MeshSkills.failure)
                with patch('assembly_v2.skills.MeshSkills',return_value=trial):
                    result=MeshSkills.preflight(original,{},'test')
                self.assertFalse(result['accepted']);self.assertTrue(result['main_state_unchanged'])
                self.assertEqual(result['stopped_checkpoint']['label'],'copy_stopped')
                self.assertEqual(result['installation_feasible_with_grasp' if when=='insert' else 'grasp_feasible'],expected)
                self.assertEqual(result['grasp_feasible'] if when=='insert' else result['installation_feasible_with_grasp'],
                                 'passed' if when=='insert' else 'not_run')


if __name__=='__main__':unittest.main()
