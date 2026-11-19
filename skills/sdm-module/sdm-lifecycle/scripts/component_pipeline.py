"""Resident delivery between original SDM capabilities, not a second science loop.

Scientific decisions remain in proposal_review/ModelRunner and native Codex.
Only typed, frozen local work is registered. Platform effects remain owned by
the original Manager/Guard. Restart reuses jobs and submission identities.
"""
import copy
import hashlib
import time
from pathlib import Path

from component_payload import ref
from component_research import reference, progress, round_evidence
from research_replan import once
from review_storage import atomic_json, locked, read_json
from task_pipeline import digest


def validate(cfg):
    spec=cfg.get('component_pipeline',{})
    from component_submission import enabled
    governed_submission=enabled(spec)
    if 'sdm_governance' in spec:
        from research_acceptance import load as load_governance
        governance=load_governance(spec['sdm_governance'],spec)
        project_profile=spec.get('workload',{}).get('adapter')=='project/v1'
        if (spec.get('decision_policy',{}).get('version')!=(5 if project_profile else 4) or spec['decision_policy'].get('budget')!=
                {'known_task_uids':[],'max_new_submissions':governance['max_submissions']}):
            raise ValueError('same_nonrenewing_governance_and_candidate_budget_required')
        if reference(spec['sdm_governance']['seed']).resolve().is_relative_to(Path(cfg['cwd']).resolve()):
            raise ValueError('governance_outside_model_workspace_required')
    if spec.get('sdm_intent') is not None and spec.get('submission_gate') != {'version':2}:
        raise ValueError('original_sdm_intent_cannot_downgrade_submission_gate')
    if spec.get('submission_gate') == {'version':2}:
        from research_intent import load
        _, intent = load(spec.get('sdm_intent'))
        if (intent['project_id'] != cfg['project_id'] or cfg.get('engine_contract_digest') != intent['engine_contract_digest']
                or not cfg.get('normative_manifest')):
            raise ValueError('pipeline_requires_pinned_original_sdm_intent')
        if reference(spec['sdm_intent']['seed']).resolve().is_relative_to(Path(cfg['cwd']).resolve()):
            raise ValueError('sdm_intent_outside_model_workspace_required')
    if 'workload' in spec:
        from research_workloads import settings
        settings(spec['workload'])
    if (cfg.get('schema')!='sdm-event-service/v3' or spec.get('version') not in (1,2,3,4)
            or not isinstance(spec.get('objective'),str) or not spec['objective'].strip()
            or spec.get('minimum_rounds',3)<(1 if spec.get('version')==4 else 3)
            or spec.get('minimum_revisions',2)<(0 if spec.get('version')==4 else 2)):
        raise ValueError('explicit_three_round_component_pipeline_required')
    for name in ('initial_request','baseline_source','delegation'):
        source=reference(spec[name])
        if source.resolve().is_relative_to(Path(cfg['cwd']).resolve()):
            raise ValueError('pipeline_trusted_inputs_outside_model_workspace_required')
    from component_event_source import settings
    settings(spec['initial_source'],cfg,{'kind':'component-source'})
    if spec['initial_source'].get('version') in (3,4):
        from research_model_runner import guidance_sources
        required = {}; guidance_sources(required)
        declared = {row['path']: row['sha256'] for row in cfg.get('guidance_manifest', [])}
        if any(declared.get(path) != sha.removeprefix('sha256:') for path, sha in required.items()):
            raise ValueError('component_review_guidance_must_be_frozen_before_start')
    if 'code_review_feedback' in spec and (spec['version'] not in (2,3,4) or spec['code_review_feedback']!={'version':1}
            or type(spec['code_review_feedback']['version']) is not int):
        raise ValueError('explicit_code_review_feedback_policy_required')
    for name,kind in (('coordinator_template','coordinator'),('implementation_template','implementation'),('source_template','component-source')):
        if cfg.get('task_templates',{}).get(spec.get(name),{}).get('kind')!=kind:
            raise ValueError('pipeline_typed_templates_required')
    policy=spec.get('proposal_review',{})
    if policy.get('version')!=4 or policy.get('backend')!='model' or policy.get('context_required') is not True:
        raise ValueError('original_condition_driven_model_review_required')
    models=policy.get('models',{})
    if set(models)!={'a','b'} or not all(isinstance(v,str) and v for v in models.values()) or models['a'].casefold()==models['b'].casefold():
        raise ValueError('distinct_proposer_and_critic_models_required')
    comparison=spec.get('comparison_identity',{})
    if set(comparison)!={'protocol','source_sha256','split_sha256','selection'} or any(not isinstance(v,str) or not v for v in comparison.values()):
        raise ValueError('frozen_pipeline_comparison_identity_required')
    history=spec.get('verified_history',[])
    if history:
        actual=progress(history,spec.get('minimum_rounds',3),spec.get('minimum_revisions',2))
        if actual['fair_comparison']!=comparison or 'initial_science_plan' not in spec:
            raise ValueError('cold_resume_requires_verified_history_and_original_successor_plan')
        reference(spec['initial_science_plan'])
    manager=read_json(reference(spec['initial_source']['manager_config']))
    request=read_json(reference(spec['initial_request']))
    delegation=read_json(reference(spec['delegation']))
    if 'initial_submission' in spec:
        initial=spec['initial_submission']
        if (set(initial)!={'version','authorization','confirmed_plan'} or type(initial['version']) is not int
                or initial['version'] not in (1,2) or history
                or initial['version']==2 and (not governed_submission or spec['version']!=4)):
            raise ValueError('explicit_fresh_initial_submission_required')
        original=read_json(reference(spec['initial_source']['plan']))
        confirmed=read_json(reference(initial['confirmed_plan']))
        auth=read_json(reference(initial['authorization']))
        if ('sdm_intent' in confirmed or 'sdm_intent' in spec) and (
                confirmed.get('sdm_intent') != spec.get('sdm_intent')
                or spec.get('submission_gate') != {'version':2}):
            raise ValueError('initial_sdm_intent_cannot_be_removed_or_replaced')
        if confirmed.get('sdm_governance') != spec.get('sdm_governance'):
            raise ValueError('initial_governance_cannot_be_removed_or_replaced')
        if (original['request']!=request or confirmed.get('comparison_identity')!=comparison
                or confirmed.get('source_ref')!=delegation.get('source_ref')
                or confirmed.get('target')!=spec.get('decision_policy',{}).get('target')
                or confirmed.get('training')!=spec['initial_source'].get('expected',{}).get('config')
                or confirmed.get('payload_manifest',{}).get('code_sha256')!=spec['initial_source'].get('expected',{}).get('code_sha256')
                or auth.get('plan_sha256')!='sha256:'+spec['initial_source']['plan']['sha256']
                or auth.get('delegation')!=spec['delegation']):
            raise ValueError('initial_submission_requires_bound_plan_and_standing_delegation')
    if (delegation.get('schema')!='sdm-research-delegation/v1' or delegation.get('allow_successor_submission') is not True
            or not delegation.get('source_ref') or delegation.get('project_id')!=cfg['project_id']
            or delegation.get('comparison_identity')!=comparison
            or delegation.get('namespace')!=manager['namespace'] or delegation.get('owner')!=manager['owner']
            or delegation.get('component_uid')!=request['uid'] or request['action']!='task.submit'):
        raise ValueError('original_user_delegation_scope_required')
    from required_skills import inspect
    inspect(manager)
    if spec['version'] in (3,4):
        limits=delegation.get('training_limits',{})
        if (set(limits)!={'min_epochs','max_epochs','default_epochs'}
                or any(type(v) is not int for v in limits.values())
                or not ((1<=limits['min_epochs']<=limits['default_epochs']<=limits['max_epochs']) if spec.get('workload',{}).get('adapter')=='project/v1' else (10<=limits['min_epochs']<=limits['default_epochs']<=limits['max_epochs']<=30))):
            raise ValueError('structured_delegated_training_limits_required')
        if 'resume_approved_plan' in spec or 'resume_result' in spec:
            raise ValueError('cross_runtime_review_replay_requires_original_frozen_verifier')
    if spec['version']==4:
        from component_candidates import policy
        policy(spec.get('decision_policy'))
    return spec


