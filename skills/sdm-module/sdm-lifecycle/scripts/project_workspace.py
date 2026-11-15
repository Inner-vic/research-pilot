"""Default local project registration, bounded discovery and retained-evidence review.

This is an offline workflow over existing material, not a substitute training graph.
No model, platform adapter, network, repository write or resource action is invoked.
"""
import hashlib,json,math,re,subprocess,time
from pathlib import Path
from review_storage import atomic_json,locked
from project_driver import file_digest
from task_pipeline import digest


def plain(path):
    path=Path(path)
    if not path.is_absolute() or path.is_symlink() or path.resolve()!=path:
        raise ValueError('absolute_unlinked_project_path_required')
    return path


def git(repo,*args):
    result=subprocess.run(['git','-C',str(repo),*args],capture_output=True,text=True,timeout=15,
        env={**__import__('os').environ,'GIT_OPTIONAL_LOCKS':'0'}, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:raise ValueError('registered_git_repository_required')
    return result.stdout.strip()


def registration(config,root,profile=None):
    from raw_materials import evidence_kind
    kind=evidence_kind(config)
    if config.get('schema')!='sdm-project-workspace/v1' or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}',config.get('project_id','')):
        raise ValueError('project_workspace_identity_required')
    if not isinstance(config.get('material_roots'),list) or not 1<=len(config['material_roots'])<=16:raise ValueError('bounded_material_root_list_required')
    repo=plain(config['repository']) if config.get('repository') is not None else None
    roots=[plain(p) for p in config['material_roots']]
    if not roots or any(not p.is_dir() for p in roots):raise ValueError('existing_material_roots_required')
    if any(root==p or root.is_relative_to(p) for p in roots):raise ValueError('workspace_must_not_be_discovered_as_input')
    def observe(name,*args):
        tick=time.perf_counter();value=git(repo,*args)
        if profile is not None:profile[name]=time.perf_counter()-tick
        return value
    repository_root=commit=None;verified_snapshot=None
    if repo and re.match(r'^/mnt/[a-zA-Z]/',str(repo)):
        from repository_snapshot import snapshot as content_snapshot
        query_profile={};query=content_snapshot(repo,root,query_profile)
        if profile is not None:profile['content_query']=query_profile
        if query is not None:repository_root,verified_snapshot=query;commit=verified_snapshot['commit']
    if repo and verified_snapshot is None:
        location=observe('identity_seconds','rev-parse','--show-toplevel','HEAD')
        repository_root,commit=location.rsplit('\n',1)
        if not re.fullmatch(r'[0-9a-f]{40,64}',commit):raise ValueError('registered_git_commit_required')
    identity={'schema':'sdm-project-registration/v1','project_id':config['project_id'],'evidence_kind':kind,'repository':str(repo) if repo else None,
        'repository_root':repository_root,
        'material_roots':[str(p) for p in roots],'repository_copied':False,'repository_modified':False}
    path=root/'project.json'
    if path.exists():
        previous=json.loads(path.read_bytes())
        if previous.get('evidence_kind','retained-material')!=kind:raise ValueError('project_evidence_kind_changed_use_separate_workspace')
        if any(previous.get(k)!=identity[k] for k in ('project_id','repository','repository_root')):raise ValueError('project_identity_changed_use_separate_workspace')
        if previous!=identity:
            archive=root/'registrations'/(digest(previous).split(':')[1]+'.json')
            if not archive.exists():atomic_json(archive,previous)
            atomic_json(path,identity)
    else:atomic_json(path,identity)
    snapshot=verified_snapshot if verified_snapshot is not None else {'commit':commit,'dirty':bool(observe('status_seconds','status','--porcelain','--untracked-files=no')),
        'tracked_diff_digest':digest(observe('diff_seconds','diff','--binary','HEAD'))} if repo else {'commit':None,'dirty':None,'tracked_diff_digest':None}
    return identity,roots,snapshot


