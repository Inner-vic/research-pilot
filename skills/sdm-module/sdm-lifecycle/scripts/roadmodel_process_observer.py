"""Pinned runtime wrapper: observe actual optimizer steps and completed saves.

Executed inside each training rank, never imported by configuration/handshake.
No checkpoint deserialization, parameter change, source edit or automatic retry.
"""
import argparse
import json
import os
from pathlib import Path
import runpy
import sys
import math
import tempfile


class UpdateHealth:
    """Classify actual AMP boundaries, never sampled/stale console gradients."""
    def __init__(self,startup_limit=20,established_limit=5):
        self.attempts=0;self.bad=0;self.successes=0
        self.startup_limit=startup_limit;self.established_limit=established_limit

    def feed(self,grad_finite,loss_finite,scale_before,scale_after,updates):
        self.attempts+=1
        self.bad=0 if grad_finite and updates==1 else self.bad+1
        if not loss_finite:reason='nonfinite_loss'
        elif not all(math.isfinite(v) and v>0 for v in (scale_before,scale_after)):reason='invalid_amp_scale'
        elif updates not in (0,1):reason='unexpected_optimizer_update_count'
        elif not grad_finite and updates:reason='nonfinite_optimizer_update'
        elif grad_finite and updates!=1:reason='finite_gradient_without_update'
        elif not grad_finite and scale_after>=scale_before:reason='amp_skip_without_scale_backoff'
        elif not grad_finite and self.successes and self.bad>=self.established_limit:reason='persistent_update_failure'
        elif not grad_finite and not self.successes and self.bad>=self.startup_limit:reason='amp_calibration_exhausted'
        else:reason=None
        if grad_finite and updates==1:self.successes+=1
        return {'attempt':self.attempts,'consecutive_bad_updates':self.bad,'successful_updates':self.successes,
            'classification':reason or ('finite_update' if updates else 'amp_scale_backoff'),
            'stop':reason is not None}


def finite_state(torch,value):
    """Inspect tensor contents; no pickle reads or declarations as health proof."""
    if hasattr(torch,'is_tensor') and torch.is_tensor(value):
        return bool(torch.isfinite(value).all().item())
    if isinstance(value,dict):return all(finite_state(torch,v) for v in value.values())
    if isinstance(value,(list,tuple)):return all(finite_state(torch,v) for v in value)
    if isinstance(value,float):return __import__('math').isfinite(value)
    return True


def finite_checkpoint_training_state(torch,value):
    # RoadModel's best_traj contains legitimate +inf "not evaluated" metadata.
    # Numerical recovery eligibility concerns model and training state; metric
    # eligibility remains a separate review and must not certify these sentinels.
    return (isinstance(value,dict) and isinstance(value.get('state_dict'),dict)
        and all(finite_state(torch,value[k]) for k in
            ('state_dict','optimizer','scheduler','amp_scaler','epoch','global_iter') if k in value))


def write(path,value):
    tmp=path.with_suffix('.tmp')
    with tmp.open('x') as stream:
        json.dump(value,stream,allow_nan=False);stream.flush();os.fsync(stream.fileno())
    os.replace(tmp,path)


