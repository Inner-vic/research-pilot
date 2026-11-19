"""Actual SDM validators and immutable local evidence; no ML/platform/model calls."""
import ast
import copy
import hashlib
import json
from pathlib import Path

import pytest

from canonical import digest
from component_payload import ref, harness
from contracts import AssetBundle, ContractError, evaluate_scientific_decision, validate_scientific_rule
from lifecycle_rules import check_experiment_kernel
from prepare_public_ts import metadata
from research_acceptance import prepare, publish, load, submission, project
from research_intent import prepare as intent_prepare, publish as intent_publish, _record
from review_storage import atomic_json, read_json
from test_sdm_governance import contents
from test_component_submission_gate import research, approve_initial


def material(tmp,project_id='controlled-study'):
    source=tmp/'input.ts'
    source.write_text('@univariate true\n@timestamps false\n@data\n'+'\n'.join(
        ','.join(str(i*10+j) for j in range(8))+':'+str(i%2) for i in range(16)),encoding='utf-8')
    raw=source.read_bytes(); info=metadata(raw,'controlled-fixed-validation-v1')
    comparison={'protocol':'controlled-fixed-validation-v1','source_sha256':hashlib.sha256(raw).hexdigest(),
        'split_sha256':hashlib.sha256(json.dumps(info['split'],sort_keys=True).encode()).hexdigest(),
        'selection':'max validation macro_f1 then min validation loss; earliest tie'}
    intent=contents();intent['brief']['ml_contract_applicability']={'required_contract_ids':['G01','G02','G03','G07','G08','G09','G10','G13']}
    spec={'project_id':project_id,'research_intent':intent,'comparison_identity':comparison,
        'workload':{'version':2,'adapter':'binary-component/v2','training_loss_hook':True,'seed_values':[42],
                    'source_ref':'Synthetic transport fixture, no real research.',
                    'engineering_preflight':{'version':1,'max_steps':32,'examples_per_class':2}}}
    from prepare_public_ts import render
    (tmp/'baseline.py').write_bytes(render({'SOURCE':spec['workload']['source_ref'],
        'DATA_SHA':comparison['source_sha256'],'PROTOCOL':comparison['protocol'],'REMOTE_ROOT':'/mnt/synthetic-only',
        'INPUT_FILE':source.name,'SAMPLES':info['samples'],'LENGTH':info['length'],'CLASS_LABELS':info['class_labels'],
        'TRAIN_SAMPLES':info['train_samples'],'VAL_SAMPLES':info['validation_samples']}))
    (tmp/'model.py').write_text('def build_model(config): raise AssertionError("Synthetic fixture never executes ML")\ndef build_optimizer(parameters,config): raise AssertionError("not executed")\ndef build_training_loss(config): raise AssertionError("not executed")\n',encoding='utf-8')
    spec.update(baseline_source=ref(tmp/'baseline.py'),baseline_model=ref(tmp/'model.py'))
    seed=intent_prepare(spec)
    refs={'S02:'+seed['brief']['instance_key']:digest(seed['brief'])}
    def record(kind, content):
        return _record(kind,kind.lower()+'-controlled',{'source_refs':refs,**content},spec['project_id'],'2026-11-15T00:00:00Z')
    contract={
        'G03':record('G03',{'dataset_identity':'controlled-training-source','version':comparison['source_sha256'],
            'schema':{'signal':'float32 univariate length 8','label':'binary integer'},
            'quality_policy':{'finite':True,'fixed_length':8},'lineage':{'source':str(source)}}),
        'G07':record('G07',{'ontology_identity':'binary-controlled','classes':['negative','positive'],
            'semantics':{'negative':'raw label zero','positive':'raw label one'},'version':'controlled-v1'}),
        'G09':record('G09',{'inputs':['signal'],'outputs':['logits'],'shape':{'signal':[1,8],'logits':[2]},
            'dtype':'float32','units':{'signal':'arbitrary','logits':'dimensionless'},
            'runtime_validator_digest':'sha256:'+'5'*64}),
        'G10':record('G10',{'evaluator_identity':comparison['protocol'],
            'metric_definitions':{'macro_f1':'Unweighted binary F1 mean'},'slices':['fixed-validation'],
            'tolerances':{'metric':1e-10},'inconclusive_rule':'Adaptive validation cannot establish confirmatory superiority.',
            'owner_principal_ids':['research-owner']})}
    contract['G08']=record('G08',{'operations':[{'operation_id':'identity','type':'identity'}],
        'probabilities':{'identity':1.0},'ordering':['identity'],
        'geometry_effects':{'identity':{'signal':'unchanged','labels':'unchanged'}}})
    contract['G01']=record('G01',{'principles':['Retain all attempts and comparison identity'],
        'separation_of_duties':{'required':True,'generator':'proposal','critic':'independent review','executor':'original Manager'},
        'freeze_policy':{'immutable':True,'required':True,'basis':'immutable source references'},
        'exception_policy':{'authority':'required','missing_evidence':'block instead of inferring acceptance'}})
    contract['G13']=record('G13',{'node_types':['Run','Attempt','Result','Plan'],
        'edge_types':['uses','used-by'],'reciprocity':{'uses':'used-by'},'closure':{'required':True}})
    contract['G02']=record('G02',{'references':sorted(k+':'+v['instance_key'] for k,v in contract.items()),
        'source_refs':{k+':'+v['instance_key']:digest(v) for k,v in contract.items()}})
    baseline=record('B01',{'baseline_id':'initial-controlled-baseline','scope':'Fixed binary validation baseline',
        'model':'sha256:'+spec['baseline_model']['sha256'],'data':'G03:g03-controlled','evaluator':'G10:g10-controlled',
        'tolerances':{'metric':1e-10}})
    baseline['content'].pop('source_refs');baseline['content_digest']=digest(baseline['content'])
    rule={'schema':'sdm-scientific-rule/v1','support_predicate':'mechanism_supported',
        'rejection_predicate':'mechanism_rejected','inconclusive_rule':'underpowered_or_no_decisive_result','invalid_rule':'protocol_invalid'}
    experiment={'experiment_id':'controlled-experiment','protocol_channel':'exploratory','promotable':False,
        'hypothesis':seed['brief']['content']['hypothesis'],'variables':{'independent':'model receptive field',
        'dependent':'fixed validation macro F1'},'budget':{'max_submissions':2},'decision_rule':rule,
        'author_principal_ids':['research-owner']}
    spec['sdm_governance']={'schema':'sdm-component-governance/v1','records':{'contracts':contract,
        'baseline':baseline,'experiment':experiment},'data_source':ref(source),'max_submissions':2,
        'not_applicable':{'G%02d'%n:'No spatial, augmentation, robustness, deployment or formal governance claim in this fixed binary exploratory fixture.'
                          for n in (4,5,6,11,12)}}
    return spec,seed


