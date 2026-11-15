import sys
from pathlib import Path
from copy import deepcopy
import pytest
sys.path.insert(0,str(Path(__file__).parents[1]))
from loop_analysis import request,validate
from task_pipeline import digest

def case():
 history=[{'round':1,'parameters':{'amp':True,'learning_rate':4e-5},
   'training':{'status':'completed','actual':{'exit_code':0,'iteration_evidence_complete':True,'nonfinite_gradient_count':7,'log_excerpt':'actual fixture log'}},
   'evaluation':{'status':'completed','actual':{'surface_miou':10.0}}}]
 cfg={'max_rounds':3,'analysis_objective':'Investigate the actual numerical observations.',
   'project_metrics':['training.nonfinite_gradient_count','evaluation.surface_miou'],
   'allowed_variables':{'amp':{'type':'boolean'},'learning_rate':{'type':'number','minimum':1e-7,'maximum':4e-5}}}
 packet=request(history,cfg)
 response={'request_digest':digest(packet),'action':'run-next-round','changed_variable':'learning_rate',
   'next_parameters':{'amp':True,'learning_rate':3e-5},'facts':[{'evidence_id':'r1.training.nonfinite_gradient_count','value':7}],
   'hypotheses':[{'statement':'A rate-sensitive effect is possible.','evidence_ids':['r1.training.nonfinite_gradient_count'],'limitations':'A single fixture observation cannot identify cause.'}],
   'previous_experiment_assessment':'Initial observation only.','reason':'Test the stated hypothesis.',
   'hypothesis':'A smaller rate may reduce numerical instability.','expected_observations':['Compare gradient and loss observations.']}
 return packet,response

def test_model_selects_nonpreset_rate_without_rule_override():
 packet,response=case(); assert validate(response,packet)['next_parameters']['learning_rate']==3e-5

@pytest.mark.parametrize('mutation,error',[
 (lambda r:r.update(request_digest='wrong'),'analysis_request_mismatch'),
 (lambda r:r['facts'][0].update(value=0),'invented_analysis_evidence'),
 (lambda r:r['next_parameters'].update(amp=False),'exactly_one_loop_variable_required'),
 (lambda r:r['next_parameters'].update(learning_rate=1.0),'variable_outside_bounds'),
 (lambda r:r.update(action='shell-command'),'bounded_analysis_action_required'),
 (lambda r:r.update(expected_observations=[]),'falsifiable_experiment_required')])
def test_invalid_model_proposals_are_rejected(mutation,error):
 packet,response=case(); mutation(response)
 with pytest.raises(ValueError,match=error): validate(response,packet)

def test_failure_evidence_is_available_but_uncertain_execution_cannot_restart():
 packet,response=case(); packet['history'][0]['training']['uncertain_effect']=True
 response['request_digest']=digest(packet)
 with pytest.raises(ValueError,match='uncertain_execution'): validate(response,packet)
 response.update(action='stop-analysis',reason='Reconcile execution instead of guessing.')
 assert validate(response,packet)['action']=='stop-analysis'

def test_metrics_are_project_configuration_not_gradient_gate():
 packet,response=case(); packet['project_metrics']=['evaluation.custom_height_error']
 packet['history'][0]['training']['actual'].pop('nonfinite_gradient_count')
 response['request_digest']=digest(packet)
 assert validate(response,packet)['action']=='run-next-round'
