"""One cancellation/deadline contract independent of video recording."""
import math
import subprocess
import time


class Budget:
    def __init__(self, seconds, *, clock=time.monotonic, cancelled=None):
        if not math.isfinite(seconds) or seconds<=0:raise ValueError('Finite positive budget required.')
        self.clock,self.deadline,self.cancelled=clock,clock()+seconds,cancelled
    def check(self,phase='operation'):
        if self.cancelled and self.cancelled():raise TimeoutError('cancelled:'+phase)
        if self.clock()>=self.deadline:raise TimeoutError('deadline:'+phase)
    def remaining(self,limit=None):
        self.check(); value=self.deadline-self.clock()
        return max(.001,min(value,limit)) if limit is not None else max(.001,value)
    def process(self,args,*,timeout=None,**kwargs):
        self.check('process_start')
        result=subprocess.run(args,timeout=self.remaining(timeout),**kwargs)
        self.check('process_end')
        return result


class ControlBudget:
    """Control attempts do not alter the globally approved API ledger cap."""
    def __init__(self,part_count):self.limit=4*part_count+4;self.used=0
    def begin(self):
        if self.used>=self.limit:raise RuntimeError('control_generation_budget')
        self.used+=1
