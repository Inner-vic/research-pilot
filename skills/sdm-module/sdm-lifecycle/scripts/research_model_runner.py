"""Worker v5 result-purpose contracts and shared raw evidence over ModelRunner."""
import copy
import hashlib
import json
from pathlib import Path

from model_runner import ModelRunner, InvalidModelReply, binding, completed, configuration, request
from research_model_receipts import checked, complete, intent, seal
from review_storage import atomic_json, read_json
from task_pipeline import digest
from research_language import GUIDANCE


def object_schema(properties):
    return {'type':'object','properties':properties,'required':list(properties),'additionalProperties':False}


def optional(schema):
    return {'anyOf':[schema,{'type':'null'}]}


def value_schema(value, depth=0):
    if depth > 8:raise ValueError('registered_evidence_shape_too_deep')
    if value is None:return {'type':'null'}
    if type(value) is bool:return {'type':'boolean'}
    if type(value) is int:return {'type':'integer'}
    if type(value) is float:return {'type':'number'}
    if isinstance(value,str):return {'type':'string'}
    if isinstance(value,dict):return object_schema({k:value_schema(v,depth+1) for k,v in value.items()})
    if isinstance(value,list):
        choices={digest(value_schema(v,depth+1)):value_schema(v,depth+1) for v in value}
        items=list(choices.values()) or [{'type':'string'}]
        return {'type':'array','items':items[0] if len(items)==1 else {'anyOf':items}}
    raise ValueError('registered_json_evidence_required')


def parameters(variables):
    props={}
    for key,spec in variables.items():
        if spec.get('type')=='enum':
            from loop_contract import parameter_values
            choices=spec.get('choices')
            if not isinstance(choices,list) or not 1<=len(choices)<=64:
                raise ValueError('bounded_research_enum_choices_required')
            groups={}
            for choice in choices:
                parameter_values({key:choice},{key:spec})
                kind={str:'string',bool:'boolean',int:'integer',float:'number'}[type(choice)]
                groups.setdefault(kind,[]).append(choice)
            variants=[{'type':kind,'enum':values} for kind,values in groups.items()]
            props[key]=variants[0] if len(variants)==1 else {'anyOf':variants}
            continue
        if spec.get('type') not in {'number','integer','boolean','string'}:
            raise ValueError('supported_research_parameter_type_required')
        props[key]={k:v for k,v in spec.items() if k in {'type','minimum','maximum','enum'}}
    return object_schema(props)


