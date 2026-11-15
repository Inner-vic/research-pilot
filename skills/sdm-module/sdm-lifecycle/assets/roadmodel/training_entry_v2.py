"""FP32 pilot guard: real update checks, bounded IO, verified retention."""
import json
import os
from pathlib import Path
import runpy
import sys
import tempfile
import time
import hashlib
import torch
from torch.optim.optimizer import register_optimizer_step_pre_hook, register_optimizer_step_post_hook
from roadmodel_runtime_safety import HealthPersistence, finite_tree, storage_preflight, verified_checkpoint_save, atomic


def run(target, arguments):
    if '--amp' in arguments or '--resume-from' in arguments:
        raise ValueError('v2_pilot_requires_fp32_independent_weights_start')
    root = Path(os.environ['SDM_TELEMETRY_ROOT']).resolve()
    local = Path(tempfile.mkdtemp(prefix='sdm-roadmodel-health-'))
    persistence = HealthPersistence(root/'progress.json', local/'progress.json')
    updates = 0
    began = time.monotonic()
    original_save = torch.save
    original_load = torch.load
    original_load_state = torch.nn.Module.load_state_dict
    original_backward = torch.Tensor.backward
    loads=[];model_loads=[];optimizer_verified=False;baseline_states=[];loaded_models=[]
    expected_baseline=Path(arguments[arguments.index('--load-from')+1]).resolve() if '--load-from' in arguments else None
    import misc.ckpt_clean as cleaner
    original_cleaner = cleaner.start_cleaner_thread
    # This wrapper owns retention after verification; upstream's asynchronous
    # cleaner cannot know whether the newest file completed CPU readback.
    cleaner.start_cleaner_thread = lambda *a, **k: None
    storage_preflight(root)

    def row(**extra):
        return {'schema':'roadmodel-pilot-health/v2','optimizer_updates':updates,
                'actual_optimizer_post_hook':True,'execution_device':'cuda',
                'elapsed_seconds':time.monotonic()-began, **extra}

    def before(optimizer, args, kwargs):
        nonlocal optimizer_verified
        if not optimizer_verified:
            if expected_baseline is None or not any(r['path']==str(expected_baseline) for r in loads):
                raise ValueError('actual_common_baseline_load_missing')
            if not model_loads or any(r['invalid_keys'] for r in model_loads):
                raise ValueError('unexpected_model_loading_keys')
            initial_digest=hashlib.sha256()
            for module,expected in loaded_models:
                for name,parameter in module.named_parameters():
                    if '.feature_semantic_residual.' in name:continue
                    reference=expected.get(name)
                    actual=parameter.detach().cpu()
                    if reference is None or not torch.equal(actual,reference.to(actual.dtype)):
                        raise ValueError('applied_parameter_not_from_common_baseline:'+name)
                    initial_digest.update(name.encode());initial_digest.update(str(actual.dtype).encode())
                    initial_digest.update(str(tuple(actual.shape)).encode());initial_digest.update(actual.contiguous().numpy().tobytes())
            ids={id(p) for g in optimizer.param_groups for p in g['params']}
            for head in heads():
                if not all(p.requires_grad and id(p) in ids for p in head.parameters()):
                    raise ValueError('semantic_parameters_missing_from_optimizer')
            optimizer_verified=True
            atomic(root/'load-and-optimizer.json',{'actual_loads':loads,'model_loads':model_loads,
                'new_parameter_count':sum(p.numel() for h in heads() for p in h.parameters()),
                'initial_parameter_digest':initial_digest.hexdigest(),
                'all_new_parameters_in_optimizer':True,'optimizer_verified':True})
            baseline_states.clear();loaded_models.clear()
        parameters = [p for group in optimizer.param_groups for p in group['params']]
        if not finite_tree(torch, [optimizer.state, parameters, [p.grad for p in parameters]]):
            persistence.publish(row(numerical_failure='nonfinite_update_prevented'), force=True)
            raise FloatingPointError('nonfinite_update_prevented')

    def after(optimizer, args, kwargs):
        nonlocal updates
        updates += 1
        if not finite_tree(torch, [optimizer.state, [p for g in optimizer.param_groups for p in g['params']]]):
            persistence.publish(row(numerical_failure='nonfinite_state_after_update'), force=True)
            raise FloatingPointError('nonfinite_state_after_update')
        persistence.publish(row())

    def save(value, destination, *args, **kwargs):
        complete = isinstance(value, dict) and {'state_dict','optimizer','scheduler','epoch','global_iter'} <= value.keys()
        if not complete:
            return original_save(value, destination, *args, **kwargs)
        target_path = Path(destination).resolve()
        if not target_path.is_relative_to(root):
            raise ValueError('new_run_checkpoint_directory_required')
        verified_checkpoint_save(torch, original_save, value, target_path, args, kwargs, keep=2)
        instances=[m.runtime_snapshot() for m in heads()]
        atomic(root/'semantic-runtime.json',{'checkpoints_verified':True,'epoch':value['epoch'],
               'optimizer_updates':updates,'heads':instances})
        persistence.publish(row(checkpoint=str(target_path), checkpoint_cpu_reload=True), force=True)

    handles = [register_optimizer_step_pre_hook(before), register_optimizer_step_post_hook(after)]
    def heads():
        return [head for name,module in list(sys.modules.items()) if name.endswith('.feature_semantic_residual')
                for head in getattr(module,'INSTANCES',[])]

    def load(path,*args,**kwargs):
        value=original_load(path,*args,**kwargs)
        if isinstance(path,(str,Path)) and expected_baseline is not None and Path(path).resolve()==expected_baseline:
            with Path(path).open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
            loads.append({'path':str(expected_baseline),'sha256':digest})
            state=value.get('state_dict',value)
            baseline_states.append({k.removeprefix('module.'):v.detach().cpu() for k,v in state.items() if torch.is_tensor(v)})
        return value

    def load_state(module,state,*args,**kwargs):
        result=original_load_state(module,state,*args,**kwargs)
        if hasattr(module,'head') and hasattr(module,'encoder'):
            missing=list(result.missing_keys);unexpected=list(result.unexpected_keys)
            invalid=[k for k in missing if '.feature_semantic_residual.' not in k]+unexpected
            if not baseline_states:invalid.append('common_baseline_not_read_before_model_load')
            else:loaded_models.append((module,baseline_states[-1]))
            model_loads.append({'missing_keys':missing,'unexpected_keys':unexpected,'invalid_keys':invalid,
                'loaded_key_count':len(state),'allowed_missing':'new head.feature_semantic_residual only'})
        return result

    def backward(tensor,*args,**kwargs):
        if not finite_tree(torch,tensor):raise FloatingPointError('nonfinite_loss_before_backward')
        return original_backward(tensor,*args,**kwargs)

    torch.save = save
    torch.load = load
    torch.nn.Module.load_state_dict = load_state
    torch.Tensor.backward = backward
    argv = sys.argv
    sys.argv = [str(target), *arguments]
    sys.path.insert(0, str(Path(target).resolve().parent))
    primary = None
    try:
        persistence.publish(row(started=True), force=True)
        runpy.run_path(str(target), run_name='__main__')
    except BaseException as error:
        primary = error
        raise
    finally:
        torch.save = original_save
        torch.load = original_load
        torch.nn.Module.load_state_dict = original_load_state
        torch.Tensor.backward = original_backward
        cleaner.start_cleaner_thread = original_cleaner
        for handle in handles:
            handle.remove()
        sys.argv = argv
        try:
            persistence.publish(row(completed=primary is None,
                                    original_error=None if primary is None else repr(primary)), force=True)
        except Exception as storage_error:
            print('TERMINAL_PERSISTENCE_FAILED '+repr(storage_error), file=sys.stderr, flush=True)
            if primary is None:
                raise


if __name__ == '__main__':
    sys.path.insert(0, str(Path(sys.argv[1]).resolve().parent))
    run(sys.argv[1], sys.argv[2:])
