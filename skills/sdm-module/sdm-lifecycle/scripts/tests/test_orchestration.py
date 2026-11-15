"""Behavioral checks for the five-phase graph; synthetic CPU records only."""
from copy import deepcopy
import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest

import orchestration as o
import task_pipeline as pipeline


class OrchestrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.now = dt.datetime.now(dt.timezone.utc)
        self.window = {'observed_at': (self.now-dt.timedelta(seconds=5)).isoformat(),
                       'valid_until': (self.now+dt.timedelta(minutes=20)).isoformat()}
        self.records = {}
        self.task = {'objective': 'execute-and-learn', 'orchestration_version': 2, 'graph_revision':3,
                     'orchestration': {'review_mode': 'local-agent', 'source_isolation': False,
                                       'records': self.records},
                     'drivers': {str(k): {'argv': ['CPU-fixture'], 'receipt': 'unused'} for k in pipeline.STEPS},
                     'cleanup_driver': {'argv': ['CPU-cleanup'], 'receipt': 'unused'}}
        self.background = self.put('background', goal='test fixture', scope='CPU fixture only',
                                   success_criteria='observe intended ordering', user_agreement_ref='fixture-user-message')
        checks = [{'id': name, 'status': 'unknown', 'rationale': 'Explicitly unmeasured fixture'} for name in
                  ('factor-and-controls', 'actual-progress', 'schedule-and-resume', 'evaluation-and-selection',
                   'uncertainty', 'total-cost')]
        self.research = self.put('research', question='route question', hypothesis='CPU route preserves boundaries',
                                stage='diagnostic', comparison={'factor': 'none'},
                                schedule={'unit': 'updates', 'horizon': 100, 'stop_at': 10, 'warmup': 5},
                                initialization={'kind': 'weights-only'},
                                evaluation={'scope': 'fixture', 'selection_rule': 'no candidate selection'},
                                decision_criteria={'stop': 'on error'}, method_checks=checks)
        request = {'provider': 'fixture-devspace', 'namespace': 'fixture-space', 'cluster': 'fixture-cluster',
                   'queue_id': 'fixture-queue', 'roles': [{'instances': 1, 'gpu_type': 'fixture-GPU',
                   'gpus_per_instance': 1, 'cpu_cores_per_instance': 2, 'memory_gib_per_instance': 4}]}
        self.inventory = self.put('inventory', **self.window, account_ref='fixture-account', source_ref='fixture-catalog',
                                  options=[{**{k: request[k] for k in ('provider','namespace','cluster','queue_id')},
                                            'source_ref': 'fixture-catalog', 'availability': 'unknown', 'request': request}])
        self.proposal = self.put('proposal', **self.window, inventory_digest=o.value_digest(self.inventory), request=request,
                                 purpose='CPU fixture only', quote_source='fixture-quote', currency='CNY', billing_basis='fixture',
                                 monitoring='fixture monitor', stop_path='fixture same-UID stop', output_path='fixture-output',
                                 max_cost=1, max_seconds=100, cleanup_seconds=10, queue_wait_seconds=20)
        self.method_review = self.put('method_review', research_digest=o.value_digest(self.research),
                                      method_checks=deepcopy(checks))
        self.plan_review = self.put('plan_review', **self.review(__import__('review_collaboration').plan_context(self.task)),
                                    background_digest=o.value_digest(self.background), proposal_digest=o.value_digest(self.proposal))
        self.confirmation = self.put('confirmation', proposal_digest=o.value_digest(self.proposal),
                                     confirmed=True, actor='human', channel='user-message', source_ref='synthetic-user-fixture',
                                     confirmed_at=(self.now-dt.timedelta(seconds=1)).isoformat(),
                                     expires_at=(self.now+dt.timedelta(minutes=20)).isoformat(), approval_route='direct-user')
        self.context = self.put('review_context', goal='fixture goal', user_agreement={'source_ref':'fixture-user-message'},
                                baseline='fixture baseline', accepted_plan='fixture plan', change_summary='none',
                                fixed_conditions={'source':'fixture'}, prior_findings=[], evidence_index=[], open_questions=[],
                                current_scope={'execution_allowed':False}, decision_question='what remains?', claim_scope='workflow',
                                background_digest=o.value_digest(self.background), research_digest=o.value_digest(self.research),
                                method_review_digest=o.value_digest(self.method_review), proposal_digest=o.value_digest(self.proposal))
        self.response = self.put('review_response', **self.review(self.context))
        self.consistency = self.put('evidence_consistency', review_response_digest=o.value_digest(self.response),
                                    review_context_digest=o.value_digest(self.context), status='passed',
                                    checks=[{'claim':'CPU fixture only','evidence_ref':'fixture-result',
                                             'finding':'No platform result is claimed','consistent':True}])
        self.quality = self.put('conclusion_quality', review_response_digest=o.value_digest(self.response),
                               consistency_digest=o.value_digest(self.consistency), status='passed',
                               supporting_evidence=['fixture-result'], counterevidence=[], limitations=['CPU only'],
                               alternatives_considered='A real platform run remains separate', blocking_gaps=[],
                               reason='This supports software-boundary checks only')
        self.next_action = self.put('next_action', quality_digest=o.value_digest(self.quality),
                                    conclusion='CPU fixture ends', recommended_action='stop',
                                    options=[{'action':'stop','reason':'fixture is complete'}],
                                    reason='fixture ends', evidence_ref='fixture-result', execution_allowed=False)
        self.put('transition', action='stop', execution_allowed=False, reason='fixture ends', evidence_ref='fixture-result',
                 next_action_digest=o.value_digest(self.next_action))

    def review(self, context):
        return dict(context_digest=o.value_digest(context), mode='local-agent', reviewer='fixture-agent',
                    response_ref='fixture-response', alignment_acknowledged=True, questions=[], blocking_gaps=[],
                    status='final', reason='fixture reasoning', alternatives='collect evidence', limitations='CPU only')

    def put(self, name, **fields):
        value={'schema':'sdm-orchestration-'+name.replace('_','-')+'/v1', **fields}
        path=self.root/(name+'.json')
        path.write_text(json.dumps(value), encoding='utf-8')
        self.records[name]={'path':str(path)}
        return value

    def update(self, name, **fields):
        value=o.needed(self.task,name)
        value.update(fields)
        self.put(name,**{k:v for k,v in value.items() if k!='schema'})

    def driver(self, seen):
        def run(driver, root, key, budget):
            seen.append(key)
            return {'status':'completed'}
        return run

    def test_graph_is_acyclic_and_maps_every_node_exactly_once(self):
        for goal in ('execute-and-learn','evaluate-only','baseline-reproduction'):
            for code in (False,True):
                for baseline in (False,True):
                    task={**self.task,'objective':goal,'code_change':code,'baseline_required':baseline}
                    nodes=pipeline.graph(task); resolved=set()
                    while len(resolved)<len(nodes):
                        ready={k for k,n in nodes.items() if set(n['depends'])<=resolved}-resolved
                        self.assertTrue(ready, f'cycle: {goal} {code} {baseline}')
                        resolved.update(ready)
                    self.assertEqual(len(nodes),34)
                    self.assertEqual(sum(n.get('owner_kind')=='phase' for n in nodes.values()),25)
                    self.assertEqual(sum(n.get('owner_kind')=='capability' for n in nodes.values()),9)

    def test_missing_confirmation_waits_before_any_compute_or_cleanup(self):
        Path(self.records['confirmation']['path']).unlink()
        seen=[]
        result=pipeline.coordinate(self.task,self.root/'attempt',self.driver(seen))
        self.assertEqual(result['status'],'awaiting-user-confirmation')
        self.assertFalse(set(seen)&o.COMPUTE_NODES)
        self.assertNotIn('cleanup',seen)

    def test_confirmation_arrival_resumes_without_repeating_preparation(self):
        Path(self.records['confirmation']['path']).unlink()
        seen=[]; root=self.root/'attempt'
        pipeline.coordinate(self.task,root,self.driver(seen)); before=list(seen)
        self.put('confirmation',**{k:v for k,v in self.confirmation.items() if k!='schema'})
        result=pipeline.coordinate(self.task,root,self.driver(seen))
        self.assertEqual(result['status'],'complete')
        for key in before:self.assertEqual(seen.count(key),1)
        self.assertIn('15',seen)
        self.assertEqual(seen.count('cleanup'),1)

    def test_local_agent_completes_without_any_pro_session_or_transport(self):
        seen=[]
        result=pipeline.coordinate(self.task,self.root/'attempt',self.driver(seen))
        self.assertEqual(result['status'],'complete')
        self.assertEqual(len(result['phases']),5)
        self.assertLess(seen.index('cleanup'),seen.index('21'))
        self.assertNotIn('session_ref',self.response)

    def test_review_questions_wait_after_release_and_do_not_advance_decision(self):
        self.update('review_response',status='questions',questions=[{'id':'q1','evidence':'actual updates'}])
        seen=[]; root=self.root/'attempt'
        result=pipeline.coordinate(self.task,root,self.driver(seen))
        self.assertEqual(result['status'],'awaiting-review')
        self.assertEqual(seen.count('cleanup'),1)
        self.assertNotIn('20',result['nodes'])
        self.update('review_response',status='final',questions=[])
        result=pipeline.coordinate(self.task,root,self.driver(seen))
        self.assertEqual(result['status'],'complete')
        self.assertEqual(seen.count('15'),1)
        self.assertEqual(seen.count('cleanup'),1)

    def test_release_failure_allows_diagnosis_but_blocks_archive_and_successor(self):
        seen=[]
        def run(d,r,k,b):
            seen.append(k)
            return {'status':'failed' if k=='cleanup' else 'completed'}
        result=pipeline.coordinate(self.task,self.root/'attempt',run)
        self.assertEqual(result['status'],'cleanup-required')
        self.assertEqual(result['nodes']['19']['status'],'completed')
        self.assertEqual(result['nodes']['20']['recommendation']['execution_allowed'],False)
        self.assertNotIn('21',seen)

    def test_changed_accepted_background_cannot_replay(self):
        Path(self.records['confirmation']['path']).unlink()
        root=self.root/'attempt'
        pipeline.coordinate(self.task,root,self.driver([]))
        self.update('background',scope='changed')
        with self.assertRaisesRegex(ValueError,'historical_receipt_changed'):
            pipeline.coordinate(self.task,root,self.driver([]))

    def test_feishu_or_tool_pending_approval_is_not_human_confirmation(self):
        self.update('confirmation',approval_route='feishu',channel='tool-response')
        with self.assertRaisesRegex(ValueError,'human_user_channel'):
            o.gate(self.task,'28',self.now)

    def test_switching_provider_requires_new_bound_proposal_and_confirmation(self):
        proposal=deepcopy(self.proposal);proposal['request']['provider']='fixture-component'
        with self.assertRaisesRegex(ValueError,'not_in_account_catalog'):
            o.validate_record('proposal',proposal,self.task,self.now)
        self.update('confirmation',proposal_digest='sha256:wrong')
        with self.assertRaisesRegex(ValueError,'proposal_mismatch'):
            o.gate(self.task,'28',self.now)

    def test_unavailable_provider_and_stale_inventory_cannot_be_selected(self):
        self.inventory['options'][0]['availability']='unavailable'
        self.put('inventory',**{k:v for k,v in self.inventory.items() if k!='schema'})
        self.update('proposal',inventory_digest=o.value_digest(self.inventory))
        with self.assertRaisesRegex(ValueError,'unavailable'):o.gate(self.task,'27',self.now)
        self.update('inventory',valid_until=(self.now-dt.timedelta(seconds=1)).isoformat())
        with self.assertRaisesRegex(ValueError,'stale'):o.gate(self.task,'26',self.now)

    def test_authorization_expiry_stops_dispatch_even_after_gate_was_accepted(self):
        o.gate(self.task,'28',self.now)
        self.update('confirmation',expires_at=(self.now-dt.timedelta(seconds=1)).isoformat())
        with self.assertRaisesRegex(ValueError,'expired'):o.dispatch_guard(self.task,True)

    def test_existing_allocation_does_not_require_new_catalogue_observation(self):
        self.update('inventory',valid_until=(self.now-dt.timedelta(seconds=1)).isoformat())
        o.dispatch_guard(self.task,True)
        with self.assertRaisesRegex(ValueError,'stale'):o.dispatch_guard(self.task,False)

    def test_method_checks_reject_unbound_support_and_inconsistent_schedule(self):
        value=deepcopy(self.research)
        value['method_checks'][0]['status']='supported'
        with self.assertRaisesRegex(ValueError,'method_evidence'):o.validate_record('research',value,self.task,self.now)
        value=deepcopy(self.research);value['schedule']['stop_at']=101
        with self.assertRaisesRegex(ValueError,'inconsistent_training_schedule'):
            o.validate_record('research',value,self.task,self.now)
        value=deepcopy(self.research);value['initialization']['kind']='resume'
        with self.assertRaisesRegex(ValueError,'resume_evidence'):o.validate_record('research',value,self.task,self.now)

    def test_pro_mode_requires_actual_transport_and_matching_context(self):
        self.task['orchestration']['review_mode']='pro'
        self.update('review_response',mode='pro')
        with self.assertRaisesRegex(ValueError,'pro_session_transport'):o.gate(self.task,'31',self.now)
        self.update('review_response',session_ref='fixture-session',transport_receipt_ref='fixture-delivery',context_digest='wrong')
        with self.assertRaisesRegex(ValueError,'context_digest'):o.gate(self.task,'31',self.now)

    def test_probe_cannot_be_promoted_as_candidate_confirmation(self):
        self.update('transition',action='confirm-candidate')
        with self.assertRaisesRegex(ValueError,'probe_cannot_confirm'):o.gate(self.task,'33',self.now)

    def test_context_prompt_contains_human_agreement_and_plan_not_only_results(self):
        rendered=o.alignment_prompt(self.context)
        self.assertIn('fixture-user-message',rendered)
        self.assertIn('fixture plan',rendered)
        bad=deepcopy(self.context);bad.pop('accepted_plan')
        with self.assertRaisesRegex(ValueError,'accepted_plan'):o.alignment_prompt(bad)

    def test_unversioned_tasks_also_use_the_current_34_node_graph(self):
        nodes=pipeline.graph({'objective':'execute-and-learn'})
        self.assertEqual(len(nodes),34)
        self.assertIn('28',nodes['7']['depends'])

    def test_template_cannot_dispatch_and_unknown_versions_are_rejected(self):
        task={**self.task,'preparation_only':True}
        with self.assertRaisesRegex(ValueError,'not_executable'):
            pipeline.coordinate(task,self.root/'template',self.driver([]))
        with self.assertRaisesRegex(ValueError,'historical_graph_is_read_only'):
            pipeline.graph({**self.task,'orchestration_version':3})

    def test_fresh_review_prompt_renders_complete_alignment_context(self):
        import model_review_bridge as bridge
        from review_guidance import PROMPT_VERSION
        packet={'objective':'fixture','alignment_context':self.context}
        prompt=bridge.render_prompt(packet,PROMPT_VERSION).decode()
        self.assertIn('fixture-user-message',prompt)
        self.assertIn('fixture baseline',prompt)
        self.assertIn('fixture plan',prompt)
        self.assertIn('local-agent',prompt)

    def test_v2_review_bundle_replay_keeps_exact_historical_prompt(self):
        from unittest.mock import patch
        import model_review_bridge as bridge
        packet={'schema':'sdm-loop-analysis-request/v1','objective':'fixture','history':[]}
        request=self.root/'request.json';request.write_text(json.dumps(packet))
        output=self.root/'old-review'
        with patch.object(bridge,'PROMPT_VERSION','sdm-research-review/v2'):
            first=bridge.prepare(request,[],output)
        raw={p.name:p.read_bytes() for p in output.iterdir()}
        self.assertEqual(bridge.prepare(request,[],output),first)
        self.assertEqual({p.name:p.read_bytes() for p in output.iterdir()},raw)

    def test_shared_devspace_records_process_limit_without_claiming_exclusive_cards(self):
        request=deepcopy(self.proposal['request'])
        request['roles'][0].update(allocation_mode='shared',gpus_per_instance=None,process_device_limit=1)
        self.inventory['options'][0]['request']=request
        self.put('inventory',**{k:v for k,v in self.inventory.items() if k!='schema'})
        self.update('proposal',request=request,inventory_digest=o.value_digest(self.inventory))
        self.assertEqual(o.gate(self.task,'27',self.now)['status'],'completed')
        request['roles'][0]['gpus_per_instance']=1
        self.inventory['options'][0]['request']=request
        self.put('inventory',**{k:v for k,v in self.inventory.items() if k!='schema'})
        self.update('proposal',request=request,inventory_digest=o.value_digest(self.inventory))
        with self.assertRaisesRegex(ValueError,'not_exclusive'):o.gate(self.task,'27',self.now)

    def test_required_worktree_driver_blocks_resource_acquisition_when_missing(self):
        self.task['orchestration']['source_isolation']=True
        seen=[]
        result=pipeline.coordinate(self.task,self.root/'attempt',self.driver(seen))
        self.assertEqual(result['status'],'needs-driver')
        self.assertIn('23',result['remaining'])
        self.assertFalse(set(seen)&o.COMPUTE_NODES)

    def test_configured_entrypoint_preserves_version_and_uses_builtin_gates(self):
        from unittest.mock import patch
        import run_project_pipeline as configured
        observed=[]
        config={'supported_graph_revisions':[3],'provider':'fixture','run_id':'fixture','driver_runtime':{'kind':'fixture'},
                'orchestration_version':2,'supported_orchestration_versions':[2],
                'orchestration':self.task['orchestration']}
        def coordinate(task,root):
            observed.append(task)
            return {'status':'complete','nodes':{},'cleanup':{'status':'completed'}}
        with patch.object(configured,'resolve_driver',return_value=self.root/'fixture.py'), \
             patch.object(configured,'driver_mode',return_value='native'), \
             patch.object(configured,'coordinate',side_effect=coordinate):
            configured.run(config,self.root/'configured')
        self.assertEqual(observed[0]['orchestration_version'],2)
        self.assertNotIn('28',observed[0]['drivers'])
        self.assertIn('7',observed[0]['drivers'])
        with self.assertRaisesRegex(ValueError,'historical_graph_is_read_only'):
            configured.run({**config,'orchestration_version':1},self.root/'unsupported')

    def test_old_observations_resume_on_current_graph_without_old_execution_or_retraining(self):
        old={'objective':'execute-and-learn','drivers':deepcopy(self.task['drivers']),
             'cleanup_driver':deepcopy(self.task['cleanup_driver'])}
        root=self.root/'old-attempt';root.mkdir()
        rows=[{'task_digest':pipeline.digest(old),'node':'1','result':{'status':'completed'}},
              {'task_digest':pipeline.digest(old),'node':'15','result':{'status':'failed','reason':'retained-failure'}},
              {'task_digest':pipeline.digest(old),'node':'20','result':{'status':'completed'}}]
        (root/'events.jsonl').write_text('\n'.join(json.dumps(row) for row in rows)+'\n')
        (root/'cleanup.json').write_text(json.dumps({'status':'completed','reason':'fixture-already-released'}))
        original={p.name:p.read_bytes() for p in root.iterdir()}
        seen=[]
        result=pipeline.coordinate(old,root,self.driver(seen))
        self.assertEqual(result['status'],'awaiting-evidence')
        self.assertEqual(result['coverage']['total_nodes'],34)
        self.assertEqual(result['nodes']['15']['reason'],'retained-failure')
        self.assertNotIn('20',result['nodes'])
        self.assertFalse(set(seen)&o.COMPUTE_NODES)
        self.assertEqual({name:(root/name).read_bytes() for name in original},original)
        manifest=json.loads((root/'current-graph-r3/migration.json').read_bytes())
        self.assertEqual(manifest['current_graph_nodes'],34)
        self.assertFalse(manifest['old_graph_executed'])
        first=list(seen)
        pipeline.coordinate(old,root,self.driver(seen))
        self.assertEqual(seen,first)

    def test_old_pending_effect_is_imported_as_uncertain_never_dispatched_again(self):
        old={'objective':'execute-and-learn'}
        root=self.root/'uncertain-old';root.mkdir()
        (root/'events.jsonl').write_text(json.dumps({'task_digest':pipeline.digest(old),'node':'1',
                                                     'result':{'status':'completed'}})+'\n')
        (root/'14-started.json').write_text('{}')
        result=pipeline.coordinate(old,root,self.driver([]))
        self.assertTrue(result['nodes']['14']['uncertain_effect'])
        self.assertEqual(result['nodes']['14']['reason'],'historical_effect_requires_reconciliation')

    def test_uncertain_prior_effect_blocks_all_new_resource_drivers(self):
        root=self.root/'uncertain-current';root.mkdir()
        current=pipeline.current_task(self.task,root)
        (root/'events.jsonl').write_text(json.dumps({'task_digest':pipeline.digest(current),'node':'14',
            'result':{'status':'failed','uncertain_effect':True,'reason':'uncertain prior submission'}})+'\n')
        seen=[]
        result=pipeline.coordinate(self.task,root,self.driver(seen))
        self.assertFalse(set(seen)&o.COMPUTE_NODES)
        self.assertTrue(result['nodes']['14']['uncertain_effect'])
        self.assertEqual(result['nodes']['7']['status'],'skipped')


if __name__=='__main__':unittest.main()