def test_real_original_contracts_baseline_row_and_experiment_kernel(tmp_path):
    spec,intent=material(tmp_path);value=prepare(spec,intent)
    assert all(r['passed'] for r in value['record_checks'])
    assert value['record_checks'][0]['step']==4
    assert value['record_checks'][1]['full_artifact_qualified'] is False
    bundle=AssetBundle.load()
    assert value['record_checks'][1]['gate_digest']==digest(bundle.document('lifecycle-steps.json')['steps'][5]['gate'])
    selected={**spec,'sdm_intent':intent_publish(intent,tmp_path/'intent.json')}
    selected['sdm_governance']=publish(value,tmp_path/'governance.json')
    assert load(selected['sdm_governance'],selected)==value


@pytest.mark.parametrize('fault',['data','split','missing-contract','scope','hypothesis','budget','kernel','legacy-preflight'])
def test_before_effect_failures_cannot_be_review_overrides(tmp_path,fault):
    spec,intent=material(tmp_path)
    if fault=='data':spec['comparison_identity']['source_sha256']='0'*64
    if fault=='split':spec['comparison_identity']['split_sha256']='0'*64
    if fault=='missing-contract':spec['sdm_governance']['records']['contracts'].pop('G10')
    if fault=='scope':spec['sdm_governance']['not_applicable']['G04']='skip'
    if fault=='hypothesis':spec['sdm_governance']['records']['experiment']['hypothesis']='Different unsupported hypothesis'
    if fault=='budget':spec['sdm_governance']['max_submissions']=True
    if fault=='kernel':spec['sdm_governance']['records']['experiment']['variables']={}
    if fault=='legacy-preflight':spec['workload']['version']=1;spec['workload'].pop('engineering_preflight')
    with pytest.raises((ValueError,ContractError)):prepare(spec,intent)


def test_recovery_replays_core_receipt_instead_of_trusting_cached_passes(tmp_path):
    spec,intent=material(tmp_path);value=prepare(spec,intent)
    value['record_checks'][1]['gate_digest']='sha256:'+'0'*64
    policy=publish(value,tmp_path/'forged-cache.json')
    with pytest.raises(ValueError,match='receipt_replay_changed'):load(policy)


def test_data_mutation_blocks_even_with_unchanged_prepared_receipt(tmp_path):
    spec,intent=material(tmp_path);value=prepare(spec,intent);policy=publish(value,tmp_path/'governance.json')
    Path(value['data_source']['path']).write_text('changed',encoding='utf-8')
    with pytest.raises(ValueError,match='evidence_changed'):load(policy)


