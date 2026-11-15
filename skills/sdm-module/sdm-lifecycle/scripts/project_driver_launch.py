"""Revalidate the bound project package immediately before a task/cleanup launch."""
import argparse
import json
import os
from pathlib import Path

from project_driver import resolve


def mode(config):
    value=config.get('driver_runtime')
    if value is None:return 'windows-wsl'
    if not isinstance(value,dict) or set(value)!={'mode','python'} or value['mode'] not in {'native','windows-wsl'}:
        raise ValueError('explicit_project_driver_runtime_required')
    if not isinstance(value['python'],str) or not Path(value['python']).is_absolute():
        raise ValueError('absolute_project_python_required')
    return value['mode']


def command(config,project,node,receipt,handshake=None,context=None):
    path=resolve(config,config['linux_host'])
    if handshake is None and node!='cleanup' and node not in ({str(i) for i in range(1,22)} | {'23'}):raise ValueError('known_task_node_required')
    runtime=mode(config)
    python=config.get('driver_runtime',{}).get('python',config.get('windows_python_wsl'))
    if not python:raise ValueError('project_python_required')
    if runtime=='native':
        if handshake is not None:return [python,'-B',str(path),'--project',str(project),'--handshake',str(handshake),'--receipt',str(receipt)]
        if context is not None:return [python,'-B',str(path),'--project',str(project),'--node',node,'--receipt',str(receipt),'--handshake-context',str(context)]
        return [python,'-B',str(path),'--project',str(project),'--node',node,'--receipt',str(receipt)]
    from run_project_pipeline import windows
    if handshake is not None:return [python,'-B',windows(path),'--project',windows(project),'--handshake',windows(handshake),'--receipt',windows(receipt)]
    if context is not None:return [python,'-B',windows(path),'--project',windows(project),'--node',node,'--receipt',windows(receipt),'--handshake-context',windows(context)]
    return [python,'-B',windows(path),'--project',windows(project),'--node',node,'--receipt',windows(receipt)]


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--project',type=Path,required=True)
    p.add_argument('--node');p.add_argument('--receipt',type=Path,required=True)
    p.add_argument('--handshake',type=Path);p.add_argument('--handshake-context',type=Path)
    args=p.parse_args();config=json.loads(args.project.read_bytes())
    if args.receipt.exists():raise ValueError('fresh_driver_receipt_required')
    try:
        if bool(args.node)==bool(args.handshake):raise ValueError('one_project_driver_operation_required')
        argv=command(config,args.project,args.node,args.receipt,args.handshake,args.handshake_context)
        os.execve(argv[0],argv,{**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})
    except (ValueError,OSError) as error:
        value={'task_node':'driver-handshake' if args.handshake else args.node,'status':'failed','reason':str(error) if isinstance(error,ValueError) else type(error).__name__,
            'phase':'driver-binding-check','execution_started':False}
        with args.receipt.open('x',encoding='utf-8') as stream:json.dump(value,stream)
        print(json.dumps(value));raise SystemExit(3)

if __name__=='__main__':main()
