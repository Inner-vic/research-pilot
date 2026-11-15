"""Isolated bootstrap: join the already-owned job before loading backend code."""
import ctypes
from ctypes import wintypes as w
import os
from pathlib import Path
import runpy
import sys


def main(name,target,folder):
    if os.name!='nt':raise ValueError('contained_transport_requires_windows_host')
    api=ctypes.WinDLL('kernel32',use_last_error=True)
    api.OpenJobObjectW.argtypes=[w.DWORD,w.BOOL,w.LPCWSTR];api.OpenJobObjectW.restype=w.HANDLE
    api.AssignProcessToJobObject.argtypes=[w.HANDLE,w.HANDLE];api.AssignProcessToJobObject.restype=w.BOOL
    api.GetCurrentProcess.restype=w.HANDLE
    api.CloseHandle.argtypes=[w.HANDLE];api.CloseHandle.restype=w.BOOL
    # The owner is the sole persistent handle holder. If it has died, opening
    # fails; if it dies during enrollment, closing this last temporary handle
    # kills this process before target code can run.
    job=api.OpenJobObjectW(1,False,name)  # ASSIGN_PROCESS only
    if not job:raise ctypes.WinError(ctypes.get_last_error())
    try:
        if not api.AssignProcessToJobObject(job,api.GetCurrentProcess()):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:api.CloseHandle(job)
    sys.path.insert(0,str(Path(__file__).resolve().parent))
    from review_storage import read_json,atomic_json
    from research_model_receipts import seal
    saved=read_json(Path(folder)/'request.json')
    atomic_json(Path(folder)/'guard-joined.json',seal({'schema':'sdm-model-guard-joined/v1',
        'binding':saved['binding'],'job_name':name,'helper_pid':os.getpid(),
        'temporary_job_handle_closed':True}))
    sys.argv=[target,folder]
    runpy.run_path(target,run_name='__main__')


if __name__=='__main__':main(*sys.argv[1:])
