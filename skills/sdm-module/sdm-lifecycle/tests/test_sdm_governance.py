"""Original SDM rules in component control; synthetic transports, no ML."""
import ast
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from canonical import digest
from contracts import AssetBundle, ContractError, _read_relative_regular
from lifecycle_rules import asset_inputs, check_records, verify_assets
from research_intent import prepare, publish, for_review, validate_proposal
from review_storage import atomic_json, read_json
from component_payload import ref
from test_component_submission_gate import research, approve_initial, checked_initial
import test_component_submission_gate as existing


def contents(chinese=False):
    requirement = {'requirement_id': 'req-controlled', 'uri': 'artifact://request/controlled-v1',
        'owner': 'Research owner', 'impact': 'Improve fixed validation quality with a controlled comparison.',
        'non_goals': ['No untouched-test superiority claim.']}
    brief = {'brief_id': 'brief-controlled', 'requirement_id': requirement['requirement_id'],
        'hypothesis': 'A wider temporal receptive field may improve fixed validation macro F1.',
        'mechanism': 'A wider receptive field can capture longer temporal patterns.',
        'uncertainty': 'Unknown effect and independent seed stability.',
        'history': ['New study; no trained baseline or improvement is claimed.'],
        'source_refs': {}, 'target_as_must': False}
    if chinese:
        requirement.update(owner='研究负责人', impact='在固定验证集上改进分类质量，保持比较条件一致。',
                           non_goals=['不声明未使用测试集上的统计优势。'])
        brief.update(hypothesis='扩大时序感受野有望提高固定验证集的分类指标，结果可由实际比较推翻。',
                     mechanism='较宽的感受野可能捕捉更长时间范围内的形态差异。',
                     uncertainty='效果和不同随机种子下的稳定性尚不确定。', history=['这是新研究，尚未取得训练结果。'],
                     source_refs={})
    return {'requirement': requirement, 'brief': brief}


@pytest.mark.parametrize('chinese', [False, True])
def test_original_schemas_semantics_and_real_row_gates_are_used(chinese):
    value = prepare({'project_id': 'control-intent', 'research_intent': contents(chinese)})
    bundle = AssetBundle.load()
    assert value['engine_contract_digest'] == bundle.engine_contract_digest
    for step, record in zip((1, 2), value['record_checks']):
        row = bundle.document('lifecycle-steps.json')['steps'][step-1]
        assert record['passed'] and not record['findings']
        assert record['gate_digest'] == digest(row['gate'])
        assert record['transition_accepted'] is record['execution_authority'] is False
    assert value['remaining_integration_steps'] == list(range(3, 22))


@pytest.mark.parametrize('field,value', [('non_goals', []), ('impact', 'TODO'), ('uri', 'artifact://request/latest')])
def test_original_requirement_failures_are_not_review_overrides(field, value):
    material = contents(); material['requirement'][field] = value
    with pytest.raises(ValueError, match='sdm_intent_blocked'):
        prepare({'project_id': 'control-intent', 'research_intent': material})


@pytest.mark.parametrize('field,value', [('mechanism', ''), ('uncertainty', 'TODO'),
    ('target_as_must', True), ('hypothesis', 'TODO'), ('history', [])])
def test_original_brief_failures_remain_blocking(field, value):
    material = contents(); material['brief'][field] = value
    with pytest.raises(ValueError, match='sdm_intent_blocked'):
        prepare({'project_id': 'control-intent', 'research_intent': material})


def test_brief_cannot_substitute_other_requirement_or_digest():
    material = contents(); material['brief']['requirement_id'] = 'different-requirement'
    with pytest.raises(ValueError, match='exact_original_requirement'):
        prepare({'project_id': 'control-intent', 'research_intent': material})


def test_adapter_preserves_original_structured_uncertainty_and_optional_contract_fields(tmp_path):
    from research_intent import proposal_schema
    from contracts import validate_schema
    material=contents();brief=material['brief']
    brief['uncertainty']={'effect':'Unknown until actual comparison','seed_stability':['Not yet measured']}
    brief['source_refs']['G03:data-snapshot']='sha256:'+'7'*64
    brief['ml_contract_applicability']={'required_contract_ids':['G03','G10']}
    seed=prepare({'project_id':'control-intent','research_intent':material});brief=seed['brief']['content']
    policy=publish(seed,tmp_path/'intent.json');fixed=for_review(policy,brief)
    for current in (policy,fixed):
        assert not validate_schema(brief,proposal_schema(current))
        assert all(row['passed'] for row in validate_proposal({'hypothesis':brief['hypothesis'],'sdm_brief':brief},current))