def test_shared_dispatch_gates_register_budget_without_refunding_unknown(tmp_path,monkeypatch):
    spec,intent=material(tmp_path);value=prepare(spec,intent)
    spec['sdm_intent']=intent_publish(intent,tmp_path/'intent.json')
    spec['sdm_governance']=publish(value,tmp_path/'governance.json')
    atomic_json(tmp_path/'plan.json',{'request':{'explicit_transport_fixture':True}})
    atomic_json(tmp_path/'plan-2.json',{'request':{'explicit_transport_fixture':2}})
    atomic_json(tmp_path/'plan-3.json',{'request':{'explicit_transport_fixture':3}})
    monkeypatch.setattr('research_acceptance.run_kernel',lambda *_:(value['records']['experiment'],value['record_checks'][1]))
    monkeypatch.setattr('component_payload.executed_request_files',lambda *_:({'model.py':(tmp_path/'model.py').read_text(encoding='utf-8')},{}))
    accepted={'manager_plan':ref(tmp_path/'plan.json'),
              'sdm_intent_checks':{'record_checks':intent['record_checks']},'decoded_proof':{'verified':True},
              'payload_manifest':{'code_sha256':'synthetic-transport-only','identities':{'trainer.py':value['protected_trainer_sha256'],'model.py':spec['baseline_model']['sha256']}}}
    state={'rounds':[],'attempts':[]}
    first=submission(spec,accepted,state,True)
    atomic_json(tmp_path/'run-1.json',first);state['governance_runs']=[ref(tmp_path/'run-1.json')]
    assert submission(spec,accepted,state,True)==first
    second=copy.deepcopy(accepted);second['manager_plan']=ref(tmp_path/'plan-2.json')
    atomic_json(tmp_path/'run-2.json',submission(spec,second,state,True));state['governance_runs'].append(ref(tmp_path/'run-2.json'))
    third=copy.deepcopy(accepted);third['manager_plan']=ref(tmp_path/'plan-3.json')
    with pytest.raises(ValueError,match='budget_exhausted'):submission(spec,third,state,True)
    with pytest.raises(ValueError,match='baseline_available'):submission(spec,accepted,{'rounds':[],'attempts':[]},False)


def test_old_frozen_study_is_not_reinterpreted():
    assert project({'component_pipeline':{}},{}) is None


def test_new_rule_validation_is_shared_with_original_scientific_decision():
    rule={'schema':'sdm-scientific-rule/v1','support_predicate':'yes','rejection_predicate':'no',
          'inconclusive_rule':'underpowered_or_no_decisive_result','invalid_rule':'protocol_invalid'}
    assert validate_scientific_rule(rule)==('yes','no')
    result=evaluate_scientific_decision(rule,[{'run_id':'run-1','required_attempt_ids':['attempt-1']}],
        [{'run_id':'run-1','attempt_id':'attempt-1','terminal_status':'successful','accounted':True,
          'validity':'valid','system_failure':False}],
        {'protocol_invalid':False,'underpowered':True,'predicates':{'yes':False,'no':False}})
    assert result['outcome']=='inconclusive'
    bad={**rule,'rejection_predicate':'yes'}
    with pytest.raises(ContractError):validate_scientific_rule(bad)


def test_remote_preflight_is_emitted_without_local_ml_import_or_execution(tmp_path):
    spec,_=material(tmp_path)
    source=Path(__file__).parents[1]/'scripts/public_ts_baseline.py'
    rendered=harness(source.read_bytes(),spec['comparison_identity'],spec['workload'])
    tree=ast.parse(rendered)
    assert any(isinstance(n,ast.FunctionDef) and n.name=='remote_preflight' for n in tree.body)
    main=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='main')
    calls=[n for n in ast.walk(main) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='remote_preflight']
    assert len(calls)==1
    assert b"result['engineering_preflight'] =" in rendered or b"result['engineering_preflight']=" in rendered


def test_loss_or_syntax_does_not_stand_in_for_remote_resume_proof():
    from component_results import verify_engineering_preflight
    with pytest.raises(ValueError,match='actual_remote'):verify_engineering_preflight({'syntax_verified':True})
    proof={'schema':'sdm-remote-engineering-preflight/v1','policy':{'version':1,'max_steps':32,'examples_per_class':2},
           'checks':{'tensor':True,'finite':True,'tiny_fit':True,'resume':False},
           'tiny_steps':8,'total_update_steps':10,'initial_tiny_loss':1.,'final_tiny_loss':.4,'initial_state_restored':True}
    with pytest.raises(ValueError,match='did_not_pass'):verify_engineering_preflight({'engineering_preflight':proof})


def lens_response():
    names=('protocol','statistics','failure_cases','reproducibility','deployment')
    return {'five_lenses':{name:'inconclusive' for name in names},
            'reasons':{name:'Synthetic transport fixture; no actual scientific acceptance claim.' for name in names},
            'deviation_disposition':'none'}


