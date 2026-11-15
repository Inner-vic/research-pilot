"""SCM/CI fault-boundary checks; local fixtures are not platform acceptance."""
import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scm_ci import Adapter, ApiError, digest, monitor, plan
from external_operations import Operation, sha
from review_storage import atomic_json

REV = 'a' * 40

def config(provider='gitlab'):
    return {'schema':'sdm-scm-ci/v1','provider':provider,'base_url':'https://ci.example.test' if provider=='jenkins' else 'https://git.example.test',
        'namespace':'team/research','owner':'researcher','project':'team/research','project_id':7,'job':'research/checks',
        'request_key':'study-round-1','configuration_digest':'sha256:'+'b'*64,'parameters':{},'allowed_writes':['pipeline' if provider=='gitlab' else 'build'],
        'resource':{'kind':'pipeline' if provider=='gitlab' else 'build','revision':REV,'ref':'experiment'}}

class FakeAPI:
    def __init__(self,c):
        self.c=c;self.calls=[];self.posts=0;self.pipeline=None;self.fail_after_post=False;self.queue_expired=False;self.wrong_revision=False
    def request(self,method,endpoint,body=None,headers=None,binary=False):
        self.calls.append((method,endpoint));path=endpoint.split('?')[0]
        if method=='POST':
            self.posts+=1;self.pipeline={'id':12,'sha':REV,'status':'running','web_url':'https://git.example.test/pipelines/12'}
            if self.fail_after_post:raise ApiError('transport')
            return self.pipeline
        if path=='user':return {'username':self.c['owner']}
        if path=='projects/team%2Fresearch':return {'id':7,'path_with_namespace':self.c['namespace']}
        if path.endswith('/repository/branches/experiment'):return {'name':'experiment','commit':{'id':REV}}
        if path.endswith('/pipelines'):return [self.pipeline] if self.pipeline else []
        if path.endswith('/variables'):return [{'key':k,'value':v} for k,v in Adapter(self.c,self).params().items()]
        if path.endswith('/pipelines/12'):return self.pipeline
        if path=='whoAmI/api/json':return {'anonymous':False,'name':self.c['owner']}
        if path=='job/research/job/checks':return {}
        if path=='job/research/job/checks/api/json':
            if 'allBuilds' in endpoint:return {'allBuilds':[self.build()]}
            return {'url':'https://ci.example.test/job/research/job/checks/','buildable':True,'property':[{'parameterDefinitions':[{'name':k} for k in Adapter(self.c,self).params()]}]}
        if path=='queue/api/json':return {'items':[]}
        if path=='queue/item/9/api/json':
            if self.queue_expired:raise ApiError('http',404)
            return {'id':9,'task':{'url':'https://ci.example.test/job/research/job/checks/'},'executable':{'number':3}}
        if path=='job/research/job/checks/3/api/json':return self.build()
        if path.endswith('/progressiveText'):return b'complete\n',{'X-Text-Size':'47','X-More-Data':'false'}
        if '/artifact/' in path:return b'actual-result',{}
        raise AssertionError(endpoint)
    def build(self):
        return {'number':3,'queueId':9,'result':'SUCCESS','building':False,'actions':[
            {'parameters':[{'name':k,'value':v} for k,v in Adapter(self.c,self).params().items()]},
            {'lastBuiltRevision':{'SHA1':'c'*40 if self.wrong_revision else REV}}]}

