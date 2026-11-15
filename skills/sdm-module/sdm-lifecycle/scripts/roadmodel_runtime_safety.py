"""Bounded health IO and transactional checkpoint protection for new runs."""
import errno
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import tempfile
import time
import subprocess
import sys
import signal
import stat


class StorageProtectionError(RuntimeError):
    pass


def directory_bytes(root):
    """One-pass regular-file snapshot; atomic renames may remove a listed name."""
    total=0
    for path in Path(root).rglob('*'):
        try:info=path.lstat()
        except FileNotFoundError:continue
        if stat.S_ISREG(info.st_mode):total+=info.st_size
    return total


def finite_tree(torch, value):
    groups = {}
    def visit(item):
        if torch.is_tensor(item):
            tensor = item.coalesce().values() if item.is_sparse else item
            groups.setdefault(str(tensor.device), []).append(torch.isfinite(tensor).all())
        elif isinstance(item, dict):
            return all(visit(v) for v in item.values())
        elif isinstance(item, (tuple, list)):
            return all(visit(v) for v in item)
        elif isinstance(item, float):
            return math.isfinite(item)
        return True
    if not visit(value):
        return False
    return all(bool(torch.stack(flags).all().item()) for flags in groups.values())


def atomic(path, value, durable=True):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix='.'+path.name+'-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, allow_nan=False)
            stream.flush()
            if durable:
                os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            Path(temporary).unlink(missing_ok=True)
        except OSError:
            pass


class HealthPersistence:
    def __init__(self, shared, local, interval=30.0, local_interval=5.0, clock=time.monotonic):
        self.shared, self.local = Path(shared), Path(local)
        if self.shared.resolve() == self.local.resolve() or interval <= 0 or local_interval <= 0:
            raise ValueError('distinct_bounded_health_paths_required')
        self.interval, self.local_interval, self.clock = interval, local_interval, clock
        self.last_shared = self.last_local = float('-inf')
        self.latest = None

    def publish(self, row, force=False):
        self.latest = row
        now = self.clock()
        if force or now-self.last_local >= self.local_interval:
            try:
                atomic(self.local, row, durable=False)
                self.last_local = now
            except OSError as error:
                print('LOCAL_HEALTH_WRITE_FAILED '+repr(error), flush=True)
        if force or now-self.last_shared >= self.interval:
            try:
                atomic(self.shared, row)
                self.last_shared = now
            except OSError as error:
                diagnostic = {'reason':'shared_health_write_failed','errno':error.errno,
                              'original':repr(error),'last_health':row}
                try:
                    atomic(self.local.with_name(self.local.stem+'-storage-failure.json'), diagnostic, durable=False)
                except OSError:
                    pass
                print('STORAGE_PROTECTION '+json.dumps(diagnostic, allow_nan=False), flush=True)
                raise StorageProtectionError('shared_health_write_failed:'+str(error.errno)) from error


def storage_preflight(directory, minimum_free_bytes=2*1024**3):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(directory).free
    if free < minimum_free_bytes:
        raise StorageProtectionError('insufficient_checkpoint_headroom')
    inode_free = None
    if hasattr(os, 'statvfs'):
        stat = os.statvfs(directory)
        inode_free = stat.f_favail
        if stat.f_files > 0 and inode_free < 32:
            raise StorageProtectionError('insufficient_free_inodes')
    descriptor, name = tempfile.mkstemp(prefix='.sdm-storage-probe-', dir=directory)
    try:
        block = b'\0'*65536
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(block); stream.flush(); os.fsync(stream.fileno())
        if Path(name).read_bytes() != block:
            raise StorageProtectionError('storage_probe_readback_mismatch')
    except OSError as error:
        raise StorageProtectionError('storage_probe_failed:'+str(error.errno)) from error
    finally:
        try:
            Path(name).unlink(missing_ok=True)
        except OSError:
            pass
    return {'free_bytes':free,'free_inodes':inode_free,'write_fsync_readback':True,
            'quota_verified':False,'quota_boundary':'Filesystem free space is not a user-quota proof; EDQUOT/ENOSPC fail closed'}


