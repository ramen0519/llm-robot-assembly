"""Frozen comparison manifests; all fixtures here are offline contracts."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from assembly_v12.contracts import write,read,digest,InputError
from assembly_v12.entry import plan_trials,execute_trials,CONDITIONS


class Trials(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.prepared=self.root/'prepared.json';write(self.prepared,{'source_run_hash':'offline-source-a'})
        self.manifest={'memory_root':str(self.root/'memory'),'shared_run_config':{'policy':'scripted','seconds':20},
                       'cases':[{'prepared':str(self.prepared),'lineage_id':'case-01','family':'cell',
                                 'split':'development','scenario':'nominal'}]}
        self.snapshot={'schema':'assembly_memory_snapshot/v1.2','cases':[]};self.snapshot['id']=digest(self.snapshot)

    def freeze(self):
        write(self.root/'input.json',self.manifest)
        return plan_trials(self.root/'input.json',self.root/'frozen.json',self.snapshot)

    def test_four_conditions_share_preparation_memory_and_budget(self):
        d=self.freeze();self.assertEqual({c['condition'] for c in d['cases']},set(CONDITIONS))
        self.assertEqual(len({c['prepared_sha256'] for c in d['cases']}),1)
        self.assertEqual(len({c['memory_snapshot_id'] for c in d['cases']}),1)
        self.assertEqual(d['shared_run_config'],self.manifest['shared_run_config'])

    def test_collection_is_memory_off_and_evaluation_requires_snapshot(self):
        self.manifest['cases'][0]['split']='collection'
        self.assertEqual({r['condition'] for r in self.freeze()['cases']},{'S0','R0'})
        self.manifest['cases'][0]['split']='evaluation';write(self.root/'input.json',self.manifest)
        with self.assertRaisesRegex(ValueError,'fixed collection-memory'):
            plan_trials(self.root/'input.json',self.root/'frozen2.json')

    def test_same_lineage_not_split_between_collection_and_evaluation(self):
        self.manifest['cases'].append({**self.manifest['cases'][0],'split':'evaluation'})
        with self.assertRaisesRegex(ValueError,'Duplicate lineage'):self.freeze()

    def test_same_original_export_not_split_by_merely_renaming_case(self):
        self.manifest['cases'].append({**self.manifest['cases'][0],'lineage_id':'renamed','split':'evaluation'})
        with self.assertRaisesRegex(ValueError,'same source disassembly'):self.freeze()

    def test_modified_prepared_scene_stops_before_execution(self):
        self.freeze();write(self.prepared,{'source_run_hash':'changed'})
        with patch('assembly_v12.execution.run') as run:
            with self.assertRaisesRegex(ValueError,'Prepared scene changed'):
                execute_trials(self.root/'frozen.json',self.root/'out',1)
            run.assert_not_called()

    def test_collection_command_cannot_execute_evaluation_rows(self):
        self.manifest['cases'][0]['split']='evaluation';self.freeze()
        with patch('assembly_v12.execution.run') as run:
            with self.assertRaisesRegex(ValueError,'No cases'):
                execute_trials(self.root/'frozen.json',self.root/'out',1,split='collection')
            run.assert_not_called()

    def test_implementation_change_stops_before_execution(self):
        self.freeze()
        with patch('assembly_v12.artifacts.implementation_versions',return_value={'different':'implementation'}):
            with patch('assembly_v12.execution.run') as run:
                with self.assertRaisesRegex(ValueError,'Implementation/dependencies changed'):
                    execute_trials(self.root/'frozen.json',self.root/'out',1)
                run.assert_not_called()

    def test_paid_batch_without_aggregate_cap_stops_before_client(self):
        config=Path(__file__).resolve().parents[1]/'config/llm.astra-high-native-frame.toml'
        self.manifest['shared_run_config'].update(policy='astra',llm_config=str(config));self.freeze()
        with patch('llm_runtime.create_openai_client') as client:
            with self.assertRaisesRegex(InputError,'aggregate budget'):
                execute_trials(self.root/'frozen.json',self.root/'out',1)
            client.assert_not_called()


if __name__=='__main__':unittest.main()