def test_actual_critic_receipt_is_adapted_to_original_s10_checks(tmp_path):
    from model_runner_transport import save
    from research_acceptance import evidence_review
    spec,intent=material(tmp_path);policy=publish(prepare(spec,intent),tmp_path/'governance.json')
    call=tmp_path/'critic';call.mkdir();save(call,'synthetic-binding','completed',{}, {})
    response={'verdict':'approve','reviewer':{'identity':'critic-model','source_ref':str(call/'backend-completed.json')},
              'sdm_evidence_review':lens_response()}
    accepted=evidence_review(policy,response,digest({'results':[]}))
    assert accepted['record']['artifact_type']=='S10'
    assert accepted['record_check']['passed'] and not accepted['record_check']['transition_accepted']
    response['sdm_evidence_review']['deviation_disposition']='unresolved'
    with pytest.raises(ValueError,match='evidence_review_blocked'):evidence_review(policy,response,digest([]))
    response['verdict']='revise'
    assert not evidence_review(policy,response,digest([]))['record_check']['passed']


def test_convenient_authoring_still_uses_original_object_validators(tmp_path):
    spec,intent=material(tmp_path);full=spec['sdm_governance']
    spec['sdm_governance']={'schema':full['schema'],'data_source':full['data_source'],
        'max_submissions':2,'variables':full['records']['experiment']['variables'],'author_principal_ids':['research-owner']}
    value=prepare(spec,intent)
    assert set(value['records']['contracts'])=={'G01','G02','G03','G07','G08','G09','G10','G13'}
    assert all(row['passed'] for row in value['record_checks'])


@pytest.fixture
def integrated(research, monkeypatch):
    """Real local control path; only native/model/platform transports substituted."""
    from component_pipeline import ComponentPipeline
    from component_payload import contract, task_request
    from research_event_service import Inbox
    from lifecycle_rules import asset_inputs
    import test_component_submission_gate as existing
    cfg=research.cfg;cfg['root']=str(research.tmp/'integrated-service');spec=research.spec;tmp=research.tmp/'new-input';tmp.mkdir()
    inputs,intent=material(tmp,cfg['project_id'])
    spec['decision_policy']={'version':4,'target':spec['decision_policy']['target'],
        'budget':{'known_task_uids':[],'max_new_submissions':2}}
    spec.update(comparison_identity=inputs['comparison_identity'],workload=inputs['workload'],submission_gate={'version':2})
    spec['baseline_source']=inputs['baseline_source']
    with (research.profile/'model.py').open('a',encoding='utf-8') as stream:
        stream.write('\ndef build_training_loss(config): raise AssertionError("Synthetic fixture never executes ML")\n')
    inputs['baseline_model']=ref(research.profile/'model.py')
    baseline=inputs['sdm_governance']['records']['baseline']
    baseline['content']['model']='sha256:'+inputs['baseline_model']['sha256']
    baseline['content_digest']=digest(baseline['content'])
    spec['sdm_intent']=intent_publish(intent,tmp/'intent.json')
    spec['sdm_governance']=publish(prepare(inputs,intent),tmp/'governance.json')
    delegation=read_json(research.tmp/'delegation.json');delegation['comparison_identity']=spec['comparison_identity']
    atomic_json(research.tmp/'delegation.json',delegation);spec['delegation']=ref(research.tmp/'delegation.json')
    execution=contract(spec['baseline_source'],tmp/'protected',spec['comparison_identity'],research.manager_cfg,'component',
        training_limits=delegation['training_limits'],next_round=1,workload=spec['workload'])
    parameters=read_json(research.profile/'parameters.json');parameters['comparison_identity']=spec['comparison_identity']
    atomic_json(research.profile/'parameters.json',parameters)
    request,expected,payload=task_request({'root':str(research.profile),
        'files':{n:ref(research.profile/n)['sha256'] for n in ('model.py','parameters.json')}},
        execution,research.parent,cfg['project_id'],1)
    plan=tmp/'manager-plan.json';atomic_json(plan,research.manager.plan(request))
    normalized=tmp/'request.json';atomic_json(normalized,read_json(plan)['request']);spec['initial_request']=ref(normalized)
    spec['initial_source'].update(plan=ref(plan),expected=expected)
    confirmed=tmp/'confirmed.json';atomic_json(confirmed,{'schema':'sdm-confirmed-initial-study/v1',
        'source_ref':delegation['source_ref'],'comparison_identity':spec['comparison_identity'],
        'target':spec['decision_policy']['target'],'training':parameters['training'],'payload_manifest':payload,
        'sdm_intent':spec['sdm_intent'],'sdm_governance':spec['sdm_governance']})
    auth=tmp/'authorization.json';atomic_json(auth,{'schema':'lpai-manager-authorization/v1',
        'plan_sha256':'sha256:'+ref(plan)['sha256'],'direct_user_confirmation':True,
        'source_ref':delegation['source_ref'],'delegation':spec['delegation']})
    spec['initial_submission'].update(authorization=ref(auth),confirmed_plan=ref(confirmed))
    bundle,files=asset_inputs();module=Path(cfg['runtime_root']).parent.parent
    cfg['engine_contract_digest']=bundle.engine_contract_digest
    cfg['normative_manifest']={p.relative_to(module).as_posix():hashlib.sha256(raw).hexdigest() for p,raw in files.items()}
    saved=existing.save
    def reply(folder,bound,status,response,detail):
        response=copy.deepcopy(response)
        if 'hypothesis' in response:
            response.update(hypothesis=intent['brief']['content']['hypothesis'],sdm_brief=intent['brief']['content'])
            from proposal_input import restore
            envelope=read_json(folder/'request.json')
            payload=restore(envelope['request']['evidence'],Path(str(folder).removeprefix('\\\\?\\')).parent.parent)
            if payload['shared']['project']['kind']=='research-replan':
                from test_component_research_continuation import direction
                response['research_revision']=direction('stop-research')
                response['research_revision']['evidence_ids']=[payload['shared']['packet']['context']['sources'][0]['id']]
        else:response['sdm_evidence_review']=lens_response()
        return saved(folder,bound,status,response,detail)
    monkeypatch.setattr(existing,'save',reply)
    research.execution=execution;research.payload=payload
    research.inbox=Inbox(cfg);research.pipeline=ComponentPipeline(research.inbox);research.state=research.pipeline.load()
    return research


