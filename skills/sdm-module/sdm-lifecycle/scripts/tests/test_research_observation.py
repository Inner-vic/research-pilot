"""Live semantic changes interleaved with actual CPU processes, not retrofill."""
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import pytest
import research_observation as research
import devspace_study as study
from test_devspace_study import case, public, respond, ENTRY


def config_at(tmp_path):
    config, root = case(tmp_path)
    config['research_observation'] = {'version':1,'project_id':'cpu-dialogue','question_id':'q-rate',
        'store':str(tmp_path/'research.sqlite3'),'run_roots':[str(root)]}
    config['devspace_study']['authorization']['study_digest'] = study.binding(config)
    return config, root


def change(kind, node, **extra):
    return {'schema':'sdm-research-event/v1','request_id':'request-'+node,'id':node,
        'kind':kind,'question_id':'q-rate','text':'CPU fixture '+node,
        'actor':{'kind':'agent','source_ref':'synthetic CPU conversation, not user consent'}, **extra}


def record_public(config, root, body):
    project=root.parent/'project.json';project.write_text(json.dumps(config))
    path=root.parent/(body['id']+'.json');path.write_text(json.dumps(body))
    result=subprocess.run([sys.executable,'-B',str(ENTRY),'--project',str(project),
                           '--research-event',str(path)],capture_output=True,text=True,timeout=20)
    assert result.returncode==0,result.stderr
    return json.loads(result.stdout)


def test_public_live_dialogue_run_analysis_successor_and_correction(tmp_path):
    config,root=config_at(tmp_path)
    record_public(config,root,change('question','q-rate',expected_revision=0))
    record_public(config,root,change('hypothesis','h1',expected_revision=1))
    assert not root.exists()  # Dialogue observation does not launch anything.
    first=public(config,root,True)
    snapshot=first['review']['research_context']
    assert snapshot['status']=='available' and Path(snapshot['path']).is_file()
    assert any(n['kind']=='hypothesis' for n in json.loads(Path(snapshot['path']).read_bytes())['nodes'])
    ctx=research.context(config);run1=ctx['runs'][0]['id']
    assert ctx['runs'][0]['evidence_kind']=='cpu-simulation'
    record_public(config,root,change('finding','f1',run_ids=[run1],
        text='The actual first CPU result is .01; increase the declared parameter to .02.'))
    before=research.context(config)
    respond(first)
    second=public(config,root,True)
    ctx=research.context(config)
    assert len(ctx['runs'])==2 and any(n.get('projection')=='accepted-agent-decision' for n in ctx['nodes'])
    run2=next(r['id'] for r in ctx['runs'] if r['id']!=run1)
    record_public(config,root,change('correction','c1',run_ids=[run1,run2],
        text='The fixture score is its input parameter, not evidence of scientific model improvement.',
        relations=[{'relation':'revises','target':'h1','basis':'observed'}]))
    respond(second,'stop');done=public(config,root)
    assert done['status']=='completed'
    final=research.context(config)
    assert len(final['runs'])==2
    assert next(n for n in final['nodes'] if n['id']=='h1')['text']=='CPU fixture h1'
    assert len([n for n in final['nodes'] if n.get('projection')=='accepted-agent-decision'])==2
    assert before['revision'] < final['revision']
    run_changes=[n for n in final['timeline'] if n['kind']=='run']
    assert final['timeline'][0]['kind']=='question' and final['timeline'][1]['kind']=='hypothesis'
    assert run_changes and any(n['kind']=='correction' for n in final['timeline'])
    size=final['revision'];public(config,root)
    assert research.context(config)['revision']==size
    assert not (root/'round-03').exists()


