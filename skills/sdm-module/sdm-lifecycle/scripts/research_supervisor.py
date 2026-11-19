"""Own one frozen host and restart at proved boundaries, never a science loop.

Process exit is the trigger. No scheduled chat, model call or platform mutation.
OS login/startup deployment is a separate explicit installation decision.
"""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import time
# Isolated Python excludes the script directory; trust only this frozen directory.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
from review_storage import atomic_json,locked,read_json
from task_pipeline import digest
from research_recovery import inspect


def run(config_path):
    from research_event_service import configuration
    from research_runtime import owner_alive
    from model_containment import WindowsJob
    config_path=Path(config_path).resolve();cfg=configuration(read_json(config_path));root=Path(cfg['root'])
    if Path(__file__).resolve().parent!=Path(cfg['runtime_root']).resolve():
        raise ValueError('run_supervisor_from_original_frozen_runtime')
    with locked(root,'.supervisor.lock','supervisor_already_owned'):
        if owner_alive(cfg):raise ValueError('original_host_still_running')
        journal=root/'supervision.json';value=read_json(journal) if journal.exists() else {
            'schema':'sdm-host-supervision/v1','config_sha256':digest(cfg),'generations':[],'failures':[]}
        if value['config_sha256']!=digest(cfg):raise ValueError('supervisor_configuration_changed')
        if (root/'host-stop.json').exists():
            return {'status':'explicitly-stopped','next':'remove-stop-only-by-explicit-start-command'}
        while True:
            if read_json(config_path)!=cfg:raise ValueError('supervised_config_bytes_changed')
            recovery=inspect(cfg);atomic_json(root/'recovery.json',recovery)
            if not recovery['can_restart_host']:
                value.update(status=recovery['action'],finished_at=time.time());atomic_json(journal,value);return value
            index=len(value['generations']);folder=root/'supervision'/('g'+str(index));folder.mkdir(parents=True,exist_ok=False)
            binding={'config_sha256':digest(cfg),'generation':index}
            atomic_json(folder/'request.json',{'binding':binding,'config_path':str(config_path)})
            owned=WindowsJob(folder,binding)
            try:
                with (folder/'stdout.log').open('xb') as out,(folder/'stderr.log').open('xb') as err:
                    child=subprocess.Popen([sys.executable,'-I','-S','-X','utf8','-B',
                        str(Path(__file__).with_name('model_process_guard.py')),owned.name,
                        str(Path(__file__).with_name('research_host_child.py')),str(folder)],
                        stdin=subprocess.DEVNULL,stdout=out,stderr=err,
                        creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                    generation={'index':index,'pid':child.pid,'started_at':time.time(),'binding':binding}
                    value['generations'].append(generation);value.update(status='running',supervisor_pid=os.getpid())
                    atomic_json(journal,value)
                    code=child.wait()
                    generation.update(exit_code=code,finished_at=time.time())
            finally:owned.close('supervised-host-exited')
            atomic_json(journal,value)
            if (root/'completion-exit.json').exists() or (root/'host-stop.json').exists():
                value.update(status='completed' if (root/'completion-exit.json').exists() else 'stopped',
                    finished_at=time.time());atomic_json(journal,value);return value
            recovery=inspect(cfg);atomic_json(root/'recovery.json',recovery)
            signature=digest({'exit_code':code,'progress':recovery['progress_fingerprint']})
            if not recovery['can_restart_host'] or signature in value['failures']:
                value.update(status='needs-reconciliation' if not recovery['can_restart_host'] else 'stalled-no-progress',
                    finished_at=time.time());atomic_json(journal,value);return value
            value['failures'].append(signature);atomic_json(journal,value)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',required=True,type=Path)
    parser.add_argument('--inspect-recovery',action='store_true')
    parser.add_argument('--clear-explicit-stop',action='store_true')
    args=parser.parse_args()
    if args.inspect_recovery:result=inspect(read_json(args.config))
    else:
        if args.clear_explicit_stop:
            cfg=read_json(args.config)
            if (Path(cfg['root'])/'PAUSED').exists():raise ValueError('user_paused_host_requires_checked_resume_or_codex_cold_adoption')
            markers=[Path(cfg['root'])/name for name in ('host-stop.json','STOP_AFTER_DRAIN','DRAINING')]
            for marker in markers:
                if marker.exists() and read_json(marker)['config_sha256'].removeprefix('sha256:')!=digest(cfg).removeprefix('sha256:'):raise ValueError('stop_marker_binding_changed')
            for marker in markers:marker.unlink(missing_ok=True)
        result=run(args.config)
    import json
    print(json.dumps(result,ensure_ascii=False))