def test_initial_review_dispatch_durable_registration_and_recovery_use_same_core(integrated,monkeypatch):
    from research_acceptance import verified_registration
    from component_pipeline import ComponentPipeline
    approve_initial(integrated)
    assert integrated.backend.writes==0
    original=integrated.backend.mutate
    def mutate(request,path):
        disk=read_json(integrated.pipeline.path)
        assert len(disk['governance_runs'])==1  # Crash boundary: durable BEFORE external effect.
        return original(request,path)
    monkeypatch.setattr(integrated.backend,'mutate',mutate)
    integrated.pipeline.step(integrated.state)
    assert integrated.backend.writes==1
    integrated.pipeline.save(integrated.state)
    registered=read_json(integrated.state['governance_runs'][0]['path'])
    verified_registration(integrated.spec,registered,integrated.manager_cfg)
    before=len(integrated.calls)
    recovered=ComponentPipeline(integrated.inbox).load()
    view=project(integrated.cfg,recovered)
    assert len(view['steps'])==13 and view['steps'][7]['status']=='evidence-pending'
    assert view['steps'][8]['status']=='operational-verified'
    assert integrated.backend.writes==1 and len(integrated.calls)==before
    from research_recovery import inspect
    assert inspect(integrated.cfg)['sdm_acceptance']==view
    from research_acceptance import read_projection
    assert read_projection(integrated.cfg,recovered)==view
    changed=copy.deepcopy(recovered);changed['attempts'][0]['stage']='changed'
    with pytest.raises(ValueError,match='stale_or_changed'):read_projection(integrated.cfg,changed)


def test_paused_dispatch_does_not_consume_budget_or_launch_task(integrated):
    approve_initial(integrated)
    (integrated.inbox.root/'PAUSED').write_text('explicit pause fixture')
    integrated.pipeline.step(integrated.state)
    assert integrated.backend.writes==0 and not integrated.state.get('governance_runs')


