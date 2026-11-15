"""Bound native-action inspection and receipt checks; never operate a platform UI."""
import importlib.util
import math
import os
from pathlib import Path
import sqlite3
import sys
import time

from review_storage import read_json
from task_pipeline import digest


def request(value,binding,uid,action,route,now=None):
    now=time.time() if now is None else now
    if not isinstance(value,dict):raise ValueError('bound_native_action_request_required')
    when=value.get('requested_at')
    if (value.get('schema')!='sdm-devspace-action/v1' or value.get('binding')!=binding
            or value.get('uid')!=uid or action not in {'start','stop'} or value.get('action')!=action
            or value.get('route')!=route or type(when) not in (int,float)
            or not math.isfinite(when) or not 0<when<=now):
        raise ValueError('bound_native_action_request_required')
    return value


def receipt(value,original,binding,uid,action,route,now=None):
    now=time.time() if now is None else now
    request(original,binding,uid,action,route,now)
    if not isinstance(value,dict):raise ValueError('bound_native_action_receipt_required')
    when=value.get('at')
    if (value.get('request_digest')!=digest(original) or value.get('uid')!=uid
            or value.get('action')!=action or value.get('performed') is not True
            or not isinstance(value.get('source_ref'),str) or not value['source_ref'].strip()
            or type(when) not in (int,float) or not math.isfinite(when)
            or not original['requested_at']<=when<=now):
        raise ValueError('bound_native_action_receipt_required')
    return value


def capabilities(plan):
    native=plan['resource']['route']=='native-ui'
    return {'schema':'sdm-runtime-capabilities/v1','control_os':os.name,'python':sys.version.split()[0],
        'sqlite_version':sqlite3.sqlite_version,
        'control_log':'task_pipeline events.jsonl with portable OS coordinator lock',
        'formal_qualification_ledger':{'fcntl_available':importlib.util.find_spec('fcntl') is not None,
            'scope':'Separate qualification workspace journal; not the diagnostic main event log'},
        'lifecycle':{'route':plan['resource']['route'],'operator_mode':'agent-assisted' if native else 'configured-route',
            'active_agent_required':native,'independent_native_verified':False,
            'independent_background_native_handoff':False,
            'completion_requires':'Bound performed receipt and official same-UID readback; UI presence or GET alone is not an action'},
        'qualification':'Runtime declarations only; same-version main/SDK/operator tests are separate evidence'}


def prepare(plan,binding,root,action):
    """Produce exact read-only handoff material, without intent or performed claims."""
    if action not in {'start','stop'}:raise ValueError('native_start_or_stop_required')
    root=Path(root).resolve();state=read_json(root/'execution.json')
    uid=plan['resource']['uid'];route=plan['resource']['route']
    if state.get('binding')!=binding or state.get('uid')!=uid:
        raise ValueError('native_action_execution_binding_changed')
    original=request(read_json(root/(action+'-request.json')),binding,uid,action,route)
    return {'schema':'sdm-native-action-inspection/v1','request_digest':digest(original),'request':original,
        'resource':{key:plan['resource'][key] for key in ('uid','name','namespace','cluster','route')},
        'operator_mode':'agent-assisted','requires_original_authorization_and_official_get':True,
        'receipt_fields':['request_digest','uid','action','performed','at','source_ref'],
        'native_action_performed':False,'execution_authority':False,'platform_queried':False,
        'next_step':'Check exact UID readback and existing action/receipt before any UI action; unknown actions require reconciliation, not a repeat click'}
