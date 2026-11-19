"""Resident service CPU mocks: never call platform or mutate training."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(SCRIPTS))
import task_event_collector as c
import task_event_service as s

TASK={'uid':'00000000-0000-4000-8000-000000000002','namespace':'cpu-test'}

def pod(name='task-master-0',status='Running'):
    return {'task_uid':TASK['uid'],'namespace':TASK['namespace'],'step':'task','name':name,'status':status,'exit_code':None}


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name).resolve()
        self.config={'task_event_collector':{'version':1,'store':str(self.root/'events.db'),'tasks':[TASK]},
                     'task_event_service':{'version':1,'read_only':True,'evidence_root':str(self.root/'evidence'),
                                           'bindings':[{**TASK,'cluster':'test-cluster'}]}}
    def receipt(self, ids):
        from datetime import datetime, timezone
        contract=s.notifications(self.config)['receipt_contract']
        path=self.root/'delivery.json'
        path.write_text(json.dumps({**contract,'ids':ids,'utc_time':datetime.now(timezone.utc).isoformat(),
                                    'text':'CPU test delivery only','delivered':True}))
        return str(path)

    def tearDown(self):self.temp.cleanup()
    def observe(self,status='pending',pods=None):
        db=c.connect(c.validate(self.config),'live-platform-observation')
        try:c.observe(db,TASK,{**TASK,'status':status},pods or [])
        finally:db.close()
    def reader(self,*args):
        if args[2]=='summary-events':return []
        return {'result':[{'values':[['100','Epoch 2 Iter 12/100 Loss: 0.5'],
                                    ['101','NORMAL_UPDATE_HEALTH {"optimizer_updates": 3}']]}]}
    def outbox(self):return s.notifications(self.config)['notifications']

    def test_pending_and_helper_do_not_claim_training_or_notify(self):
        self.observe(pods=[pod('task-helper'),pod(status='Pending')])
        with patch.object(s,'read_cli',side_effect=AssertionError('network')):
            s.consume(self.config,self.reader)
        self.assertEqual(self.outbox(),[])

    def test_run_and_actual_training_emit_distinct_dedup_notifications(self):
        self.observe('running',[pod()]);s.consume(self.config,self.reader)
        self.assertEqual({x['kind'] for x in self.outbox()},{'execution_started','training_progress_observed'})
        s.consume(self.config,self.reader);self.assertEqual(len(self.outbox()),2)
        self.assertEqual(c.inbox(self.config,consumer='resident-diagnostics')['events'],[])

    def test_platform_running_with_helper_only_does_not_claim_gpu_start(self):
        self.observe('running',[pod('task-helper'),pod(status='Pending')])
        s.consume(self.config,self.reader)
        self.assertEqual([n['kind'] for n in self.outbox()],['platform_running'])

    def test_failed_task_diagnostic_is_read_only_and_never_auto_retry(self):
        self.observe('failed',[pod(status='Failed')]);calls=[]
        def read(*args):
            calls.append(args[2])
            return {'result':[{'values':[['1','RuntimeError: CUDA out of memory']]}]} if args[2]=='logs' else []
        s.consume(self.config,read)
        notes=self.outbox();self.assertIn('terminal_failed',{n['kind'] for n in notes})
        self.assertEqual(set(calls),{'logs','summary-events'})
        report=json.loads(Path(notes[0]['report']).read_text())
        self.assertEqual(report['analysis']['training_mutations'],0)
        self.assertFalse(report['analysis']['root_cause_confirmed'])

    def test_single_amp_nonfinite_text_does_not_trigger_failure(self):
        snapshot=c.normalize(TASK,{**TASK,'status':'running'},[pod()])
        result=s.analyze(snapshot,{'rank':[{'text':'grad_norm: nan Loss: 0.4'}]})
        self.assertEqual(result['signals'],['nonfinite_text_requires_update_context'])
        self.assertFalse(result['root_cause_confirmed'])

    def test_notification_delivery_requires_receipt_and_is_idempotent(self):
        self.observe('failed');s.consume(self.config,self.reader);eid=self.outbox()[0]['id']
        with self.assertRaises(ValueError):s.notifications(self.config,{'ids':[eid]})
        self.assertEqual(len(self.outbox()),1)
        receipt=self.receipt([eid])
        for _ in range(2):s.notifications(self.config,{'ids':[eid],'delivery_receipt':receipt})
        self.assertEqual(self.outbox(),[])

    def test_bad_ack_rolls_back(self):
        self.observe('failed');s.consume(self.config,self.reader);eid=self.outbox()[0]['id']
        with self.assertRaises(ValueError):s.notifications(self.config,{'ids':[eid,'missing'],'delivery_receipt':'test'})
        self.assertEqual(len(self.outbox()),1)

    def test_receipt_content_project_time_and_integrity_are_verified(self):
        self.observe('failed');s.consume(self.config,self.reader);eid=self.outbox()[0]['id']
        receipt=Path(self.receipt([eid]));original=receipt.read_bytes()
        for change in ({'project_binding':'other-project'},{'ids':[]},{'text':''},
                       {'utc_time':'2020-01-01T00:00:00Z'},{'evidence_kind':'cpu-simulation'},
                       {'delivered':False},{'store':str(self.root/'other.db')}):
            value=json.loads(original);value.update(change);receipt.write_text(json.dumps(value))
            with self.assertRaises(ValueError):s.notifications(self.config,{'ids':[eid],'delivery_receipt':str(receipt)})
            self.assertEqual(len(self.outbox()),1)
        receipt.write_bytes(original)
        ack={'ids':[eid],'delivery_receipt':str(receipt)}
        s.notifications(self.config,ack);s.notifications(self.config,ack)
        value=json.loads(original);value['text']='changed after delivery';receipt.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError,'receipt_changed'):s.notifications(self.config,ack)

    def test_unknown_id_valid_receipt_does_not_partially_ack(self):
        self.observe('failed');s.consume(self.config,self.reader);eid=self.outbox()[0]['id']
        receipt=self.receipt([eid,'missing'])
        with self.assertRaisesRegex(ValueError,'unknown_notification'):
            s.notifications(self.config,{'ids':[eid,'missing'],'delivery_receipt':receipt})
        self.assertEqual(len(self.outbox()),1)

    def test_consume_failure_does_not_ack_event(self):
        self.observe('failed')
        with patch.object(s,'save_report',side_effect=OSError('disk_full')):
            with self.assertRaises(OSError):s.consume(self.config,self.reader)
        self.assertEqual(len(c.inbox(self.config,consumer='resident-diagnostics')['events']),1)
        s.consume(self.config,self.reader);self.assertEqual(len(self.outbox()),1)

    def test_log_unavailable_still_notifies_task_failure(self):
        self.observe('failed',[pod(status='Failed')])
        def broken(*_):raise ValueError('offline')
        s.consume(self.config,broken)
        report=json.loads(Path(self.outbox()[0]['report']).read_text())
        self.assertTrue(report['collection_errors']);self.assertFalse(report['analysis']['root_cause_confirmed'])

    def test_simulated_store_cannot_drive_live_consumer(self):
        db=c.connect(c.validate(self.config),'cpu-simulation');db.close()
        with self.assertRaisesRegex(ValueError,'live_service_store'):s.consume(self.config,self.reader)

    def test_production_read_gate_and_stop_marker(self):
        with self.assertRaisesRegex(ValueError,'explicit_allow_live'):s.run(self.config,once=True)
        Path(self.config['task_event_collector']['store']+'.stop').touch()
        with patch.object(c,'poll',side_effect=AssertionError('query')):
            self.assertEqual(s.run(self.config,allow_live=True)['status'],'service_stopped')

    def test_public_notification_entry_never_dispatches(self):
        self.observe('failed');s.consume(self.config,self.reader)
        config=self.root/'project.json';config.write_text(json.dumps(self.config))
        p=subprocess.run([sys.executable,'-B',str(SCRIPTS/'run_project_pipeline.py'),'--project',str(config),'--task-notifications'],capture_output=True,text=True)
        self.assertEqual(p.returncode,0,p.stderr)
        self.assertEqual(json.loads(p.stdout)['training_mutations'],0)

    def test_log_sort_and_credential_redaction(self):
        result=s.log_lines({'result':[{'values':[['20','api_key=lpak_example'],['10','Authorization: Bearer secret']]}]})
        self.assertEqual([x['timestamp'] for x in result],['10','20'])
        self.assertNotIn('secret',str(result));self.assertNotIn('lpak_example',str(result))

    def test_diagnostic_cli_does_not_create_windows_console(self):
        binary=self.root/'lpai-agent.exe';binary.touch()
        cfg={**self.config['task_event_collector'],'cli_path':str(binary)}
        result=subprocess.CompletedProcess([],0,json.dumps({'status':'ok','data':{}}),'')
        for operation,cluster,name in [('summary-events',None,None),('logs','cpu-cluster','task-master-0')]:
            with patch.object(s.subprocess,'run',return_value=result) as run:
                s.read_cli(cfg,TASK,operation,cluster,name)
                self.assertEqual(run.call_args.kwargs['creationflags'],getattr(subprocess,'CREATE_NO_WINDOW',0))
                self.assertTrue(run.call_args.kwargs['capture_output'])

    def test_invalid_binding_rejected(self):
        self.config['task_event_service']['bindings'][0]['uid']='different'
        with self.assertRaisesRegex(ValueError,'binding'):s.settings(self.config)


if __name__=='__main__':unittest.main()
