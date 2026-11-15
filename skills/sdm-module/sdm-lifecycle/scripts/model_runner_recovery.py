"""Exact original SDK turn readback; never starts/resumes a turn or resends input."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from review_storage import atomic_json,read_json,native_path,locked
from research_model_receipts import checked,seal
from task_pipeline import digest


def recover(config,value,folder,expected):
    if config.get('sdk_recovery')!={'version':2} or config.get('backend')!='sdk':return False
    folder=native_path(folder)
    with locked(folder,'.model-reconcile.lock','original_model_reconciliation_busy'):
        if (folder/'backend-completed.json').exists():return True
        required=('sdk-active.json','sdk-turn-intent.json','context-effective.json','backend-accepted.json')
        if any(not (folder/n).is_file() for n in required):return False
        active=read_json(folder/'sdk-active.json')
        intent=checked(folder/'sdk-turn-intent.json','sdm-sdk-turn-intent/v1')
        effective=checked(folder/'context-effective.json','sdm-model-context-effective/v1')
        accepted=checked(folder/'backend-accepted.json','sdm-model-backend-accepted/v1')
        if (any(x.get('binding')!=expected for x in (active,intent,effective,accepted))
                or active.get('thread_id')!=intent.get('thread_id') or effective.get('thread_id')!=active.get('thread_id')
                or not active.get('turn_id') or not active.get('thread_id')):
            raise ValueError('original_sdk_readback_identity_changed')
        from model_containment import verify_native_owner_empty,WindowsJob
        try:owner=verify_native_owner_empty(folder,expected)
        except (OSError,ValueError):return False
        checkpoint=folder/'reconciliation.json'
        state=read_json(checkpoint) if checkpoint.exists() else {'binding':expected,'attempts':[]}
        if state['binding']!=expected:raise ValueError('original_sdk_reconciliation_binding_changed')
        if state.get('retry_at',0)>time.time():return False
        root=folder/'readback'/str(len(state['attempts'])+1);native_path(root).mkdir(parents=True,exist_ok=False)
        scope={'original_binding':expected,'active':active,'operation':'thread/read-only'}
        request={'schema':'sdm-sdk-readback-request/v1','binding':scope,'config':config,'active':active}
        atomic_json(root/'request.json',seal(request));job=WindowsJob(root,scope)
        env={k:v for k,v in os.environ.items() if not k.startswith('CODEX_')}
        env.update(CODEX_HOME=config['codex_home'],PYTHONUTF8='1')
        try:
            argv=[getattr(sys,'_base_executable',None) or sys.executable,'-I','-S','-X','utf8','-B',
                str(Path(__file__).with_name('model_process_guard.py')),job.name,str(Path(__file__)),str(root)]
            with (root/'stdout.log').open('xb') as out,(root/'stderr.log').open('xb') as err:
                process=subprocess.Popen(argv,env=env,cwd=config['cwd'],stdin=subprocess.DEVNULL,stdout=out,stderr=err,
                    creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                try:process.wait(timeout=45)
                except subprocess.TimeoutExpired:job.stop('readback-timeout');process.wait(timeout=5)
        finally:job.close('readback-finished')
        report=root/'readback.json';known=None
        if report.exists():
            known=checked(report,'sdm-sdk-readback-result/v1')
            if known['binding']!=scope:raise ValueError('sdk_readback_response_binding_changed')
        row={'folder':str(root),'status':known['status'] if known else 'readback-unavailable','at':time.time(),'new_model_calls':0}
        state['attempts'].append(row)
        if not known or known['status'] not in {'completed','failed','interrupted'}:
            state['retry_at']=time.time()+min(900,30*2**min(len(state['attempts'])-1,5));atomic_json(checkpoint,state);return False
        response=known.get('response')
        if known['status']=='completed' and not isinstance(response,dict):raise ValueError('sdk_readback_completed_object_required')
        proof={'path':str(report),'sha256':hashlib.sha256(report.read_bytes()).hexdigest()}
        from model_runner_transport import save
        save(folder,expected,known['status'],response,{'backend':'sdk','thread_id':active['thread_id'],'turn_id':active['turn_id'],
            'sdk_version':config['sdk_version'],'experimental_api':False,'requested_model':value['model'],
            'reconciled_from_original_readback':proof,'owner_empty':owner,'new_model_calls':0,
            'identity_qualification':'Exact original completed stored turn, no turn/start or resume'})
        state.update(status='recovered-original-completion',completion_proof=proof,retry_at=None);atomic_json(checkpoint,state);return True


def readback(folder):
    folder=native_path(folder);request=checked(folder/'request.json','sdm-sdk-readback-request/v1')
    joined=checked(folder/'guard-joined.json','sdm-model-guard-joined/v1')
    from model_containment import assert_current_member
    if joined['binding']!=request['binding'] or joined['helper_pid']!=os.getpid():raise ValueError('sdk_readback_guard_changed')
    assert_current_member(joined['job_name'])
    from model_runner import configuration
    cfg=configuration(request['config']);sys.path.insert(0,cfg['sdk_root'])
    from openai_codex import Codex,CodexConfig,JsonRpcError
    from model_context import sdk_options
    from unittest.mock import patch
    native=subprocess.Popen
    def hidden(*args,**kw):
        kw['creationflags']=kw.get('creationflags',0)|getattr(subprocess,'CREATE_NO_WINDOW',0)
        return native(*args,**kw)
    active=request['active'];result={'schema':'sdm-sdk-readback-result/v1','binding':request['binding'],'status':'unavailable','new_model_calls':0}
    try:
        with patch('openai_codex.client.subprocess.Popen',side_effect=hidden):
            with Codex(CodexConfig(**sdk_options(cfg))) as client:
                raw=client._client.thread_read(active['thread_id'],include_turns=True).model_dump(mode='json',by_alias=True)
        thread=raw['thread']
        if thread['id']!=active['thread_id']:raise ValueError('sdk_readback_original_thread_changed')
        turns=[t for t in thread.get('turns',[]) if t['id']==active['turn_id']]
        if len(turns)==1:
            turn=turns[0];result['status']=turn['status'];result['turn_id']=turn['id']
            if turn['status']=='completed':
                messages=[i for i in turn.get('items',[]) if i.get('type')=='agentMessage' and i.get('phase')=='final_answer']
                if not messages:messages=[i for i in turn.get('items',[]) if i.get('type')=='agentMessage' and i.get('phase') is None]
                if len(messages)!=1:raise ValueError('unique_original_final_answer_required')
                response=json.loads(messages[0]['text'])
                if not isinstance(response,dict):raise ValueError('original_readback_json_object_required')
                result['response']=response
    except JsonRpcError as error:result.update(status='readback-unavailable',error_code=error.code)
    atomic_json(folder/'readback.json',seal(result))


if __name__=='__main__':readback(sys.argv[1])
