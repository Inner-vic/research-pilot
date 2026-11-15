"""Receipts for exact Popen handles owned by one model transport, never PID scans."""
import subprocess
import time
from pathlib import Path

from research_model_receipts import seal
from review_storage import atomic_json
from task_pipeline import digest


class OwnedProcesses:
    def __init__(self, folder, binding, kind):
        if kind not in {'helper','sdk','cli'}:
            raise ValueError('owned_model_process_kind_required')
        self.path=Path(folder)/(kind+'-processes.json')
        self.binding=binding
        self.kind=kind
        self.entries=[]

    def add(self, process, argv):
        self.entries.append((process, {'pid':process.pid,'command_digest':digest(list(argv)),
                                     'created_at':time.time()}))
        try:self.save()
        except OSError:
            self.stop_all()
            raise
        return process

    def save(self):
        rows=[]
        for process,entry in self.entries:
            code=process.poll()
            rows.append({**entry,'returncode':code,'local_exit_observed':code is not None})
        atomic_json(self.path,seal({'schema':'sdm-owned-model-processes/v1',
            'binding':self.binding,'kind':self.kind,'processes':rows,'updated_at':time.time(),
            'qualification':'Only local exact-child handle observations; no provider terminal claim'}))

    def stop_all(self):
        for process,_ in self.entries:
            if process.poll() is None:
                process.terminate()
                try:process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill();process.wait(timeout=5)
        self.save()