class ResearchPipeline:
    def __init__(self,inbox):
        self.inbox=inbox;self.spec=validate(inbox.cfg)
        self.root=inbox.root/'component-pipeline';self.root.mkdir(parents=True,exist_ok=True)
        self.path=self.root/'pipeline.json'
        self.origin=self.root/'host-binding.json'
        once(self.origin,{'schema':'sdm-component-host-binding/v1','configuration_digest':digest(inbox.cfg)})
        self.manager_config=read_json(reference(self.spec['initial_source']['manager_config']))

    def load(self):
        value=read_json(self.path) if self.path.exists() else {
            'schema':'sdm-component-pipeline-state/v1','config_digest':digest(self.inbox.cfg),
            'rounds':copy.deepcopy(self.spec.get('verified_history',[])),
            'observed_task_uids':copy.deepcopy(self.spec.get('observed_task_uids',[])),
            'attempts':[{'id':'initial','source':self.spec['initial_source'],
                'stage':'initial-submitting' if self.spec.get('initial_submission') else 'watching','poll_at':0,**({'science_plan':self.spec['initial_science_plan']}
                    if self.spec.get('initial_science_plan') else {})}],'status':'running'}
        if value.get('config_digest')!=digest(self.inbox.cfg):raise ValueError('pipeline_configuration_changed')
        return value

    def save(self,state,health=None):
        atomic_json(self.path,state)
        # A derived status view must not fail or replay a research operation.
        try:
            if health is not None:
                atomic_json(self.root/'health.json',{**health,'config_sha256':digest(self.inbox.cfg),
                    'state_sha256':digest(state)})
            from research_acceptance import project as acceptance_project,publish_projection
            publish_projection(self.inbox.cfg,state,acceptance_project(self.inbox.cfg,state))
            from research_progress import publish
            publish(self.inbox.cfg,state)
        except Exception as error:
            try:
                atomic_json(self.root/'progress-error.json',{'status':'projection-failed',
                    'error_type':type(error).__name__,'at':time.time(),'research_operation_retried':False})
            except OSError:pass  # Optional projection cannot override the committed ledger.

    def enqueue(self,label,template,data):
        from research_drain import requested,DrainRequested
        with self.inbox.condition:
            if requested(self.inbox):raise DrainRequested()
            from research_event_service import digest as event_digest
            binding='component-pipeline:'+label+':'+event_digest(data)
            self.inbox.register(binding,{'template':template,'origin':ref(self.origin),'data':data})
            state={'binding':binding,'phase':'exited','registered_input_digest':event_digest(data)}
            event={'schema':'sdm-completion-event/v1','binding':binding,'phase':'exited',
                'state':state,'state_sha256':event_digest(state)}
            return self.inbox.accept(event)['id']

    def coordinator(self,mode,project,output):
        return self.enqueue(mode,self.spec['coordinator_template'],
            {'mode':mode,'project':ref(project),'output':str(output)})

    def completed(self,job):
        with self.inbox.db() as db:
            row=db.execute('SELECT status FROM jobs WHERE id=?',(job,)).fetchone()
        if row is None:raise ValueError('original_pipeline_job_missing')
        if row['status'] in ('received','preparing','running'):return None
        if row['status']!='completed':
            raise ValueError('pipeline_original_job_'+row['status']+'_reconcile_only')
        receipt=read_json(self.inbox.root/job/'result.json')
        if receipt.get('job_id')!=job or receipt.get('status')!='completed':
            raise ValueError('original_pipeline_receipt_changed')
        return receipt['detail']

    def base_project(self):
        return {**({'sdm_governance':copy.deepcopy(self.spec['sdm_governance'])} if self.spec.get('sdm_governance') else {}),
                'platform_requirements':self.manager_config['platform_requirements'],
                'proposal_review':self.spec['proposal_review']}

    def record_result(self,state,current,folder):
        ready=read_json(reference(current['result']))
        rows=copy.deepcopy(state['rounds'])
        if ready.get('valid_training_round') is True:
            actual=round_evidence(current['result'])
            if actual['fair_comparison']!=self.spec['comparison_identity']:
                raise ValueError('pipeline_actual_comparison_identity_changed')
            matches=[i for i,row in enumerate(rows) if round_evidence(row['result'])['uid']==actual['uid']]
            if len(matches)>1:raise ValueError('duplicate_verified_task_uid_requires_reconciliation')
            if matches and rows[matches[0]]['result']!=current['result']:
                previous=read_json(reference(rows[matches[0]]['result']))
                if (previous['artifact_sha256']!=ready['artifact_sha256']
                        or previous.get('original_plan_sha256')!=ready.get('original_plan_sha256')):
                    raise ValueError('same_task_readback_artifacts_changed')
                rows[matches[0]]={**rows[matches[0]],'result':current['result']}
            if not matches:
                row={'result':current['result']}
                if rows:
                    row['accepted_plan']=current['science_plan']
                elif current.get('science_plan') and read_json(reference(current['science_plan'])).get('preparation_recovery'):
                    row['preparation_plan']=current['science_plan']
                rows.append(row)
            state['rounds']=rows
            state['goal_assessment']=progress(rows,self.spec.get('minimum_rounds',3),self.spec.get('minimum_revisions',2))
        elif ready.get('schema')!='sdm-component-diagnosis-ready/v1':
            raise ValueError('pipeline_verified_result_or_diagnosis_required')
        from research_continuation import enabled
        if not rows and not enabled(self.inbox.cfg):
            current.update(stage='needs-attention',reason='initial-baseline-execution-failed',diagnosis=current['result'])
            once(folder/'initial-failure.json',{'status':'initial-baseline-execution-failed',
                'diagnosis':current['result'],'model_calls':0,'platform_writes':0,
                'next':'Repair the evidenced input/runtime prerequisite before any scientific ranking or new submission.'})
            return
        return ready,rows

    def replan(self,state,current,folder):
        if self.spec.get('workload',{}).get('adapter')=='project/v1':
            from project_pipeline_facts import replan
            return replan(self,state,current,folder)
        recorded=self.record_result(state,current,folder)
        if recorded is None:return
        ready,rows=recorded
        config=self.base_project()
        delegation=read_json(reference(self.spec['delegation']))
        execution_limits=delegation.get('resources','Preserve the delegated component resource scope.')
        if self.spec['version'] in (3,4):
            import json
            execution_limits += '\nHard training limits: '+json.dumps(delegation['training_limits'],sort_keys=True)
        spec={'version':2,'parent_route':'lpai-component','result_contract':'sdm-binary-classification/v1',
            'objective':self.spec['objective']+'\nBinding execution limits: '+execution_limits,
            'rounds':rows,'minimum_rounds':self.spec.get('minimum_rounds',3),
            'minimum_revisions':self.spec.get('minimum_revisions',2),
            'constraints':'Original explicit research delegation, fixed protocol and component resources; all ML remote. '
                'Choose a result-driven informative intervention; ordinary experiment completion is not project completion. '
                +execution_limits,
            'sources':[self.spec['baseline_source'],self.spec['initial_request'],current['source']['plan']]}
        spec['sources'].extend(self.spec.get('additional_evidence',[]))
        if self.spec.get('sdm_governance'):spec['sources'].append(self.spec['sdm_governance']['seed'])
        refusals=[*self.spec.get('previous_implementation_feedback',[]),*state.get('implementation_block_history',[]),
            *current.get('capability_feedback',[])]
        if refusals:
            from component_scientific_evidence import history as refusal_history
            spec['sources'].extend([refusal_history(refusals,folder),ref(folder/'research-refusal-index.json')])
        if self.spec['version']==4 and rows:
            spec.update(version=3,decision_policy=self.spec['decision_policy'],
                observed_task_uids=list(dict.fromkeys(
                    state.get('observed_task_uids',[])+[round_evidence(row['result'])['uid'] for row in rows]+
                    [read_json(reference(attempt['result']))['uid'] for attempt in state['attempts'] if attempt.get('result')])))
            state['observed_task_uids']=spec['observed_task_uids']
        if self.spec['version'] in (3,4) and rows:
            from component_research import analyze
            analysis=folder/'result-analysis.json';once(analysis,analyze(rows))
            current['analysis']=ref(analysis);spec['sources'].append(current['analysis'])
            from component_diagnostics import prepare
            diagnostics=prepare(rows,folder/'existing-diagnostics');spec['sources'].extend(diagnostics)
            # The scientist must see the actual executed implementation, not
            # infer it from baseline code or a truncated compressed command.
            if ready.get('valid_training_round') is True:
                from component_payload import executed_request_files
                from component_event_source import publish_bytes
                parent_plan=read_json(reference(current['source']['plan']))
                result=round_evidence(current['result'])['result']
                files,proof=executed_request_files(parent_plan['request'],result['code_sha256'])
                code_root=folder/'executed-parent';code_root.mkdir(exist_ok=True)
                for name,text in files.items():
                    path=code_root/name;publish_bytes(path,text.encode('utf-8'));spec['sources'].append(ref(path))
                proof_path=code_root/'proof.json'
                once(proof_path,{**proof,'manager_plan':current['source']['plan'],'result':current['result']})
                spec['sources'].append(ref(proof_path))
                if current.get('science_plan'):spec['sources'].append(current['science_plan'])
        if self.spec['version']==4 and rows:
            from component_candidates import recipe_history
            recipes=recipe_history(rows,folder);spec['sources'].extend([recipes,ref(folder/'historical-recipe-index.json')])
            from component_payload import contract
            capabilities=contract(self.spec['baseline_source'],folder/'research-capabilities',self.spec['comparison_identity'],
                self.manager_config,read_json(reference(self.spec['initial_request']))['uid'],
                training_limits=delegation['training_limits'],next_round=len(rows)+1,workload=self.spec.get('workload'))
            spec['sources'].append(capabilities)
            interface=read_json(reference(capabilities))
            spec['sources'].extend(interface['protected_inputs'])
            spec['constraints']+=(' Actual protected trainer and execution contract are supplied before scientific approval. '
                'Only model.py and parameters.json are editable. Verify every required measurement is available; '
                'do not require extra raw/EMA evaluation, custom trainer diagnostics or trainer/global access unless '
                'the supplied interface implements them. A missing capability is evidence for plan revision, not a '
                'manifest defect or permission to modify protected code. Do not repeat a blocked unexecuted plan unchanged.')
            spec['constraints']+=(' The verified recipe index supplies every measured score and accepted intervention; '
                'full model/training records remain registered. Inspect relevant negative neighbors and treatment-specific '
                'refusals, not every past proposal indiscriminately. The original coordinator independently rejects exact '
                'recipe replays before dispatch. A controlled experiment needs a grounded falsifiable reason, not proof '
                'that it is globally superior or will succeed. Request only raw evidence that can change this decision.')
            spec['constraints']+=(' The measured incumbent trajectory and persistent-error summary distinguish '
                'research activity from actual progress. Address what the failed interventions changed in the '
                'mechanism assessment and why this next experiment has greater decision value than obtaining '
                'discriminating evidence or changing assumptions. A new AST alone is not a scientific reason. '
                'These observed counts impose no trial cap and do not require replacing a model mechanically.')
            if self.spec['decision_policy']['version']==3:
                spec['constraints']+=(' EXPLICIT REPRODUCTION EXCEPTION: this operational verification authorizes '
                    'the exact baseline recipe named in decision_policy.verification, with a new actual task UID. '
                    'It is not a novelty claim. Preserve all scientific model/optimizer/seed/settings and accept '
                    'only the bound reproduction scope; do not declare that authorized baseline replay a defect.')
            from component_candidates import registry
            candidates=registry(rows,spec['decision_policy'],spec['observed_task_uids'])
            path=folder/'candidate-registry.json';once(path,candidates)
            current['candidate_registry']=ref(path);spec['sources'].append(ref(path))
            state['incumbent']=candidates['incumbent']
            state['candidate_goal']=candidates['goal'];state['submission_budget']=candidates['budget']
            state['goal_assessment']={**state['goal_assessment'],
                'chain_qualification_met':state['goal_assessment']['goal_met'],
                'goal_met':candidates['goal']['met'],'candidate_goal':candidates['goal'],
                'submission_budget':candidates['budget']}
            # Give both scientists the best actual executable recipe, not only
            # the latest inferior code. Checkpoint is a reference, not a warm
            # start permission; a new trial normally initializes from scratch.
            from component_payload import executed_request_files
            from component_event_source import publish_bytes
            incumbent=candidates['incumbent']
            if incumbent.get('manager_plan'):
                original=read_json(reference(incumbent['manager_plan']))
                result=round_evidence(incumbent['result'])['result']
                files,proof=executed_request_files(original['request'],result['code_sha256'])
                best_root=folder/'executed-incumbent';best_root.mkdir(exist_ok=True)
                for name,text in files.items():
                    code=best_root/name;publish_bytes(code,text.encode('utf-8'));spec['sources'].append(ref(code))
                proof_path=best_root/'proof.json'
                once(proof_path,{**proof,'manager_plan':incumbent['manager_plan'],'result':incumbent['result']})
                spec['sources'].extend([ref(proof_path),incumbent['manager_plan']])
            code=[reference(r) for r in spec['sources'] if Path(r['path']).parent.name in ('executed-parent','executed-incumbent')]
            # Tiny model/parameters/proof of the incumbent precede duplicated
            # trainer text and large provenance registries. Both raw curves
            # for latest and best precede older full trajectories.
            decision_indexes=[folder/'historical-recipe-index.json']+([folder/'research-refusal-index.json'] if refusals else [])
            priority=decision_indexes+[reference(diagnostics[0])]+[p for p in code if p.name!='trainer.py']+[
                reference(capabilities),*[reference(r) for r in interface['protected_inputs']],reference(recipes),analysis]
            priority.extend(p for row in (rows[-1],next(row for row in rows if row['result']==incumbent['result']))
                for p in round_evidence(row['result'])['sources'] if p.name=='result.json')
            priority.extend([path,*[p for p in code if p.name=='trainer.py']])
            config['proposal_review']=copy.deepcopy(config['proposal_review'])
            config['proposal_review']['source_budget']={'version':1,'serialized_bytes':96*1024,
                'max_file_bytes':32768,'priority':[str(p) for p in dict.fromkeys(priority)]}
        if ready.get('valid_training_round') is not True:
            spec['parent_diagnosis']=current['result']
            spec['constraints']+=(' This terminal task is not a comparable result. Diagnose its actual error and '
                'propose a concrete supported repair within model.py/parameters.json; an approved repair proceeds '
                'through original implementation, independent code review and submission checks. '
                'Do not repeat its recipe, change protected data/evaluation/preflight, or infer a quality score.')
            if rows:
                from component_payload import executed_request_files
                from component_event_source import publish_bytes
                failed=read_json(reference(current['source']['plan']))
                files,proof=executed_request_files(failed['request'],current['source']['expected'].get('code_sha256'))
                code_root=folder/'executed-parent';code_root.mkdir(exist_ok=True)
                for name,text in files.items():
                    path=code_root/name;publish_bytes(path,text.encode('utf-8'));spec['sources'].append(ref(path))
                path=code_root/'proof.json'
                once(path,{**proof,'manager_plan':current['source']['plan'],'diagnosis':current['result'],
                    'scope':'Failed-task payload, never a verified candidate.'})
                spec['sources'].append(ref(path))
        if not rows:
            spec['preparation_recovery']={'version':1}
            spec['constraints']+=(' No valid training baseline exists. Diagnose the actual failed task and repair only '
                'evidenced preparation/model-interface errors within the existing execution contract. Do not rank '
                'the failure, claim quality improvement, change the measurement protocol or repeat the failed recipe. '
                'If protected data/runtime capabilities or permissions are missing, identify the concrete prerequisite.')
            from component_payload import contract
            capabilities=contract(self.spec['baseline_source'],folder/'preparation-capabilities',self.spec['comparison_identity'],
                self.manager_config,read_json(reference(self.spec['initial_request']))['uid'],
                training_limits=delegation['training_limits'],next_round=1,workload=self.spec.get('workload'))
            spec['sources'].append(capabilities)
            spec['sources'].extend(read_json(reference(capabilities))['protected_inputs'])
            from component_payload import executed_request_files
            from component_event_source import publish_bytes
            failed=read_json(reference(current['source']['plan']))
            files,proof=executed_request_files(failed['request'],current['source']['expected'].get('code_sha256'))
            code_root=folder/'executed-parent';code_root.mkdir(exist_ok=True)
            for name,text in files.items():
                path=code_root/name;publish_bytes(path,text.encode('utf-8'));spec['sources'].append(ref(path))
            path=code_root/'proof.json'
            once(path,{**proof,'manager_plan':current['source']['plan'],'diagnosis':current['result'],
                'scope':'Declared failed-task payload under original verified Manager identity, not a successful training result.'})
            spec['sources'].append(ref(path))
        config['research_replan']=spec
        if self.spec.get('sdm_intent') is not None:
            from research_intent import for_review
            config['sdm_intent']=for_review(self.spec['sdm_intent'])
        project=folder/'replan-project.json';once(project,config)
        current['replan_root']=str(folder/'replan')
        current['job']=self.coordinator('research-replan',project,folder/'replan')
        if current.get('result_handoff') is not None:
            current['result_handoff'].update(model_requested=True,analysis_enqueued_at=time.time(),job_id=current['job'])
        current['stage']='reviewing-plan'

    def implementation(self,current,folder,plan):
        from component_payload import contract
        bound=contract(self.spec['baseline_source'],folder/'protected',self.spec['comparison_identity'],
            self.manager_config,read_json(reference(self.spec['initial_request']))['uid'],
            **({'training_limits':read_json(reference(self.spec['delegation']))['training_limits'],
                'next_round':read_json(plan)['goal_assessment']['valid_training_rounds']+1}
                if self.spec['version'] in (3,4) else {}),workload=self.spec.get('workload'))
        config=self.base_project();config.pop('proposal_review')
        config['research_implementation']={'version':2 if self.spec['version'] in (2,3,4) else 1,'approved_plan':ref(plan),
            'implementation_request':ref(plan.parent/'implementation-request.json'),
            'review_project':ref(plan.parent/'replan-config.json'),'review_root':str(plan.parent),
            'workspace':str(Path(self.inbox.cfg['cwd'])/current['id']),
            'execution_contract':bound,'sources':[current['result'],self.spec['baseline_source']]}
        if self.spec['version']==4:
            config['research_implementation']['capability_feedback']=self.spec.get('implementation_feedback',{'version':1})
        if read_json(plan).get('parent_diagnosis_ref'):
            replan=read_json(plan.parent/'replan-config.json')['research_replan']
            config['research_implementation']['sources'].extend(r for r in replan['sources']
                if Path(r['path']).parent.name=='executed-parent')
        if self.spec['version']==4 and (read_json(plan).get('selected_candidate') or read_json(plan).get('candidate_management')):
            from component_candidates import selected_reference
            selected=selected_reference(read_json(plan))
            config['research_implementation']['sources'].append(selected['result'])
            replan=read_json(plan.parent/'replan-config.json')['research_replan']
            config['research_implementation']['sources'].extend(r for r in replan['sources']
                if Path(r['path']).parent.name in ('executed-parent','executed-incumbent'))
            from component_payload import executed_request_files
            from component_event_source import publish_bytes
            original=read_json(reference(selected['manager_plan']))
            actual=round_evidence(selected['result'])['result']
            files,proof=executed_request_files(original['request'],actual['code_sha256'])
            base_root=folder/'selected-base';base_root.mkdir(exist_ok=True)
            for name,text in files.items():
                path=base_root/name;publish_bytes(path,text.encode('utf-8'))
                config['research_implementation']['sources'].append(ref(path))
            path=base_root/'proof.json'
            once(path,{**proof,'selected_candidate':selected,'decision':read_json(plan)['research_revision']['candidate_decision']})
            config['research_implementation']['sources'].append(ref(path))
        project=folder/'implementation-project.json';once(project,config)
        current.update(implementation_project=str(project),implementation_root=str(folder/'implementation'),
            science_plan=ref(plan),execution_contract=bound,stage='preparing-implementation')
        current['job']=self.coordinator('prepare-implementation',project,folder/'implementation')

    def freeze_implementation(self,current,folder):
        project=Path(current['implementation_project'])
        if self.spec['version'] in (2,3,4):
            config=read_json(project)
            config['research_implementation']['native_completion']={
                'job_id':current['job'],'receipt':ref(self.inbox.root/current['job']/'result.json'),
                'implementation_job':ref(Path(current['implementation_root'])/'implementation-job.json')}
            project=folder/('freeze-project-'+current['job']+'.json');once(project,config)
        job=self.coordinator('freeze-implementation',project,Path(current['implementation_root']))
        current.update(job=job,freeze_project=str(project),stage='freezing')

    def capability_replan(self,state,current,folder,result):
        feedback=read_json(reference(result['feedback']))
        if (feedback.get('schema')!='sdm-implementation-capability-gap/v1'
                or feedback.get('approved_plan')!=current['science_plan']
                or feedback.get('execution_contract')!=current['execution_contract']
                or feedback.get('execution_authority') is not False):
            raise ValueError('bound_implementation_capability_feedback_required')
        previous=current.setdefault('capability_feedback',[])
        contract=read_json(reference(current['execution_contract']))
        signature=digest({'parent_result':current['result']['sha256'],
            'kind':feedback.get('reported_kind'),'claim':' '.join(feedback.get('implementer_claim','').split()),
            'contract':{k:v for k,v in contract.items() if k!='protected_inputs'},
            'protected_inputs':sorted(row['sha256'] for row in contract.get('protected_inputs',[]))})
        signatures=current.setdefault('capability_signatures',[])
        repeated=any(row['signature']==signature for row in signatures) or result['feedback'] in previous
        signatures.append({'signature':signature,'feedback':result['feedback']})
        if result['feedback'] not in previous:previous.append(result['feedback'])
        if result['feedback'] not in state.setdefault('implementation_block_history',[]):state['implementation_block_history'].append(result['feedback'])
        if repeated:
            current.update(stage='needs-attention',reason='same_capability_gap_requires_host_resolution',
                continuation={'phase':'blocked','category':'repeated-implementation-claim-with-unchanged-contract',
                    'next_action':'independent-host-diagnosis-required','signature':signature,
                    'automatic_resubmit':False,'model_retry_authorized':False})
            return
        target=folder/('capability-replan-'+result['feedback']['sha256'][:16])
        current['continuation_folder']=str(target)
        self.replan(state,current,target)

    def correct_implementation(self,current,folder,result):
        if self.spec['version'] not in (2,3,4):raise ValueError('implementation_feedback_requires_new_pipeline_v2')
        from implementation_validation import check_feedback
        config=read_json(current['freeze_project']);selected=config['research_implementation']
        feedback=check_feedback(result['feedback'],selected)
        if (feedback['native_completion']!=selected['native_completion']
                or result.get('failure_signature')!=feedback['failure_signature']
                or selected['approved_plan']!=current['science_plan']):
            raise ValueError('original_completed_implementation_feedback_required')
        history=current.get('implementation_failures',[])
        row={'signature':feedback['failure_signature'],'feedback':result['feedback'],
            'freeze_project':ref(current['freeze_project']),'native_completion':feedback['native_completion']}
        if any(item['signature']==row['signature'] for item in history):
            current.update(stage='needs-attention',reason='implementation-validation-stalled',
                           implementation_failures=[*history,row]);return
        # Register before advancing the durable stage. A crash here reuses the
        # same feedback-bound job; it never resends an unknown native call.
        # Keep Windows review paths bounded across repeated corrections. The
        # full feedback hash stays in the immutable project/job; a prefix
        # collision is rejected by once(), never silently reused.
        target=self.inbox.root/'i'/result['feedback']['sha256'][:24]
        project=target/'project.json'
        config['research_implementation'].pop('native_completion')
        config['research_implementation']['validation_feedback']=result['feedback']
        once(project,config)
        job=self.coordinator('prepare-implementation',project,target)
        current.update(job=job,stage='preparing-implementation',implementation_project=str(project),
            implementation_root=str(target),implementation_failures=[*history,row])

    def code_review(self,state,current,folder,profile):
        from component_payload import task_request
        from proposal_review import subject
        if self.spec.get('code_review_feedback'):
            signature=digest({'reason':'original_critic_revision_required','files':profile['files']})
            if any(row['signature']==signature for row in current.get('implementation_failures',[])):
                current.update(stage='needs-attention',reason='implementation-code-review-stalled');return
            folder=self.inbox.root/'c'/ref(Path(current['implementation_root'])/'implemented-profile.json')['sha256'][:24]
        request,expected,manifest=task_request(profile,current['execution_contract'],
            read_json(reference(self.spec['initial_request'])),self.inbox.cfg['project_id'],len(state['rounds'])+1)
        plan=read_json(reference(current['science_plan']));proposal=plan['proposal']
        parameters=read_json(Path(profile['root'])/read_json(reference(current['execution_contract']))['parameters_file'])['training']
        # All five fields describe the exact accepted intervention and runtime.
        request['task_info']={'problem_statement':plan['objective'],
            'solution_approach':plan['research_revision']['intervention'],
            'expected_result':str(proposal['expected_observations']),
            'expected_duration':parameters['expected_minutes'],
            'expected_duration_desc':str(proposal['cost_and_queue'])+'; '
                +str(parameters['epochs'])+' epochs, retained frozen component resources; bounded by payload watchdog.'}
        request_path=folder/'successor-request.json';once(request_path,request)
        current.update(successor_request=ref(request_path),expected=expected,payload_manifest=manifest)
        config=self.base_project();config['component_code_review']={
            'implemented_profile':ref(Path(current['implementation_root'])/'implemented-profile.json'),
            'accepted_plan':current['science_plan'],'execution_contract':current['execution_contract'],
            'request':ref(request_path),'payload_manifest':manifest}
        if self.spec.get('sdm_intent') is not None:
            from research_intent import for_review
            config['sdm_intent']=for_review(self.spec['sdm_intent'],proposal['sdm_brief'])
        if self.spec.get('code_review_feedback'):
            config['proposal_review']=copy.deepcopy(config['proposal_review'])
            config['proposal_review']['revision_target']='implementation'
        project=folder/'code-review-project.json'
        sources=[Path(profile['root'])/name for name in profile['files']]
        sources.extend([reference(current['execution_contract']),reference(current['science_plan']),request_path])
        if self.spec.get('sdm_governance'):sources.append(reference(self.spec['sdm_governance']['seed']))
        scientific_sources=[]
        if self.spec['version']==4:
            from component_scientific_evidence import handoff
            scientific_sources=[reference(r) for r in handoff(plan,folder/'scientific-evidence')]
            sources.extend(scientific_sources)
        contract_value=read_json(reference(current['execution_contract']))
        sources.extend(reference(row) for row in contract_value['protected_inputs'])
        if self.spec['version'] in (3,4):
            sources.extend([reference(self.spec['baseline_source']),reference(current['result']),
                            reference(current['source']['plan'])])
            history_rows=state['rounds']
            if self.spec['version']==4 and self.spec['decision_policy']['version'] in (2,3) and history_rows:
                # Same raw history used by science, without one source per
                # completed experiment becoming an accidental trial limit.
                history_path=Path(current['replan_root'])/'historical-results.jsonl'
                sources.append(history_path)
                history_rows=[state['rounds'][-1]]
            for row in history_rows:
                # Scientific review owns the full predictions/splits. The
                # implementation gate retains original metric trajectories;
                # do not duplicate every sample table in every code dialogue.
                sources.extend(p for p in round_evidence(row['result'])['sources'] if p.name=='result.json')
            # Current platform evidence is a read-only Manager plan, not a
            # model assertion or the stale parent component snapshot. No
            # Guard intent or write is created until the original code gate.
            from research_execution import Manager
            preflight_path=folder/'manager-preflight.json'
            if not preflight_path.exists():once(preflight_path,Manager(self.manager_config).plan(request))
            preflight=read_json(preflight_path)
            normalized=preflight.get('request',{})
            if ({k:v for k,v in normalized.items() if k in request}!=request
                    or set(normalized)-set(request)-{'component_settings'}):
                raise ValueError('reviewed_current_manager_preflight_changed')
            current['preflight_plan']=ref(preflight_path)
            runtime=copy.deepcopy(request['data'])
            for section in ([runtime['lpai_task'],*runtime['lpai_task']['roles']] if 'lpai_task' in runtime else []):
                section['cmd']={'sha256':hashlib.sha256(section['cmd'].encode()).hexdigest(),
                                'projection':'command bytes retained in original request and decoded proof'}
            operational=folder/'current-execution-binding.json'
            once(operational,{'schema':'sdm-current-component-execution-binding/v1',
                'manager_preflight':ref(preflight_path),'request':ref(request_path),
                'observed_at':preflight['created_at'],'current_component':preflight['baseline'],
                'requested_native_data':runtime,'component_settings':normalized['component_settings'],
                'watchdog':{'declared_timeout_seconds':1800,'kill_after_seconds':30,
                    'remote_failure_or_cancel_cleanup_exercised':False},
                'submission_policy':'Original Manager apply re-reads the exact stable component configuration before a single Guard intent. '
                    'Unknown writes reconcile only; no automated stop, retry or transport fallback.',
                'qualification':'Current official read and static command contract; not a claim of platform cancellation/cleanup or availability qualification.',
                'execution_authority':False})
            from component_submission import execution_binding_view
            binding_view=execution_binding_view(ref(preflight_path),preflight_path,folder)
            sources.extend([preflight_path,operational,binding_view])
        from component_payload import verify_request_payload
        proof=folder/'payload-verification.json';once(proof,verify_request_payload(request,manifest))
        sources.append(proof)
        if self.spec['version'] in (3,4):
            from proposal_review import FIELDS
            from component_execution_evidence import produce as operational_evidence
            def read_progress(value):
                current['execution_preflight']=value
                self.save(state,health={'status':'running','stage':current['stage'],'at':time.time(),
                    'operation_stage':value['phase'],'model_calls_during_wait':0})
            verified=operational_evidence(Manager(self.manager_config),request,current['source']['plan'],
                current['result'],state['rounds'],folder/'original-execution-evidence',
                **({'preparation_recovery':plan['preparation_recovery']} if plan.get('preparation_recovery') else {}),
                **({'reproduction_scope':self.spec['decision_policy']['verification']}
                    if self.spec['version']==4 and self.spec['decision_policy']['version']==3 else {}),workload=self.spec.get('workload'),on_progress=read_progress)
            sources.append(reference(verified))
            for item in read_json(reference(verified))['raw_evidence']:sources.append(reference(item))
            view=folder/'approved-plan-view.json'
            once(view,{'schema':'sdm-approved-plan-fidelity-view/v1','original_plan':current['science_plan'],
                'accepted_review':plan['accepted_review_ref'],'research_revision':plan['research_revision'],
                'proposal_fields':{key:proposal[key] for key in FIELDS},
                'projection':'All original proposal fidelity fields; metadata/duplicate evidence indexes omitted, original retained.'})
            sources.append(view)
            replan=read_json(Path(current['replan_root'])/'replan-config.json')['research_replan']
            parent_code=[]
            for item in replan.get('sources',[]):
                path=reference(item)
                if path.parent.name in ('executed-parent','executed-incumbent'):parent_code.append(path)
            sources.extend(parent_code)
            if self.spec['version']==4:
                selected_project=read_json(current['implementation_project'])['research_implementation']
                selected_files=[reference(r) for r in selected_project['sources'] if Path(r['path']).parent.name=='selected-base']
                sources.extend(selected_files);parent_code.extend(selected_files)
            # Preserve all raw sources in the bound index; foreground only the
            # current/parent code and original plan fidelity. Leave room for
            # actual critique and supplements within ModelRunner's 256KiB cap.
            priority=[Path(profile['root'])/name for name in profile['files']]+scientific_sources
            trainer_hashes={row['sha256'] for row in contract_value['protected_inputs']}
            parent_priority=[p for p in parent_code if ref(p)['sha256'] not in trainer_hashes]
            # A large owned-recipe inventory must not displace the actual
            # protected trainer, complete launch proof or current execution.
            priority += [binding_view,view,reference(current['execution_contract']),
                *[reference(row) for row in contract_value['protected_inputs']],proof,operational,
                *parent_priority,reference(verified)]
            config['proposal_review']=copy.deepcopy(config['proposal_review'])
            config['proposal_review']['source_budget']={'version':1,'serialized_bytes':96*1024,
                'max_file_bytes':32768,'priority':[str(p) for p in dict.fromkeys(priority)]}
        once(project,config)
        context={'schema':'sdm-review-context/v1','subject_digest':digest(subject(config)),
            'goal':'Review exact implementation fidelity, validity of the fixed evaluation and safe remote execution before submission.',
            'constraints':{'local_ml':'No local imports, fitting or model evaluation. Syntax is not functional validation.',
                'authority':'Original delegation/Manager/Guard; no resource or permission expansion.',
                **({'platform_boundary':'Current Manager preflight and execution-binding evidence are supplied. '
                    'Review exact code, evaluation, resource intent and declared watchdog. The original Manager rechecks current '
                    'component settings before apply. Do not confuse an untested cleanup/cancellation scenario, price or queue '
                    'availability with an implementation defect; report these limits without inventing guarantees.'}
                    if self.spec['version'] in (3,4) else {}),
                **({'machine_verification':'Original collector/Manager own task identity and provider normalization. '
                    'Use the current parent reconciliation and complete owned-component recipe inventory. Raw data hash '
                    'inequality alone is not an unresolved intent when declared configuration and remote artifact identities '
                    'are verified. A blocker needs a specific declared command/image/role/resource/mount mismatch or genuinely '
                    'missing proof. Unsubmitted drafts are not completed experiments; exact recipe matching does not claim '
                    'general program-semantic equivalence outside this owned component.'} if self.spec['version'] in (3,4) else {}),
                'fidelity':'Reject implementation that changes holdout/evaluation, leaks labels or scores, writes artifacts independently, '
                    'monkeypatches the harness or uses files/network/platform side effects. Testable effectiveness may remain uncertain.'},
            'data':self.spec['comparison_identity'],'code_revision':manifest,
            'configuration':request['task_info'],'history':{'parent_result':current['result'],'accepted_plan':current['science_plan']},
            'unknowns':'Remote functional behavior and effectiveness are unproven until actual execution. Review exact code and recipe against approved intervention.',
            'sources':[{'id':'code-source-'+str(i),'kind':'code' if path.suffix=='.py' else 'config',
                **ref(path)} for i,path in enumerate(dict.fromkeys(sources))]}
        from review_context import validate_context
        output=folder/'code-review';output.mkdir(exist_ok=True)
        validate_context(context,context['subject_digest']);once(output/'review-context.json',context)
        current['job']=self.coordinator('proposal-review',project,output)
        current.update(stage='reviewing-code',code_review_project=str(project),code_review_root=str(output))

    def start_initial_review(self,current,folder,phase):
        from component_submission import initial_review
        science=current.get('submission_reviews',{}).get('science') if phase=='code' else None
        output=folder/('initial-'+phase+'-review')
        project=initial_review(self.inbox.cfg,self.spec,self.manager_config,phase,output,science)
        current['job']=self.coordinator('proposal-review',project,output)
        current.update(stage='reviewing-initial',initial_review_phase=phase,
            code_review_project=str(project),code_review_root=str(output))

    def complete_initial_review(self,current,folder,result):
        if result.get('status')!='proposal-approved':
            current.update(stage='needs-attention',reason='original-initial-review:'+str(result.get('status')));return
        from component_submission import binding,verified_review
        selected=binding(current['code_review_project'],current['code_review_root'],result['receipt'])
        verified_review(selected)
        phase=current['initial_review_phase']
        if phase not in ('science','code'):raise ValueError('original_initial_review_phase_required')
        current.setdefault('submission_reviews',{})[phase]=selected
        if phase=='science':self.start_initial_review(current,folder,'code')
        else:current.update(stage='initial-submitting',initial_review_phase='complete')

    def dispatch_submission(self,current,folder,plan_ref,authorization_ref,initial=False,state=None):
        """Both intents share one SDM gate; known Guard intent always reconciles."""
        from research_execution import Manager
        manager=Manager(self.manager_config);plan=read_json(reference(plan_ref));guard=manager.guard(plan)
        with guard.lock():guard.load();phase=guard.state['phase']
        if phase!='new':return manager.reconcile(str(reference(plan_ref)))
        if (self.inbox.root/'PAUSED').exists():return None
        from component_submission import enabled,verify,successor_reviews
        if enabled(self.spec):
            reviews=current.get('submission_reviews') if initial else None
            if not initial:
                authorization=read_json(reference(authorization_ref))
                reviews=successor_reviews(current,reference(authorization['code_review']))
            payload=(read_json(reference(self.spec['initial_submission']['confirmed_plan']))['payload_manifest']
                     if initial else current['payload_manifest'])
            acceptance=verify(self.spec,self.manager_config,plan_ref,authorization_ref,reviews,payload,
                **({} if initial else {'science_plan':current['science_plan']}))
            # The initial Attempt also produces its first successor. Bind each
            # new receipt directory to the exact Manager plan, never reuse the
            # baseline's immutable receipt in the same Attempt folder.
            receipts=folder if initial else folder/'submissions'/plan_ref['sha256'].removeprefix('sha256:')
            receipts.mkdir(parents=True,exist_ok=True)
            once(receipts/'submission-acceptance.json',acceptance)
            current['submission_acceptance']=ref(receipts/'submission-acceptance.json')
            if self.spec.get('sdm_governance'):
                if state is None:raise ValueError('governance_requires_original_pipeline_state')
                from research_acceptance import submission as governance_submission
                registered=governance_submission(self.spec,acceptance,state,initial)
                registered['submission_receipt']=current['submission_acceptance']
                path=receipts/('initial-core-submission.json' if initial else 'successor-core-submission.json')
                once(path,registered)
                if ref(path) not in state.setdefault('governance_runs',[]):state['governance_runs'].append(ref(path))
                # Durable Run/Attempt registration before crossing Guard effect boundary.
                atomic_json(self.path,state)
        elif initial:raise ValueError('fresh_initial_submission_requires_sdm_review_gate')
        # Pause may arrive while original review receipts are being replayed.
        if (self.inbox.root/'PAUSED').exists():return None
        return manager.apply(str(reference(plan_ref)),str(reference(authorization_ref)))

    def prepare_submission(self,current,folder,result):
        if self.spec.get('code_review_feedback') and result.get('status')=='proposal-revision-required':
            from implementation_code_review import feedback
            config=read_json(current['code_review_project'])
            selected=read_json(current['freeze_project'])['research_implementation']
            target=self.inbox.root/'cf'/ref(result['receipt'])['sha256'][:24]
            repair=feedback(config,current['code_review_root'],result,selected['workspace'],
                selected['native_completion'],target)
            self.correct_implementation(current,folder,repair);return
        if result.get('status')!='proposal-approved':
            current.update(stage='needs-attention',reason='original-code-review:'+str(result.get('status')));return
        receipt=read_json(reference(ref(result['receipt'])))
        if receipt.get('approved') is not True:raise ValueError('original_code_review_approval_required')
        from research_execution import Manager
        request=read_json(reference(current['successor_request']))
        path=folder/'manager-plan.json'
        if path.exists():
            plan=read_json(path)
            normalized=plan.get('request',{})
            if ({k:v for k,v in normalized.items() if k in request}!=request
                    or set(normalized)-set(request)-{'component_settings'}):
                raise ValueError('original_successor_manager_plan_changed')
        else:
            plan=(read_json(reference(current['preflight_plan'])) if current.get('preflight_plan')
                  else Manager(self.manager_config).plan(request))
            once(path,plan)
        delegation=read_json(reference(self.spec['delegation']))
        auth={'schema':'lpai-manager-authorization/v1','plan_sha256':'sha256:'+ref(path)['sha256'],
            'direct_user_confirmation':True,'source_ref':delegation['source_ref'],
            'actor':'Codex under original explicit standing research delegation; not a newly fabricated user click',
            'delegation':self.spec['delegation'],'science_plan':current['science_plan'],'code_review':ref(result['receipt'])}
        authorization=folder/'manager-authorization.json';once(authorization,auth)
        current.update(stage='submitting',manager_plan=ref(path),manager_authorization=ref(authorization))

    def submit(self,state,current,folder):
        result=self.dispatch_submission(current,folder,current['manager_plan'],current['manager_authorization'],state=state)
        if result is None:return
        atomic_json(folder/'submission-observation.json',result)
        if result.get('status') not in ('verified','already-completed','pending_approval','pending-approval-unresolved',
                                     'submission-outcome-unresolved'):
            raise ValueError('original_submission_requires_reconciliation')
        # Preserve every completed attempt and its original scientific plan;
        # the next source observes exactly the new Manager plan/Guard.
        source=copy.deepcopy(self.spec['initial_source']);source.update(
            version=source['version'] if source.get('version') in (3,4) else 1,
            binding='component-source:'+current['manager_plan']['sha256'],plan=current['manager_plan'],expected=current['expected'])
        # A repair binds only the original old plan. Fresh successor plans use
        # this runtime; never inherit an initial intent's recovery exception.
        source.pop('readonly_runtime_repair',None)
        following={'id':'attempt-'+str(len(state['attempts'])), 'stage':'watching','poll_at':0,
            'source':source,'science_plan':current['science_plan']}
        current['stage']='successor-registered'
        state['attempts'].append(following)

    def step(self,state):
        if state['status']!='running':return None
        if (self.inbox.root/'PAUSED').exists():return 15
        if self.spec.get('sdm_governance'):
            from research_acceptance import project as acceptance_project
            acceptance_project(self.inbox.cfg,state)
        current=state['attempts'][-1]
        from research_continuation import enabled,take_resume,attention
        if enabled(self.inbox.cfg):
            take_resume(self.inbox,current,self)
            retry=current.get('continuation',{}).get('retry_at',0)
            if retry and retry>time.time():return retry-time.time()
            if current['stage']=='needs-attention':
                folder=Path(current.get('continuation_folder',self.root/current['id']))
                return 0 if attention(self,state,current,folder) else 60
        folder=Path(current.get('continuation_folder',self.root/current['id']));folder.mkdir(parents=True,exist_ok=True)
        stage=current['stage']
        if stage=='initial-submitting':
            from research_execution import Manager
            initial=self.spec['initial_submission'];manager=Manager(self.manager_config)
            path=reference(current['source']['plan']);plan=read_json(path);guard=manager.guard(plan)
            with guard.lock():guard.load();phase=guard.state['phase']
            if phase=='new' and current.get('initial_review_phase')!='complete':
                from component_submission import enabled
                if initial.get('version')!=2 or not enabled(self.spec):
                    raise ValueError('fresh_initial_submission_requires_sdm_review_gate')
                self.start_initial_review(current,folder,'science');return 0
            result=self.dispatch_submission(current,folder,current['source']['plan'],initial['authorization'],initial=True,state=state)
            if result is None:return 15
            once(folder/'initial-dispatch.json',{'config_sha256':digest(self.inbox.cfg),
                'plan':current['source']['plan'],'authorization':initial['authorization'],
                'scope':'original initial intent owned by this frozen host; Guard owns the effect boundary'})
            if result.get('status') not in ('verified','already-completed','pending_approval',
                    'pending-approval-unresolved','submission-outcome-unresolved'):
                raise ValueError('initial_submission_requires_original_reconciliation')
            atomic_json(folder/'initial-submission-observation.json',result)
            current.update(stage='watching',initial_submission=initial,initial_submission_status=result['status'])
            return 0
        if stage=='reviewing-initial':
            detail=self.completed(current['job'])
            if detail is None:return 15
            self.complete_initial_review(current,folder,detail['coordinator_result']);return 0
        if stage=='watching':
            remaining=current.get('poll_at',0)-time.time()
            if remaining>0:return remaining
            spec=current['source']
            self.inbox.register(spec['binding'],{'template':self.spec['source_template'],
                'origin':ref(self.origin),'data':{'evidence':spec['plan']}})
            from research_execution_source import Source as ComponentSource
            def record(event):
                current['result']=event['state']['result_ready']
                current['stage']='result-ready'
                current['result_handoff']={'received_at':time.time(),'state_sha256':event.get('state_sha256'),'model_requested':False}
                return {'id':digest(event),'status':'recorded-for-original-sdm'}
            source=ComponentSource(self.inbox,spec,root=folder/'source',deliver=record)
            status,delay=source.cycle();current['observation']=status
            current['poll_at']=time.time()+(delay or 0)
            if enabled(self.inbox.cfg):
                from research_continuation import observed
                observed(current,status)
            return delay if delay is not None else 0
        if stage=='result-ready':self.replan(state,current,folder)
        elif stage=='reviewing-plan':
            detail=self.completed(current['job'])
            if detail is None:return 15
            result=detail['coordinator_result']
            if self.spec.get('sdm_governance') and result.get('plan_path'):
                from research_acceptance import completion
                path=folder/'core-decision.json'
                once(path,completion(self.spec,state,ref(result['plan_path'])))
                current['governance_decision']=ref(path)
            if result.get('status') in ('research-goal-met','research-stopped'):
                if self.spec.get('sdm_governance'):state['governance_completion']=current['governance_decision']
                current['stage']=result['status'];state['status']=result['status'];return None
            if not result.get('agent_continuation_required'):
                current.update(stage='needs-attention',reason='original-plan-review:'+str(result.get('status')));return None
            if (self.spec['version']==4 and self.spec['decision_policy']['version']==3
                    and state['candidate_goal']['verification']['status']=='mismatch'):
                current.update(stage='needs-attention',reason='actual-reproduction-mismatch-reconcile-only');return None
            accepted=read_json(reference(ref(result['plan_path'])))
            if accepted.get('parent_diagnosis_ref') is not None:
                # A reviewed failed-task repair has no successful result to
                # aggregate. Preserve that terminal UID and use the SAME
                # implementation, code review and submission gate as science.
                failure=read_json(reference(current['result']))
                if (accepted['parent_diagnosis_ref']!=current['result']
                        or accepted.get('parent_task_uid')!=failure.get('uid')
                        or failure.get('schema')!='sdm-component-diagnosis-ready/v1'
                        or failure.get('valid_training_round') is not False
                        or failure.get('platform_status') not in {'failed','stopped','cancelled','canceled'}
                        or failure.get('original_plan_sha256')!=current['source']['plan']['sha256'].removeprefix('sha256:')
                        or bool(accepted.get('preparation_recovery'))!= (not state['rounds'])):
                    raise ValueError('accepted_repair_requires_exact_failed_parent')
                if accepted['research_revision']['change_level']!='none':
                    self.implementation(current,folder,Path(result['plan_path']))
                    return 0
            if result.get('status')=='research-plan-approved-awaiting-diagnosis':
                # An existing-evidence plan is not a model.py implementation.
                from component_diagnostics import prepare
                failure_ref=accepted.get('parent_diagnosis_ref')
                diagnostics=prepare(state['rounds'],folder/'accepted-diagnostics',**({'failure':failure_ref} if failure_ref else {}))
                fingerprint=digest({'adapter':'failed-task-evidence/v1' if failure_ref else 'existing-evidence/v1',
                    'results':[r['result']['sha256'] for r in state['rounds']],**({'failure':failure_ref['sha256']} if failure_ref else {})})
                seen=state.setdefault('diagnostic_history',[])
                if any(row['fingerprint']==fingerprint for row in seen):
                    current.update(stage='needs-attention',reason='diagnosis-requires-new-supported-evidence')
                    current['continuation']={'phase':'blocked','category':'diagnostic-no-new-evidence',
                        'next_action':'qualified-evidence-adapter-required','automatic_resubmit':False}
                    return None
                seen.append({'fingerprint':fingerprint,'plan':ref(result['plan_path']),'evidence':diagnostics})
                state.setdefault('implementation_block_history',[]).extend(diagnostics)
                target=folder/('diagnostic-replan-'+fingerprint.removeprefix('sha256:')[:16])
                current['continuation_folder']=str(target)
                self.replan(state,current,target)
                return 0
            self.implementation(current,folder,Path(result['plan_path']))
        elif stage=='preparing-implementation':
            detail=self.completed(current['job'])
            if detail is None:return 15
            result=detail['coordinator_result']
            if result.get('status')!='implementation-job-prepared':
                current.update(stage='needs-attention',reason=str(result.get('status')));return None
            current['job']=self.enqueue('implement',self.spec['implementation_template'],{'job':ref(result['job_path'])})
            current['stage']='implementing'
        elif stage=='implementing':
            if self.completed(current['job']) is None:return 15
            if self.spec['version'] in (3,4) and current.get('implementation_failures'):
                # A completed refusal or an unchanged response cannot become
                # another "repair" merely because its delivery ID changed.
                previous=read_json(reference(current['implementation_failures'][-1]['feedback']))
                config=read_json(current['implementation_project'])['research_implementation']
                contract_value=read_json(reference(config['execution_contract']))
                workspace=Path(config['workspace']).resolve()
                unchanged=True
                for name in contract_value['allowed_files']:
                    path=workspace/name
                    if (not path.resolve().is_relative_to(workspace) or path.is_symlink()
                            or not path.is_file() or name not in previous['files']
                            or ref(path)['sha256']!=previous['files'][name]['sha256']):
                        unchanged=False;break
                # A code critique needs changed code. A validation-only defect
                # can legitimately be fixed in implementation.json alone.
                if unchanged and previous.get('schema')=='sdm-implementation-code-feedback/v1':
                    current.update(stage='needs-attention',reason='completed-correction-made-no-source-progress',
                        blocked_native_receipt=ref(self.inbox.root/current['job']/'result.json'))
                    return None
            self.freeze_implementation(current,folder)
        elif stage=='freezing':
            detail=self.completed(current['job'])
            if detail is None:return 15
            profile=detail['coordinator_result']
            if profile.get('status')=='implementation-capability-gap' and self.spec['version']==4:
                self.capability_replan(state,current,folder,profile);return 0
            if profile.get('status')=='implementation-correction-required':
                self.correct_implementation(current,folder,profile);return 0
            if profile.get('status')!='requires-original-code-and-execution-preflight':
                current.update(stage='needs-attention',reason=str(profile.get('status')));return None
            current['frozen_implementation']=ref(Path(current['implementation_root'])/'implemented-profile.json')
            current['stage']='preflighting-code'
            self.save(state,health={'status':'running','stage':current['stage'],'at':time.time(),
                'operation_stage':'preparing-official-read','model_calls_during_wait':0})
        elif stage=='preflighting-code':
            self.code_review(state,current,folder,read_json(reference(current['frozen_implementation'])))
        elif stage=='reviewing-code':
            detail=self.completed(current['job'])
            if detail is None:return 15
            self.prepare_submission(current,folder,detail['coordinator_result'])
        elif stage=='submitting':self.submit(state,current,folder)
        elif stage=='needs-attention':return None
        else:raise ValueError('unknown_original_component_pipeline_stage')
        return 0

    def run(self):
        with locked(self.root,'.pipeline.lock','component_pipeline_already_owned'):
            while not self.inbox.done.is_set():
                from research_drain import requested,DrainRequested
                from research_continuation import enabled
                with self.inbox.condition:
                    if requested(self.inbox):
                        self.inbox.condition.wait(timeout=1);continue
                    self.inbox.component_step_active=True
                state=self.load();before=copy.deepcopy(state)
                try:
                    validate(self.inbox.cfg)
                    delay=self.step(state)
                    from research_continuation import enabled
                    current=state['attempts'][-1]
                    if enabled(self.inbox.cfg) and current.get('continuation',{}).get('phase')=='retry-wait' and current['continuation'].get('retry_at',0)<=time.time():
                        current.pop('continuation',None)
                    self.save(state,health={'status':{'blocked':'requires-reconciliation','retry-wait':'retry-wait','reconciling':'reconciling-original'}.get(current.get('continuation',{}).get('phase'),state['status']),
                        'stage':current['stage'],'continuation':current.get('continuation'),
                        'at':time.time(),'model_calls_during_wait':0})
                    if (state['status']=='running' and state['attempts'][-1]['stage']=='watching'
                            and state['attempts'][-1]['id']!='initial'):
                        from research_recovery import checkpoint
                        checkpoint(self.inbox.cfg,'known-task-wait',source=state['attempts'][-1]['source']['plan'])
                    if delay is None and enabled(self.inbox.cfg) and state['attempts'][-1]['stage']=='needs-attention':
                        delay=0
                    if delay is None:
                        if state['status'] in ('research-goal-met','research-stopped'):
                            from research_chain import audit
                            atomic_json(self.root/'continuous-chain.json',audit(self.inbox.cfg,state))
                        if (state['status'] in ('research-goal-met','research-stopped')
                                and self.inbox.cfg.get('completion_shutdown')=={'version':1}):
                            with self.inbox.db() as db:
                                counts=db.execute('select status,count(*) from jobs group by status').fetchall()
                            if any(row[0]!='completed' for row in counts):raise ValueError('completion_exit_requires_resolved_deliveries')
                            from research_context import Context
                            context=Context(self.inbox.cfg).load()
                            if context.get('pending_job') or context.get('pending_turn'):
                                raise ValueError('completion_exit_requires_idle_native_context')
                            atomic_json(self.inbox.root/'completion-exit.json',{'status':state['status'],
                                'idle_jobs':[list(row) for row in counts],'pending_job':None,'pending_turn':None,
                                'platform_actions':0})
                            self.inbox.terminal_callback()
                        return
                except DrainRequested:
                    state=before;delay=1
                    self.save(state,health={'status':'draining','stage':state['attempts'][-1]['stage'],
                        'at':time.time(),'model_calls_during_wait':0})
                except Exception as error:
                    # Keep the original stage/job/Guard. No model or write
                    # gets a fresh identity because observation failed.
                    delay=60
                    from research_continuation import enabled,failed,submission_intent
                    if enabled(self.inbox.cfg):
                        current=state['attempts'][-1]
                        continuation=failed(current,error,original_intent=submission_intent(self,current,error))
                        delay=max(0,continuation['retry_at']-time.time()) if continuation.get('retry_at') else 60
                    import re
                    code=str(error) if re.fullmatch('[a-z][a-z0-9_:-]{0,180}',str(error)) else type(error).__name__
                    self.save(state,health={'status':{'retry-wait':'retry-wait','reconciling':'reconciling-original'}.get(state['attempts'][-1].get('continuation',{}).get('phase'),'requires-reconciliation'),'at':time.time(),'error_code':code,
                        'stage':state['attempts'][-1]['stage'],'error_type':type(error).__name__,'automatic_resubmit':False})
                finally:
                    with self.inbox.condition:
                        self.inbox.component_step_active=False
                        notify=getattr(self.inbox.condition,'notify_all',None)
                        if notify:notify()
                with self.inbox.condition:self.inbox.condition.wait(timeout=max(0,delay) if enabled(self.inbox.cfg) else min(delay,15))


# Historical import compatibility: one shared lifecycle controller.
ComponentPipeline = ResearchPipeline