def test_source_reference_uses_original_full_record_digest_and_resume_keeps_it(tmp_path):
    spec={'project_id':'control-intent','research_intent':contents()}
    seed=prepare(spec);brief=seed['brief']['content'];root='S01:'+seed['requirement']['instance_key']
    assert brief['source_refs'][root]==digest(seed['requirement'])
    assert brief['source_refs'][root]!=seed['requirement']['content_digest']
    policy=publish(seed,tmp_path/'intent.json');original=(tmp_path/'intent.json').read_bytes()
    assert publish(prepare(spec),tmp_path/'intent.json')==policy
    assert (tmp_path/'intent.json').read_bytes()==original
    wrong=copy.deepcopy(brief);wrong['source_refs'][root]=seed['requirement']['content_digest']
    with pytest.raises(ValueError,match='exact_original_requirement'):
        validate_proposal({'hypothesis':wrong['hypothesis'],'sdm_brief':wrong},policy)


def test_pinned_intent_and_read_only_code_review_cannot_silently_change(tmp_path):
    material = contents(); value = prepare({'project_id': 'control-intent', 'research_intent': material})
    policy = publish(value, tmp_path/'intent.json')
    material['brief']=value['brief']['content']
    review = for_review(policy, material['brief'])
    proposal = {'hypothesis': material['brief']['hypothesis'], 'sdm_brief': material['brief']}
    assert all(row['passed'] for row in validate_proposal(proposal, review))
    changed = copy.deepcopy(proposal); changed['sdm_brief']['mechanism'] = 'A different mechanism requires a new scientific plan.'
    with pytest.raises(ValueError, match='cannot_replace_frozen_sdm_brief'):
        validate_proposal(changed, review)
    wrong = {**policy, 'engine_contract_digest': 'sha256:'+'0'*64}
    with pytest.raises(ValueError, match='pinned_engine_changed'): for_review(wrong)
    (tmp_path/'intent.json').write_text('{}')
    with pytest.raises(ValueError): for_review(policy)


def test_initialization_validates_intent_before_freeze_or_any_external_call(tmp_path, monkeypatch):
    import research_initialize
    monkeypatch.setattr(research_initialize, 'freeze', lambda *_: pytest.fail('Must validate before freezing'))
    material = contents(); material['brief']['target_as_must'] = True
    spec = {key: {} for key in ('template','manager_config','request_template','baseline_source','baseline_model',
                               'comparison_identity','training','target','workload')}
    spec.update(schema='sdm-research-initialization/v2', project_id='intent-control', source_ref='Synthetic authority only',
                objective='Intent validation only', research_intent=material)
    path = tmp_path/'spec.json'; atomic_json(path, spec)
    with pytest.raises(ValueError, match='sdm_intent_blocked'):
        research_initialize.prepare(path, tmp_path/'freeze',tmp_path/'store',tmp_path/'workspace',tmp_path/'output/service.json')
    assert not (tmp_path/'freeze').exists() and not (tmp_path/'output').exists()


@pytest.fixture
def governed(research, monkeypatch):
    from component_pipeline import ComponentPipeline
    from research_event_service import Inbox
    cfg = research.cfg; cfg['root'] = str(research.tmp/'governed-service')
    material = contents()
    value = prepare({'project_id': cfg['project_id'], 'research_intent': material})
    material['brief']=value['brief']['content']
    policy = publish(value, research.tmp/'intent.json')
    cfg['component_pipeline']['sdm_intent'] = policy
    cfg['component_pipeline']['submission_gate'] = {'version': 2}
    cfg['component_pipeline']['proposal_review']['dialogue_version'] = 4
    confirmed_path=Path(cfg['component_pipeline']['initial_submission']['confirmed_plan']['path'])
    confirmed=read_json(confirmed_path);confirmed['sdm_intent']=policy;atomic_json(confirmed_path,confirmed)
    cfg['component_pipeline']['initial_submission']['confirmed_plan']=ref(confirmed_path)
    bundle, files = asset_inputs(); module = Path(cfg['runtime_root']).parent.parent
    cfg['engine_contract_digest'] = bundle.engine_contract_digest
    cfg['normative_manifest'] = {p.relative_to(module).as_posix():hashlib.sha256(raw).hexdigest() for p,raw in files.items()}
    # Only the model/platform transport is replaced. Real schemas, semantic
    # rules, row gates, review receipts, Manager/Guard and pipeline execute.
    saved = existing.save
    def model_reply(folder, bound, status, response, detail):
        response = copy.deepcopy(response)
        if 'hypothesis' in response:
            response.update(hypothesis=material['brief']['hypothesis'], sdm_brief=copy.deepcopy(material['brief']))
        return saved(folder,bound,status,response,detail)
    monkeypatch.setattr(existing, 'save', model_reply)
    research.inbox = Inbox(cfg); research.pipeline = ComponentPipeline(research.inbox)
    research.state = research.pipeline.load(); research.intent = material
    return research