def output_schema(role, project, packet, state, candidate=None):
    string={'type':'string'}
    strings={'type':'array','items':string}
    if role=='b':
        challenge=object_schema({'id':string,'basis':string,'resolution_criterion':string,
            'severity':{'type':'string','enum':['minor','major','blocker']},
            'status':{'type':'string','enum':['open','resolved']}})
        draft_schema={'type':'string'} if candidate is None else {'type':'string','enum':[digest(candidate)]}
        return object_schema({'draft_digest':draft_schema,
            'verdict':{'type':'string','enum':['approve','revise','needs-evidence','reject','stalled']},
            'reason':string,'challenges':{'type':'array','items':challenge}})
    ids=list(packet['evidence'])
    if not ids or len(ids)>200:raise ValueError('bounded_registered_evidence_schema_required')
    citation={'type':'string','enum':ids}
    shapes={digest(value_schema(v)):value_schema(v) for v in packet['evidence'].values()}
    if len(shapes)>40:raise ValueError('bounded_evidence_shape_union_required')
    types=list(shapes.values())
    fact=object_schema({'evidence_id':citation,'value':types[0] if len(types)==1 else {'anyOf':types}})
    variables=project.get('devspace_study',{}).get('allowed_variables',{})
    iteration=object_schema({'action':{'type':'string','enum':['stop']}})
    if variables:
        continuing=object_schema({'action':{'type':'string','enum':['run-next-round','retry-round']},
            'parameters':parameters(variables),'changed_variable':optional(string),'hypothesis':string,
            'expected_observations':string,'failed_attempt_binding':optional(string),
            'resume':optional(object_schema({'from_execution_binding':string,'artifact_sha256':string}))})
        iteration={'anyOf':[iteration,continuing]}
    step=object_schema({'action':string,'reason':string,'acceptance':string,
        'authorization':{'type':'string','enum':['existing','required','none']},
        'status':{'type':'string','enum':['ready','blocked','completed']},
        'stop_decision':{'type':'string','enum':['stop','continue','needs-evidence']},
        'stop_reason':string,'blocker':optional(string),'evidence_paths':optional(strings),
        'iteration':optional(iteration)})
    if project.get('research_iteration'):
        from research_replan import direction_schema
        step['properties']['research_direction']=direction_schema(ids)
        step['required'].append('research_direction')
    assessment=object_schema({'evidence_consistent':{'type':'boolean'},'conclusion_supported':{'type':'boolean'},
        'supporting_evidence':{'type':'array','items':citation},'counterevidence':strings,
        'limitations':strings,'alternatives':strings})
    actions = ['run-next-round', 'stop-analysis', 'stop-budget']
    if project.get('devspace_study') and not packet.get('allowed_variables'):
        # These rows are material roles, not completed Loop experiments. The
        # executable decision is validated separately in next_step.iteration.
        actions = ['stop-analysis']
    return object_schema({'request_digest':{'type':'string','enum':[digest(packet)]},
        'facts':{'type':'array','items':fact},
        'hypotheses':{'type':'array','items':object_schema({'statement':string,
            'evidence_ids':{'type':'array','items':citation},'limitations':string})},
        'previous_experiment_assessment':string,'reason':string,
        'action':{'type':'string','enum':actions},
        'next_step':step,'workflow_assessment':assessment if state.get('main_workflow') else optional(assessment),
        'evidence_kind':optional(string),
        'next_parameters':optional(parameters(packet.get('allowed_variables',{}))),
        'changed_variable':optional(string),'hypothesis':optional(string),'expected_observations':optional(string)})


def decoded(role, value):
    value=copy.deepcopy(value)
    if role=='b':return value
    for key in ('workflow_assessment','evidence_kind','next_parameters','changed_variable','hypothesis','expected_observations'):
        if value.get(key,False) is None:value.pop(key)
    step=value.get('next_step',{})
    for key in ('blocker','evidence_paths','iteration'):
        if step.get(key,False) is None:step.pop(key)
    iteration=step.get('iteration',{})
    for key in ('changed_variable','failed_attempt_binding','resume'):
        if iteration.get(key,False) is None:iteration.pop(key)
    return value


def guidance_sources(sources):
    skill=Path(__file__).parents[1]
    paths=[skill/'references/research-strategy.md',skill.parent/'sdm-agent-research-planner/SKILL.md',
           skill.parent/'sdm-agent-evidence-review/SKILL.md']
    for path in paths:sources[str(path)]='sha256:'+hashlib.sha256(path.read_bytes()).hexdigest()


