"""Incremental R capability: missing choices, resource arithmetic and observed state.

No provider calls or execution authority. Drafts remain editable until accepted;
the coordinator and bound platform adapter own confirmation and actual actions.
"""

def draft_questions(name, value):
    if name == 'inventory':
        questions=[]
        if not value.get('account_ref'):
            questions.append({'field':'account_ref','question':'本次使用哪个账号或项目空间？'})
        if not value.get('options'):
            questions.append({'field':'options','question':'先核实当前账号有哪些符合任务需求的资源入口和队列。'})
        return questions or [{'field':'inventory_evidence','question':'补齐当前队列状态、证据来源及观察时间；未知项明确保留。'}]
    questions=[]
    request=value.get('request',{})
    if not isinstance(request,dict):request={}
    for key,prompt in (
        ('provider','本次准备通过开发空间、组件任务还是其他已支持入口运行？'),
        ('queue_id','结合已核实选项，本次选用哪个资源队列？')):
        if not request.get(key):questions.append({'field':'request.'+key,'question':prompt})
    roles=request.get('roles')
    if not isinstance(roles,list) or not roles:
        questions.append({'field':'request.roles','question':'本次需要几台实例、什么 GPU 型号和多少卡？是否有 master/worker 等不同角色？'})
    else:
        for i,role in enumerate(roles):
            role=role if isinstance(role,dict) else {}
            for key,label in (('instances','实例数'),('gpu_type','GPU 型号'),
                              ('gpus_per_instance','每实例卡数'),
                              ('cpu_cores_per_instance','每实例 CPU'),
                              ('memory_gib_per_instance','每实例内存')):
                if key not in role or (role[key] is None and not (
                        key=='gpus_per_instance' and role.get('allocation_mode')=='shared')):
                    questions.append({'field':f'request.roles.{i}.{key}',
                                      'question':f'请结合任务需求和实际套餐确定第 {i+1} 个角色的{label}。'})
            if role.get('allocation_mode')=='shared' and not role.get('process_device_limit'):
                questions.append({'field':f'request.roles.{i}.process_device_limit',
                                  'question':'共享资源不代表独占卡数，本次程序最多使用几个设备？'})
    for key,prompt in (
        ('purpose','本次用这些资源完成什么任务？'),
        ('max_seconds','本次等待、运行和停止合计允许多长时间？'),
        ('max_cost','结合报价和未知计费项，本次接受的费用预算是多少？'),
        ('quote_source','补齐这份具体资源配置的价格来源，未知项不要按免费处理。'),
        ('stop_path','本次怎样停止，并核验同一任务或空间已释放？')):
        if key not in value or value[key] in (None,''):
            questions.append({'field':key,'question':prompt})
    return questions or [{'field':'planning_status',
                          'question':'核对完整资源明细及尚未知项，定稿后再请求用户确认具体方案。'}]


def discovery_route(value):
    """Select a read-only collector; never treat UI as the first provider probe."""
    check=value.get('capability_check',{})
    if not (check.get('source') and check.get('observed_at')):
        return {'collector':'sdk-cli','action':'inspect-installed-skill-sdk-and-cli-help',
                'required_output':'capability_check: source, observed_at, missing_fields'}
    missing=check.get('missing_fields')
    if not isinstance(missing,list):
        return {'collector':'sdk-cli','action':'determine-supported-and-missing-fields'}
    if missing:
        return {'collector':'browser-or-computer-use','action':'collect-only-missing-fields',
                'fields':missing,'preserve_sdk_cli_observations':True}
    return {'collector':'sdk-cli','action':'collect-supported-resource-facts'}


def draft_progress(name, value):
    questions=draft_questions(name,value)
    # Platform facts and a proposed configuration are agent work, not user homework.
    user_fields={'account_ref','purpose'}
    user_questions=[q for q in questions if q['field'] in user_fields]
    status='awaiting-user-input' if user_questions else 'needs-resource-investigation'
    route=discovery_route(value)
    return {'status':status,'reason':'resource_plan_incomplete',
            'record_kind':name,'questions':user_questions,
            'next_question':user_questions[0] if user_questions else None,
            'agent_work':questions,'collection_route':route,
            'known_fields':sorted(value),'execution_allowed':False,
            'next_action':('resolve-missing-user-scope' if user_questions else
                           'collect-resource-facts-and-prepare-configuration-budget-recommendation')}


def resource_totals(request):
    """Count all roles, preserving shared capacity as unknown rather than zero."""
    roles=request.get('roles')
    if not isinstance(roles,list) or not roles:raise ValueError('resource_roles_required')
    instances=cards=0;shared=False;device_limit=0
    for role in roles:
        n=role.get('instances')
        if type(n) is not int or n<=0:raise ValueError('positive_instances_required')
        instances+=n
        if role.get('allocation_mode')=='shared':
            limit=role.get('process_device_limit')
            if role.get('gpus_per_instance') is not None or type(limit) is not int or limit<=0:
                raise ValueError('explicit_shared_process_limit_required')
            shared=True;device_limit+=n*limit
        else:
            count=role.get('gpus_per_instance')
            if type(count) is not int or count<0:raise ValueError('explicit_gpu_count_required')
            cards+=n*count;device_limit+=n*count
    return {'instances':instances,'dedicated_gpu_count':cards,
            'total_gpu_allocation':None if shared else cards,
            'contains_shared_allocation':shared,'program_device_ceiling':device_limit}


def observed_stage(raw_phase, optimizer_updates=None, identity_verified=False):
    """Describe what is observed; a ready container alone is never training."""
    phase=str(raw_phase).lower()
    if phase in {'pending','queued','scheduling'}:stage='queueing'
    elif phase in {'starting','initializing','image_pulling','creating'}:stage='starting'
    elif phase in {'ready','running'}:
        stage='model-updating' if type(optimizer_updates) is int and optimizer_updates>0 else 'environment-ready'
    elif phase in {'stopped','unstarted'}:
        stage='released' if identity_verified else 'stopped-identity-unverified'
    elif phase in {'failed','error'}:stage='failed-release-unverified'
    else:stage='unknown'
    return {'raw_phase':raw_phase,'observed_stage':stage,
            'model_updates_observed':stage=='model-updating',
            'release_verified':stage=='released'}