def verified_checkpoint_save(torch, save, value, target, args=(), kwargs=None, keep=2):
    if type(keep) is not int or keep < 1:
        raise ValueError('positive_checkpoint_retention_required')
    target = Path(target)
    kwargs = kwargs or {}
    def tensor_bytes(item):
        if torch.is_tensor(item):return item.numel()*item.element_size()
        if isinstance(item,dict):return sum(tensor_bytes(v) for v in item.values())
        if isinstance(item,(list,tuple)):return sum(tensor_bytes(v) for v in item)
        return 0
    storage_preflight(target.parent,max(2*1024**3,2*tensor_bytes(value)))
    descriptor, name = tempfile.mkstemp(prefix='.'+target.name+'-', dir=target.parent)
    os.close(descriptor)
    temporary = Path(name)
    try:
        save(value, temporary, *args, **kwargs)
        with temporary.open('r+b') as stream:
            stream.flush();os.fsync(stream.fileno())
            checkpoint_sha = hashlib.file_digest(stream, 'sha256').hexdigest()
        loaded = torch.load(temporary, map_location='cpu', weights_only=True)
        required = {'state_dict','optimizer','scheduler','epoch','global_iter'}
        if not isinstance(loaded, dict) or not required <= loaded.keys():
            raise StorageProtectionError('checkpoint_state_incomplete')
        scope = {k:loaded[k] for k in required | {'amp_scaler'} if k in loaded}
        if not finite_tree(torch, scope):
            raise StorageProtectionError('checkpoint_state_nonfinite_after_readback')
        if loaded['epoch'] != value['epoch'] or loaded['global_iter'] != value['global_iter']:
            raise StorageProtectionError('checkpoint_progress_readback_mismatch')
        os.replace(temporary, target)
        atomic(target.with_suffix(target.suffix+'.verified.json'),
               {'sha256':checkpoint_sha,'bytes':target.stat().st_size,
                'epoch':loaded['epoch'],'global_iter':loaded['global_iter'],'cpu_reload':True})
        # Only this run's successfully verified epoch files are eligible.
        if target.name.startswith('epoch_') and target.name.endswith('.pth'):
            candidates = []
            for candidate in target.parent.glob('epoch_*.pth'):
                if candidate.is_symlink() or not candidate.with_suffix('.pth.verified.json').is_file():
                    continue
                try:
                    number = int(candidate.stem.removeprefix('epoch_'))
                except ValueError:
                    continue
                candidates.append((number,candidate))
            for _, candidate in sorted(candidates)[:-keep]:
                candidate.unlink()
                candidate.with_suffix('.pth.verified.json').unlink(missing_ok=True)
        return {'sha256':checkpoint_sha,'cpu_reload':True}
    except OSError as error:
        raise StorageProtectionError('checkpoint_write_failed:'+str(error.errno)) from error
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


class TrainingProcessError(RuntimeError):
    def __init__(self,reason,exit_code=None):
        self.exit_code=exit_code
        super().__init__(reason+' exit_code='+str(exit_code))


def live_process_group_members(group):
    members=[]
    for path in Path('/proc').iterdir():
        if not path.name.isdigit():continue
        try:
            text=(path/'stat').read_text()
            fields=text[text.rfind(')')+2:].split()
            if int(fields[2])==group and fields[0] not in {'Z','X'}:members.append(int(path.name))
        except FileNotFoundError:continue
    return members


def confirm_process_group_exit(group,timeout=3):
    deadline=time.monotonic()+timeout
    while True:
        members=live_process_group_members(group)
        if not members:return {'verified':True,'scope':'posix-process-group','group':group,
            'qualification':'No live group members in /proc; exited zombies hold no executing workload'}
        if time.monotonic()>=deadline:raise RuntimeError('process_group_exit_unconfirmed:'+str(members[:16]))
        time.sleep(.05)


