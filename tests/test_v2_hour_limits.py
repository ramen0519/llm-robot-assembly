from pathlib import Path
import sys,unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from assembly_v2.entry import main
from llm_runtime import LlmConfig

class HourLimitTests(unittest.TestCase):
    def test_one_hour_cli_still_rejects_unbounded_or_longer_run(self):
        with patch('assembly_v2.entry.pipeline',return_value=({'status':'physical_success'},None)) as call:
            self.assertEqual(main(['run','--input','unused','--seconds','3600']),0)
            self.assertEqual(call.call_args.args[0].seconds,3600)
        with patch('assembly_v2.entry.pipeline') as call:
            for value in ('3601','nan','inf','0'):
                with self.assertRaises(SystemExit):main(['run','--input','unused','--seconds',value])
            call.assert_not_called()

    def test_long_shared_history_has_explicit_finite_limits(self):
        cfg=LlmConfig(model='gpt-6-astra',reasoning_effort='high',max_input_tokens=100000,max_total_tokens=2000000)
        cfg.validate()
        for changes in ({'max_input_tokens':100001},{'max_total_tokens':2000001},{'reasoning_effort':'low'}):
            with self.assertRaises(ValueError):LlmConfig(**{**cfg.public(),**changes}).validate()

if __name__=='__main__':unittest.main()