@pytest.mark.parametrize('next_action',['stop-research','revise-plan'])
def test_complete_evidence_review_original_scientific_decision_and_frontend_projection(integrated,next_action):
    """Transport-controlled end-to-end evidence handoff, not a new ML experiment."""
    from research_acceptance import completion
    from proposal_review import gate, subject
    from research_intent import for_review
    from task_pipeline import digest as runtime_digest
    approve_initial(integrated);integrated.pipeline.step(integrated.state)
    attempt=integrated.state['attempts'][0]
    value=load(integrated.spec['sdm_governance']);root=integrated.tmp/'retained-artifacts';root.mkdir()
    split=value['data_facts']['split'];ids=split['validation_ids'];truth=[i%2 for i in ids]
    (root/'split.json').write_bytes(json.dumps(split,sort_keys=True).encode())
    atomic_json(root/'predictions.json',{'row_ids':ids,'y_true':truth,'y_pred':truth})
    proof={'schema':'sdm-remote-engineering-preflight/v1','policy':value['workload']['engineering_preflight'],
        'checks':{'tensor':True,'finite':True,'tiny_fit':True,'resume':True},'tiny_steps':8,'total_update_steps':10,
        'initial_tiny_loss':1.,'final_tiny_loss':.3,'initial_state_restored':True}
    atomic_json(root/'result.json',{'schema':'sdm-component-result/v1',**value['comparison_identity'],
        'test_used':False,'validation':{'macro_f1':1.,'accuracy':1.},'config':{'seed':42,'epochs':20},
        'engineering_preflight':proof})
    (root/'checkpoint.pt').write_bytes(b'Synthetic transport bytes, not a trained model')
    ready=root/'ready.json';atomic_json(ready,{'schema':'sdm-component-result-ready/v1','valid_training_round':True,
        'uid':'synthetic-verified-task','primary_metric':1.,'artifact_root':str(root),
        'original_plan_sha256':attempt['source']['plan']['sha256'],
        'artifact_sha256':{p.name:ref(p)['sha256'] for p in root.iterdir()}})
    attempt['result']=ref(ready);integrated.state['rounds']=[{'result':ref(ready)}]
    cfg=integrated.pipeline.base_project();cfg['sdm_intent']=for_review(integrated.spec['sdm_intent'])
    cfg['research_replan']={'version':2,'rounds':integrated.state['rounds'],'objective':'Close bounded synthetic verification.'}
    initial=read_json(attempt['submission_acceptance']['path'])
    context=read_json(Path(initial['reviews']['science']['project']['path']).parent/'review-context.json')
    context['subject_digest']=runtime_digest(subject(cfg));context['history']={'retained_results':integrated.state['rounds']}
    context['sources'] += [{'id':'actual-result-'+str(i),'kind':'artifact',**ref(p)} for i,p in enumerate(root.iterdir())]
    folder=integrated.tmp/'closing-review';folder.mkdir();atomic_json(folder/'project.json',cfg);atomic_json(folder/'review-context.json',context)
    # The explicit transport substitute selects the requested next action;
    # acceptance still passes through the original model/semantic/replay code.
    import test_component_research_continuation as control
    original_direction=control.direction
    from unittest.mock import patch
    with patch.object(control,'direction',lambda *_:original_direction(next_action)):
        accepted=gate(cfg,folder)
    assert accepted['status']=='proposal-approved'
    reviewed=read_json(accepted['receipt'])
    plan={'schema':'sdm-research-plan/v1','proposal':reviewed['proposal'],
          'research_revision':reviewed['proposal']['research_revision'],
          'evidence_ids':reviewed['proposal']['research_revision']['evidence_ids'],
          'status':'research-stopped' if next_action=='stop-research' else 'research-plan-approved-awaiting-implementation',
          'review_project_ref':ref(folder/'project.json'),
          'review_root':str(folder),'accepted_review_ref':ref(accepted['receipt'])}
    atomic_json(folder/'research-plan.json',plan)
    closed=completion(integrated.spec,integrated.state,ref(folder/'research-plan.json'))
    assert closed['scientific_decision']['outcome']=='inconclusive'
    assert closed['independent_evidence_review']['record_check']['passed']
    atomic_json(folder/'core-completion.json',closed)
    integrated.state['governance_completion']=ref(folder/'core-completion.json')
    view=project(integrated.cfg,integrated.state)
    assert all(row['status']!='evidence-pending' for row in view['steps'])
    assert view['scientific_qualification'] is False
    if next_action=='revise-plan':
        from component_payload import contract,task_request
        # Follow the reviewed direction through the same code/authority/dispatch
        # gate with a distinct payload. Actual implementation work is covered
        # by the unchanged native-Context regression suite, not faked as ML here.
        integrated.state.pop('governance_completion')
        profile=integrated.tmp/'next-profile';profile.mkdir()
        (profile/'model.py').write_bytes((integrated.profile/'model.py').read_bytes())
        parameters=read_json(integrated.profile/'parameters.json')
        parameters['training'].update(round=2,channels=24)
        atomic_json(profile/'parameters.json',parameters)
        scope=contract(integrated.spec['baseline_source'],integrated.tmp/'next-contract',
            integrated.spec['comparison_identity'],integrated.manager_cfg,'component',
            training_limits=read_json(integrated.spec['delegation']['path'])['training_limits'],
            next_round=2,workload=integrated.spec['workload'])
        request,expected,payload=task_request({'root':str(profile),'files':{n:ref(profile/n)['sha256']
            for n in ('model.py','parameters.json')}},scope,integrated.parent,integrated.cfg['project_id'],2)
        next_root=integrated.tmp/'next-code-review';next_root.mkdir()
        atomic_json(next_root/'request.json',request);atomic_json(next_root/'preflight.json',integrated.manager.plan(request))
        code_cfg=integrated.pipeline.base_project();code_cfg['sdm_intent']=for_review(integrated.spec['sdm_intent'],reviewed['proposal']['sdm_brief'])
        code_cfg['component_code_review']={'accepted_plan':ref(folder/'research-plan.json'),
            'request':ref(next_root/'request.json'),'payload_manifest':payload,'execution_contract':scope}
        atomic_json(next_root/'project.json',code_cfg)
        code_context=copy.deepcopy(context);code_context['subject_digest']=runtime_digest(subject(code_cfg))
        code_context['sources'] += [{'id':'new-code-'+str(i),'kind':'code' if p.suffix=='.py' else 'config',**ref(p)}
                                    for i,p in enumerate([profile/'model.py',profile/'parameters.json',next_root/'request.json',folder/'research-plan.json'])]
        atomic_json(next_root/'review-context.json',code_context)
        code=gate(code_cfg,next_root);assert code['status']=='proposal-approved'
        attempt.update(stage='reviewing-code',science_plan=ref(folder/'research-plan.json'),
            successor_request=ref(next_root/'request.json'),expected=expected,payload_manifest=payload,
            preflight_plan=ref(next_root/'preflight.json'),code_review_project=str(next_root/'project.json'),code_review_root=str(next_root))
        integrated.pipeline.prepare_submission(attempt,next_root,code)
        integrated.pipeline.submit(integrated.state,attempt,next_root)
        assert integrated.backend.writes==2 and len(integrated.state['governance_runs'])==2
        registrations=[read_json(r['path']) for r in integrated.state['governance_runs']]
        assert registrations[0]['gate_digest']==registrations[1]['gate_digest']
        assert registrations[1]['experiment_kernel']['hypothesis']==reviewed['proposal']['sdm_brief']['hypothesis']
        assert registrations[1]['experiment_kernel']['variables']['independent']==reviewed['proposal']['research_revision']['intervention']
        assert not registrations[1]['initial_baseline_establishment'] and registrations[1]['baseline_results']
        return
    # Missing terminal evidence cannot be concealed by a target or model agreement.
    attempt.pop('result')
    with pytest.raises(ValueError,match='terminal_evidence'):completion(integrated.spec,integrated.state,ref(folder/'research-plan.json'))


