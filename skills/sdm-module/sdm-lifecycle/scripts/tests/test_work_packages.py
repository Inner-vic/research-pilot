"""CPU acceptance of consolidated workflow execution; platform effects simulated."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

import task_pipeline as pipeline
import work_packages as packages
from project_driver import file_digest


class WorkPackageTests(unittest.TestCase):
    def setUp(self):
        from test_orchestration import OrchestrationTests
        self.f = OrchestrationTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)

    def material(self, package, records=None):
        refs = ['cpu-original-input:' + package]
        value = {'schema': 'sdm-work-package-material/v1', 'work_package': package,
                 'evidence_refs': refs,
                 'checks': {k: {'finding': 'CPU synthetic material inspected', 'evidence_refs': refs}
                            for k in packages.MATERIAL_CHECKS[package]}, 'records': records or {}}
        path = self.f.root / (package + '-material.json')
        path.write_text(json.dumps(value), encoding='utf-8')
        ref = {'path': str(path), 'digest': file_digest(path)}
        self.f.task.setdefault('work_package_materials', {})[package] = ref
        for name in records or {}:
            Path(self.f.records[name]['path']).unlink()
        for key in packages.MATERIAL_CHECKS[package]:
            self.f.task['drivers'].pop(key, None)
        return path

    def test_consolidated_task_material_replaces_two_driver_calls_without_replacing_agreement_gate(self):
        self.material('task-contract', {'background': self.f.background})
        seen = []
        result = pipeline.coordinate(self.f.task, self.f.root/'merged', self.f.driver(seen))
        self.assertEqual(result['status'], 'complete')
        self.assertFalse({'1', '2'} & set(seen))
        self.assertEqual(result['nodes']['22']['record_kind'], 'background')
        self.assertEqual(result['nodes']['1']['receipt'], result['nodes']['2']['receipt'])
        self.assertEqual(len(result['work_packages']), 11)
        first = list(seen)
        pipeline.coordinate(self.f.task, self.f.root/'merged', self.f.driver(seen))
        self.assertEqual(seen, first)

    def test_one_plan_review_and_decision_material_preserve_all_typed_checks(self):
        f = self.f
        self.material('task-contract', {'background': f.background})
        self.material('experiment-plan', {name: getattr(f, attr) for name, attr in
                                        [('research','research'), ('method_review','method_review'),
                                         ('plan_review','plan_review')]})
        self.material('result-review', {name: __import__('orchestration').needed(f.task, name) for name in
                                      packages.MATERIAL_RECORDS['result-review']})
        self.material('action-choice', {name: __import__('orchestration').needed(f.task, name) for name in
                                      packages.MATERIAL_RECORDS['action-choice']})
        self.material('evaluation')
        seen = []
        result = pipeline.coordinate(f.task, f.root/'all-merged', f.driver(seen))
        self.assertEqual(result['status'], 'complete')
        self.assertFalse({'1','2','6','18'} & set(seen))
        self.assertTrue(all(result['nodes'][k]['status']=='completed' for k in
                            ('22','24','25','34','29','31','19','32','20','33')))
        self.assertEqual(result['nodes']['19']['production']['source_digest'],
                         result['nodes']['32']['production']['source_digest'])
        self.assertFalse(result['nodes']['33']['decision']['execution_allowed'])

    def test_merged_background_cannot_omit_real_agreement_field(self):
        value = deepcopy(self.f.background)
        del value['user_agreement_ref']
        self.material('task-contract', {'background': value})
        seen=[]
        result=pipeline.coordinate(self.f.task,self.f.root/'bad-background',self.f.driver(seen))
        self.assertEqual(result['nodes']['22']['status'],'failed')
        self.assertNotIn('14',seen)

    def test_material_cannot_automatically_create_human_resource_confirmation(self):
        self.material('task-contract', {'confirmation': self.f.confirmation})
        seen=[]
        with self.assertRaisesRegex(ValueError,'material_contract_invalid'):
            pipeline.coordinate(self.f.task,self.f.root/'fake-consent',self.f.driver(seen))
        self.assertEqual(seen,[])

    def test_material_cannot_replace_actual_training_or_code_checks(self):
        self.f.task['work_package_materials']={'experiment-run': {'path':'/invalid','digest':'sha256:'+'0'*64}}
        with self.assertRaisesRegex(ValueError,'known_material_work_package'):
            pipeline.coordinate(self.f.task,self.f.root/'fake-training',self.f.driver([]))

    def test_changed_accepted_material_is_rejected_without_replaying_calls(self):
        path=self.material('task-contract', {'background': self.f.background})
        seen=[]
        pipeline.coordinate(self.f.task,self.f.root/'changed',self.f.driver(seen))
        before=list(seen)
        path.write_text('{}',encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'pinned_json_changed'):
            pipeline.coordinate(self.f.task,self.f.root/'changed',self.f.driver(seen))
        self.assertEqual(seen,before)

    def test_required_code_review_failure_prevents_acquisition(self):
        f=self.f
        f.task.update(code_change=True, code_review_required=True)
        seen=[]
        def run(d,r,k,b):
            seen.append(k)
            return {'status':'failed' if k=='11' else 'completed'}
        result=pipeline.coordinate(f.task,f.root/'code-review-failed',run)
        self.assertEqual(result['nodes']['11']['status'],'failed')
        self.assertNotIn('14',seen)
        self.assertNotIn('cleanup',seen)

    def test_missing_required_code_review_driver_prevents_submission(self):
        f=self.f
        f.task.update(code_change=True, code_review_required=True)
        del f.task['drivers']['11']
        seen=[]
        result=pipeline.coordinate(f.task,f.root/'missing-review',f.driver(seen))
        self.assertEqual(result['status'],'needs-driver')
        self.assertIn('11',result['remaining'])
        self.assertNotIn('14',seen)

    def test_review_policy_is_explicit_and_does_not_force_review_on_reused_code(self):
        with self.assertRaisesRegex(ValueError,'code_review_requires_code_change'):
            pipeline.graph({'objective':'execute-and-learn','code_review_required':True})
        for field in ('code_review_required','code_change'):
            with self.assertRaisesRegex(ValueError,'boolean_task_option_required'):
                pipeline.graph({'objective':'execute-and-learn',field:'true'})

    def test_cleanup_pending_leaves_review_available_but_archive_pending(self):
        f=self.f;seen=[]
        def run(d,r,k,b):
            seen.append(k)
            return {'status':'failed' if k=='cleanup' else 'completed'}
        result=pipeline.coordinate(f.task,f.root/'cleanup-pending',run)
        self.assertEqual(result['status'],'cleanup-required')
        self.assertEqual(result['nodes']['32']['status'],'completed')
        self.assertEqual(seen.count('cleanup'),1)
        self.assertNotIn('21',seen)
        self.assertEqual(result['work_packages'][-1]['status'],'pending')
        before=list(seen)
        pipeline.coordinate(f.task,f.root/'cleanup-pending',run)
        self.assertEqual(seen,before)

    def test_public_project_entry_selects_material_once_and_enforces_required_review(self):
        import run_project_pipeline as entry
        f=self.f
        self.material('task-contract', {'background': f.background})
        config={'provider':'fixture','run_id':'merged-fixture','supported_graph_revisions':[3],
                'driver_runtime':{'kind':'fixture'},'orchestration':f.task['orchestration'],
                'work_package_materials':f.task['work_package_materials'],
                'code_change':True,'code_review_required':True}
        tasks=[]
        def coordinate(task,root):
            tasks.append(task)
            return {'status':'complete','nodes':{},'cleanup':{'status':'completed'}}
        with patch.object(entry,'resolve_driver',return_value=f.root/'fixture.py'), \
             patch.object(entry,'driver_mode',return_value='native'), \
             patch.object(entry,'coordinate',side_effect=coordinate):
            entry.run(config,f.root/'entry')
        self.assertFalse({'1','2'} & tasks[0]['drivers'].keys())
        self.assertIn('11',tasks[0]['drivers'])
        self.assertTrue(tasks[0]['code_review_required'])

    def test_public_task_cli_uses_merged_material_and_preserves_failure_diagnosis(self):
        f=self.f
        self.material('task-contract', {'background':f.background})
        root=f.root/'public';root.mkdir()
        driver=f.root/'driver.py'
        driver.write_text('import json,sys\nfrom pathlib import Path\n'
                          'key=sys.argv[1]\n'
                          'result={"task_node":key,"status":"failed" if key=="15" else "completed"}\n'
                          'Path(sys.argv[2]).write_text(json.dumps(result))\n',encoding='utf-8')
        for key in f.task['drivers']:
            receipt=root/(key+'.json')
            f.task['drivers'][key]={'argv':[sys.executable,'-B',str(driver),key,str(receipt)],'receipt':str(receipt)}
        receipt=root/'release.json'
        f.task['cleanup_driver']={'argv':[sys.executable,'-B',str(driver),'cleanup',str(receipt)],'receipt':str(receipt)}
        task=f.root/'task.json';task.write_text(json.dumps(f.task))
        command=[sys.executable,'-B',pipeline.__file__,'--task',str(task),'--output',str(root)]
        result=subprocess.run(command,capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stderr)
        report=json.loads(result.stdout)
        self.assertEqual(report['nodes']['15']['status'],'failed')
        self.assertEqual(report['nodes']['19']['status'],'completed')
        self.assertEqual(report['cleanup']['status'],'completed')
        self.assertEqual(report['work_packages'][0]['display_id'],'1.1')
        self.assertFalse((root/'1-started.json').exists())
        second=subprocess.run(command,capture_output=True,text=True,timeout=30)
        self.assertEqual(second.returncode,0,second.stderr)
        self.assertEqual(json.loads(second.stdout)['work_packages'],report['work_packages'])

    def test_migration_cannot_take_over_an_unclosed_legacy_allocation(self):
        f=self.f
        original=pipeline.current_task(f.task,f.root/'legacy')
        del original['workflow_revision']
        root=f.root/'legacy';root.mkdir()
        rows=[{'task_digest':pipeline.digest(original),'node':'14','result':{'status':'completed'}}]
        journal=root/'events.jsonl'
        journal.write_text('\n'.join(json.dumps(row) for row in rows)+'\n')
        before=journal.read_bytes();seen=[]
        with self.assertRaisesRegex(ValueError,'historical_allocation_not_closed'):
            pipeline.coordinate(original,root,f.driver(seen))
        self.assertEqual(journal.read_bytes(),before)
        self.assertEqual(seen,[])
        self.assertFalse((root/'current-workflow-r1').exists())


if __name__=='__main__':unittest.main()