class SCMCI(unittest.TestCase):
    def test_unknown_submit_recovers_original_pipeline_without_second_post(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t).resolve();c=config();api=FakeAPI(c);api.fail_after_post=True;a=Adapter(c,api)
            cp=root/'config.json';atomic_json(cp,c);auth=root/'auth.json';sp=root/'spec.json';state=root/'operation.json'
            authorization={k:c[k] for k in ('provider','namespace','owner','request_key','configuration_digest')}
            authorization.update(action='submit',component_uid=c['project'])
            authorization.update(direct_user_confirmation=True,source_ref='unit-test://authorized-local-fixture',old_uid=None)
            atomic_json(auth,authorization)
            plan(cp,sp,state,auth,root/'registry');spec=json.loads(sp.read_bytes())
            class Op(Operation):
                def command(self,name,uid=None):
                    if name=='probe':return {'available':True,'operations':['submit','lookup','read-new'],'version':'fixture','source_ref':'fixture://only'}
                    if name=='lookup':return a.lookup()
                    if name=='submit':return a.submit(sp,state)
                    return a.read(uid)
            with self.assertRaisesRegex(ApiError,'transport'):Op(spec,state,60).run()
            self.assertEqual(api.posts,1)
            restored=Op(spec,state,60).run()
            self.assertEqual(restored['uid'],'12');self.assertEqual(api.posts,1)
            with self.assertRaisesRegex(ValueError,'durable_original_submit_intent_required|original_guard_intent_required'):
                a.submit(sp,state)
            self.assertEqual(api.posts,1)

    def test_post_attempt_marker_blocks_blind_replay_even_if_guard_is_still_unknown(self):
        from submission_guard import Guard
        with tempfile.TemporaryDirectory() as t:
            root=Path(t).resolve();c=config();api=FakeAPI(c);api.fail_after_post=True;a=Adapter(c,api)
            spec={'provider':c['provider'],'namespace':c['namespace'],'owner':c['owner'],'request_key':c['request_key'],
                'component_uid':c['project'],'configuration_digest':c['configuration_digest'],'submission_registry':str(root/'registry')}
            state=root/'operation.json';g=Guard(spec,state);g.load();g.before_submit(state)
            with self.assertRaises(ApiError):a.post_once(spec,state,'projects/team%2Fresearch/pipeline',{})
            with self.assertRaisesRegex(ValueError,'adapter_write_already_attempted'):a.post_once(spec,state,'projects/team%2Fresearch/pipeline',{})
            self.assertEqual(api.posts,1)

    def test_expired_queue_resolves_original_build_not_last_build(self):
        c=config('jenkins');api=FakeAPI(c);api.queue_expired=True;a=Adapter(c,api)
        v=a.read('queue:9')
        self.assertEqual((v['uid'],v['build_number']),('queue:9',3));self.assertTrue(v['source_revision_verified'])
        self.assertFalse(any('lastBuild' in p for _,p in api.calls))
        self.assertEqual(a.log('queue:9',40)['next_offset'],47)

    def test_success_with_wrong_git_revision_cannot_collect_artifact_or_complete_node(self):
        c=config('jenkins');api=FakeAPI(c);api.wrong_revision=True;a=Adapter(c,api)
        v=a.read('queue:9');self.assertTrue(v['successful']);self.assertFalse(v['source_revision_verified'])
        with self.assertRaisesRegex(ValueError,'successful_pinned_build_required'):
            a.artifact('queue:9','result.json','unused',hashlib.sha256(b'actual-result').hexdigest())
        self.assertFalse(any('/artifact/' in p for _,p in api.calls))

    def test_truncated_scan_never_reports_no_effect(self):
        c=config();c['max_pages']=1;a=Adapter(c,FakeAPI(c))
        with patch.object(a,'get',return_value=[{}]*100):
            with self.assertRaisesRegex(ValueError,'lookup_incomplete'):a.pages('projects/7/pipelines')

    def test_artifact_hash_and_exclusive_destination(self):
        c=config('jenkins');a=Adapter(c,FakeAPI(c));h=hashlib.sha256(b'actual-result').hexdigest()
        with tempfile.TemporaryDirectory() as t:
            out=Path(t)/'result.json'
            with self.assertRaisesRegex(ValueError,'artifact_sha256_mismatch'):a.artifact('queue:9','result.json',out,'0'*64)
            self.assertFalse(out.exists());self.assertEqual(a.artifact('queue:9','result.json',out,h)['sha256'],h)
            with self.assertRaises(FileExistsError):a.artifact('queue:9','result.json',out,h)
            with self.assertRaisesRegex(ValueError,'invalid_remote_path'):a.artifact('queue:9','../escape',Path(t)/'escape',h)

    def test_monitor_authentication_failure_does_not_loop_or_submit(self):
        c=config('jenkins');a=Adapter(c,FakeAPI(c))
        with tempfile.TemporaryDirectory() as t,patch.object(a,'read',side_effect=ApiError('authentication',401)) as read:
            with self.assertRaises(ApiError):monitor(a,'queue:9',Path(t)/'monitor.json',60,1)
            self.assertEqual(read.call_count,1)
            self.assertEqual(json.loads((Path(t)/'monitor.json').read_bytes())['error'],'authentication:401')

    def test_old_state_or_changed_scope_is_rejected_before_any_request(self):
        c=config('jenkins');api=FakeAPI(c);a=Adapter(c,api)
        with tempfile.TemporaryDirectory() as t:
            state=Path(t)/'monitor.json';atomic_json(state,{'config_digest':'changed','uid':'queue:9'})
            with self.assertRaisesRegex(ValueError,'ci_monitor_identity_changed'):monitor(a,'queue:9',state,30,1)
            self.assertEqual(api.calls,[])

if __name__=='__main__':unittest.main()
