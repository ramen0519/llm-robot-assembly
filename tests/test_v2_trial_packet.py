from pathlib import Path
from copy import deepcopy
import sys,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from assembly_v2.packets import record_list_packet

def merge(a,b):
    out=deepcopy(a)
    for k,v in b.items():out[k]=merge(out[k],v) if isinstance(v,dict) and isinstance(out.get(k),dict) else deepcopy(v)
    return out

def expand(v):
    if isinstance(v,list):return [expand(x) for x in v]
    if not isinstance(v,dict):return v
    if v.get('schema')=='factored_record_list/v1':
        return [merge(expand(v['common']),{k:expand(x) for k,x in zip(v['columns'],row)}) for row in v['rows']]
    return {k:expand(x) for k,x in v.items()}

class TrialPacket(unittest.TestCase):
    def test_mixed_failure_unknown_and_contact_signs_roundtrip(self):
        value={'trials':[{'gap':g,'status':s,'physical_success':False,'evidence':{'source':'geometry_only','distance_m':d}}
                         for g,s,d in ((4,'blocked',-.001),(8,'unknown',None),(16,'clear',.002))]}
        saved=deepcopy(value);packed=record_list_packet(value)
        self.assertEqual(value,saved);self.assertEqual(expand(packed),saved)
        self.assertEqual(packed['trials']['schema'],'factored_record_list/v1')
    def test_missing_field_not_conflated_with_null(self):
        value=[{'status':'unknown'},{'status':'unknown','distance':None}]
        self.assertEqual(expand(record_list_packet(value)),value)
    def test_nested_trial_lists_and_empty_lists_preserve_order(self):
        value=[{'goal':[{'part':'0','depth':1},{'part':'2','depth':2}],'extra':[]},
               {'goal':[{'part':'0','depth':3},{'part':'2','depth':4}],'extra':[]}]
        self.assertEqual(expand(record_list_packet(value)),value)

if __name__=='__main__':unittest.main()
