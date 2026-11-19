"""Original accepted candidate selection, not a new baseline qualification rule."""
import copy
from pathlib import Path
import sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts/tests'))
from test_component_research_continuation import result,ref,direction
from review_storage import read_json,atomic_json
from component_candidates import registry,selected_reference,review_view


def fixture(root,count=3):
 rows=[]
 for index,prediction in enumerate(([0,1,1,0],[0,0,1,0],[1,0,0,1])[:count],1):
  row=result(root,index,prediction);path=Path(row['result']['path']);ready=read_json(path)
  artifact=Path(ready['artifact_root'])/'result.json';report=read_json(artifact);report['selected_epoch']=2;report['code_sha256']='c'*64;atomic_json(artifact,report)
  ready['artifact_sha256']['result.json']=ref(artifact)['sha256']
  manager=root/('manager-'+str(index)+'.json');atomic_json(manager,{'request':{'request_key':'synthetic-reference-'+str(index)}})
  ready.update(submission_identity={'manager_plan':ref(manager)},original_plan_sha256=ref(manager)['sha256'])
  atomic_json(path,ready);row['result']=ref(path);rows.append(row)
 uids=[read_json(row['result']['path'])['uid'] for row in rows]
 candidates=registry(rows,{'version':1,'target':{'metric':'validation.macro_f1','operator':'>=','value':.99},
   'budget':{'known_task_uids':[uids[0]],'max_new_submissions':10}},uids)
 chosen=candidates['incumbent'];revision=direction()
 revision['candidate_decision']={'choice':'continue' if count==1 else 'rollback','base_task_uid':chosen['uid'],
  'reason':'Use the evidenced candidate, not chronological adjacency','why_not_alternatives':'Later result regressed',
  'falsifier':'The next controlled intervention fails the declared comparison','cost_justification':'Original bounded scope'}
 proposal={'research_revision':revision,'comparison':'same actual validation','outcome_actions':'retain all observations'}
 accepted=root/'accepted.json';atomic_json(accepted,{'approved':True,'proposal':proposal,'response':{'verdict':'approve'}})
 plan={'schema':'sdm-research-plan/v1','status':'research-plan-approved-awaiting-implementation','research_revision':revision,
  'evidence_ids':revision['evidence_ids'],'proposal':proposal,'accepted_review_ref':ref(accepted),
  'candidate_management':candidates,'selected_candidate':chosen}
 return plan


def test_later_candidate_survives_a_newer_regression_without_locking_initial_result(tmp_path):
 plan=fixture(tmp_path);selected=selected_reference(plan);c=plan['candidate_management']
 assert selected['uid']==c['candidates'][1]['uid'] and selected['uid'] not in (c['candidates'][0]['uid'],c['latest']['uid'])
 assert review_view(c)['reference_policy']['initial_role']=='exploration-start'
 assert c['reference_policy']['formal_baseline_qualification_inferred'] is False


def test_low_initial_score_is_a_valid_exploration_reference_not_formal_qualification(tmp_path):
 plan=fixture(tmp_path,1);assert selected_reference(plan)['metric']==.5
 assert plan['candidate_management']['reference_policy']['formal_baseline_qualification_inferred'] is False


@pytest.mark.parametrize('damage',['first-instead-of-selected','latest-instead-of-selected','metric','protocol','recipe','decision'])
def test_original_accepted_reference_cannot_be_substituted(tmp_path,damage):
 plan=fixture(tmp_path);c=plan['candidate_management']
 if damage=='first-instead-of-selected':plan['selected_candidate']=c['candidates'][0]
 elif damage=='latest-instead-of-selected':plan['selected_candidate']=c['latest']
 elif damage=='metric':
  plan['selected_candidate']['metric']=.95
 elif damage=='protocol':c['comparison_identity']={**c['comparison_identity'],'protocol':'another'}
 elif damage=='recipe':
  changed=tmp_path/'changed-manager.json';atomic_json(changed,{'synthetic':'different'});plan['selected_candidate']['manager_plan']=ref(changed)
 elif damage=='decision':
  plan['research_revision']=copy.deepcopy(plan['research_revision']);plan['research_revision']['candidate_decision']['base_task_uid']=c['latest']['uid']
 with pytest.raises(ValueError):selected_reference(plan)


def test_failed_result_never_becomes_a_comparison_candidate(tmp_path):
 plan=fixture(tmp_path);selected=plan['selected_candidate'];ready=read_json(selected['result']['path']);ready['valid_training_round']=False
 atomic_json(selected['result']['path'],ready);selected['result']=ref(Path(selected['result']['path']))
 with pytest.raises(ValueError,match='verified_component_training_round'):selected_reference(plan)

def implementation_owner(root,plan):
 from types import SimpleNamespace
 from unittest.mock import Mock
 from component_pipeline import ComponentPipeline
 starting=root/'starting.py';starting.write_text('# Explicit control-only starting recipe\n')
 initial=root/'request.json';atomic_json(initial,{'uid':'synthetic-resource'})
 delegation=root/'delegation.json';atomic_json(delegation,{'training_limits':{'min_epochs':10,'max_epochs':30,'default_epochs':20}})
 plan['goal_assessment']={'valid_training_rounds':3};path=root/'research-plan.json';atomic_json(path,plan)
 atomic_json(root/'implementation-request.json',{'synthetic':'unexecuted request'})
 atomic_json(root/'replan-config.json',{'research_replan':{'sources':[]}})
 owner=SimpleNamespace(spec={'version':4,'baseline_source':ref(starting),'comparison_identity':plan['candidate_management']['comparison_identity'],
  'initial_request':ref(initial),'delegation':ref(delegation)},manager_config={},inbox=SimpleNamespace(cfg={'cwd':str(root/'workspace')}),
  base_project=lambda:{'proposal_review':{}},coordinator=Mock(return_value='control-only-job'))
 return owner,path,ComponentPipeline.implementation


def test_implementation_handoff_uses_the_reviewed_later_recipe(tmp_path):
 from unittest.mock import patch
 plan=fixture(tmp_path);owner,path,implement=implementation_owner(tmp_path,plan)
 folder=tmp_path/'handoff';folder.mkdir();current={'id':'next','result':plan['candidate_management']['latest']['result']}
 # Opaque execution-contract and payload decoding substitutes only; original selection checks run.
 with patch('component_payload.contract',return_value=ref(path)),patch('component_payload.executed_request_files',return_value=({'model.py':'# chosen second candidate, control only\n'},{})):
  implement(owner,current,folder,path)
 proof=read_json(folder/'selected-base/proof.json')
 assert proof['selected_candidate']['uid']==plan['candidate_management']['candidates'][1]['uid']
 assert (folder/'selected-base/model.py').read_text().startswith('# chosen second')
 owner.coordinator.assert_called_once();assert current['stage']=='preparing-implementation'


@pytest.mark.parametrize('wrong_index',[0,2])
def test_implementation_blocks_reference_substitution_before_dispatch(tmp_path,wrong_index):
 from unittest.mock import patch
 plan=fixture(tmp_path);plan['selected_candidate']=plan['candidate_management']['candidates'][wrong_index]
 owner,path,implement=implementation_owner(tmp_path,plan);folder=tmp_path/'handoff';folder.mkdir()
 with patch('component_payload.contract',return_value=ref(path)):
  with pytest.raises(ValueError,match='selected_reference_must_match_accepted_candidate'):
   implement(owner,{'id':'next','result':plan['candidate_management']['latest']['result']},folder,path)
 owner.coordinator.assert_not_called();assert not (folder/'selected-base').exists()
