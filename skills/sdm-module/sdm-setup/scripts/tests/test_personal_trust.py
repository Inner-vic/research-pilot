"""Verify personal bootstrap against the real authority verifier."""
import base64
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS.parents[1] / 'sdm-lifecycle/scripts'))
from authority import AuthorityError, verify_attestation
from canonical import canonical_bytes, digest
from contracts import AssetBundle


class PersonalTrustTests(unittest.TestCase):
    def test_fresh_identity_verifies_and_rejects_tampering_and_reinit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            trust, private = root / 'public', root / 'private'
            command = [sys.executable, '-B', str(SCRIPTS / 'personal_trust.py'), 'init',
                       '--trust-root', str(trust), '--private-root', str(private), '--principal', 'personal-test-owner']
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout)
            snapshot = json.loads((trust / 'trust-snapshot.json').read_text())
            row = snapshot['keys'][0]
            self.assertNotIn('independent-protocol-reviewer', row['roles'])
            self.assertEqual((private / 'personal-ed25519.pem').stat().st_mode & 0o777, 0o600)
            now = datetime.now(timezone.utc).replace(microsecond=0)
            stamp = lambda value: value.isoformat().replace('+00:00', 'Z')
            subject = {'action': 'platform.result', 'role': 'platform-producer', 'principal_id': row['principal_id'],
                       'key_digest': row['key_digest'], 'issued_at': stamp(now), 'expires_at': stamp(now + timedelta(minutes=5)),
                       'nonce': 'personal-trust-test-nonce', 'expected_revision': 0,
                       'governed_ids': {'workspace_id': 'personal-test'}, 'digests': {'request': digest({'test': 'crypto-only'})}}
            message, signature = root / 'subject.json', root / 'signature.bin'
            message.write_bytes(canonical_bytes(subject))
            subprocess.run([snapshot['verifier']['path'], 'pkeyutl', '-sign', '-rawin', '-inkey', str(private / 'personal-ed25519.pem'),
                            '-in', str(message), '-out', str(signature)], check=True, capture_output=True)
            attestation = {'schema': 'sdm-authority-attestation/v1', 'subject': subject,
                           'signature': base64.b64encode(signature.read_bytes()).decode().rstrip('='),
                           'public_key_pem': row['public_key_pem'], 'trust_snapshot': snapshot, 'trust_snapshot_digest': digest(snapshot)}
            bundle = AssetBundle.load(SCRIPTS.parents[1] / 'sdm-lifecycle/assets')
            kwargs = dict(action='platform.result', expected_subject={'governed_ids': subject['governed_ids'], 'digests': subject['digests']},
                          expected_revision=0, bundle=bundle, trusted_snapshot=snapshot, trusted_snapshot_digest=digest(snapshot))
            verify_attestation(attestation, **kwargs)
            changed = deepcopy(attestation)
            changed['subject']['nonce'] += '-changed'
            with self.assertRaises(AuthorityError):
                verify_attestation(changed, **kwargs)
            self.assertEqual(subprocess.run(command, capture_output=True).returncode, 3)


if __name__ == '__main__':
    unittest.main()