def discover(roots,include_globs=None,byte_limits=None):
    """Discover supported review material by convention; never crawl an entire repo."""
    from raw_materials import SUFFIXES,glob_list,matches
    if include_globs is not None:glob_list(include_globs)
    limits={'max_file_bytes':256*1024*1024,'max_total_bytes':1024*1024*1024}
    if byte_limits is not None:
        if not isinstance(byte_limits,dict) or set(byte_limits)-set(limits):
            raise ValueError('bounded_material_discovery_limits_required')
        limits.update(byte_limits)
    if any(type(v) is not int or not 1<=v<=1024*1024*1024 for v in limits.values()):
        raise ValueError('bounded_material_discovery_limits_required')
    found={};total=0;visited=0
    def walk(root):
        import os
        directories=0
        for folder,dirs,names in os.walk(root,followlinks=False):
            directories+=1
            if directories>10000:raise ValueError('material_discovery_walk_budget_exceeded_narrow_roots')
            for name in dirs:
                child=Path(folder)/name
                if child.is_symlink() or child.resolve()!=child:raise ValueError('linked_project_material_rejected')
            dirs[:]=sorted(d for d in dirs if not d.startswith('.') and d not in {'__pycache__','node_modules'})
            for name in sorted(names):yield Path(folder)/name
    for root in roots:
        for path in walk(root):
            visited+=1
            if visited>10000:raise ValueError('material_discovery_walk_budget_exceeded_narrow_roots')
            relative=path.relative_to(root)
            if any(part.startswith('.') or part in {'__pycache__','node_modules'} for part in relative.parts):continue
            if path.is_symlink():raise ValueError('linked_project_material_rejected')
            if include_globs is not None and not matches(path,root,include_globs):continue
            if path.suffix.lower() not in SUFFIXES:continue
            if path.resolve()!=path:raise ValueError('linked_project_material_rejected')
            if not path.is_file():continue
            size=path.stat().st_size;total+=size
            if len(found)>=512 or size>limits['max_file_bytes'] or total>limits['max_total_bytes']:raise ValueError('material_discovery_budget_exceeded_narrow_roots')
            found[str(path)]={'path':str(path),'bytes':size,'digest':file_digest(path)}
    return list(found.values())


def select(config,materials):
    indexed={r['path']:r for r in materials};candidates=[]
    for row in materials:
        p=Path(row['path'])
        if p.name=='analysis-request.json' and str(p.with_name('manifest.json')) in indexed:candidates.append(str(p.parent))
    chosen=config.get('case_directory')
    if chosen is not None:
        chosen=str(plain(chosen))
        if chosen not in candidates:raise ValueError('selected_case_not_in_discovered_material')
    elif len(candidates)==1:chosen=candidates[0]
    elif not candidates:return None,{'status':'awaiting-evidence','discovered_files':len(materials),
        'next_action':'Inspect indexed raw material and prepare the shared request/manifest records; no new adapter is required.'}
    else:return None,{'status':'awaiting-project-selection','candidates':candidates,'human_question':'Select one evidence bundle; do not silently choose the newest.'}
    return Path(chosen),None


