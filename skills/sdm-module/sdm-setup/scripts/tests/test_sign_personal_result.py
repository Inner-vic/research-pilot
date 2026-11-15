"""Disposable test evidence never enters a real workspace."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1]
MODULE = SCRIPTS.parents[1]
sys.path.insert(0, str(MODULE / 'sdm-lifecycle/scripts'))
sys.path.insert(0, str(MODULE / 'tests/fixtures/platform-skills/fake-data-platform'))
from canonical import canonical_bytes, digest
from contracts import AssetBundle
from fixture_common import operation_evidence


class PersonalResultTests(unittest.TestCase):
    def test_complete_result_seals_and_partial_result_never_emits(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            public, private = root / 'public', root / 'private'
            principal = 'disposable-personal-test'
            init = subprocess.run([sys.executable, '-B', str(SCRIPTS / 'personal_trust.py'), 'init',
                '--trust-root', str(public), '--private-root', str(private), '--principal', principal], capture_output=True)
            self.assertEqual(init.returncode, 0, init.stdout)
            bundle = AssetBundle.load(MODULE / 'sdm-lifecycle/assets')
            _, contract, _ = bundle.operation_contract('data.labeling-status')
            request = {'request_id': 'REQ-disposable01', 'operation': 'data.labeling-status',
                'binding_identity': 'test-platform', 'binding_skill_digest': digest({'test': 'skill'}),
                'adapter_contract_digest': digest(contract), 'workspace_id': 'disposable-test',
                'workspace_revision': 0, 'gate': 'G3', 'step': 3, 'subjects': {'artifact_id': 'test'},
                'idempotency_key': digest({'test': 'key'}), 'expected_evidence': contract['required_evidence'],
                'evidence_cardinality': contract['evidence_cardinality']}
            result = {key: request[key] for key in ('request_id', 'operation', 'binding_identity',
                'binding_skill_digest', 'adapter_contract_digest', 'workspace_id', 'workspace_revision',
                'gate', 'step', 'subjects', 'idempotency_key')}
            result.update(schema='sdm-handoff-result/v1', result_id='RES-disposable01', dispatch_correlation='test-dispatch',
                producer_identity='test-platform', producer_principal_id=principal, terminal_status='successful',
                evidence=[{'type': name, 'value': value} for name, value in operation_evidence(request).items()],
                completed_at='2026-01-02T03:04:05Z')
            request_file, result_file, output = root / 'request', root / 'result', root / 'sealed'
            request_file.write_bytes(canonical_bytes(request))
            result_file.write_bytes(canonical_bytes(result))
            command = [sys.executable, '-B', str(SCRIPTS / 'sign_personal_result.py'),
                '--lifecycle-root', str(MODULE / 'sdm-lifecycle'), '--request', str(request_file), '--result', str(result_file),
                '--trust-root', str(public), '--private-root', str(private), '--principal', principal, '--output', str(output)]
            sealed = subprocess.run(command, capture_output=True)
            self.assertEqual(sealed.returncode, 0, sealed.stdout)
            self.assertFalse(json.loads(sealed.stdout)['s13_accepted'])
            self.assertTrue(output.exists())
            output.unlink()
            result['evidence'] = []
            result_file.write_bytes(canonical_bytes(result))
            rejected = subprocess.run(command, capture_output=True)
            self.assertEqual(rejected.returncode, 3, rejected.stdout)
            self.assertFalse(output.exists())


if __name__ == '__main__':
    unittest.main()
