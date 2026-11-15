import json
import hashlib
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).parents[1]))
import run_project_loop as p

class ProjectLoopTests(unittest.TestCase):
    def config(self,base):
        loc=base/'input-locators.json'; loc.write_text(json.dumps({'execution_output_root':'/lpai/base','checkpoint_digest':'baseline'}))
        driver=base/'fixture-driver.py'; driver.write_text('# Isolated project driver; subprocess is mocked in these legacy tests.\n')
        return {'provider':'roadmodel-devspace-v1','run_id':'fixture-loop','max_rounds':3,'max_attempts':1,'decision_mode':'legacy-rule',
                'initial_parameters':{'amp':True,'learning_rate':4e-5},'locators_linux':str(loc),
                'project_driver':{'path':str(driver),'digest':'sha256:'+hashlib.sha256(driver.read_bytes()).hexdigest()},
                'remote_loop_root':'/lpai/fixture-loop','windows_python_wsl':'unused','fixed_subset_digest':'subset'}
    def cleanup(self,argv,**kwargs):
        Path(argv[argv.index('--receipt')+1]).write_text(json.dumps({'status':'completed','resource':{'phase':'stopped'}}))
        return SimpleNamespace(returncode=0,stdout='',stderr='')
    def observed(self,root,summary,node,name):
        plan=json.loads((root/'round-plan.json').read_bytes()); controls=plan['parameters']
        actual={'source_commit':'commit','source_url':'url','source_index_digest':'index','subset_digest':'subset',
                'fixed_config_digest':'fixed','evaluator_source_digest':'evaluator','fixed_subset_path':'/lpai/base/subset.json',
                'selected_gpu_uuid':'gpu','checkpoint_digests':['round-'+str(plan['round'])],
                'loop_parameters':controls,'exit_code':0,'iteration_evidence_complete':True,
                'gradient_record_count':8,'nonfinite_gradient_count':7 if controls['amp'] else 0,'surface_miou':0.0}
        return {'status':'completed','actual':actual}
    def test_three_rounds_change_one_variable_and_release_once(self):
        calls=[]
        def pipeline(config,root):
            calls.append(config['loop_parameters'])
            self.assertEqual(config['max_attempts'],1); self.assertEqual(config['resource_owner'],'loop')
            return {'attempts':[{'status':'complete'}]}
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp); config=self.config(base)
            with patch.object(p,'windows',side_effect=str),patch.object(p,'pipeline',side_effect=pipeline), \
                 patch.object(p,'accepted_observation',side_effect=self.observed),patch.object(p.subprocess,'run',side_effect=self.cleanup) as stop:
                result=p.run(config,base/'output')
            self.assertEqual(len(calls),3); self.assertEqual(stop.call_count,1)
            self.assertEqual(p.changed(calls[0],calls[1]),['amp'])
            self.assertEqual(p.changed(calls[1],calls[2]),['learning_rate'])
            self.assertEqual(result['decision']['action'],'stop-budget')
            self.assertEqual(result['cleanup']['resource']['phase'],'stopped')
    def test_unexpected_round_failure_still_releases_resource(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp); config=self.config(base)
            with patch.object(p,'windows',side_effect=str),patch.object(p,'pipeline',side_effect=ValueError('fault')), \
                 patch.object(p.subprocess,'run',side_effect=self.cleanup) as stop:
                result=p.run(config,base/'output')
            self.assertEqual(result['status'],'blocked'); self.assertEqual(stop.call_count,1)
            self.assertEqual(result['cleanup']['resource']['phase'],'stopped')

    def test_model_bridge_selects_arbitrary_bounded_rate_and_can_stop_early(self):
        calls=[]
        def pipeline(config,root):
            calls.append(config['loop_parameters'])
            return {'attempts':[{'status':'complete'}]}
        def analysis(history,config,root,write_once):
            if len(history)==1:
                return {'action':'run-next-round','next_parameters':{'amp':True,'learning_rate':3e-5},
                    'changed_variable':'learning_rate','hypothesis':'Fixture model proposal from observations.'}
            return {'action':'stop-analysis','reason':'Fixture evidence does not justify another round.'}
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp); config=self.config(base)
            config.update(decision_mode='model-bridge',analysis_objective='Fixture diagnosis',
                allowed_variables={'amp':{'type':'boolean'},'learning_rate':{'type':'number','minimum':1e-7,'maximum':4e-5}})
            with patch.object(p,'windows',side_effect=str),patch.object(p,'pipeline',side_effect=pipeline), \
                 patch.object(p,'accepted_observation',side_effect=self.observed),patch.object(p,'model_analyze',side_effect=analysis), \
                 patch.object(p,'analyze',side_effect=AssertionError('legacy rules must not run')), \
                 patch.object(p.subprocess,'run',side_effect=self.cleanup):
                result=p.run(config,base/'output')
            self.assertEqual(calls,[{'amp':True,'learning_rate':4e-5},{'amp':True,'learning_rate':3e-5}])
            self.assertEqual(result['decision']['action'],'stop-analysis')

    def test_artifact_transfer_failure_does_not_block_analysis_or_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp); config=self.config(base)
            loc=json.loads(Path(config['locators_linux']).read_bytes())
            loc.update(dataset_namespace='fixture',devspace_name='fixture',devspace_uid='fixture')
            Path(config['locators_linux']).write_text(json.dumps(loc))
            config.update(cluster='fixture',ssh_identity='fixture',
                result_records={'repository':str(base/'reports')},project_metrics=['training.nonfinite_gradient_count'])
            def invocation(argv,**kwargs):
                if '--observation' in argv:
                    return SimpleNamespace(returncode=3,stdout='',stderr='fixture transfer failed')
                return self.cleanup(argv,**kwargs)
            with patch.object(p,'windows',side_effect=str),patch.object(p,'pipeline',return_value={'attempts':[{'status':'complete'}]}), \
                 patch.object(p,'accepted_observation',side_effect=self.observed),patch.object(p.subprocess,'run',side_effect=invocation):
                result=p.run(config,base/'output')
            self.assertEqual(len(result['rounds']),3)
            self.assertEqual(result['cleanup']['resource']['phase'],'stopped')
            records=sorted((base/'reports').glob('docs/sdm/runs/fixture-loop/round-*/result.json'))
            self.assertEqual(len(records),3)
            self.assertTrue(all(json.loads(x.read_bytes())['retention']['status']=='not-verified' for x in records))

if __name__=='__main__': unittest.main()
