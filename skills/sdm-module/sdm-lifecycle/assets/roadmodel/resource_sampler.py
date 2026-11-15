"""Sample the selected GPU and workload process CPU; no synthetic load."""
import os
import subprocess
import threading
import time


class Sampler:
    def __init__(self, gpu_uuid):
        self.gpu_uuid=gpu_uuid;self.rows=[];self.errors=[]
        self.stop_event=threading.Event();self.thread=None

    def start(self):
        self.started=time.monotonic()
        def sample():
            while not self.stop_event.is_set():
                try:
                    raw=subprocess.check_output(['nvidia-smi','--id='+self.gpu_uuid,
                        '--query-gpu=utilization.gpu,utilization.memory,memory.used,memory.total',
                        '--format=csv,noheader,nounits'],text=True,timeout=10)
                    gpu,mem,used,total=[float(x.strip()) for x in raw.strip().split(',')]
                    cpu=os.times()
                    self.rows.append({'seconds':time.monotonic()-self.started,'gpu_percent':gpu,
                        'memory_activity_percent':mem,'memory_used_mib':used,'memory_total_mib':total,
                        'cpu_parent_and_reaped_children_seconds':cpu.user+cpu.system+cpu.children_user+cpu.children_system})
                except Exception as error:
                    if len(self.errors)<10:self.errors.append(repr(error))
                self.stop_event.wait(5)
        self.thread=threading.Thread(target=sample,daemon=True);self.thread.start()

    def finish(self):
        self.stop_event.set()
        if self.thread:self.thread.join(timeout=12)
        return {'samples':self.rows,'errors':self.errors,'gpu_uuid':self.gpu_uuid,
                'scope':'Selected physical shared GPU includes other tenants; CPU counter excludes live child CPU until reaped',
                'platform_culling_window_verified':False,'synthetic_load':False}
