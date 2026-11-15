"""Local experiment identities; no retry dispatch or model checkpoint loading."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from experiment_identity import bind
from project_driver import file_digest


class ExperimentIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.config={'run_id':'run-1','study_id':'study','trial_id':'trial',
            'experiment_parameters':{'batch':12},'trial_inputs':{'source':'code1','data':'data1','protocol':'eval1'}}

    def folder(self,name):
        path=self.root/name;path.mkdir();return path

    def parent(self):
        root=self.folder('first');bind(self.config,root,1)
        path=root/'experiment-identity.json'
        return {'path':str(path),'digest':file_digest(path)}

    def test_repeat_binding_is_stable(self):
        root=self.folder('stable');first=bind(self.config,root,1)
        self.assertEqual(first,bind(self.config,root,1))

    def test_checkpoint_hash_does_not_read_entire_file_into_memory(self):
        import hashlib
        path=self.root/'larger-checkpoint';payload=b'CPU-block'*(300000)
        path.write_bytes(payload)
        with patch.object(Path,'read_bytes',side_effect=AssertionError('whole-file read')):
            self.assertEqual(file_digest(path),'sha256:'+hashlib.sha256(payload).hexdigest())

    def test_missing_input_identity_is_unverified_not_reproducible(self):
        cfg={'run_id':'unverified'};value=bind(cfg,self.folder('unverified'),1)
        self.assertEqual(value['qualification'],'input-identities-unverified')
        self.assertIsNone(value['trial_fingerprint'])

    def test_changed_parameters_cannot_rewrite_attempt(self):
        root=self.folder('immutable');bind(self.config,root,1)
        self.config['experiment_parameters']['batch']=6
        with self.assertRaisesRegex(ValueError,'identity_changed'):bind(self.config,root,1)

    def test_retry_preserves_trial_and_uses_new_attempt(self):
        parent=self.parent();cfg={**self.config,'run_id':'run-2','attempt_kind':'retry','parent_identity':parent}
        value=bind(cfg,self.folder('retry'),1)
        self.assertEqual(value['trial_id'],'trial');self.assertEqual(value['attempt_id'],'run-2-a01')

    def test_parameter_change_is_new_trial_not_retry(self):
        parent=self.parent();cfg=deepcopy(self.config);cfg.update(run_id='run-2',attempt_kind='retry',parent_identity=parent)
        cfg['experiment_parameters']['batch']=6
        with self.assertRaisesRegex(ValueError,'same_trial_inputs'):bind(cfg,self.folder('changed'),1)

    def test_same_attempt_id_cannot_be_presented_as_retry(self):
        cfg={**self.config,'attempt_kind':'retry','parent_identity':self.parent()}
        with self.assertRaisesRegex(ValueError,'new_attempt'):bind(cfg,self.folder('duplicate'),1)

    def test_changed_helper_bundle_is_not_a_retry_of_the_same_trial(self):
        self.config['project_driver']={'digest':'same-entry','bundle_digest':'bundle-1'}
        parent=self.parent();cfg=deepcopy(self.config)
        cfg.update(run_id='run-2',attempt_kind='retry',parent_identity=parent)
        cfg['project_driver']['bundle_digest']='bundle-2'
        with self.assertRaisesRegex(ValueError,'same_trial_inputs'):bind(cfg,self.folder('helper-change'),1)

    def test_resume_requires_checkpoint_and_verified_state_evidence(self):
        parent=self.parent();cfg={**self.config,'run_id':'run-2','attempt_kind':'resume','parent_identity':parent}
        root=self.folder('resume')
        with self.assertRaisesRegex(ValueError,'checkpoint_required'):bind(cfg,root,1)
        checkpoint=self.root/'checkpoint.bin';checkpoint.write_bytes(b'CPU metadata fixture, not a real checkpoint')
        cfg['resume_checkpoint']={'path':str(checkpoint),'digest':file_digest(checkpoint)}
        evidence=self.root/'restore.json'
        payload={'schema':'sdm-checkpoint-restore/v1','checkpoint_digest':file_digest(checkpoint),'status':'verified',
            'restored_states':{'model':True,'optimizer':True,'scheduler':True,'rng':True}}
        evidence.write_text(json.dumps(payload));cfg['restore_evidence']={'path':str(evidence),'digest':file_digest(evidence)}
        cfg['required_resume_states']=['model','optimizer','scheduler','rng','scaler']
        with self.assertRaisesRegex(ValueError,'complete_training_state'):bind(cfg,root,1)
        payload['restored_states']['scaler']=True;evidence.write_text(json.dumps(payload))
        cfg['restore_evidence']['digest']=file_digest(evidence)
        value=bind(cfg,root,1)
        self.assertEqual(value['checkpoint_digest'],file_digest(checkpoint))


if __name__=='__main__':unittest.main()