def guarded_training(argv,cwd,env,log_path,progress_path,sampler,publish_samples,
                     maximum_seconds=900,stall_seconds=240,storage_root=None,maximum_storage_bytes=8*1024**3):
    """Bound only this child's process group; preserve errors across telemetry IO."""
    child=None;primary=None;started=time.monotonic();last_progress=started;last_updates=0;last_storage=started
    cleanup={'verified':False,'scope':'not-started'}
    def stop():
        if child is None:return
        if os.name=='posix':
            # The leader may have exited while its descendants still own CUDA.
            try:os.killpg(child.pid,signal.SIGTERM)
            except ProcessLookupError:pass
            try:child.wait(timeout=2)
            except subprocess.TimeoutExpired:pass
            try:os.killpg(child.pid,signal.SIGKILL)
            except ProcessLookupError:pass
            child.wait(timeout=10)
            cleanup.update(confirm_process_group_exit(child.pid))
        elif child.poll() is None:
            child.terminate()
            try:child.wait(timeout=2)
            except subprocess.TimeoutExpired:child.kill();child.wait(timeout=10)
            cleanup.update(verified=child.poll() is not None,scope='primary-child-only-on-Windows')
        elif os.name!='posix':cleanup.update(verified=True,scope='primary-child-only-on-Windows')
    try:
        with Path(log_path).open('wb') as stream:
            sampler.start()
            child=subprocess.Popen(argv,cwd=cwd,env=env,stdout=stream,stderr=subprocess.STDOUT,
                start_new_session=os.name=='posix',creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            while child.poll() is None:
                now=time.monotonic()
                if now-started>=maximum_seconds:raise TrainingProcessError('engineering_wall_budget_exhausted')
                try:
                    updates=json.loads(Path(progress_path).read_bytes()).get('optimizer_updates',0)
                    if updates>last_updates:last_progress=now;last_updates=updates
                except FileNotFoundError:pass
                if now-last_progress>=stall_seconds:raise TrainingProcessError('engineering_no_progress_timeout')
                if storage_root is not None and now-last_storage>=30:
                    if directory_bytes(storage_root)>maximum_storage_bytes:
                        raise StorageProtectionError('engineering_output_storage_cap_exceeded')
                    storage_preflight(storage_root);last_storage=now
                time.sleep(.2)
            if child.returncode:raise TrainingProcessError('RoadModel_failed',child.returncode)
    except BaseException as error:
        primary=error
        raise
    finally:
        try:stop()
        except Exception as error:
            print('CHILD_CLEANUP_FAILED '+repr(error),file=sys.stderr,flush=True)
            if primary is None:primary=error;raise
        finally:
            try:publish_samples({**sampler.finish(),'process_group_cleanup':cleanup})
            except Exception as error:
                print('RESOURCE_SAMPLES_WRITE_FAILED '+repr(error)+' original='+repr(primary),file=sys.stderr,flush=True)
                if primary is None:raise
    return cleanup


def export_verified_checkpoint(source,target,root,maximum_bytes=8*1024**3):
    """Move the verified file on the same filesystem; never duplicate its bytes."""
    source,target,root=Path(source),Path(target),Path(root)
    if target.exists() or target.is_symlink() or not source.resolve().is_relative_to(root.resolve()):
        raise StorageProtectionError('fresh_verified_export_required')
    receipt=source.with_suffix(source.suffix+'.verified.json')
    proof=json.loads(receipt.read_bytes())
    with source.open('rb') as stream:actual=hashlib.file_digest(stream,'sha256').hexdigest()
    if actual!=proof['sha256']:raise StorageProtectionError('export_source_digest_mismatch')
    if source.stat().st_dev!=target.parent.stat().st_dev:raise StorageProtectionError('same_filesystem_export_required')
    storage_preflight(root)
    if directory_bytes(root)>maximum_bytes-1024**2:raise StorageProtectionError('final_export_output_cap_exceeded')
    os.replace(source,target)
    os.replace(receipt,target.with_suffix(target.suffix+'.verified.json'))
    if directory_bytes(root)>maximum_bytes-1024**2:raise StorageProtectionError('final_export_output_cap_exceeded')
    return proof