def shared_evidence(project, state, packet, sources, job, source_visibility=0, source_budget=None):
    path=Path(job)/'shared-evidence.json'
    bound={'project_digest':digest(project),'request_digest':digest(packet),'sources':sources}
    priority={};limit=160*1024;file_limit=16384
    if source_budget is not None:
        if (set(source_budget)!={'version','serialized_bytes','max_file_bytes','priority'}
                or source_budget['version']!=1 or type(source_budget['serialized_bytes']) is not int
                or not 32768<=source_budget['serialized_bytes']<=160*1024
                or type(source_budget['max_file_bytes']) is not int or not 16384<=source_budget['max_file_bytes']<=65536
                or not isinstance(source_budget['priority'],list) or len(source_budget['priority'])>64
                or len(set(source_budget['priority']))!=len(source_budget['priority'])
                or not set(source_budget['priority'])<=set(sources)):
            raise ValueError('explicit_bounded_shared_source_budget_required')
        priority={name:i for i,name in enumerate(source_budget['priority'])}
        limit=source_budget['serialized_bytes'];file_limit=source_budget['max_file_bytes']
        bound['source_budget']=source_budget
    if source_visibility:
        if source_visibility!=1:raise ValueError('explicit_source_visibility_version_required')
        bound['source_visibility']=1
    if path.exists():
        saved=checked(path,'sdm-shared-research-evidence/v1')
        if saved['binding']!=bound:raise ValueError('shared_research_evidence_changed')
        return saved['evidence']
    if any(Path(job).glob('*/intent.json')):raise ValueError('shared_evidence_missing_requires_reconciliation')
    selected={k:state[k] for k in ('main_workflow','active_round','active_attempt','binding','uid','evidence_kind',
        'outcome','stop_verified','diagnostic_only','training_quality_verified','research_iteration','research_goal_evaluation') if k in state}
    shared={'project':project,'state':selected,'packet':packet,'raw_sources':[]}
    if source_visibility:
        shared['source_visibility_rule']='Each raw source has its own visibility and omitted_bytes. complete-text/0 is complete for that file; an excerpt of another file is not evidence that all sources were truncated.'
    if packet.get('context',{}).get('schema')=='sdm-review-context/v1':
        shared['decision_contract']={'current_review_receipts':
            'These proposal/critic calls are still in progress. The host validates distinct models, exact raw requests, completions and acceptance after this snapshot; do not require future self-receipts as scientific evidence or infer that a parent plan grants execution authority.',
            'evidence_scope':'Missing decisive facts may require evidence. Unknown intervention effectiveness is the experiment to run, not a result to prove before planning. A truncated source is not a complete independent code review; use bounded registered supplements or a fresh evidence version.'}
    if packet.get('schema') == 'sdm-loop-analysis-request/v1':
        shared['decision_contract'] = {
            'scope': 'Judge experimental evidence and the proposed next action, not completion of this in-progress review.',
            'conclusion_supported': 'Whether the stated experimental assessment/next action is supported; do not equate this with all workflow nodes completed.',
            'current_review_receipts': 'These calls and their archive/consumption receipts are produced after this snapshot. The coordinator verifies them separately; do not require future self-receipts to assess scientific evidence.',
            'resource': 'A verified stopped UID is not billing proof. Preserve unknown billing as a limitation; enforce actual configured authorization, not an invented receipt requirement.',
            'iteration': 'Propose a grounded eligible successor within the frozen Study domain or stop/needs-evidence. Never claim the experiment already ran or grant execution authority.'}
        if project.get('devspace_study') and not packet.get('allowed_variables'):
            shared['decision_contract']['material_analysis_action'] = (
                'Top-level action MUST be stop-analysis: request.history rows identify source material roles, not Loop execution rounds. '
                'This does not stop the Study. Choose executable continue/retry/stop ONLY in next_step.iteration using project.devspace_study.allowed_variables. '
                'A top-level run-next-round would invoke the unrelated Loop history validator and its incomplete_execution error is a wire mismatch, not evidence that this actual devspace run failed.')
        if project.get('research_iteration'):
            shared['decision_contract']['research_goal']=(
                'Assess progress toward research_iteration.objective separately from closing this local Study. '
                'In next_step.research_direction choose continue-plan for a prespecified eligible arm of the current plan, justified refinement, a substantive plan revision, a discriminating diagnosis, goal-met or research stop. '
                'Diminishing comparable results should prompt reassessment of assumptions and intervention scale; no fixed trial count or universal metric threshold. '
                'Do not automatically replace the model, but defend another tuning step against changes in model/representation/training method/data/measurement. '
                'For revise-plan/diagnose stop the current iteration scope; the original worker automatically opens existing Generator–Critic planning. Scope exhaustion is not goal-met or new execution authority.')
    if len(json.dumps(shared,ensure_ascii=False).encode('utf-8'))>128*1024:
        raise ValueError('shared_research_base_exceeds_bound')
    # Preserve small complete observations and failure-path excerpts before
    # large source listings. Every omitted byte remains explicit metadata.
    ordered = sorted(sources.items(), key=lambda row: (
        priority.get(row[0],len(priority)),
        not Path(row[0]).resolve().is_relative_to(Path(project.get('research_worker',{}).get('output',job)).resolve()),
        Path(row[0]).suffix.lower() in {'.pt', '.pth', '.ckpt', '.bin', '.pkl'},
        Path(row[0]).stat().st_size > 16384, Path(row[0]).stat().st_size, row[0]))
    entries=[]
    for name,expected in ordered:
        source=Path(name)
        if source.name in {'project.json','analysis-request.json'}:continue
        raw=source.read_bytes()
        if 'sha256:'+hashlib.sha256(raw).hexdigest()!=expected:raise ValueError('shared_raw_source_changed')
        item={'path':name,'digest':expected,'bytes':len(raw)}
        if source_visibility:
            registered={str(Path(row['path']).resolve()):row['id'] for row in packet.get('context',{}).get('sources',[])}
            item.update(source_id=registered.get(str(source.resolve())),visibility='metadata-only',
                scope='registered-source' if str(source.resolve()) in registered else 'research-guidance-or-context')
        item.update(inlined=False,omitted_bytes=len(raw),reason='Binary or serialized shared input budget exhausted')
        shared['raw_sources'].append(item);entries.append((item,raw))
    size=lambda value:len(json.dumps(value,ensure_ascii=False,allow_nan=False).encode('utf-8'))
    if size(shared)>limit:raise ValueError('shared_source_catalog_exceeds_serialized_bound')
    for item,raw in entries:
        take=min(file_limit,len(raw));base=size(item)
        if b'\0' not in raw:
            try:
                raw.decode('utf-8')
                while take>0:
                    candidate={k:v for k,v in item.items() if k not in ('reason','inlined','omitted_bytes')}
                    if len(raw)<=take:candidate.update(inlined=True,text=raw.decode('utf-8'),omitted_bytes=0)
                    else:candidate.update(inlined=True,head=raw[:take//2].decode('utf-8',errors='replace'),
                        tail=raw[-take//2:].decode('utf-8',errors='replace'),omitted_bytes=len(raw)-take)
                    if source_visibility:candidate['visibility']='complete-text' if len(raw)<=take else 'excerpt'
                    if size(shared)-base+size(candidate)<=limit:
                        item.clear();item.update(candidate);break
                    take//=2
            except UnicodeDecodeError:item['reason']='Not UTF-8 text'
    atomic_json(path,seal({'schema':'sdm-shared-research-evidence/v1','binding':bound,'evidence':shared}))
    return shared


def runner_settings(cfg):
    value=cfg.get('model_runner')
    if not isinstance(value,dict):raise ValueError('explicit_worker_model_runner_required')
    if value.get('cli_path')!=cfg['cli_path'] or value.get('codex_home')!=cfg['codex_home']:
        raise ValueError('worker_model_runner_identity_mismatch')
    value=configuration(value)
    from planner_session import selection
    selection(cfg,value)
    if value['version']==4 and value['backend']=='sdk':
        from model_context import sdk_options
        sdk_options(value)  # reject unavailable launch policy during the existing pre-action settings check
    return value


def call(cfg, shared, role, prompt, folder, candidate=None, caller=None):
    folder=Path(folder)
    parent=intent(cfg,role,prompt,folder)
    runner_config=runner_settings(cfg)
    project,packet,state=shared['project'],shared['packet'],shared['state']
    schema=output_schema(role,project,packet,state,candidate)
    evidence={'shared':shared,'shared_digest':digest(shared)}
    if role=='b':evidence.update(draft=candidate,draft_digest=digest(candidate))
    value={'schema':'sdm-model-request/v1','purpose':'result-analysis' if role=='a' else 'result-critique',
        'contract_id':'sdm-result-analysis/v1' if role=='a' else 'sdm-result-critique/v1',
        'model':cfg['models'][role],'evidence':evidence,'prompt':GUIDANCE+'\n'+prompt,'output_schema':schema,
        'input_kind':'tool-output','parent_call_id':parent['call_id']}
    if runner_config['version']==4:value['input_kind']=runner_config['context_policy']['input_kind']
    from planner_session import attach
    attach(value,cfg,runner_config)
    request(value)  # validate bounds before claiming a transport
    target=Path(cfg['output'])/'model-calls'/parent['call_id'].split(':')[-1][:24]
    link_path=folder/'runner-link.json'
    link={'schema':'sdm-worker-runner-link/v1','parent_call_id':parent['call_id'],'path':str(target),
          'request_digest':digest(value),'runner_config_digest':digest(runner_config),'shared_digest':digest(shared)}
    if link_path.exists():
        if checked(link_path,link['schema'])!=seal(link):raise ValueError('immutable_worker_runner_link_changed')
        if not (target/'request.json').exists():raise ValueError('worker_runner_transport_missing_requires_reconciliation')
    else:
        if (target/'request.json').exists():raise ValueError('worker_runner_transport_without_parent')
        atomic_json(link_path,seal(link))
    from research_worker import validate_response,validate_critique
    def validate(raw):
        canonical=decoded(role,raw)
        return validate_response(canonical,packet,state) if role=='a' else validate_critique(canonical,candidate)
    launcher=None
    if caller is not None:
        def launcher(path,envelope):
            from model_runner_transport import save
            save(path,envelope['binding'],'completed',caller(cfg,role,prompt,folder),{'backend':'control-fixture'})
    runner=ModelRunner(runner_config)
    try:
        result=runner.run(value,target,validate,launcher=launcher)
        raw=result['response']
    except InvalidModelReply as error:
        raw=error.response  # original worker supplies precise feedback, never a blind model retry
        result=completed(target,binding(runner_config,value))
    canonical=decoded(role,raw)
    provenance={'model_runner':str(target),'completion_digest':digest(result),
        'shared_digest':digest(shared),'parent_call_id':parent['call_id']}
    complete(cfg,role,prompt,folder,canonical,provenance)
    return canonical


def validate_persisted(cfg, folder, value):
    """Check underlying raw completion before consuming an approved canonical reply."""
    provenance=value.get('provenance',{})
    if not isinstance(provenance,dict):raise ValueError('worker_runner_provenance_required')
    folder=Path(folder)
    role=folder.name[-1]
    parent=intent(cfg,role,(folder/'prompt.txt').read_text(encoding='utf-8'),folder)
    target=Path(provenance.get('model_runner','')).resolve()
    expected=Path(cfg['output']).resolve()/'model-calls'/parent['call_id'].split(':')[-1][:24]
    if target!=expected or provenance.get('parent_call_id')!=parent['call_id']:
        raise ValueError('worker_runner_completion_path_changed')
    link=checked(folder/'runner-link.json','sdm-worker-runner-link/v1')
    saved=checked(target/'request.json','sdm-model-runner-envelope/v1')
    pending=checked(target/'intent.json','sdm-model-runner-intent/v1')
    bound=binding(cfg['model_runner'],saved['request'])
    if (saved['config']!=cfg['model_runner'] or saved['binding']!=bound or pending['binding']!=bound
            or link['request_digest']!=digest(saved['request']) or saved['request'].get('parent_call_id')!=parent['call_id']
            or saved['request']['model']!=cfg['models'][role]
            or saved['request']['purpose']!=value['identity']['purpose']):
        raise ValueError('worker_runner_raw_binding_changed')
    shared=checked(folder.parent/'shared-evidence.json','sdm-shared-research-evidence/v1')['evidence']
    if saved['request']['evidence']['shared']!=shared or digest(shared)!=provenance['shared_digest']:
        raise ValueError('worker_runner_shared_evidence_changed')
    result=completed(target,bound)
    if (result['status']!='completed' or digest(result)!=provenance['completion_digest']
            or decoded(role,result['response'])!=value['response']):
        raise ValueError('worker_runner_raw_response_changed')