def read_case(folder,materials,response_required=True):
    indexed={r['path']:r for r in materials}
    for name in ('manifest.json','analysis-request.json','analysis-response.json'):
        if name=='analysis-response.json' and not response_required and not (folder/name).exists():continue
        if file_digest(folder/name)!=indexed[str(folder/name)]['digest']:raise ValueError('discovered_material_changed')
    manifest=json.loads((folder/'manifest.json').read_bytes())
    if manifest.get('schema') not in {'sdm-model-review-manifest/v1','sdm-model-review-manifest/v2'} or not isinstance(manifest.get('files'),dict):raise ValueError('existing_review_manifest_required')
    verified=[]
    if manifest['schema']=='sdm-model-review-manifest/v2':
        sources=manifest.get('sources')
        if not isinstance(sources,list) or not sources:raise ValueError('bound_original_materials_required')
        for row in sources:
            path=plain(row['path']);observed=indexed.get(str(path))
            if observed is None or observed!=row or file_digest(path)!=row['digest']:raise ValueError('original_material_not_registered_or_changed')
            verified.append({'name':str(path),'digest':row['digest']})
    for name,expected in manifest['files'].items():
        if not re.fullmatch(r'[a-zA-Z0-9_.-]+',name) or name in {'.','..'}:raise ValueError('review_material_name_required')
        path=plain(folder/name)
        if not path.is_file() or path.stat().st_size>16*1024*1024 or file_digest(path)!=expected:raise ValueError('evidence_integrity_failed:'+name)
        verified.append({'name':name,'digest':expected})
    packet=json.loads((folder/'analysis-request.json').read_bytes())
    response=json.loads((folder/'analysis-response.json').read_bytes()) if (folder/'analysis-response.json').exists() else None
    if not isinstance(packet,dict) or response is not None and not isinstance(response,dict):raise ValueError('structured_case_request_response_required')
    if digest(packet)!=manifest['request_digest']:raise ValueError('manifest_request_mismatch')
    sources={row['digest'] for row in verified}
    for row in packet.get('history',[]):
        for stage in ('training','evaluation'):
            raw=row.get(stage,{}).get('actual',{}).get('log_digest')
            if raw is not None and raw not in sources:raise ValueError('history_log_not_bound_to_manifest')
    return packet,response,verified


def diagnosis(packet,verified):
    observations=[];missing=[];artifacts=[]
    from loop_contract import field
    for row in packet.get('history',[]):
        for stage in ('training','evaluation'):
            actual=row.get(stage,{}).get('actual',{})
            for name in ('checkpoint_paths','execution_output','output_path'):
                if name in actual:artifacts.append({'round':row['round'],'stage':stage,'field':name,'value':actual[name],
                    'verification':'retained-reference-not-opened-or-certified'})
        for metric in packet.get('project_metrics',[]):
            stage,_,suffix=metric.partition('.')
            value=field(row.get(stage,{}).get('actual',{}),suffix)
            if value is None:missing.append({'round':row['round'],'metric':metric});continue
            if type(value) in (int,float) and not math.isfinite(value):value={'state':'nonfinite'}
            observations.append({'round':row['round'],'metric':metric,'value':value})
    return {'schema':'sdm-retained-diagnosis/v1','evidence_kind':packet.get('evidence_kind','retained-material'),'verified_material':verified,'observations':observations,'artifact_references':artifacts,
        'missing_metric_fields':missing,'limitations':['Retained evidence; no fresh model execution.',
            'Byte integrity does not independently authenticate S13 or reviewer identity.',
            'Finite logged gradients do not prove optimizer updates; missing fields are not zero.']}


