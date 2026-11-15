"""CPU integration for SDM adapter declarations and shared record production."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

import execution_contracts as contracts
from project_driver import file_digest
import task_pipeline as pipeline


class ExecutionContractTests(unittest.TestCase):
    def setUp(self):
        from test_orchestration import OrchestrationTests
        self.f=OrchestrationTests();self.f.setUp();self.addCleanup(self.f.doCleanups)

    def manifest(self,nodes):
        f=self.f
        driver=f.root/'project-driver.py';driver.write_text('# CPU declaration fixture')
        path=f.root/'capabilities.json'
        value={'schema':'sdm-driver-capabilities/v1','provider':'fixture','graph_revision':3,
            'driver_digest':file_digest(driver),'nodes':{k:{'input_contract':'project/v1','output_contract':'receipt/v1'} for k in nodes}}
        path.write_text(json.dumps(value))
        return {'provider':'fixture','driver_capabilities':{'path':str(path),'digest':file_digest(path)}},driver

    def bundle(self,records):
        path=self.f.root/'bundle.json'
        path.write_text(json.dumps({'schema':'sdm-evidence-bundle/v1','records':records}))
        return {'kind':'bundle-projection','source':{'path':str(path),'digest':file_digest(path)}}

    def test_missing_declaration_is_explicitly_unverified(self):
        cfg,driver=self.manifest({'1','cleanup'});cfg.pop('driver_capabilities')
        self.assertEqual(contracts.capabilities(cfg,{'1'},driver)['status'],'unverified')

    def test_incomplete_declared_adapter_is_incompatible(self):
        cfg,driver=self.manifest({'1','cleanup'})
        report=contracts.capabilities(cfg,{'1','16'},driver)
        self.assertEqual(report['status'],'incompatible');self.assertEqual(report['missing_nodes'],['16'])

    def test_valid_declaration_is_not_labelled_behavioral_verification(self):
        cfg,driver=self.manifest({'1','cleanup'})
        report=contracts.capabilities(cfg,{'1'},driver)
        self.assertEqual(report['status'],'declared')
        self.assertEqual(report['qualification'],'declaration-not-behavioral-proof')

    def test_external_cleanup_does_not_require_project_cleanup_implementation(self):
        cfg,driver=self.manifest({'1'})
        self.assertEqual(contracts.capabilities(cfg,{'1'},driver,cleanup_required=False)['status'],'declared')

    def test_changed_driver_and_manifest_are_rejected(self):
        cfg,driver=self.manifest({'1','cleanup'})
        driver.write_text('# changed implementation')
        with self.assertRaisesRegex(ValueError,'binding_mismatch'):contracts.capabilities(cfg,{'1'},driver)
        Path(cfg['driver_capabilities']['path']).write_text('{}')
        with self.assertRaisesRegex(ValueError,'pinned_json_changed'):contracts.capabilities(cfg,{'1'},driver)

    def test_unverified_adapter_cannot_acquire_through_coordinator(self):
        f=self.f;f.task['project_capability_check']={'status':'unverified'};seen=[]
        result=pipeline.coordinate(f.task,f.root/'unverified',f.driver(seen))
        self.assertIn('project-capabilities:unverified',result['nodes']['14']['missing_drivers'])
        self.assertFalse(set(seen)&__import__('orchestration').COMPUTE_NODES)

    def test_shared_bundle_produces_missing_records_and_resume_does_not_repeat(self):
        f=self.f;spec=self.bundle({'background':f.background,'research':f.research})
        f.task['orchestration']['record_producers']={name:deepcopy(spec) for name in ('background','research')}
        for name in ('background','research'):Path(f.records[name]['path']).unlink()
        seen=[];root=f.root/'produced'
        first=pipeline.coordinate(f.task,root,f.driver(seen))
        self.assertEqual(first['nodes']['22']['production']['kind'],'bundle-projection')
        self.assertEqual(first['nodes']['24']['production']['source_digest'],spec['source']['digest'])
        calls=list(seen);second=pipeline.coordinate(f.task,root,f.driver(seen))
        self.assertEqual(first['status'],second['status']);self.assertEqual(calls,seen)

    def test_automatic_confirmation_is_rejected_before_any_driver(self):
        f=self.f
        f.task['orchestration']['record_producers']={'confirmation':self.bundle({'confirmation':f.confirmation})}
        seen=[]
        with self.assertRaisesRegex(ValueError,'confirmation_cannot'):pipeline.coordinate(f.task,f.root/'consent',f.driver(seen))
        self.assertEqual(seen,[])

    def test_invalid_projected_record_does_not_pass_gate(self):
        f=self.f;bad=deepcopy(f.background);bad.pop('user_agreement_ref')
        f.task['orchestration']['record_producers']={'background':self.bundle({'background':bad})}
        Path(f.records['background']['path']).unlink()
        result=pipeline.coordinate(f.task,f.root/'invalid',f.driver([]))
        self.assertEqual(result['nodes']['22']['status'],'failed')

    def test_existing_record_is_not_overwritten_by_producer(self):
        f=self.f;other=deepcopy(f.background);other['goal']='other'
        f.task['orchestration']['record_producers']={'background':self.bundle({'background':other})}
        raw=Path(f.records['background']['path']).read_bytes()
        result=pipeline.coordinate(f.task,f.root/'existing',f.driver([]))
        self.assertNotIn('production',result['nodes']['22'])
        self.assertEqual(Path(f.records['background']['path']).read_bytes(),raw)

    def test_gate_uses_already_loaded_record_without_loading_it_again(self):
        import orchestration as o
        f=self.f;loaded=o.load(f.task,'background')
        with patch.object(o,'load',side_effect=AssertionError('duplicate read')):
            self.assertEqual(o.gate(f.task,'22',loaded=loaded)['status'],'completed')

    def test_unused_producer_source_is_not_loaded_when_record_exists(self):
        f=self.f;spec=self.bundle({'background':f.background})
        f.task['orchestration']['record_producers']={'background':spec}
        Path(spec['source']['path']).unlink()
        result=pipeline.coordinate(f.task,f.root/'lazy',f.driver([]))
        self.assertEqual(result['status'],'complete')

    def test_public_cli_generates_record_from_pinned_bundle(self):
        f=self.f;spec=self.bundle({'background':f.background})
        f.task['orchestration']['record_producers']={'background':spec}
        Path(f.records['background']['path']).unlink()
        root=f.root/'cli';root.mkdir()
        driver=f.root/'cpu.py';driver.write_text('import sys,json\nfrom pathlib import Path\n'
            'key=sys.argv[1];Path(sys.argv[2]).write_text(json.dumps({"task_node":key,"status":"failed" if key=="15" else "completed"}))\n')
        for key in f.task['drivers']:
            receipt=root/(key+'.json')
            f.task['drivers'][key]={'argv':[sys.executable,'-B',str(driver),key,str(receipt)],'receipt':str(receipt)}
        receipt=root/'cleanup-receipt.json'
        f.task['cleanup_driver']={'argv':[sys.executable,'-B',str(driver),'cleanup',str(receipt)],'receipt':str(receipt)}
        task=f.root/'cli-task.json';task.write_text(json.dumps(f.task))
        cmd=[sys.executable,'-B',pipeline.__file__,'--task',str(task),'--output',str(root)]
        result=subprocess.run(cmd,capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stderr)
        report=json.loads(result.stdout)
        self.assertEqual(report['nodes']['22']['production']['source_digest'],spec['source']['digest'])
        self.assertEqual(report['nodes']['15']['status'],'failed')


if __name__=='__main__':unittest.main()
