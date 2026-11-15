"""Explicit, per-user login deployment; never installs a periodic chat task."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
from review_storage import read_json
from task_pipeline import digest


def deployment(config_path):
    path=Path(config_path).resolve();cfg=read_json(path)
    root=Path(cfg['runtime_root'])
    # A separately frozen deployment helper may target an older frozen host;
    # neither its manifest nor the original host's configuration is rewritten.
    own=Path(__file__).resolve();manifest=read_json(own.parents[2]/'manifest.json')
    import hashlib
    if manifest['runtime_manifest'].get(own.name)!=hashlib.sha256(own.read_bytes()).hexdigest():
        raise ValueError('deployment_helper_must_be_frozen')
    actual={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in root.glob('*.py')}
    if not actual or actual!=cfg.get('runtime_manifest'):raise ValueError('target_host_runtime_changed')
    code='import json,sys;sys.path.insert(0,sys.argv[1]);from research_event_service import configuration;configuration(json.load(sys.stdin))'
    checked=subprocess.run([sys.executable,'-I','-S','-B','-c',code,str(root)],input=json.dumps(cfg),
        capture_output=True,encoding='utf-8',timeout=60,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if checked.returncode:raise ValueError('target_original_frozen_validation_failed')
    pythonw=Path(sys.executable).with_name('pythonw.exe')
    if not pythonw.is_file():raise ValueError('hidden_windows_python_host_required')
    task='SDM-host-'+digest({'root':cfg['root'],'project_id':cfg['project_id']}).removeprefix('sha256:')[:20]
    import re
    if not re.fullmatch(r'SDM-host-[0-9a-f]{20}',task):raise ValueError('safe_owned_host_task_name_required')
    args=subprocess.list2cmdline(['-I','-S','-X','utf8','-B',str(root/'research_supervisor.py'),'--config',str(path)])
    return {'task_name':task,'description':'SDM frozen host '+digest(cfg),
        'execute':str(pythonw),'arguments':args,'trigger':'current-user-logon','run_level':'Limited',
        'periodic':False,'model_calls':0,'platform_writes':0}


def install(config_path,remove=False):
    spec=deployment(config_path)
    def quote(value):return "'"+value.replace("'","''")+"'"
    command=("$ErrorActionPreference='Stop';$name="+quote(spec['task_name'])+";$description="+quote(spec['description'])+
        ";$old=Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue;"
        "if($old -and $old.Description -ne $description){throw 'Owned SDM task binding changed'};")
    if remove:command+="if($old){Unregister-ScheduledTask -TaskName $name -Confirm:$false};"
    else:
        command+=("$user=[System.Security.Principal.WindowsIdentity]::GetCurrent().Name;"
            "$action=New-ScheduledTaskAction -Execute "+quote(spec['execute'])+" -Argument "+quote(spec['arguments'])+";"
            "$trigger=New-ScheduledTaskTrigger -AtLogOn -User $user;"
            "$settings=New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero);"
            "$principal=New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited;"
            "$null=Register-ScheduledTask -TaskName $name -Description $description -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force;")
    command+="[pscustomobject]@{task_name=$name;description=$description;installed=[bool](Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue)} | ConvertTo-Json -Compress"
    process=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-WindowStyle','Hidden','-Command',command],
        capture_output=True,encoding='utf-8',creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if process.returncode:raise ValueError('login_host_registration_failed_check_local_OS_permissions')
    result=json.loads(process.stdout)
    from review_storage import atomic_json
    import time
    cfg=read_json(config_path)
    atomic_json(Path(cfg['root'])/'host-login-registration.json',{
        **result,'config_sha256':digest(cfg),'observed_at':time.time(),'os_login_recovery_verified':False})
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--config',required=True)
    action=parser.add_mutually_exclusive_group();action.add_argument('--install-login',action='store_true');action.add_argument('--uninstall-login',action='store_true')
    args=parser.parse_args()
    value=install(args.config,args.uninstall_login) if args.install_login or args.uninstall_login else deployment(args.config)
    print(json.dumps(value,ensure_ascii=False))