def run(config,root):
    from raw_materials import evidence_kind
    kind=evidence_kind(config)
    root=plain(Path(root).absolute());root.mkdir(parents=True,exist_ok=True)
    started=time.perf_counter()
    with locked(root,'.project.lock','project_workspace_busy'):
        timings={};tick=time.perf_counter()
        registration_profile={}
        identity,roots,snapshot=registration(config,root,registration_profile)
        timings['registration_detail']=registration_profile
        timings['registration_seconds']=time.perf_counter()-tick;tick=time.perf_counter()
        materials=discover(roots,config.get('material_include_globs'),config.get('material_discovery_limits'));index={'schema':'sdm-project-material-index/v1','materials':materials}
        index_id=digest(index).split(':')[1]
        index_path=root/'materials'/('index-'+index_id+'.json')
        if not index_path.exists():atomic_json(index_path,index)
        timings['discovery_seconds']=time.perf_counter()-tick;tick=time.perf_counter()
        production=None
        if config.get('material_preparation') is not None:
            from raw_materials import _coordinator_prepare
            folder,production=_coordinator_prepare(config,materials,root)
            waiting=None
            # Only the current generated folder is indexed, never old attempts.
            materials=materials+discover([folder]);index={'schema':'sdm-project-material-index/v1','materials':materials}
            produced_index=root/'materials'/('index-'+digest(index).split(':')[1]+'.json')
            if not produced_index.exists():atomic_json(produced_index,index)
        else:
            folder,waiting=select(config,materials)
            if waiting and waiting['status']=='awaiting-evidence':
                from raw_materials import prepare
                folder,waiting=prepare(config,materials,root)
        timings['preparation_seconds']=time.perf_counter()-tick
        if waiting:return {**waiting,'evidence_kind':kind,'workspace':str(root),'platform_mutated':False,'timings':timings}
        tick=time.perf_counter()
        packet,response,verified=read_case(folder,materials,response_required=False)
        if packet.get('evidence_kind','retained-material')!=kind:raise ValueError('project_packet_evidence_kind_mismatch')
        from proposal_review import mode,pro_binding
        review_mode=mode(config)
        if review_mode=='pro':
            if response is not None:raise ValueError('configured_pro_cannot_use_local_review')
            from loop_analysis import external_analysis,AnalysisPending
            try:response=external_analysis(packet,{'external_review':pro_binding(config)},folder,allow_historical=True)
            except AnalysisPending:pass
        if response is None:
            waiting={'status':'awaiting-review','evidence_kind':kind,'request_path':str(folder/'analysis-request.json'),
                'response_path':str(folder/'analysis-response.json'),'request_digest':digest(packet),
                'review_mode':review_mode,'verified_material':verified,
                'next_action':'Review the registered evidence and supply a context-bound response; no adapter or resource request is needed.',
                'execution_allowed':False,'platform_mutated':False,'timings':timings,
                'preparation_task':production.get('preparation_task') if production else None}
            timings['verification_seconds']=time.perf_counter()-tick
            if review_mode=='pro':
                waiting.update(external_review_result=str(folder/'external-review-result.json'),
                               pro_binding=pro_binding(config),response_path=str(folder/'external-review-result.json'))
            if config.get('require_next_step'):
                waiting['response_requirements'] = {'next_step': ['action', 'reason', 'acceptance',
                    'authorization', 'status', 'stop_decision', 'stop_reason'],
                    'instruction': 'Analyze the result, decide stop/continue, and state the next action; archive alone is insufficient.'}
            atomic_json(root/'review-inbox.json',waiting)
            return waiting
        if config.get('require_next_step'):
            from loop_analysis import validate_next_step
            validate_next_step(response)
        binding={'registration_digest':digest(identity),'material_index_digest':digest(index),
            'request_digest':digest(packet),'response_digest':digest(response),'repository_snapshot_digest':digest(snapshot),
            'processing_digest':digest({name:file_digest(Path(__file__).with_name(name)) for name in
                ('project_workspace.py','repository_snapshot.py','raw_materials.py','loop_analysis.py','loop_contract.py','history_context.py','review_storage.py','proposal_review.py','pro_review_workflow.py','pro_review_session.py')})}
        case_id=digest(binding).split(':')[1][:24];target=root/'runs'/case_id
        prior=target/'result.json'
        if prior.exists():
            accepted=json.loads((target/'accepted.json').read_bytes())
            if accepted!={'binding':binding,'result_digest':file_digest(prior)}:raise ValueError('project_result_changed')
            result=json.loads(prior.read_bytes())
            for name,expected in result['archive_digests'].items():
                if file_digest(plain(target/name))!=expected:raise ValueError('project_archive_changed:'+name)
            if result['binding']!=binding:raise ValueError('project_case_binding_changed')
            return {**result,'resumed':True,'timings':timings,'elapsed_seconds':time.perf_counter()-started}
        from loop_analysis import validate
        decision=validate(response,packet)
        result={'schema':'sdm-offline-project-result/v1','project_id':config['project_id'],'status':'completed',
            'workflow':'retained-evidence-review','evidence_kind':kind,
            'qualification':'CPU simulation acceptance only; no real project, model or platform qualification' if kind=='cpu-simulation' else 'real retained-material review; not a 34-node training execution',
            'binding':binding,'repository_snapshot':snapshot,'case_directory':str(folder),'review_origin':'response-for-current-produced-materials' if production else 'existing-response-revalidated',
            'decision':decision,'review_mode':review_mode,'recommendation':'Continue offline work; this result creates no model execution authority.',
            'execution_allowed':False,'training_dispatched':False,'platform_mutated':False,'nodes_added':0,
            'input_files_copied':False,'resumed':False,'human_questions':0,
            'elapsed_seconds':time.perf_counter()-started,'timings':timings,
            'material_production':production}
        if response.get('next_step') is not None:
            from loop_analysis import validate_next_step
            result['next_step'] = validate_next_step(response)
            result['recommendation'] = result['next_step']['action']
        timings['verification_seconds']=time.perf_counter()-tick
        target.mkdir(parents=True,exist_ok=True)
        atomic_json(target/'diagnosis.json',diagnosis(packet,verified));atomic_json(target/'decision.json',decision)
        atomic_json(target/'review.json',response)
        report='# '+config['project_id']+'：留存材料评审\n\n来源：'+str(folder)+'\n\n决定：'+decision['action']+'。'+decision['reason']+'\n\n'
        report+='证据类型：'+kind+'。'+('本案例仅为CPU模拟验收；所有生成指标均非模型效果、真实训练或平台验收证据。' if kind=='cpu-simulation' else '已有材料的本地评审，不是新模型执行。')+'\n\n'
        report+='验证材料数：'+str(len(verified))+'；已核对请求、引用事实、假设与决定。\n\n'
        report+='评审判断：'+response['previous_experiment_assessment']+'\n\n'
        report+='引用事实（原始值；来源定位见analysis-request的provenance）：\n\n'
        for fact in response['facts']:
            report+='- '+fact['evidence_id']+' = '+json.dumps(fact['value'],ensure_ascii=False)+'\n'
        report+='\n解释与限制：\n\n'
        for item in response['hypotheses']:
            report+='- '+item['statement']+'；限制：'+item['limitations']+'；依据：'+', '.join(item['evidence_ids'])+'\n'
        report+='\n'
        if result.get('next_step'):
            report+='下一步：'+json.dumps(result['next_step'],ensure_ascii=False)+'\n\n'
        report+='限制：历史证据不等于新评估；不认证外部评审者身份；原件完整性不证明训练成功或checkpoint实体可用。缺失指标见diagnosis.json，不补造零值。\n\n没有执行授权、GPU、平台动作或新训练。\n'
        (target/'REPORT.md').write_text(report,encoding='utf-8')
        result['archive_digests']={name:file_digest(target/name) for name in ('diagnosis.json','decision.json','review.json','REPORT.md')}
        atomic_json(prior,result)
        atomic_json(target/'accepted.json',{'binding':binding,'result_digest':file_digest(prior)})
        atomic_json(root/'latest.json',{'case_id':case_id,'result':str(prior),'result_digest':file_digest(prior)})
        if not (root/'README.md').exists():(root/'README.md').write_text('# '+config['project_id']+' 项目工作区\n\nproject.json登记已有仓库，不复制或修改仓库。materials保存材料索引，runs保存每次独立回放与交付；大日志和模型仅引用原件。latest.json指向最近一次结果。\n',encoding='utf-8')
        return result


def execute(config,root):
    """Archive blocked local cases too; never convert missing evidence into success."""
    try:return run(config,root)
    except (ValueError,OSError,KeyError,TypeError,subprocess.SubprocessError) as error:
        result={'status':'blocked','reason':str(error),'error_type':type(error).__name__,
            'workflow':'retained-evidence-review','evidence_kind':config.get('evidence_kind','retained-material'),'execution_allowed':False,'platform_mutated':False}
        if str(error)=='project_evidence_kind_changed_use_separate_workspace':
            try:
                registered=json.loads((plain(Path(root).absolute())/'project.json').read_bytes())
                result['requested_evidence_kind']=result['evidence_kind']
                result['evidence_kind']=registered.get('evidence_kind','retained-material')
            except (ValueError,OSError,TypeError):pass
        try:
            folder=plain(Path(root).absolute())/'failures';folder.mkdir(parents=True,exist_ok=True)
            path=folder/(str(time.time_ns())+'.json')
            atomic_json(path,{**result,'configuration_digest':digest(config)})
            result['failure_record']=str(path)
        except (ValueError,OSError):pass
        return result
