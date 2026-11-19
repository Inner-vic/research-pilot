"""Receipt namespace and refusal controls; all transports are explicit substitutes."""
import copy
import sqlite3
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest
from component_payload import ref
from component_pipeline import ComponentPipeline
from review_storage import atomic_json,read_json
from task_pipeline import digest


def test_first_successor_cannot_replace_baseline_receipt_in_same_attempt(tmp_path,monkeypatch):
    import lpai_manager,component_submission
    writes=[]
    class Manager:
        def __init__(self,cfg):pass
        def guard(self,plan):return SimpleNamespace(lock=lambda:nullcontext(),load=lambda:None,state={'phase':'new'})
        def apply(self,plan,authorization):writes.append(plan);return {'status':'verified'}
    monkeypatch.setattr(lpai_manager,'Manager',Manager)
    monkeypatch.setattr(component_submission,'verify',lambda spec,manager,plan,*args,**kwargs:{'plan':plan})
    monkeypatch.setattr(component_submission,'successor_reviews',lambda *args:{'science':'synthetic','code':'synthetic'})
    pipeline=ComponentPipeline.__new__(ComponentPipeline)
    pipeline.manager_config={};pipeline.inbox=SimpleNamespace(root=tmp_path/'host')
    confirmed=tmp_path/'confirmed.json';atomic_json(confirmed,{'payload_manifest':{}})
    pipeline.spec={'submission_gate':{'version':2},'initial_submission':{'confirmed_plan':ref(confirmed)}}
    folder=tmp_path/'one-attempt';folder.mkdir();current={'payload_manifest':{},'science_plan':{}}
    review=tmp_path/'review.json';atomic_json(review,{})
    authorization=tmp_path/'auth.json';atomic_json(authorization,{'code_review':ref(review)})
    one=tmp_path/'one.json';two=tmp_path/'two.json';atomic_json(one,{'round':1});atomic_json(two,{'round':2})
    pipeline.dispatch_submission(current,folder,ref(one),ref(authorization),initial=True)
    baseline=current['submission_acceptance'];before=Path(baseline['path']).read_bytes()
    pipeline.dispatch_submission(current,folder,ref(two),ref(authorization))
    successor=current['submission_acceptance']
    assert baseline!=successor and Path(baseline['path']).read_bytes()==before
    assert read_json(successor['path'])['plan']==ref(two) and len(writes)==2
    assert ref(two)['sha256'] in successor['path']


def setup_refusal(tmp_path,monkeypatch,pending=None):
    import research_event_service,research_runtime,research_context
    cfg={'root':str(tmp_path/'old'),'research_context':{'root':str(tmp_path/'context')}}
    config=tmp_path/'config.json';atomic_json(config,cfg)
    state={'config_digest':digest(cfg),'attempts':[{'stage':'needs-attention',
        'reason':'integrity-blocked:immutable_research_replan_changed','continuation':{'resume_stage':'submitting'}}]}
    atomic_json(Path(cfg['root'])/'component-pipeline/pipeline.json',state)
    monkeypatch.setattr(research_event_service,'configuration',lambda cfg,**kwargs:cfg)
    monkeypatch.setattr(research_context,'Context',lambda cfg:SimpleNamespace(load=lambda:pending or {}))
    monkeypatch.setattr(research_runtime,'owner_alive',lambda cfg:False)
    def no_freeze(*args):raise AssertionError('Refusal must precede new runtime creation')
    monkeypatch.setattr(research_runtime,'freeze',no_freeze)
    return cfg,config,state


def test_prepared_adoption_refuses_live_original_owner(tmp_path,monkeypatch):
    import research_runtime
    from research_prepared_adoption import prepare
    cfg,config,state=setup_refusal(tmp_path,monkeypatch)
    monkeypatch.setattr(research_runtime,'owner_alive',lambda cfg:True)
    with pytest.raises(ValueError,match='original_owner_must_exit'):
        prepare(config,tmp_path/'runtime',tmp_path/'new',tmp_path/'new.json',{})
    assert not (tmp_path/'new').exists()