def test_first_submission_consumes_original_core_and_replay_stays_side_effect_free(governed):
    approve_initial(governed)
    accepted = checked_initial(governed)
    checks = accepted['sdm_intent_checks']
    assert checks['checked_steps'] == [1,2] and all(x['passed'] for x in checks['record_checks'])
    assert checks['remaining_integration_steps'] == list(range(3,22))
    assert governed.backend.writes == 0 and len(governed.calls) == 4
    checked_initial(governed)
    assert governed.backend.writes == 0 and len(governed.calls) == 4
    governed.pipeline.step(governed.state)
    assert governed.backend.writes == 1


def test_successor_consumes_the_same_original_core(governed):
    existing.test_successor_dispatch_consumes_the_same_gate_and_original_authorization(governed)


def test_pause_and_original_receipts_survive_new_core(governed, monkeypatch):
    existing.test_pause_during_receipt_replay_prevents_submit_and_resume_reuses_reviews(governed, monkeypatch)


def test_unknown_intent_never_revalidates_as_a_new_submission(governed, monkeypatch):
    monkeypatch.setattr('research_intent.verify_submission', lambda *_:pytest.fail('Unknown intent must reconcile only'))
    existing.test_unknown_guard_intent_reconciles_without_new_reviews(governed, monkeypatch)


@pytest.mark.parametrize('remove_seed', [False, True])
def test_new_profile_cannot_downgrade_or_strip_its_intent(governed, remove_seed):
    from component_pipeline import validate
    cfg=copy.deepcopy(governed.cfg)
    cfg['component_pipeline']['submission_gate']={'version':1}
    if remove_seed:cfg['component_pipeline'].pop('sdm_intent')
    with pytest.raises(ValueError, match='cannot_downgrade|cannot_be_removed'):
        validate(cfg)


def test_frontend_reads_core_receipt_without_another_pass_rule(governed, tmp_path):
    from research_dashboard_data import DashboardData
    dashboard = DashboardData(tmp_path)
    view = dashboard.intent(governed.cfg)
    assert view['checked_steps'] == [1,2] and view['transition_accepted'] is False
    assert view['remaining_integration_steps'] == list(range(3,22))
    assert view['label'] == read_json(governed.spec['sdm_intent']['seed']['path'])['projection']['label']
    cfg = copy.deepcopy(governed.cfg); cfg['component_pipeline'].pop('sdm_intent')
    assert dashboard.intent(cfg) is None


def test_new_freeze_carries_and_checks_complete_normative_closure(tmp_path):
    from research_runtime import freeze
    cfg = freeze(tmp_path/'frozen')
    assert len(cfg['normative_manifest']) == 26
    bundle = verify_assets(cfg)
    assert bundle.engine_contract_digest == AssetBundle.load().engine_contract_digest
    path = Path(cfg['runtime_root']).parent/'assets/lifecycle-steps.json'
    path.write_bytes(path.read_bytes() + b'\n')
    with pytest.raises(ValueError, match='engine_asset_changed'): verify_assets(cfg)


def test_owner_schema_tamper_or_omission_fails_closed(tmp_path):
    _, inputs = asset_inputs(); module = Path(__file__).resolve().parents[2]
    target = tmp_path/'module'
    for source, raw in inputs.items():
        path = target/source.relative_to(module);path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
    root = target/'sdm-lifecycle/assets'
    bundle = AssetBundle.load(root)
    owner = target/next(name for name in bundle.documents if name.startswith('sdm-'))
    original = owner.read_bytes(); data = json.loads(original);data['title'] = 'Tampered owner schema'
    owner.write_text(json.dumps(data))
    with pytest.raises(ContractError, match='canonical bytes'): AssetBundle.load(root)
    owner.unlink()
    with pytest.raises(ContractError): AssetBundle.load(root)


