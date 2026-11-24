"""Refactored SDK transport still accounts for failures and rejects malformed actions."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
import httpx2
from openai import OpenAI

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from assembly_v12.responses import ResponsesTransport,_tool_arguments
from failure_report import FailureCode,FailureEvent
from llm_runtime import ApiLedger,LlmConfig


class ResponsesContract(unittest.TestCase):
    def test_generation_timeout_reserves_usage_and_never_retries(self):
        requests=[]
        def handler(request):
            requests.append(request.url.path)
            if request.url.path.endswith('/input_tokens'):
                return httpx2.Response(200,json={'object':'response.input_tokens','input_tokens':100})
            raise httpx2.ReadTimeout('Explicit offline transport failure fixture.',request=request)
        with tempfile.TemporaryDirectory() as folder:
            config=LlmConfig(enabled=True,model='gpt-6-astra',reasoning_effort='high',max_model_requests=2,
                             input_usd_per_million='1',output_usd_per_million='2',max_cost_usd='1',
                             pricing_source='https://example.invalid/offline',pricing_as_of='2026-11-01')
            ledger=ApiLedger(config,Path(folder)/'usage.jsonl')
            with OpenAI(api_key='offline-not-a-credential',max_retries=0,
                        http_client=httpx2.Client(transport=httpx2.MockTransport(handler))) as client:
                transport=ResponsesTransport(config,client,ledger)
                request={'model':config.model,'input':'offline contract fixture'}
                with self.assertRaises(FailureEvent) as caught:
                    transport._call_model(request,'offline_fixture')
                self.assertEqual(caught.exception.code,FailureCode.POLICY_REQUEST_FAILED)
                summary=ledger.summary()
                self.assertEqual(summary['api_calls'],1)
                self.assertEqual(summary['unknown_usage_requests'],1)
                self.assertIsNone(summary['api_cost'])
                self.assertGreater(summary['unresolved_cost_reserve_usd'],0)
                with self.assertRaises(FailureEvent):transport._call_model(request,'offline_fixture')
                self.assertEqual(len(requests),2)

    def test_duplicate_and_nonfinite_tool_arguments_are_rejected(self):
        def response(arguments):
            return {'status':'completed','output':[{'type':'function_call','name':'propose_assembly_action',
                                                    'status':'completed','arguments':arguments}]}
        for value in ('{"x":1,"x":2}','{"x":NaN}'):
            with self.assertRaises(ValueError):_tool_arguments(response(value),'propose_assembly_action')
        with self.assertRaises(ValueError):
            _tool_arguments({'status':'incomplete','output':[]},'propose_assembly_action')


if __name__=='__main__':unittest.main()
