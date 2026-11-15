"""Quiesce dispatch before shutdown; finish in-flight calls and commit their receipts."""
import threading
import time
from review_storage import atomic_json,read_json
from task_pipeline import digest


class DrainRequested(Exception):pass


def requested(inbox):
    path=inbox.root/'DRAINING'
    if not path.exists():return False
    if read_json(path).get('config_sha256')!=digest(inbox.cfg):raise ValueError('drain_configuration_changed')
    return True


def status(inbox):
    with inbox.condition:
        with inbox.db() as db:
            pending=db.execute("select count(*) from jobs where status not in ('completed','failed','paused')").fetchone()[0]
        native_pending=False
        if inbox.cfg.get('research_context'):
            from research_context import Context
            context=Context(inbox.cfg).load();native_pending=bool(context.get('pending_job') or context.get('pending_turn'))
        active=bool(getattr(inbox,'component_step_active',False))
        return {'requested':requested(inbox),'quiescent':not(pending or native_pending or active),
            'deferred_events':sum(not p.with_suffix('.receipt.json').exists() for p in (inbox.root/'deferred-events').glob('*.json') if not p.name.endswith('.receipt.json')),
            'pending_jobs':pending,'pending_native_context':native_pending,'pipeline_step_active':active}


def request(inbox,server,stop=False):
    with inbox.condition:
        atomic_json(inbox.root/'DRAINING',{'config_sha256':digest(inbox.cfg),'at':time.time()})
        if stop:atomic_json(inbox.root/'STOP_AFTER_DRAIN',{'config_sha256':digest(inbox.cfg),'at':time.time()})
        inbox.condition.notify_all()
        if stop and not getattr(inbox,'drain_stop_worker',None):
            def finish():
                while not inbox.done.is_set():
                    if status(inbox)['quiescent']:
                        atomic_json(inbox.root/'host-stop.json',{'config_sha256':digest(inbox.cfg),'at':time.time(),
                            'reason':'explicit-local-stop-after-drain','platform_actions':0})
                        atomic_json(inbox.root/'drain-completed.json',{'config_sha256':digest(inbox.cfg),
                            'at':time.time(),'pending_jobs':0,'pending_native_context':False,'pipeline_step_active':False})
                        server.shutdown();return
                    with inbox.condition:inbox.condition.wait(timeout=1)
            thread=threading.Thread(target=finish,name='sdm-drained-stop',daemon=True)
            inbox.drain_stop_worker=thread;thread.start()
    return {'status':'draining','stop_requested':stop,'drain':status(inbox)}


def resume(inbox):
    with inbox.condition:
        if (inbox.root/'STOP_AFTER_DRAIN').exists():raise ValueError('stop_already_requested_wait_for_quiescence')
        with inbox.db() as db:
            db.execute("update jobs set status='received',updated=? where status='paused'",(time.time(),))
        (inbox.root/'DRAINING').unlink(missing_ok=True);release(inbox);inbox.condition.notify_all()



def defer(inbox,event):
    from research_event_service import digest as event_digest
    identity=event_digest(event);path=inbox.root/'deferred-events'/(identity+'.json')
    if path.exists():
        if read_json(path)!=event:raise ValueError('deferred_event_identity_changed')
    else:atomic_json(path,event)
    return {'id':identity,'duplicate':False,'status':'deferred-until-resume','durable':True}


def release(inbox):
    from research_event_service import digest as event_digest
    if requested(inbox):return
    for path in sorted((inbox.root/'deferred-events').glob('*.json')):
        if path.name.endswith('.receipt.json'):continue
        marker=path.with_suffix('.receipt.json');event=read_json(path)
        if path.stem!=event_digest(event):raise ValueError('deferred_event_identity_changed')
        if marker.exists():
            if read_json(marker)['event_digest']!=event_digest(event):raise ValueError('deferred_event_receipt_changed')
            continue
        result=inbox.accept(event)
        atomic_json(marker,{'event_digest':event_digest(event),'job_id':result['id'],'status':result['status']})


def pause(inbox):
    """Freeze queued work without inventing a terminal result or a new job identity."""
    with inbox.condition:
        atomic_json(inbox.root/'PAUSED',{'at':time.time()})
        with inbox.db() as db:
            db.execute("update jobs set status='paused',updated=? where status='received'",(time.time(),))
        inbox.condition.notify_all()
