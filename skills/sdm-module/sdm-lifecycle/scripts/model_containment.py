"""Windows request-owned Job Objects; no breakaway or global process scans.

See https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects
The guard enrolls itself before importing or launching the model backend.
"""
import ctypes
from ctypes import wintypes as w
import os
from pathlib import Path
import time
import uuid
import re

from research_model_receipts import seal
from research_model_receipts import checked
from review_storage import atomic_json


def kernel():
    if os.name!='nt':raise ValueError('contained_transport_requires_windows_host')
    dll=ctypes.WinDLL('kernel32',use_last_error=True)
    signatures={
        'CreateJobObjectW':([ctypes.c_void_p,w.LPCWSTR],w.HANDLE),
        'OpenJobObjectW':([w.DWORD,w.BOOL,w.LPCWSTR],w.HANDLE),
        'SetInformationJobObject':([w.HANDLE,ctypes.c_int,ctypes.c_void_p,w.DWORD],w.BOOL),
        'QueryInformationJobObject':([w.HANDLE,ctypes.c_int,ctypes.c_void_p,w.DWORD,ctypes.c_void_p],w.BOOL),
        'TerminateJobObject':([w.HANDLE,w.UINT],w.BOOL),
        'CloseHandle':([w.HANDLE],w.BOOL),
        'IsProcessInJob':([w.HANDLE,w.HANDLE,ctypes.POINTER(w.BOOL)],w.BOOL),
        'OpenProcess':([w.DWORD,w.BOOL,w.DWORD],w.HANDLE),
        'WaitForSingleObject':([w.HANDLE,w.DWORD],w.DWORD),
        'GetExitCodeProcess':([w.HANDLE,ctypes.POINTER(w.DWORD)],w.BOOL),
        'GetCurrentProcess':([],w.HANDLE),
    }
    for name,(args,result) in signatures.items():
        function=getattr(dll,name);function.argtypes=args;function.restype=result
    return dll


class BasicLimits(ctypes.Structure):
    _fields_=[('process_time',ctypes.c_longlong),('job_time',ctypes.c_longlong),('flags',w.DWORD),
        ('min_working_set',ctypes.c_size_t),('max_working_set',ctypes.c_size_t),('active_limit',w.DWORD),
        ('affinity',ctypes.c_size_t),('priority',w.DWORD),('scheduling',w.DWORD)]


class IoCounters(ctypes.Structure):
    _fields_=[(name,ctypes.c_ulonglong) for name in ('read_ops','write_ops','other_ops','read_bytes','write_bytes','other_bytes')]


class ExtendedLimits(ctypes.Structure):
    _fields_=[('basic',BasicLimits),('io',IoCounters),('process_memory',ctypes.c_size_t),
        ('job_memory',ctypes.c_size_t),('peak_process_memory',ctypes.c_size_t),('peak_job_memory',ctypes.c_size_t)]


class WindowsJob:
    def __init__(self,folder,binding):
        self.api=kernel();self.folder=Path(folder);self.binding=binding
        self.name='Local\\SDM-Model-'+uuid.uuid4().hex
        ctypes.set_last_error(0)
        self.handle=self.api.CreateJobObjectW(None,self.name)
        if not self.handle:raise ctypes.WinError(ctypes.get_last_error())
        if ctypes.get_last_error()==183:
            self.api.CloseHandle(self.handle);self.handle=None
            raise ValueError('fresh_owned_job_required')
        try:
            limits=ExtendedLimits();limits.basic.flags=0x2000  # KILL_ON_JOB_CLOSE; breakaway flags absent
            if not self.api.SetInformationJobObject(self.handle,9,ctypes.byref(limits),ctypes.sizeof(limits)):
                raise ctypes.WinError(ctypes.get_last_error())
            self.save('created')
        except Exception:
            self.api.CloseHandle(self.handle);self.handle=None
            raise

    def pids(self):
        capacity=32
        while capacity<=65536:
            class Pids(ctypes.Structure):
                _fields_=[('assigned',w.DWORD),('returned',w.DWORD),('pids',ctypes.c_size_t*capacity)]
            value=Pids()
            ok=self.api.QueryInformationJobObject(self.handle,3,ctypes.byref(value),ctypes.sizeof(value),None)
            if ok and value.returned>=value.assigned:
                return [int(x) for x in value.pids[:value.returned]]
            error=ctypes.get_last_error()
            if not ok and error!=234:raise ctypes.WinError(error)
            capacity=max(capacity*2,int(value.assigned)+1)
        raise ValueError('owned_job_membership_exceeds_receipt_bound')

    def save(self,state,cause=None):
        pids=self.pids()
        atomic_json(self.folder/'containment.json',seal({'schema':'sdm-model-containment/v1',
            'binding':self.binding,'platform':'windows','job_name':self.name,'kill_on_last_close':True,
            'breakaway_allowed':False,'state':state,'cause':cause,'member_pids':pids,
            'local_empty_verified':not pids,'observed_at':time.time(),
            'qualification':'Local owned job membership only, not provider or platform completion'}))

    def stop(self,cause):
        if self.pids():
            if not self.api.TerminateJobObject(self.handle,1):raise ctypes.WinError(ctypes.get_last_error())
        deadline=time.monotonic()+5
        while self.pids():
            if time.monotonic()>=deadline:raise ValueError('owned_job_exit_not_verified')
            time.sleep(.025)
        self.save('stopped-empty',cause)

    def close(self,cause='owner-finally'):
        if self.handle:
            try:self.stop(cause)
            finally:self.api.CloseHandle(self.handle);self.handle=None


def assert_current_member(name):
    api=kernel();job=api.OpenJobObjectW(4,False,name)  # QUERY only, non-inheritable
    if not job:raise ctypes.WinError(ctypes.get_last_error())
    try:
        present=w.BOOL()
        if not api.IsProcessInJob(api.GetCurrentProcess(),job,ctypes.byref(present)):
            raise ctypes.WinError(ctypes.get_last_error())
        if not present.value:raise ValueError('helper_not_in_owned_job')
    finally:api.CloseHandle(job)


def verify_native_owner_empty(folder,binding):
    """Inspect only the exact enrolled Job; never scan/kill unrelated PIDs."""
    folder=Path(folder)
    owned=checked(folder/'containment.json','sdm-model-containment/v1')
    joined=checked(folder/'guard-joined.json','sdm-model-guard-joined/v1')
    name=owned.get('job_name','')
    if (owned.get('binding')!=binding or joined.get('binding')!=binding or joined.get('job_name')!=name
            or not re.fullmatch(r'Local\\SDM-Model-[0-9a-f]{32}',name)
            or owned.get('kill_on_last_close') is not True or owned.get('breakaway_allowed') is not False
            or joined.get('temporary_job_handle_closed') is not True):
        raise ValueError('original_enrolled_native_owner_required')
    if (owned.get('state')=='stopped-empty' and owned.get('local_empty_verified') is True
            and owned.get('member_pids')==[]):
        return {'status':'owned-job-stopped-empty','job_name':name}
    api=kernel();ctypes.set_last_error(0)
    handle=api.OpenJobObjectW(4,False,name)
    if not handle:
        error=ctypes.get_last_error()
        if error==2:return {'status':'owned-job-no-longer-exists','job_name':name}
        raise ctypes.WinError(error)
    try:
        probe=WindowsJob.__new__(WindowsJob);probe.api=api;probe.handle=handle
        if probe.pids():raise ValueError('original_native_owner_still_has_processes')
        return {'status':'owned-job-observed-empty','job_name':name}
    finally:api.CloseHandle(handle)