@pytest.mark.skipif(__import__("os").name!="nt", reason="SDK v4 contained initializer requires a Windows host; validated by Windows offline controls")
def test_new_initializer_activates_shared_profile_without_any_model_or_task_write(integrated,monkeypatch):
    from research_initialize import prepare as initialize
    from test_component_submission_gate import OriginalManager
    from research_event_service import configuration
    monkeypatch.setattr('research_initialize.Manager',lambda config:OriginalManager(config,integrated.backend))
    root=integrated.tmp/'fresh-initializer';root.mkdir();template=copy.deepcopy(integrated.cfg)
    sdk=root/'marker-sdk';(sdk/'openai_codex').mkdir(parents=True)
    (sdk/'openai_codex/client.py').write_text('# Configuration-only substitute, never imported.\n')
    template['component_pipeline']['proposal_review']['model_runner'].update(version=4,backend='sdk',
        sdk_version='0.163.0',sdk_root=str(sdk),context_policy={'version':1,'user_config':'ignore',
            'project_doc_max_bytes':0,'reasoning_effort':'medium','input_kind':'tool-output'})
    atomic_json(root/'template.json',template);atomic_json(root/'request.json',integrated.parent)
    value=load(integrated.spec['sdm_governance'])
    spec={'schema':'sdm-research-initialization/v3','project_id':'fresh-governed-control',
        'source_ref':'Synthetic delegated fixture; no actual authorization or external effects.',
        'objective':'Verify the governed default entry.','template':ref(root/'template.json'),
        'manager_config':integrated.spec['initial_source']['manager_config'],'request_template':ref(root/'request.json'),
        'baseline_source':integrated.spec['baseline_source'],'baseline_model':ref(integrated.profile/'model.py'),
        'comparison_identity':integrated.spec['comparison_identity'],
        'training':read_json(integrated.profile/'parameters.json')['training'],
        'target':integrated.spec['decision_policy']['target'],'workload':integrated.spec['workload'],
        'research_intent':contents(),
        'sdm_governance':{'schema':'sdm-component-governance/v1','data_source':value['data_source'],
            'max_submissions':2,'variables':value['records']['experiment']['variables'],'author_principal_ids':['research-owner']}}
    atomic_json(root/'spec.json',spec)
    args=(root/'spec.json',root/'frozen',root/'store',root/'model-workspace',root/'control/service.json')
    import research_acceptance as core
    original_once=core.once;interrupted={'done':False}
    def interrupted_publish(path,value):
        original_once(path,value)
        if Path(path).name=='research-governance.json' and not interrupted['done']:
            interrupted['done']=True
            raise RuntimeError('Synthetic crash after immutable governance publication')
    monkeypatch.setattr(core,'once',interrupted_publish)
    with pytest.raises(RuntimeError,match='Synthetic crash'):initialize(*args)
    assert integrated.backend.writes==0 and not integrated.calls
    first=(root/'control/research-governance.json').read_bytes()
    result=initialize(*args);assert result['status']=='prepared'
    assert (root/'control/research-governance.json').read_bytes()==first
    cfg=read_json(args[-1]);assert cfg['component_pipeline']['decision_policy']['version']==4
    assert cfg['component_pipeline']['sdm_governance']
    assert cfg['component_pipeline']['initial_source']['expected']['engineering_preflight_policy']==value['workload']['engineering_preflight']
    import subprocess,sys
    checked=subprocess.run([sys.executable,'-I','-S','-X','utf8','-B','-c',
        'import sys,json;sys.path.insert(0,sys.argv[1]);from research_event_service import configuration;configuration(json.load(open(sys.argv[2],encoding="utf-8")))',
        cfg['runtime_root'],str(args[-1])],capture_output=True,text=True,timeout=60)
    assert checked.returncode==0,checked.stderr
    assert initialize(*args)['status']=='already-prepared'
    assert integrated.backend.writes==0 and not integrated.calls


