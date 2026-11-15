"""Opt-in scp-sha256-v1: stream terminal artifacts, verify before publication."""
import hashlib
from pathlib import Path
import re
import subprocess
import uuid
import time
from review_storage import atomic_json


def checksum(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def collect(plan,root,transport,names):
    started=time.monotonic()
    allowance=plan.get('artifact_transfer_timeout_seconds',600)
    if plan['transport']!='ssh':raise ValueError('streamed_artifacts_require_ssh')
    manifest=transport(plan,{'action':'artifact-manifest-v1','binding':plan_binding(plan),
        'artifacts':names,'max_artifact_bytes':plan['max_artifact_bytes'],'max_total_bytes':plan['max_total_bytes']})
    if not isinstance(manifest,dict) or not set(manifest)<=set(names):raise ValueError('unexpected_artifact_manifest')
    folder=Path(root)/'artifacts';folder.mkdir(exist_ok=True)
    if folder.resolve()!=folder:raise ValueError('linked_artifact_directory_rejected')
    ssh=plan['ssh'];scp=Path(ssh.get('executable','ssh')).with_name('scp.exe' if __import__('os').name=='nt' else 'scp')
    entries=[];total=0;failures=[]
    # Check the complete manifest before downloading anything.
    for record in manifest.values():
        if (not isinstance(record,dict) or type(record.get('bytes')) is not int or record['bytes']<0
                or not re.fullmatch(r'[a-f0-9]{64}',record.get('sha256',''))):raise ValueError('invalid_artifact_manifest')
        total+=record['bytes']
        if record['bytes']>plan['max_artifact_bytes'] or total>plan['max_total_bytes']:raise ValueError('download_budget_exceeded')
    diagnostics={'process.json','progress.json','failure.json','stdout.log','worker.log','result.json','curves.json'}
    manifest=dict(sorted(manifest.items(),key=lambda item:(item[0] not in diagnostics,item[1]['bytes'],item[0])))
    def save():
        atomic_json(Path(root)/'artifact-transfer.json',{'schema':'sdm-artifact-transfer/v1','binding':plan_binding(plan),
            'files':entries,'failures':failures,'requested':names,'allowance_seconds':allowance,
            'elapsed_seconds':time.monotonic()-started})
    save()
    for name,record in manifest.items():
        if (not isinstance(record,dict) or type(record.get('bytes')) is not int or record['bytes']<0
                or not re.fullmatch(r'[a-f0-9]{64}',record.get('sha256',''))):raise ValueError('invalid_artifact_manifest')
        target=folder/name
        if target.resolve()!=target or not target.is_relative_to(folder):raise ValueError('artifact_path_escape')
        target.parent.mkdir(parents=True,exist_ok=True)
        stage=target.with_name('.transfer-'+uuid.uuid4().hex[:16])
        try:
            if not (target.is_file() and target.stat().st_size==record['bytes'] and checksum(target)==record['sha256']):
                remote=ssh['user']+'@'+ssh['host']+':'+plan['remote_root'].rstrip('/')+'/'+name
                argv=[str(scp),'-q','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','IdentitiesOnly=yes',
                    '-o','ConnectTimeout=10','-i',ssh['key'],'-P',str(ssh['port']),remote,str(stage)]
                remaining=allowance-(time.monotonic()-started)
                if remaining<=0:raise TimeoutError('artifact_collection_allowance_exhausted')
                subprocess.run(argv,check=True,capture_output=True,timeout=remaining,
                    creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                if stage.stat().st_size!=record['bytes'] or checksum(stage)!=record['sha256']:
                    raise ValueError('streamed_artifact_checksum_mismatch')
                stage.replace(target)
            entries.append({'name':name,'path':str(target),'bytes':record['bytes'],'digest':'sha256:'+record['sha256']})
        except (OSError,ValueError,subprocess.SubprocessError) as error:
            failures.append({'name':name,'type':type(error).__name__,'reason':str(error)[:300]})
        finally:
            if stage.exists():stage.unlink()
            save()
    return {'entries':entries,'failures':failures}


def plan_binding(plan):
    from task_pipeline import digest
    return digest(plan)
