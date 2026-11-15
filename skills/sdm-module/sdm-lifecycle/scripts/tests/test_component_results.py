"""Synthetic control evidence only; no ML training/evaluation or platform calls."""
import base64
import copy
import gzip
import hashlib
import json
import unittest
from component_results import verify, marker_lines
from external_operations import sha


def fixture():
    split={'protocol':'fixture-v1','source_sha256':'b'*64,'train_ids':[10,11],
           'validation_ids':[0,1,2,3],'test_used':False}
    predictions={'row_ids':[0,1,2,3],'y_true':[0,0,1,1],'y_pred':[0,0,1,0],
                 'probability_positive':[.1,.2,.8,.4]}
    val={'macro_f1':(4/5+2/3)/2,'accuracy':.75,'confusion_matrix':[[2,0],[1,1]],'loss':.4}
    config={'epochs':2,'seed':42}
    encode=lambda v:json.dumps(v,sort_keys=True).encode()
    split_raw=encode(split);split_sha=hashlib.sha256(split_raw).hexdigest()
    result={'schema':'sdm-component-result/v1','code_sha256':'a'*64,'source_sha256':'b'*64,
            'protocol':'fixture-v1','test_used':False,'config':config,'split_sha256':split_sha,
            'selected_epoch':2,'validation':val,'history':[
                {'epoch':1,'validation':{**val,'macro_f1':.6,'loss':.6}},
                {'epoch':2,'validation':val}]}
    raw={'split.json':split_raw,'result.json':encode(result),
         'predictions.json':encode(predictions),'checkpoint.pt':b'synthetic opaque fixture bytes, not weights'}
    rows=[{'kind':'preflight','code_sha256':'a'*64,'source_sha256':'b'*64,'config':config,'split_sha256':split_sha},
          {'kind':'completed','result_sha256':hashlib.sha256(raw['result.json']).hexdigest(),'validation':val}]
    for name,value in raw.items():
        rows.extend([{'kind':'artifact','name':name,'encoding':'base64+gzip','bytes':len(value),
                      'sha256':hashlib.sha256(value).hexdigest(),'chunks':1},
                     {'kind':'artifact-chunk','name':name,'index':0,
                      'value':base64.b64encode(gzip.compress(value)).decode()}])
    lines=['SDM_JSON '+json.dumps(v) for v in rows]
    task={'uid':'task','namespace':'ns','owner':'owner','cluster':'cluster','project_uid':'component',
          'status':'succeeded','data':{'frozen':'fixture'}}
    receipt={'status':'verified','resource':{k:task[k] for k in ('uid','namespace','owner','cluster')}}
    receipt['resource']['data_digest']=sha(task['data'])
    expected={**receipt['resource'],'component_uid':'component','code_sha256':'a'*64,
              'source_sha256':'b'*64,'protocol':'fixture-v1','config':config}
    return task,receipt,lines,expected


class ComponentResultTests(unittest.TestCase):
    def test_observed_official_log_shape_requires_the_bound_pod(self):
        obs={'status':'ok','data':{'resultType':'streams','result':[{'stream':{
            'namespace':'ns','pod':'pod','container':'main'},'values':[
                ['2','SDM_JSON {"kind":"second"}'],['1','SDM_JSON {"kind":"first"}'],['3','ordinary log']]}]}}
        self.assertEqual(marker_lines(obs,'ns','pod'),['SDM_JSON {"kind":"first"}','SDM_JSON {"kind":"second"}'])
        with self.assertRaisesRegex(ValueError,'pod_identity'):marker_lines(obs,'ns','other')

    def test_complete_result_and_exact_duplicate_logs_are_idempotent(self):
        task,receipt,lines,expected=fixture()
        facts,artifacts=verify(task,receipt,lines+lines,expected)
        self.assertTrue(facts['valid_training_round'])
        self.assertEqual(len(artifacts),4)
        self.assertAlmostEqual(facts['primary_metric'],(4/5+2/3)/2)

    def test_task_identity_payload_and_terminal_must_match(self):
        for key,value in [('uid','other'),('project_uid','other'),('status','running'),('data',{'changed':True})]:
            task,receipt,lines,expected=fixture();task[key]=value
            with self.assertRaises(ValueError):verify(task,receipt,lines,expected)

    def test_unknown_submission_does_not_become_a_valid_round(self):
        task,receipt,lines,expected=fixture();receipt['status']='pending_approval'
        with self.assertRaisesRegex(ValueError,'verified_submission'):verify(task,receipt,lines,expected)

    def test_truncation_and_tampering_rejected(self):
        task,receipt,lines,expected=fixture()
        with self.assertRaisesRegex(ValueError,'chunks_incomplete'):verify(task,receipt,lines[:-1],expected)
        row=json.loads(lines[-1][len('SDM_JSON '):]);row['value']='AAAA'
        with self.assertRaises(ValueError):verify(task,receipt,lines+['SDM_JSON '+json.dumps(row)],expected)

    def test_new_split_or_code_cannot_satisfy_fair_comparison(self):
        for key in ('split_sha256','code_sha256'):
            task,receipt,lines,expected=fixture();expected[key]='c'*64
            with self.assertRaises(ValueError):verify(task,receipt,lines,expected)

    def test_metrics_recomputed_instead_of_trusting_reported_score(self):
        task,receipt,lines,expected=fixture()
        # A self-consistent producer can still report a fabricated score; the
        # selected metric and both result event copies must not avoid recount.
        rows=[json.loads(line[len('SDM_JSON '):]) for line in lines]
        for row in rows:
            if row.get('kind')=='artifact-chunk' and row['name']=='predictions.json':
                raw=gzip.decompress(base64.b64decode(row['value']));p=json.loads(raw);p['y_pred']=[0,0,1,1]
                p['probability_positive']=[.1,.2,.8,.8];raw=json.dumps(p,sort_keys=True).encode()
                row['value']=base64.b64encode(gzip.compress(raw)).decode()
                for manifest in rows:
                    if manifest.get('kind')=='artifact' and manifest['name']=='predictions.json':
                        manifest.update(bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest())
        with self.assertRaisesRegex(ValueError,'metric_recomputation'):
            verify(task,receipt,['SDM_JSON '+json.dumps(r) for r in rows],expected)


if __name__=='__main__':unittest.main()
