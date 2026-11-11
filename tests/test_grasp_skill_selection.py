"""Tool access order and provenance checks for reuse, with zero model calls."""
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from export_grasp_experience import canonical
from llm_runtime import load_llm_config
from grasp_skill_selector import select_skill


class Documents:
    snapshot_id = 'fixture'
    def search(self, query):
        return [{'document_id': 's1', 'kind':'skill','title':'A'},
                {'document_id': 'm1', 'kind':'memory','title':'B'}]

    def read(self, key):
        return {'document_id':key,'kind':'skill' if key=='s1' else 'memory'}


def run_fixture(operations):
    requests = []
    def create(**request):
        requests.append(request)
        name, args = operations[len(requests)-1]
        data = {'status':'completed','usage':{'input_tokens':10,'output_tokens':8,'total_tokens':18},
                'output':[{'type':'function_call','name':name,'arguments':canonical(args)}]}
        return SimpleNamespace(model_dump=lambda **kwargs: data)
    responses = SimpleNamespace(create=create,input_tokens=SimpleNamespace(
        count=lambda **kwargs:SimpleNamespace(input_tokens=10)))
    config = load_llm_config(ROOT/'config/llm.astra-high-reuse.toml')
    with tempfile.TemporaryDirectory() as tmp:
        result = select_skill({'current_failure':'test only'},Documents(),config=config,
                              log_dir=tmp,client=SimpleNamespace(responses=responses))
    return result, requests


class SkillSelectionTests(unittest.TestCase):
    def test_discover_read_then_decide(self):
        result, requests = run_fixture([('search_library',{'query':'post'}),
            ('read_documents',{'document_ids':['m1','s1']}),
            ('select_skill',{'document_id':'s1','reason':'Applicable.'})])
        self.assertEqual((result['status'],result['selected_id'],result['API_calls']),('selected','s1',0))
        self.assertNotIn('s1',canonical(requests[0]))
        self.assertEqual([i['name'] for i in requests[0]['tools']],['abstain','search_library'])
        self.assertEqual(result['simulated_ledger']['api_calls'],3)

    def test_search_hit_is_not_yet_selectable(self):
        result, _ = run_fixture([('search_library',{'query':'post'}),
            ('select_skill',{'document_id':'s1','reason':'Unchecked.'})])
        self.assertEqual((result['status'],result['selected_id']),('error',None))

    def test_only_executable_read_can_be_selected(self):
        result, _ = run_fixture([('search_library',{'query':'post'}),('read_documents',{'document_ids':['m1']}),
                                 ('select_skill',{'document_id':'m1','reason':'Incorrect.'})])
        self.assertEqual((result['status'],result['selected_id']),('error',None))

    def test_agent_may_abstain_immediately(self):
        result, _ = run_fixture([('abstain',{'reason':'No applicable information.'})])
        self.assertEqual((result['status'],result['selected_id']),('abstained',None))