@pytest.mark.parametrize('pending',[{'pending_job':'native-original'},{'pending_turn':'turn-original'}])
def test_prepared_adoption_refuses_unknown_native_outcome(tmp_path,monkeypatch,pending):
    from research_prepared_adoption import prepare
    cfg,config,state=setup_refusal(tmp_path,monkeypatch,pending)
    with pytest.raises(ValueError,match='native_outcome_unresolved'):
        prepare(config,tmp_path/'runtime',tmp_path/'new',tmp_path/'new.json',{})
    assert not (tmp_path/'runtime').exists()


def test_prepared_adoption_refuses_started_guard(tmp_path,monkeypatch):
    import lpai_manager
    from research_prepared_adoption import prepare
    cfg,config,state=setup_refusal(tmp_path,monkeypatch)
    manager=tmp_path/'manager.json';plan=tmp_path/'plan.json';auth=tmp_path/'auth.json'
    atomic_json(manager,{});atomic_json(plan,{'runtime_digest':'same-manager'});atomic_json(auth,{})
    cfg['component_pipeline']={'initial_source':{'manager_config':ref(manager)}};atomic_json(config,cfg)
    state['config_digest']=digest(cfg);state['attempts'][0].update(manager_plan=ref(plan),manager_authorization=ref(auth))
    atomic_json(Path(cfg['root'])/'component-pipeline/pipeline.json',state)
    with sqlite3.connect(Path(cfg['root'])/'delivery.sqlite') as db:
        db.execute('create table jobs(status text)');db.execute('insert into jobs values(?)',('completed',))
    monkeypatch.setattr(lpai_manager,'runtime_digest',lambda:'same-manager')
    monkeypatch.setattr(lpai_manager,'Manager',lambda cfg:SimpleNamespace(guard=lambda plan:SimpleNamespace(
        lock=lambda:nullcontext(),load=lambda:None,state={'phase':'mutation-started'})))
    with pytest.raises(ValueError,match='started_guard_requires_original_reconciliation'):
        prepare(config,tmp_path/'runtime',tmp_path/'new',tmp_path/'new.json',{})
    assert not (tmp_path/'new').exists()


def retained_spec(tmp_path):
    common={'submission_gate':{'version':2},'decision_policy':{'budget':{'max_new_submissions':2}}}
    owner=tmp_path/'original.json';atomic_json(owner,{'runtime_root':str(tmp_path/'frozen/scripts'),'component_pipeline':common})
    spec=copy.deepcopy(common);spec['prior_submission_verifiers']={'exact-plan':ref(owner)}
    return spec


def test_retained_review_uses_original_frozen_owner(tmp_path,monkeypatch):
    import subprocess,json
    from component_submission import verify
    spec=retained_spec(tmp_path);calls=[]
    def original(args,**kwargs):
        calls.append((args,json.loads(kwargs['input'])))
        return SimpleNamespace(returncode=0,stdout=json.dumps({'original_receipt':'verified'}))
    monkeypatch.setattr(subprocess,'run',original)
    value=verify(spec,{}, {'sha256':'exact-plan'}, {}, {}, {})
    assert value=={'original_receipt':'verified'} and len(calls)==1
    assert str(tmp_path/'frozen/scripts') in calls[0][0]
    assert calls[0][1]['plan_ref']=={'sha256':'exact-plan'}


def test_retained_review_cannot_renew_or_change_budget(tmp_path,monkeypatch):
    from component_submission import verify
    spec=retained_spec(tmp_path);spec['decision_policy']['budget']['max_new_submissions']=3
    with pytest.raises(ValueError,match='verifier_scope_changed'):
        verify(spec,{}, {'sha256':'exact-plan'}, {}, {}, {})


def test_retained_original_rejection_is_not_overridden(tmp_path,monkeypatch):
    import subprocess
    from component_submission import verify
    spec=retained_spec(tmp_path)
    monkeypatch.setattr(subprocess,'run',lambda *a,**k:SimpleNamespace(returncode=1,stderr='ValueError: original gate rejected'))
    with pytest.raises(ValueError,match='retained_original_submission_replay_failed'):
        verify(spec,{}, {'sha256':'exact-plan'}, {}, {}, {})
