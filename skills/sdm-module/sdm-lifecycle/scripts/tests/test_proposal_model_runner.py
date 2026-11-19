"""Real gate/ModelRunner contracts with synthetic model replies, no ML/platform."""
import copy
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import proposal_review as review
import proposal_model_runner as adapter
from model_runner import ModelRunner
from model_runner_transport import save
from research_model_receipts import seal
from review_storage import atomic_json,read_json
from task_pipeline import digest


class ProposalRunnerTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name)
        cli=self.root/'codex.exe';cli.write_bytes(b'control only - never executed')
        self.cfg={'proposal_review':{'version':4,'backend':'model','context_required':True,
            'models':{'a':'fixture-a','b':'fixture-b'},'model_runner':{'version':1,'backend':'cli',
                'cli_path':str(cli),'cli_sha256':hashlib.sha256(cli.read_bytes()).hexdigest(),
                'codex_home':str(self.root),'cwd':str(self.root),'timeout_seconds':30}}}
        self.source=self.root/'source.json';atomic_json(self.source,{'task':'control comparison','valid_training_runs':0})
        self.context()
        self.calls=[];self.verdicts=['approve'];self.bad_a=False;self.bad_once=False;self.unknown=False
        self.same_proposal=False;self.pause_in_model=False
        def launch(runner,folder):return self.backend(folder)
        self.patch=patch.object(ModelRunner,'_launch',new=launch);self.patch.start();self.addCleanup(self.patch.stop)

    def context(self):
        atomic_json(self.root/'review-context.json',{'schema':'sdm-review-context/v1','subject_digest':digest(review.subject(self.cfg)),
            **{key:'Registered control-only '+key for key in ('goal','constraints','data','code_revision','configuration','history','unknowns')},
            'sources':[{'id':'fixture','kind':'config','path':str(self.source),
                'sha256':hashlib.sha256(self.source.read_bytes()).hexdigest()}]})

    def backend(self,folder):
        envelope=read_json(folder/'request.json');value=envelope['request']
        from proposal_input import restore
        logical=Path(str(folder)[4:]) if str(folder).startswith('\\\\?\\') else folder
        payload=restore(value['evidence'],logical.parent.parent)
        role='a' if value['purpose']=='proposal' else 'b';revision=payload['revision']
        self.calls.append((role,revision,payload['attempt'],value['model']))
        if self.unknown:raise OSError('Injected transport outcome unknown')
        if role=='a':
            raw={key:'Control reasoning for '+key for key in review.FIELDS}
            raw['hypothesis']='Falsifiable control comparison '+str(1 if self.same_proposal else revision)
            issues=payload['previous'][-1]['response']['challenges'] if payload['previous'] else []
            raw['addresses']=[{'challenge_id':row['id'],'reason':'Addresses original question'} for row in issues]
            raw['advice_dispositions']=[{'challenge_id':row['id'],'decision':'modify','applicability':'Applies to fixture',
                'reason':'Uses a specific comparable check'} for row in issues]
            if self.bad_a or self.bad_once:
                raw['hypothesis']='';self.bad_once=False
        else:
            verdict=self.verdicts[min(revision-1,len(self.verdicts)-1)]
            prior=[row for accepted in payload['previous'] for row in accepted['response']['challenges']]
            raw={'verdict':verdict,'rationale':'Synthetic evidence-grounded critique',
                'challenges':[{'id':'issue-'+str(revision),'severity':'major','question':'Test a distinct assumption',
                    'basis':'Registered fixture','resolution_criterion':'Explicit changed counterfactual'}] if verdict=='revise' else [],
                'resolutions':[{'challenge_id':row['id'],'status':'resolved','reason':'Rechecked original criterion'} for row in prior],
                'evidence_assessment':{'source_ids':['fixture'],'judgment':'hypothesis','limitations':['Control fixture, no actual model']}}
        if self.pause_in_model:
            from research_worker import control
            control(self.root,True)
        save(folder,envelope['binding'],'completed',raw,{'backend':'control-fixture'})

    def gate(self):return review.gate(self.cfg,self.root)

    def test_both_purposes_reuse_original_gate_and_distinct_actual_call_slots(self):
        result=self.gate();self.assertEqual(result['status'],'proposal-approved');self.assertFalse(result['execution_authority'])
        self.assertEqual([(row[0],row[3]) for row in self.calls],[('a','fixture-a'),('b','fixture-b')])
        values=[read_json(path)['request'] for path in self.root.glob('proposal-review/*/model-calls/*/request.json')]
        self.assertEqual(values[0]['evidence']['shared'],values[1]['evidence']['shared'])
        self.assertEqual({value['purpose'] for value in values},{'proposal','method-review'})
        self.assertEqual(read_json(self.root/'proposal.json')['proposer']['model']['id'],'fixture-a')
        self.assertEqual(self.gate()['receipt'],result['receipt']);self.assertEqual(len(self.calls),2)

    def test_condition_loop_can_revise_without_preset_review_count(self):
        self.verdicts=['revise']*13+['approve'];result=self.gate()
        self.assertEqual(result['status'],'proposal-approved');self.assertEqual(result['review_rounds'],14)
        self.assertEqual(len(self.calls),28)

    def test_explicit_dialogue_v2_reaches_both_actual_request_envelopes_and_replays(self):
        from proposal_dialogue import prompt
        self.cfg['proposal_review']['dialogue_version']=2;self.context()
        self.verdicts=['revise','approve'];result=self.gate()
        self.assertEqual(result['status'],'proposal-approved')
        values=[read_json(p)['request'] for p in sorted(self.root.glob('proposal-review/*/model-calls/*/request.json'))]
        self.assertEqual(len(values),4)
        for value in values:
            role='a' if value['purpose']=='proposal' else 'b'
            self.assertTrue(value['prompt'].startswith(prompt(role,2)))
            # Only the reversible evidence projection may append adapter guidance.
            suffix=value['prompt'][len(prompt(role,2)):]
            self.assertEqual(bool(suffix),any(value['evidence'].get(k) for k in ('previous_projection','context_projection','packet_evidence_projection','proposer_previous_projection','literal_projection','json_text_projection')))
            self.assertEqual(value['evidence']['shared'],values[0]['evidence']['shared'])
            sources=value['evidence']['shared']['raw_sources']
            self.assertTrue(any(r.get('text')==self.source.read_text() for r in sources))
        self.assertEqual(self.gate()['receipt'],result['receipt']);self.assertEqual(len(self.calls),4)

    def test_unsupported_dialogue_version_rejected_before_model_calls(self):
        for version in (0,5,True,'2'):
            with self.subTest(version=version):
                self.cfg['proposal_review']['dialogue_version']=version
                with self.assertRaisesRegex(ValueError,'supported_proposal_dialogue'):self.gate()
        self.assertEqual(self.calls,[])

    def test_reject_stalled_or_needs_evidence_exit_without_extra_calls(self):
        for verdict,status in [('reject','proposal-rejected'),('stalled','proposal-review-stalled'),('needs-evidence','awaiting-proposal-evidence')]:
            with self.subTest(verdict=verdict):
                case=ProposalRunnerTests();case.setUp()
                try:
                    case.verdicts=[verdict];self.assertEqual(case.gate()['status'],status)
                    self.assertEqual(case.gate()['status'],status);self.assertEqual(len(case.calls),2)
                finally:case.doCleanups()

    def test_known_invalid_reply_is_corrected_with_feedback_not_repeated(self):
        self.bad_once=True;self.assertEqual(self.gate()['status'],'proposal-approved')
        self.assertEqual([row[:3] for row in self.calls],[('a',1,1),('a',1,2),('b',1,1)])
        self.gate();self.assertEqual(len(self.calls),3)

    def test_repeated_invalid_reply_stalls_without_unbounded_calls(self):
        self.bad_a=True;self.assertEqual(self.gate()['status'],'proposal-review-stalled')
        self.assertEqual(len(self.calls),2);self.gate();self.assertEqual(len(self.calls),2)

    def test_unknown_model_call_holds_original_identity(self):
        self.unknown=True;self.assertEqual(self.gate()['status'],'proposal-model-blocked')
        self.assertEqual(self.gate()['status'],'proposal-model-blocked');self.assertEqual(len(self.calls),1)
        self.assertFalse((self.root/'proposal.json').exists())

    def test_completed_call_before_canonical_publication_recovers_without_reinference(self):
        original=adapter.atomic_json;fired=[]
        def fault(path,value):
            if Path(path).name=='canonical.json' and not fired:
                fired.append(True);raise OSError('Injected after actual backend completion')
            return original(path,value)
        with patch.object(adapter,'atomic_json',side_effect=fault):self.assertEqual(self.gate()['status'],'proposal-model-blocked')
        self.assertEqual(self.gate()['status'],'proposal-approved');self.assertEqual([row[0] for row in self.calls],['a','b'])

    def test_pause_during_call_does_not_publish_proposal(self):
        self.pause_in_model=True;result=self.gate();self.assertEqual(result['status'],'proposal-model-blocked')
        self.assertFalse((self.root/'proposal.json').exists());self.assertEqual(self.gate()['status'],'proposal-review-paused')
        self.assertEqual(len(self.calls),1)

    def test_modified_raw_completion_blocked_before_returning_approval(self):
        self.gate();path=next(self.root.glob('proposal-review/*/model-calls/*/backend-completed.json'))
        value=read_json(path);value['metadata']['changed']=True;atomic_json(path,seal(value))
        with self.assertRaisesRegex(ValueError,'completion_changed'):self.gate()
        self.assertEqual(len(self.calls),2)

    def test_unowned_proposal_or_review_cannot_bypass_model_receipts(self):
        atomic_json(self.root/'proposal.json',{'forged':'proposal'})
        with self.assertRaisesRegex(ValueError,'unowned_proposal'):self.gate()
        self.assertEqual(self.calls,[])

    def test_unowned_review_file_cannot_return_approval(self):
        self.verdicts=['needs-evidence'];result=self.gate()
        path=next(self.root.glob('proposal-review/*/round-*/review-response.json'))
        value=read_json(path);value['verdict']='approve';atomic_json(path,value)
        with self.assertRaisesRegex(ValueError,'review_view_changed'):self.gate()
        self.assertEqual(len(self.calls),2)

    def test_development_space_gate_rejects_before_records_or_allocation(self):
        import devspace_main_workflow as main
        self.verdicts=['reject']
        with patch.object(main,'records',side_effect=AssertionError('Execution prepared before proposal verdict')):
            self.assertEqual(main.execution(self.cfg,self.root)['status'],'proposal-rejected')
        self.assertFalse((self.root/'execution.json').exists())

    def test_component_public_gate_rejects_before_driver_dispatch(self):
        import run_project_pipeline as entry
        self.cfg.update(provider='synthetic',supported_graph_revisions=[3]);self.context();self.verdicts=['reject']
        with patch.object(entry,'resolve_driver',side_effect=AssertionError('Component driver dispatched before proposal verdict')):
            self.assertEqual(entry.run(self.cfg,self.root)['status'],'proposal-rejected')

    def test_missing_proposal_publish_marker_recovers_without_new_calls(self):
        self.gate();path=next(self.root.glob('proposal-review/*/model-calls/*-a-*/published.json'));path.unlink()
        self.assertEqual(self.gate()['status'],'proposal-approved');self.assertTrue(path.exists());self.assertEqual(len(self.calls),2)

    def test_registered_supplement_resumes_same_dialogue_without_repeating_A(self):
        self.verdicts=['needs-evidence','approve'];self.gate();pending=self.gate()
        packet=read_json(pending['request_path'])
        query={'request_digest':digest(packet),'queries':[{'source_id':'fixture','start_line':1,'line_count':5,
            'question':'Inspect the registered original configuration'}]}
        path=self.root/'query.json';atomic_json(path,query)
        self.assertEqual(review.gate(self.cfg,self.root,evidence_query=path)['status'],'review-evidence-supplied')
        self.assertEqual(self.gate()['status'],'proposal-approved')
        self.assertEqual([row[0] for row in self.calls],['a','b','b'])

    def test_completed_critic_before_canonical_publication_recovers(self):
        original=adapter.atomic_json;fired=[]
        def fault(path,value):
            if Path(path).name=='canonical.json' and '-b-' in str(path) and not fired:
                fired.append(True);raise OSError('Injected after critic completion')
            return original(path,value)
        with patch.object(adapter,'atomic_json',side_effect=fault):self.assertEqual(self.gate()['status'],'proposal-model-blocked')
        self.assertEqual(self.gate()['status'],'proposal-approved');self.assertEqual([row[0] for row in self.calls],['a','b'])

    def test_manual_critic_view_is_rejected_before_gate_or_critic_call(self):
        original=self.backend
        def injection(folder):
            original(folder)
            if '-a-' in folder.name:atomic_json(folder.parent.parent/'round-01/review-response.json',{'unowned':True})
        self.backend=injection
        with self.assertRaisesRegex(ValueError,'unowned_proposal_review'):self.gate()
        self.assertEqual([row[0] for row in self.calls],['a'])

    def test_registered_source_change_blocks_approved_replay(self):
        self.gate();self.source.write_text('{}',encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'review_source_changed'):self.gate()
        self.assertEqual(len(self.calls),2)

    def test_pro_selection_never_invokes_local_adapter(self):
        self.cfg['proposal_review']={'version':4,'backend':'pro','context_required':True}
        self.cfg['external_review']={'project':'fixture','workflow':'review','session_root':str(self.root/'pro')}
        with patch.object(adapter,'drive',side_effect=AssertionError('Selected Pro silently replaced')):
            self.assertEqual(self.gate()['status'],'awaiting-proposal')
        self.assertEqual(self.calls,[])

    def test_missing_context_and_same_model_fail_before_calls(self):
        (self.root/'review-context.json').unlink();self.assertEqual(self.gate()['status'],'awaiting-review-context')
        self.cfg['proposal_review']['models']['b']='fixture-a'
        with self.assertRaisesRegex(ValueError,'distinct_proposal_models_required'):self.gate()
        self.assertEqual(self.calls,[])

    def auto_evidence(self,verdicts):
        self.cfg['proposal_review']['evidence_policy']={'version':1,'mode':'registered-supplements'}
        self.context();self.verdicts=verdicts
        original_save=save
        def output(folder,bound,status,raw,metadata):
            if 'verdict' in raw:
                raw=copy.deepcopy(raw);raw['evidence_queries']=[]
                if raw['verdict']=='needs-evidence':
                    request=read_json(folder/'request.json')['request'];revision=request['evidence']['revision']
                    raw['challenges']=[{'id':'visibility-'+str(revision),'severity':'major','question':'Show the registered source',
                        'basis':'Exact registered control source','resolution_criterion':'Read and reassess that source'}]
                    raw['evidence_queries']=[{'source_id':'fixture','start_line':1,'line_count':200,
                        'question':'Resolve exact source visibility, version '+str(revision)}]
                    if getattr(self,'shorten_evidence_request',False) and revision>1:
                        raw['evidence_queries'][0]['line_count']=1
                    raw['evidence_assessment']['judgment']='insufficient'
            original_save(folder,bound,status,raw,metadata)
        return patch.object(sys.modules[__name__],'save',side_effect=output)

    def test_registered_evidence_read_revises_proposal_before_second_review(self):
        with self.auto_evidence(['needs-evidence','approve']):
            result=self.gate()
        self.assertEqual(result['status'],'proposal-approved')
        self.assertEqual([c[:2] for c in self.calls],[('a',1),('b',1),('a',2),('b',2)])
        call=next(self.root.glob('proposal-review/*/model-calls/0002-a-*/request.json'))
        request=read_json(call)['request']
        supplied=request['evidence']['previous'][0]['registered_supplements'][0]['value']['results'][0]
        self.assertEqual(supplied['text'],self.source.read_text());self.assertFalse(supplied['truncated'])
        raw=request['evidence']['shared']['raw_sources']
        own=next(row for row in raw if row['path']==str(self.source))
        self.assertEqual((own['source_id'],own['visibility'],own['omitted_bytes']),('fixture','complete-text',0))
        self.assertEqual(len(self.calls),4)
        with self.auto_evidence(['needs-evidence','approve']):
            self.assertEqual(self.gate()['receipt'],result['receipt'])
        self.assertEqual(len(self.calls),4)

    def test_rephrased_same_evidence_request_stalls_without_more_model_calls(self):
        with self.auto_evidence(['needs-evidence']):
            self.assertEqual(self.gate()['status'],'proposal-review-stalled')
            self.assertEqual(self.gate()['status'],'proposal-review-stalled')
        self.assertEqual(len(self.calls),4)

    def test_smaller_range_of_already_supplied_bytes_is_not_new_evidence(self):
        self.shorten_evidence_request=True
        with self.auto_evidence(['needs-evidence']):
            self.assertEqual(self.gate()['status'],'proposal-review-stalled')
        self.assertEqual(len(self.calls),4)

    def test_evidence_queries_reject_arbitrary_files_empty_ranges_and_extra_fields(self):
        from proposal_evidence import validate,enabled
        payload={'shared':{'packet':{'context':{'sources':[{'id':'fixture','path':str(self.source)}]}}}}
        query={'source_id':'fixture','start_line':1,'line_count':1,'question':'Inspect'}
        for change in ({'source_id':'outside'},{'start_line':0},{'start_line':999},{'line_count':201},{'path':'arbitrary'}):
            with self.subTest(change=change),self.assertRaises(ValueError):
                validate({'verdict':'needs-evidence','evidence_queries':[{**query,**change}]},payload)
        with self.assertRaisesRegex(ValueError,'explicit_registered'):
            enabled({'version':4,'backend':'model','evidence_policy':{'version':True,'mode':'registered-supplements'}})


if __name__=='__main__':unittest.main()