def test_pure_record_api_does_not_forge_missing_platform_or_authority():
    checked = check_records(AssetBundle.load(),13,{})
    assert not checked['passed'] and not checked['transition_accepted']
    assert any('platform_result' in missing for missing in checked['missing'])
    with pytest.raises(ContractError):check_records(AssetBundle.load(),19,{'S14':[{}]})


@pytest.mark.skipif(os.name!='nt', reason='Windows no-follow capability')
def test_windows_rejects_hardlink_and_preserves_containment(tmp_path):
    real = tmp_path/'real.json';real.write_text('{}'); linked = tmp_path/'linked.json'
    os.link(real,linked)
    with pytest.raises(OSError):_read_relative_regular(tmp_path,'linked.json')
    with pytest.raises(ContractError):_read_relative_regular(tmp_path,'../outside.json')
    with pytest.raises(OSError):_read_relative_regular(tmp_path,'NUL')


@pytest.mark.skipif(os.name!='nt', reason='Windows no-follow capability')
def test_windows_rejects_directory_junction(tmp_path):
    target = tmp_path/'target';target.mkdir();(target/'value.json').write_text('{}')
    linked = tmp_path/'junction'
    assert linked.parent.resolve() == tmp_path.resolve()
    result = subprocess.run(['powershell','-NoProfile','-NonInteractive','-Command',
        "New-Item -ItemType Junction -Path '"+str(linked).replace("'","''")+"' -Value '"+str(target).replace("'","''")+"' | Out-Null"],capture_output=True)
    assert result.returncode == 0,result.stderr.decode(errors='replace')
    try:
        with pytest.raises(OSError):_read_relative_regular(tmp_path,'junction/value.json')
    finally:
        os.rmdir(linked)  # Remove the junction itself, never its target tree.


@pytest.mark.skipif(os.name!='nt', reason='Windows held-handle sharing')
def test_windows_pinned_handle_blocks_concurrent_file_replacement(tmp_path, monkeypatch):
    import windows_assets
    path = tmp_path/'value.json';path.write_text('{"stable":true}')
    original = windows_assets._open; attempts=[]
    def opening(name,parent,directory,**kwargs):
        handle,info = original(name,parent,directory,**kwargs)
        if not directory:
            process = subprocess.run([sys.executable,'-B','-c',
                'import pathlib,sys\ntry:pathlib.Path(sys.argv[1]).write_text("changed")\nexcept PermissionError:sys.exit(0)\nsys.exit(9)',
                str(path)],capture_output=True)
            attempts.append(process.returncode)
        return handle,info
    monkeypatch.setattr(windows_assets,'_open',opening)
    assert _read_relative_regular(tmp_path,'value.json') == b'{"stable":true}'
    assert attempts == [0]


@pytest.mark.skipif(os.name!='nt', reason='Windows metadata sharing fault')
def test_windows_transient_sharing_retry_retains_no_follow_and_share_flags(tmp_path,monkeypatch):
    import windows_assets
    path=tmp_path/'value.json';path.write_text('{}')
    nt=windows_assets._api()[-2];native=nt.NtCreateFile;calls=[]
    def attempt(*args):
        calls.append((args[6],args[8]))
        if len(calls)==1:return -1073741757  # STATUS_SHARING_VIOLATION
        return native(*args)
    monkeypatch.setattr(nt,'NtCreateFile',attempt)
    assert _read_relative_regular(tmp_path,'value.json')==b'{}'
    assert calls[0]==calls[1] and all(share in (1,7) and options & 0x200000 for share,options in calls) and calls[-1][0]==1


@pytest.mark.skipif(os.name!='nt', reason='Windows ancestor sharing boundary')
def test_windows_ancestor_writer_does_not_block_unrelated_asset_reads(tmp_path):
    import windows_assets
    c,w,*rest=windows_assets._api();kernel=rest[-1]
    kernel.CreateFileW.argtypes=[w.LPCWSTR,w.DWORD,w.DWORD,c.c_void_p,w.DWORD,w.DWORD,w.HANDLE]
    kernel.CreateFileW.restype=w.HANDLE
    assets=tmp_path/'assets';assets.mkdir();(assets/'value.json').write_text('{}')
    # Simulate another project/editor holding a writable parent directory.
    handle=kernel.CreateFileW(str(tmp_path),0x40000000,7,None,3,0x02000000|0x00200000,None)
    assert handle not in (None,c.c_void_p(-1).value),c.WinError(c.get_last_error())
    try:
        assert _read_relative_regular(assets,'value.json')==b'{}'
    finally:
        kernel.CloseHandle(handle)