def test_immutable_idempotency_cas_and_explicit_basis(tmp_path):
    config,root=config_at(tmp_path);q=change('question','q-rate',expected_revision=0)
    research.event(config,q);research.event(config,change('hypothesis','h1'))
    assert research.event(config,q)['reused']
    with pytest.raises(ValueError,match='request_conflict'):research.event(config,{**q,'text':'different'})
    with pytest.raises(ValueError,match='revision_conflict'):research.event(config,change('hypothesis','h2',expected_revision=0))
    with pytest.raises(ValueError,match='immutable'):research.event(config,{**q,'request_id':'different-request','expected_revision':2})
    with pytest.raises(ValueError,match='preserve_prior'):research.event(config,change('correction','c1'))
    with pytest.raises(ValueError,match='basis_required'):research.event(config,change('finding','f1'))
    assert len(research.context(config)['nodes'])==2


def test_observer_failure_is_visible_and_cannot_change_execution(tmp_path):
    config,root=config_at(tmp_path)
    config['research_observation']['store']=str(tmp_path)  # Directory cannot be a DB.
    config['devspace_study']['authorization']['study_digest'] = study.binding(config)
    first=public(config,root,True)
    assert first['status']=='awaiting-review' and first['stop_verified']
    assert first['research_observation']['status']=='unavailable'
    assert not first['research_observation']['queued']
    assert first['review']['research_context']['status']=='unavailable'
    malformed={**config,'research_observation':True}
    failed=research.capture_execution(malformed,first,root)
    assert failed['status']=='unavailable' and failed['execution_unchanged']


def test_semantic_decision_is_not_training_authorization(tmp_path):
    config,root=config_at(tmp_path)
    research.event(config,change('question','q-rate'))
    research.event(config,change('hypothesis','h1'))
    result=record_public(config,root,change('decision','d1',next_action={'action':'run-next-round'},
        relations=[{'relation':'motivates','target':'h1','basis':'hypothesis'}]))
    assert result['execution_authority'] is False and not root.exists()


def test_project_binding_and_other_question_run_rejected(tmp_path):
    config,root=config_at(tmp_path);research.event(config,change('question','q-rate'))
    other={**config,'research_observation':{**config['research_observation'],'project_id':'other'}}
    with pytest.raises(ValueError,match='binding_changed'):research.context(other)
    with pytest.raises(ValueError,match='other_question_run'):
        research.event(config,change('finding','f1',run_ids=['run-fabricated']))


def test_evidence_mutation_is_rejected_on_new_judgment(tmp_path):
    config,root=config_at(tmp_path);research.event(config,change('question','q-rate'))
    evidence=tmp_path/'result.json';evidence.write_text('{"score":1}')
    refs=research.retained_refs([{'path':str(evidence)}]);evidence.write_text('{"score":2}')
    with pytest.raises(ValueError,match='evidence_changed'):
        research.event(config,change('finding','f1',evidence=refs))


def test_projection_is_coordinator_owned_and_context_question_is_scoped(tmp_path):
    config,root=config_at(tmp_path)
    research.event(config,change('question','q-rate'))
    with pytest.raises(ValueError,match='coordinator_owned'):
        research.event(config,change('hypothesis','spoof',projection='accepted-agent-decision'))
    research.event(config,change('question','q-other',question_id='q-other'))
    research.event(config,change('hypothesis','h-other',question_id='q-other'))
    selected=research.context(config,'q-rate')
    assert selected['total_nodes']==1
    assert all(n['question_id']=='q-rate' for n in selected['timeline'])
    for i in range(105):research.event(config,change('hypothesis','h-'+str(i)))
    bounded=research.context(config,'q-rate')
    assert len(bounded['nodes'])==100 and len(bounded['timeline'])==100 and bounded['has_more']
    assert bounded['total_nodes']==106


def test_public_research_mode_refuses_execution_options(tmp_path):
    config,root=config_at(tmp_path)
    project=root.parent/'project.json';project.write_text(json.dumps(config))
    for flags in [ ['--research-context','--allow-devspace-live'],
                   ['--research-context','--devspace-execution'],
                   ['--research-question','q-rate'] ]:
        result=subprocess.run([sys.executable,'-B',str(ENTRY),'--project',str(project)]+flags,
                              capture_output=True,text=True,timeout=20)
        assert result.returncode!=0
    assert not root.exists()