def observe(entry,arguments,control,output,kind):
    import torch
    v2=os.environ.get('SDM_RUNTIME_POLICY_VERSION')=='2'
    if v2:
        sys.path.insert(0,str(Path(__file__).resolve().parent))
        from roadmodel_runtime_safety import finite_tree, HealthPersistence, verified_checkpoint_save, storage_preflight
        batched=lambda value:finite_tree(torch,value)
    else:batched=lambda value:finite_state(torch,value)
    control=Path(control);output=Path(output).resolve();rank=int(os.environ.get('RANK','0'))
    status=control/(kind+'-rank-'+str(rank)+'.json')
    persistence=None
    if v2:
        storage_preflight(output)
        local=Path(tempfile.mkdtemp(prefix='sdm-normal-health-'))
        persistence=HealthPersistence(control/('update-health-rank-'+str(rank)+'.json'),local/'health.json')
    if status.exists():raise ValueError('normal_observer_rank_already_completed')
    count=[0];checkpoints=[]
    original_init=torch.optim.Optimizer.__init__;original_save=torch.save
    original_load=getattr(torch,'load',None);scalers=[];scaler_inits=[];scaler_updates=[]
    health=UpdateHealth();pending={}
    utils=getattr(getattr(torch,'nn',None),'utils',None)
    original_clip=getattr(utils,'clip_grad_norm_',None);bad_updates=[0]
    def clip(parameters,*args,**kwargs):
        parameters=list(parameters);bad=[]
        import inspect
        frame=inspect.currentframe().f_back;context=frame.f_locals
        if 'loss' not in context:raise RuntimeError('normal_loss_observation_missing')
        pending.clear();pending.update(grad_finite=True,
            loss_finite=finite_state(torch,context.get('loss')),
            epoch=context.get('epoch'),micro_step=context.get('i_iter'),global_iter=context.get('global_iter'),
            updates_before=count[0])
        del frame
        if not pending['loss_finite']:raise FloatingPointError('normal_nonfinite_loss')
        gradients=[getattr(param,'grad',None) for param in parameters]
        if not v2 or not batched(gradients):
            for index,grad in enumerate(gradients):
                if grad is not None and not finite_state(torch,grad):bad.append(index)
        if bad:
            pending['grad_finite']=False
            bad_updates[0]+=1
            # Preserve the first diagnostic; do not let NaN norm clipping
            # spread NaN into otherwise finite gradients. GradScaler has
            # already inspected gradients in the pinned unscale->clip path.
            fault=control/('first-nonfinite-rank-'+str(rank)+'.json')
            if not fault.exists():
                import inspect
                frame=inspect.currentframe().f_back
                context=frame.f_locals;model=context.get('my_model');names={}
                if model is not None:names={id(p):n for n,p in model.named_parameters()}
                def identity(value,depth=0):
                    if depth>4:return None
                    if isinstance(value,dict):return {str(k):identity(v,depth+1) for k,v in list(value.items())[:32]
                        if any(s in str(k).lower() for s in ('scene','frame','path','token','meta','sample'))}
                    if isinstance(value,(list,tuple)):return [identity(v,depth+1) for v in value[:8]]
                    if isinstance(value,(str,int,float)) and not isinstance(value,bool):return str(value)[:512]
                    return None
                write(fault,{'rank':rank,'gradient_indices':bad[:32],
                    'parameter_names':[names.get(id(parameters[i]),'unknown') for i in bad[:32]],
                    'epoch':context.get('epoch'),'iteration':context.get('i_iter'),
                    'sample_identity':identity(context.get('data',{})),
                    'optimizer_updates':count[0],'consecutive_bad_updates':bad_updates[0]})
                del frame
            if not scalers:raise FloatingPointError('normal_nonfinite_unscaled_gradient')
            return torch.tensor(float('nan'),device=parameters[bad[0]].grad.device)
        bad_updates[0]=0
        result=original_clip(parameters,*args,**kwargs)
        if not finite_state(torch,result):raise FloatingPointError('normal_nonfinite_clip_norm')
        return result
    for namespace in (getattr(torch,'amp',None),getattr(getattr(torch,'cuda',None),'amp',None)):
        cls=getattr(namespace,'GradScaler',None)
        if cls is None or any(c is cls for c,_ in scaler_inits):continue
        old=cls.__init__;scaler_inits.append((cls,old))
        def scaler_init(self,*args,_old=old,**kwargs):
            _old(self,*args,**kwargs);scalers.append(self)
        cls.__init__=scaler_init
        update=getattr(cls,'update',None)
        if update is not None and not getattr(update,'_normal_amp_observed',False):
            # Both namespaces may expose inherited update methods. Restore in
            # reverse order and bind each original method in its closure.
            scaler_updates.append((cls,update))
            def scaler_update(self,*args,_update=update,**kwargs):
                if not pending:raise RuntimeError('normal_amp_update_without_observed_boundary')
                before=float(self.get_scale());result=_update(self,*args,**kwargs);after=float(self.get_scale())
                row=dict(pending);pending.clear()
                delta=count[0]-row.pop('updates_before')
                row.update(health.feed(row['grad_finite'],row['loss_finite'],before,after,delta))
                row.update(schema='normal-update-health/v1',rank=rank,scale_before=before,scale_after=after,
                    optimizer_update_delta=delta,optimizer_updates=count[0])
                if persistence:
                    persistence.publish(row,force=row['stop'])
                    if row['stop'] or row['attempt']==1 or row['attempt']%100==0:
                        print('NORMAL_UPDATE_HEALTH '+json.dumps(row,allow_nan=False),flush=True)
                else:
                    write(control/('update-health-rank-'+str(rank)+'.json'),row)
                    print('NORMAL_UPDATE_HEALTH '+json.dumps(row,allow_nan=False),flush=True)
                if row['stop']:raise FloatingPointError('normal_'+row['classification'])
                return result
            scaler_update._normal_amp_observed=True
            cls.update=scaler_update
    resume=None
    if '--resume-from' in arguments:resume=Path(arguments[arguments.index('--resume-from')+1]).resolve()
    def load(path,*args,**kwargs):
        value=original_load(path,*args,**kwargs)
        if resume is not None and isinstance(path,(str,Path)) and Path(path).resolve()==resume:
            if not finite_checkpoint_training_state(torch,value):raise ValueError('normal_resume_nonfinite_checkpoint')
            if scalers:
                if 'amp_scaler' not in value:raise ValueError('normal_amp_resume_requires_scaler')
                scalers[-1].load_state_dict(value['amp_scaler'])
        return value
    def stepped(opt,*_):
        count[0]+=1
        if v2:
            if not batched([getattr(opt,'state',{}),[g['params'] for g in opt.param_groups]]):
                raise FloatingPointError('normal_nonfinite_state_after_update')
            return
        if not finite_state(torch,getattr(opt,'state',{})):raise FloatingPointError('normal_nonfinite_optimizer_state_after_update')
        for group in getattr(opt,'param_groups',[]):
            if not all(finite_state(torch,p) for p in group['params']):
                raise FloatingPointError('normal_nonfinite_parameters_after_update')
    def initialize(self,*args,**kwargs):
        original_init(self,*args,**kwargs)
        self.register_step_post_hook(stepped)
        if hasattr(self,'register_step_pre_hook'):
            def protect(opt,*_):
                if v2:
                    parameters=[p for group in opt.param_groups for p in group['params']]
                    if not batched([opt.state,parameters,[p.grad for p in parameters]]):
                        raise FloatingPointError('normal_nonfinite_optimizer_update')
                    return
                if not finite_state(torch,opt.state):raise FloatingPointError('normal_nonfinite_optimizer_state')
                for group in opt.param_groups:
                    for param in group['params']:
                        if not finite_state(torch,param) or (param.grad is not None and not finite_state(torch,param.grad)):
                            raise FloatingPointError('normal_nonfinite_optimizer_update')
            self.register_step_pre_hook(protect)
    def save(value,destination,*args,**kwargs):
        complete=isinstance(value,dict) and {'state_dict','optimizer','scheduler','epoch','global_iter'}<=value.keys()
        if complete:
            value=dict(value)
            if scalers:value['amp_scaler']=scalers[-1].state_dict()
            if not finite_checkpoint_training_state(torch,value):raise ValueError('normal_checkpoint_nonfinite_state')
            value['normal_health']={'finite_state':True,'optimizer_updates':count[0],
                'finite_state_scope':'model-optimizer-scheduler-scaler-progress',
                'metadata_finite':finite_state(torch,{k:v for k,v in value.items() if k not in
                    ('state_dict','optimizer','scheduler','amp_scaler','epoch','global_iter')}),
                'amp_scaler_saved':bool(scalers),'full_resume_qualified':False}
        if complete and isinstance(destination,(str,Path)):
            target=Path(destination);temporary=target.with_name(target.name+'.normal-tmp-'+str(os.getpid()))
            if v2:
                if not target.resolve().is_relative_to(output):raise ValueError('new_run_checkpoint_directory_required')
                result=verified_checkpoint_save(torch,original_save,value,target,args,kwargs,keep=2)
                if persistence:persistence.publish({'rank':rank,'checkpoint':str(target),'optimizer_updates':count[0]},force=True)
            else:
                try:
                    result=original_save(value,temporary,*args,**kwargs);os.replace(temporary,target)
                finally:
                    if temporary.exists():temporary.unlink()
        else:result=original_save(value,destination,*args,**kwargs)
        if rank==0 and complete and isinstance(destination,(str,Path)):
            path=Path(destination).resolve()
            if path.is_relative_to(output):
                row={'relative_path':path.relative_to(output).as_posix(),
                    'epoch':value['epoch'],'global_iter':value['global_iter'],
                    'components':['model','optimizer','scheduler','epoch','global_iter']+(['amp_scaler'] if scalers else []),
                    'health_verified':True,'finite_state':True}
                # Keep bounded metadata across long runs; the actual checkpoint
                # files/logs remain in the project output. Persist at saves,
                # not every optimizer update, so killed ranks retain progress.
                if not checkpoints or row['epoch']>=checkpoints[0]['epoch']:checkpoints[:]=[row]
                write(control/(kind+'-rank-0-progress.json'),{'rank':0,'completed':False,
                    'optimizer_updates':count[0],'checkpoints':checkpoints,'kind':kind,'full_resume_qualified':False})
        return result
    torch.optim.Optimizer.__init__=initialize;torch.save=save
    if original_load is not None:torch.load=load
    if original_clip is not None:utils.clip_grad_norm_=clip
    original_argv=sys.argv;sys.argv=[str(entry),*arguments]
    sys.path.insert(0,str(Path(entry).resolve().parent))
    success=False;primary=None;cleaner=None;original_cleaner=None
    if v2:
        import misc.ckpt_clean as cleaner
        original_cleaner=cleaner.start_cleaner_thread
        cleaner.start_cleaner_thread=lambda *a,**k:None
    try:
        runpy.run_path(str(entry),run_name='__main__');success=True
    except SystemExit as error:
        success=error.code in (0,None)
        if not success:primary=error;raise
    except BaseException as error:
        primary=error;raise
    finally:
        torch.optim.Optimizer.__init__=original_init;torch.save=original_save;sys.argv=original_argv
        if original_load is not None:torch.load=original_load
        if original_clip is not None:utils.clip_grad_norm_=original_clip
        for cls,old in scaler_inits:cls.__init__=old
        for cls,old in reversed(scaler_updates):cls.update=old
        if cleaner is not None:cleaner.start_cleaner_thread=original_cleaner
        terminal={'rank':rank,'completed':success,'optimizer_updates':count[0],'checkpoints':checkpoints,
            'kind':kind,'full_resume_qualified':False}
        try:write(status,terminal)
        except Exception as error:
            print('NORMAL_TERMINAL_WRITE_FAILED '+repr(error)+' original='+repr(primary),file=sys.stderr,flush=True)
            if persistence:
                try:persistence.publish({**terminal,'original_error':repr(primary)},force=True)
                except Exception:pass
            if primary is None:raise


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--entry',required=True);p.add_argument('--control',required=True)
    p.add_argument('--output',required=True);p.add_argument('--kind',choices=['run','evaluate'],required=True)
    args,rest=p.parse_known_args()
    if rest[:1]==['--']:rest=rest[1:]
    observe(args.entry,rest,args.control,args.output,args.kind)