def test_emitted_main_uses_global_random_without_executing_ml(tmp_path):
    import symtable
    spec,_=material(tmp_path)
    source=harness(Path(spec['baseline_source']['path']).read_bytes(),spec['comparison_identity'],spec['workload'])
    table=symtable.symtable(source.decode(), 'trainer.py', 'exec')
    main=next(child for child in table.get_children() if child.get_name()=='main')
    assert main.lookup('random').is_global()
    assert not main.lookup('random').is_local()


def test_supervisor_bootstraps_its_frozen_directory_under_isolation():
    import subprocess,sys
    script=Path(__file__).resolve().parents[1]/'scripts/research_supervisor.py'
    result=subprocess.run([sys.executable,'-I','-S','-B',str(script),'--help'],capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr
    assert '--config' in result.stdout


@pytest.mark.parametrize('learning_rate',[0.001,1e-5,1e-12])
def test_projection_checks_runtime_configuration_with_its_original_digest_producer(tmp_path,learning_rate):
    from task_pipeline import digest as runtime_digest
    spec,intent=material(tmp_path)
    spec['sdm_intent']=intent_publish(intent,tmp_path/'intent.json')
    spec['sdm_governance']=publish(prepare(spec,intent),tmp_path/'governance.json')
    atomic_json(tmp_path/'manager.json',{})
    spec['initial_source']={'manager_config':ref(tmp_path/'manager.json'),'expected':{'config':{'lr':learning_rate}}}
    cfg={'component_pipeline':spec}
    state={'config_digest':runtime_digest(cfg),'rounds':[],'attempts':[]}
    assert project(cfg,state)['steps'][0]['status']=='verified'
    changed=copy.deepcopy(cfg);changed['component_pipeline']['initial_source']['expected']['config']['lr']=0.02
    with pytest.raises(ValueError,match='governance_state_configuration_changed'):project(changed,state)


def baseline_repair_control(integrated):
    """Real initial shared gate/Run; transport effects remain synthetic."""
    from component_research import reference
    approve_initial(integrated);integrated.pipeline.step(integrated.state)
    registered=read_json(reference(integrated.state['governance_runs'][0]))
    accepted=read_json(reference(registered['submission_receipt']))
    observed=read_json(integrated.pipeline.root/'initial/initial-submission-observation.json')
    failure=integrated.tmp/'failed-baseline.json'
    atomic_json(failure,{'schema':'sdm-component-diagnosis-ready/v1','valid_training_round':False,
        'uid':observed['resource']['uid'],'platform_status':'failed',
        'original_plan_sha256':integrated.spec['initial_source']['plan']['sha256']})
    integrated.state['attempts'][0]['result']=ref(failure)
    plan=integrated.tmp/'repair-plan.json'
    atomic_json(plan,{'preparation_recovery':{'version':1},'parent_diagnosis_ref':ref(failure),
        'parent_task_uid':observed['resource']['uid'],'parent_result_sha256':None,'research_goal_met':False,
        'execution_authority':False,'budget_renewed':False,
        'research_revision':{'action':'refine-current','change_level':'tuning'}})
    accepted['science_plan']=ref(plan)
    return accepted,plan,failure


def test_failed_registered_run_can_continue_baseline_establishment_without_a_score(integrated):
    from component_research import reference
    from research_acceptance import baseline_preparation
    accepted,plan,failure=baseline_repair_control(integrated)
    calls=len(integrated.calls);writes=integrated.backend.writes
    proof=baseline_preparation(integrated.spec,accepted,integrated.state,integrated.manager_cfg)
    assert proof['failed_parent']==ref(failure) and proof['registered_parent']==integrated.state['governance_runs'][0]
    assert not proof['valid_training_round'] and integrated.state['rounds']==[]
    assert integrated.backend.writes==writes==1 and len(integrated.calls)==calls


@pytest.mark.parametrize('violation',['uid','plan','terminal','registration'])
def test_baseline_establishment_repair_cannot_claim_an_unaccounted_predecessor(integrated,violation):
    from research_acceptance import baseline_preparation
    accepted,plan,failure=baseline_repair_control(integrated)
    if violation=='registration':integrated.state['governance_runs']=[]
    else:
        value=read_json(failure)
        if violation=='uid':value['uid']='another-task'
        elif violation=='plan':value['original_plan_sha256']='0'*64
        else:value['platform_status']='running'
        atomic_json(failure,value)
        # Bind the changed fixture consistently; the semantic identity check,
        # rather than merely a stale file digest, must still reject it.
        record=read_json(plan);record['parent_diagnosis_ref']=ref(failure);atomic_json(plan,record)
        accepted['science_plan']=ref(plan);integrated.state['attempts'][0]['result']=ref(failure)
    writes=integrated.backend.writes
    with pytest.raises(ValueError,match='baseline_repair_requires_'):
        baseline_preparation(integrated.spec,accepted,integrated.state,integrated.manager_cfg)
    assert integrated.backend.writes==writes==1 and integrated.state['rounds']==[]
