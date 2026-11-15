"""Official CLI route must preserve the original Guard; no SDK fallback."""
import json
import hashlib
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch
from test_lpai_manager import FakeBackend, data
from lpai_manager import Manager
from lpai_manager_backend import OfficialBackend


class CLIRouteTests(unittest.TestCase):
    def pending_fixture(self, unknown=False):
        import test_lpai_manager as fixtures
        case=fixtures.ManagerTests();case.setUp()
        skill=case.root/'official-lpai';skill.mkdir()
        (skill/'SKILL.md').write_text('---\nname: lpai\n---\nOfficial fixture only',encoding='utf-8')
        (skill/'manifest.json').write_text(json.dumps({'name':'lpai','version':'fixture','build_sha':'fixture'}),encoding='utf-8')
        def ref(name):
            p=skill/name;return {'path':str(p),'sha256':'sha256:'+hashlib.sha256(p.read_bytes()).hexdigest()}
        case.config.update(component_transport='official-cli',platform_requirements={'version':1,'platform':'lpai',
            'required_skills':[{'name':'lpai','skill':ref('SKILL.md'),'manifest':ref('manifest.json')}]})
        p,a=case.files()
        if unknown:
            case.backend.failure=TimeoutError('lost response after remote creation')
            with self.assertRaisesRegex(ValueError,'write_outcome_unverified_reconcile_only'):
                case.manager.apply(p,a)
        else:
            case.backend.pending=True
            self.assertEqual(case.manager.apply(p,a)['status'],'pending_approval')
        request=json.loads(p.read_bytes())['request']
        case.backend.task={'uid':'task-1','name':request['name'],'namespace':'ns','owner':'owner@example.com',
                           'project_uid':request['uid'],'cluster':'cluster','data':request['data'],'status':'pending'}
        return case,p,a

    def test_pending_reconciles_real_observed_task_without_a_second_submit(self):
        case,p,a=self.pending_fixture()
        case.backend.task_candidates=lambda name,parent:[case.backend.task]
        self.assertEqual(case.manager.reconcile(p)['status'],'verified')
        self.assertEqual(case.manager.apply(p,a)['status'],'already-completed')
        self.assertEqual(case.backend.writes,1)

    def test_pending_without_task_stays_pending_and_never_retries(self):
        case,p,a=self.pending_fixture();case.backend.task_candidates=lambda name,parent:[]
        self.assertEqual(case.manager.reconcile(p)['status'],'pending-approval-unresolved')
        with self.assertRaisesRegex(ValueError,'reconcile_only'):case.manager.apply(p,a)
        self.assertEqual(case.backend.writes,1)

    def test_lost_submit_ack_discovers_and_verifies_original_task_without_resubmission(self):
        case,p,a=self.pending_fixture(unknown=True)
        case.backend.task_candidates=lambda name,parent:[case.backend.task]
        self.assertEqual(case.manager.reconcile(p)['status'],'verified')
        self.assertEqual(case.manager.apply(p,a)['status'],'already-completed')
        self.assertEqual(case.backend.writes,1)

    def test_unknown_absent_task_remains_unknown_and_apply_stays_blocked(self):
        case,p,a=self.pending_fixture(unknown=True)
        case.backend.task_candidates=lambda name,parent:[]
        for unused in range(2):
            result=case.manager.reconcile(p)
            self.assertEqual(result,{'status':'submission-outcome-unresolved','phase':'intent',
                                    'new_platform_writes':0,'resubmit_allowed':False})
        with self.assertRaisesRegex(ValueError,'reconcile_only'):case.manager.apply(p,a)
        self.assertEqual(case.backend.writes,1)

    def test_discovery_uid_is_durable_before_readback_and_cannot_switch(self):
        for unknown in (False,True):
            with self.subTest(unknown=unknown):
                case,p,a=self.pending_fixture(unknown=unknown)
                case.backend.task_candidates=lambda name,parent:[case.backend.task]
                with patch.object(case.backend,'read',side_effect=ValueError('read_unavailable')):
                    with self.assertRaisesRegex(ValueError,'read_unavailable'):case.manager.reconcile(p)
                guard=case.manager.guard(json.loads(p.read_bytes()))
                with guard.lock():
                    guard.load();self.assertEqual(guard.state['candidate_uid'],'task-1')
                with patch.object(case.backend,'task_candidates',side_effect=AssertionError('bound UID must not be rediscovered')):
                    self.assertEqual(case.manager.reconcile(p)['status'],'verified')
                self.assertEqual(case.backend.writes,1)

    def test_unknown_discovery_refuses_ambiguity_and_wrong_identity_or_payload(self):
        for field,value in (('ambiguous',None),('owner','other@example.com'),
                            ('project_uid','other-component'),('name','other-trial'),('data',data())):
            with self.subTest(field=field):
                case,p,a=self.pending_fixture(unknown=True)
                if field=='data':value['lpai_envs'][0]['value']='changed'
                if field!='ambiguous':case.backend.task[field]=value
                case.backend.task_candidates=lambda name,parent:[case.backend.task]*(2 if field=='ambiguous' else 1)
                with self.assertRaises(ValueError):case.manager.reconcile(p)
                with self.assertRaisesRegex(ValueError,'reconcile_only'):case.manager.apply(p,a)
                self.assertEqual(case.backend.writes,1)

    def test_pending_ambiguous_or_changed_payload_cannot_be_accepted(self):
        case,p,a=self.pending_fixture()
        case.backend.task_candidates=lambda name,parent:[case.backend.task,case.backend.task]
        with self.assertRaisesRegex(ValueError,'ambiguous'):case.manager.reconcile(p)
        case.backend.task_candidates=lambda name,parent:[case.backend.task]
        case.backend.task['data']['lpai_envs'][0]['key']='Changed'
        with self.assertRaisesRegex(ValueError,'configuration_mismatch'):case.manager.reconcile(p)
        self.assertEqual(case.backend.writes,1)

    def test_cli_policy_required_before_platform_read(self):
        config={'schema':'lpai-manager/v1','namespace':'ns','owner':'owner@example.com',
                'state_root':'state','submission_registry':'registry','component_transport':'official-cli'}
        backend=FakeBackend()
        with patch.object(backend,'read',side_effect=AssertionError('no platform call')):
            with self.assertRaisesRegex(ValueError,'official_lpai_skill_required'):
                Manager(config,backend).plan({'action':'task.submit'})

    def test_read_and_five_explicit_start_flags_without_sdk(self):
        config={'namespace':'ns','owner':'owner@example.com','component_transport':'official-cli','official_cli':'lpai-agent'}
        backend=OfficialBackend(config)
        with patch.object(backend,'sdk_call',side_effect=AssertionError('SDK forbidden')), patch.object(backend,'cli',return_value={'uid':'task'}) as cli:
            self.assertEqual(backend.read('task',uid='task'),{'uid':'task'})
            self.assertEqual(cli.call_args.args[0],['tasks','get','--namespace','ns','--uid','task'])
            import tempfile
            with tempfile.TemporaryDirectory() as root:
                request={'action':'task.submit','uid':'component','name':'trial','cluster':'cluster',
                         'data':data(),'component_settings':{'framework_type':'custom'},
                         'task_info':{'problem_statement':'p','solution_approach':'s','expected_result':'r',
                                      'expected_duration':12,'expected_duration_desc':'estimated'}}
                backend.mutate(request,Path(root)/'plan.json')
                args=cli.call_args.args[0]
                for k,v in request['task_info'].items():
                    self.assertEqual(args[args.index('--'+k.replace('_','-'))+1],str(v))
                saved=json.loads(Path(args[args.index('--body-file')+1]).read_bytes())
                self.assertEqual(saved['data'],request['data'])
                self.assertEqual(saved['project_uid'],'component')

    def test_component_read_uses_bound_name_and_never_an_unsupported_uid_flag(self):
        backend=OfficialBackend({'component_transport':'official-cli','namespace':'ns','component_names':{'uid':'bound'}})
        with patch.object(backend,'cli',return_value={'uid':'uid'}) as call:
            backend.read('component',uid='uid',name='trial-name')
            self.assertEqual(call.call_args.args[0],['projects','get','--namespace','ns','--name','bound'])

    def test_catalog_rejects_partial_and_other_owner_results(self):
        backend=OfficialBackend({'namespace':'ns','owner':'owner@example.com','component_transport':'official-cli'})
        for value in ({'total':1,'items':None},{'total':2,'items':[]},
                      {'total':1,'items':[{'name':'new','namespace':'ns','owner':'other'}]}):
            with patch.object(backend,'cli',return_value=value),self.assertRaises(ValueError):backend.create_candidates('new')
        with patch.object(backend,'cli',return_value={'total':0,'items':None}):self.assertEqual(backend.create_candidates('new'),[])

    def test_explicit_cli_never_falls_back_to_sdk_create(self):
        backend=OfficialBackend({'namespace':'ns','component_transport':'official-cli'})
        with patch.object(backend,'sdk_call',side_effect=AssertionError('SDK forbidden')):
            with self.assertRaisesRegex(ValueError,'forbids_sdk'):
                backend.mutate({'action':'component.create','transport':'official-sdk'},Path('unused'))

    def test_cli_timeout_is_single_attempt(self):
        backend=OfficialBackend({'namespace':'ns','component_transport':'official-cli','official_cli':'lpai-agent',
                                 'component_names':{'component':'base'}})
        with patch('lpai_manager_backend.subprocess.run',side_effect=subprocess.TimeoutExpired('lpai-agent',60)) as call:
            with self.assertRaises(subprocess.TimeoutExpired):backend.read('component',uid='component')
        self.assertEqual(call.call_count,1)


if __name__=='__main__':unittest.main()
