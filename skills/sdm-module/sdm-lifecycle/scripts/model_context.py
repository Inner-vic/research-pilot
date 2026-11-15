"""Explicit v4 research context and safe provenance; never read user/auth files."""
import json
from pathlib import Path

from research_model_receipts import checked,seal
from review_storage import atomic_json
from task_pipeline import digest


def policy(config):
    value=config.get('context_policy')
    version=value.get('version') if isinstance(value,dict) else None
    expected={'version':version,'user_config':value.get('user_config') if isinstance(value,dict) else None,'project_doc_max_bytes':0,
        'reasoning_effort':value.get('reasoning_effort') if isinstance(value,dict) else None,
        'input_kind':'tool-output' if config['backend']=='sdk' else 'text'}
    if version==2:expected['tool_output_token_limit']=value.get('tool_output_token_limit')
    if (not isinstance(value,dict) or value!=expected or type(version) is not int or version not in (1,2)
            or type(value.get('project_doc_max_bytes')) is not int
            or value['user_config'] not in ({'ignore','inherit-with-overrides'} if config['backend']=='sdk' else {'ignore'})
            or value['reasoning_effort'] not in {'low','medium','high'}):
        raise ValueError('explicit_research_context_policy_required')
    if version==2 and (config['backend']!='sdk' or type(value['tool_output_token_limit']) is not int
            or not 16384<=value['tool_output_token_limit']<=262144):
        raise ValueError('bounded_tool_output_history_policy_required')
    return value


def overrides(config):
    value=policy(config)
    result=['--config','project_doc_max_bytes=0',
        '--config','model_reasoning_effort='+json.dumps(value['reasoning_effort'])]
    if value['version']==2:result+=['--config','tool_output_token_limit='+str(value['tool_output_token_limit'])]
    return result


def input_budget(config,value):
    selected=policy(config)
    if selected['version']==2:
        from model_runner import rendered
        # A conservative byte bound avoids assuming a tokenizer or claiming
        # that the retained SDK item proves the model saw an uncut history item.
        if len(rendered(value).encode('utf-8'))>selected['tool_output_token_limit']:
            raise ValueError('registered_input_exceeds_explicit_history_budget')


def sdk_options(config):
    if policy(config)['user_config']!='inherit-with-overrides':
        raise ValueError('sdk_ignore_user_config_not_supported')
    return {'codex_bin':config['cli_path'],'cwd':config['cwd'],'experimental_api':False,
        'launch_args_override':tuple([config['cli_path'],*overrides(config),
            'app-server','--listen','stdio://'])}


def declared(config,value,argv,folder,expected):
    selected=policy(config)
    if value.get('input_kind')!=selected['input_kind']:
        raise ValueError('research_context_input_kind_changed')
    input_budget(config,value)
    check_command(config,argv)
    receipt=seal({'schema':'sdm-model-context-declared/v1','binding':expected,'backend':config['backend'],
        'policy':selected,'argv':list(argv),'command_digest':digest(list(argv)),
        'requested_model':value['model'],'cwd':config['cwd'],'sandbox':'read-only','approval':'deny-all',
        'experimental_api':False if config['backend']=='sdk' else None,
        'qualification':'Explicit requested launch/input policy; other host/managed layers and provider state not attested'})
    atomic_json(Path(folder)/'context-declared.json',receipt)
    return receipt


def check_command(config,argv):
    selected=policy(config)
    if config['backend']=='sdk' and selected['user_config']!='inherit-with-overrides':
        raise ValueError('sdk_ignore_user_config_not_supported')
    if (not argv or argv[0]!=config['cli_path']
            or list(argv).count('--ignore-user-config')!=(1 if config['backend']=='cli' else 0)
            or list(argv).count('--config')!=(3 if selected['version']==2 else 2)
            or any(item not in argv for item in overrides(config))):
        raise ValueError('research_context_launch_policy_changed')


def sdk_matches(config,value,selected):
    return (selected.get('model')==value['model'] and selected.get('approvalPolicy')=='never'
        and selected.get('sandbox',{}).get('type')=='readOnly'
        and selected.get('sandbox',{}).get('networkAccess') is False
        and selected.get('reasoningEffort')==policy(config)['reasoning_effort']
        and selected.get('instructionSources')==[]
        and Path(selected.get('cwd','')).resolve()==Path(config['cwd']).resolve())


def sdk_effective(config,value,response,folder,expected):
    # The public typed thread/start response is captured before turn/start.
    # Never persist account, auth, config/read or the full thread object.
    raw=response.model_dump(mode='json',by_alias=True,exclude_unset=True)
    keys=('model','modelProvider','cwd','approvalPolicy','approvalsReviewer',
        'reasoningEffort','sandbox','instructionSources','serviceTier')
    selected={key:raw[key] for key in keys if key in raw}
    matches=sdk_matches(config,value,selected)
    receipt=seal({'schema':'sdm-model-context-effective/v1','binding':expected,'backend':'sdk',
        'availability':'reported-thread-start-fields','thread_id':raw['thread']['id'],'fields':selected,
        'checks_passed':matches,'before_turn':True,
        'qualification':'Safe app-server thread/start readback; not a complete hidden prompt/provider attestation'})
    atomic_json(Path(folder)/'context-effective.json',receipt)
    if not matches:raise ValueError('effective_research_context_mismatch_before_turn')
    return receipt


def cli_effective(folder,expected,pid):
    receipt=seal({'schema':'sdm-model-context-effective/v1','binding':expected,'backend':'cli',
        'availability':'not-exposed-in-cli-trace','fields':None,'local_process_id':pid,
        'qualification':'Requested command retained separately; effective server settings unknown'})
    atomic_json(Path(folder)/'context-effective.json',receipt)
    return receipt


def bundle(folder,expected):
    folder=Path(folder)
    requested=checked(folder/'context-declared.json','sdm-model-context-declared/v1')
    effective=checked(folder/'context-effective.json','sdm-model-context-effective/v1')
    if requested['binding']!=expected or effective['binding']!=expected:
        raise ValueError('model_context_receipt_binding_changed')
    return {'declared':requested,'effective':effective}


def verify(config,value,folder,expected,result):
    saved=bundle(folder,expected)
    requested=saved['declared'];effective=saved['effective']
    check_command(config,requested['argv'])
    if (requested['backend']!=config['backend'] or effective['backend']!=config['backend']
            or requested['command_digest']!=digest(requested['argv'])
            or requested['policy']!=policy(config) or requested['requested_model']!=value['model']
            or requested['cwd']!=config['cwd'] or requested['policy']['input_kind']!=value.get('input_kind')
            or digest(saved)!=result.get('metadata',{}).get('context_digest')):
        raise ValueError('model_context_completion_changed')
    if config['backend']=='sdk' and (effective.get('checks_passed') is not True
            or not sdk_matches(config,value,effective['fields'])
            or effective['thread_id']!=result['metadata'].get('thread_id')):
        raise ValueError('model_context_effective_settings_changed')
    if config['backend']=='cli' and (effective['availability']!='not-exposed-in-cli-trace' or effective['fields'] is not None):
        raise ValueError('cli_effective_context_cannot_be_invented')
    return saved
