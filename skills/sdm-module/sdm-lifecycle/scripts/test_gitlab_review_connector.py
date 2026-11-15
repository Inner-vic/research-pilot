import json
import tempfile
import unittest
from pathlib import Path
from gitlab_review_connector import Connector
from model_review_bridge import sha
from test_model_review_bridge import BridgeTests


class ConnectorTests(unittest.TestCase):
    def setUp(self):
        BridgeTests.setUpClass()
        self.raw = json.dumps(BridgeTests.packet).encode()
        self.directory = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.directory.cleanup)
        self.config = {'policy': {'hostname': 'gitlab.example.com', 'project': 'owner/project',
            'evidence_directory': 'experiments/redacted', 'project_id': 1, 'token_id': 2},
            'outbox': self.directory.name, 'reviews': {'sample': {
            'hostname': 'gitlab.example.com', 'project': 'owner/project',
            'commit': 'a' * 40, 'request_path': 'experiments/redacted/request.json',
            'files': {'experiments/redacted/request.json': sha(self.raw)},
            'redaction_approvals': {'experiments/redacted/request.json': {
                'sha256': sha(self.raw), 'status': 'approved', 'reviewed_by': 'test'}}}}}
        self.connector = Connector(self.config, lambda review, path: self.raw)

    def test_allowlist_integrity_and_pagination(self):
        page = self.connector.get_file('sample', 'experiments/redacted/request.json', 0, 10)
        self.assertEqual(page['content'], self.raw.decode()[:10])
        self.assertEqual(page['next_offset'], 10)
        with self.assertRaisesRegex(ValueError, 'file_not_allowlisted'):
            self.connector.get_file('sample', '../../secret')
        self.connector.fetch = lambda review, path: b'tampered'
        with self.assertRaisesRegex(ValueError, 'integrity'):
            self.connector.get_file('sample', 'experiments/redacted/request.json')

    def test_other_project_and_unapproved_evidence(self):
        self.config['reviews']['sample']['project'] = 'other/project'
        with self.assertRaisesRegex(ValueError, 'other_project'):
            Connector(self.config)
        self.config['reviews']['sample']['project'] = 'owner/project'
        self.config['reviews']['sample']['redaction_approvals'] = {}
        with self.assertRaisesRegex(ValueError, 'redaction_approval'):
            Connector(self.config)

    def test_sensitive_content_blocked(self):
        for content in ('glpat-secret', 'person@example.com', 'password=secret123',
                        'https://internal.example.com', '-----BEGIN PRIVATE KEY-----'):
            with self.assertRaisesRegex(ValueError, 'sensitive_content'):
                Connector.check_redacted(content)

    def test_personal_or_internal_identity_rejected(self):
        def fake(endpoint):
            if endpoint == 'user': return {'id': 3, 'username': 'project_1_bot_abc', 'external': False}
            if endpoint == 'personal_access_tokens/self':
                return {'id': 2, 'scopes': ['read_api'], 'active': True, 'expires_at': '2026-11-01'}
            return {'id': 1}
        self.connector.api_get = fake
        with self.assertRaisesRegex(ValueError, 'external_project_bot'):
            self.connector.verify_credential()

    def test_source_and_path_escape_rejected(self):
        for path in ('src/model.py', 'experiments/redacted/model.py',
                     'experiments/redacted/../source.json', 'experiments/redacted/%2e%2e/file.json'):
            self.config['reviews']['sample']['files'] = {path: sha(self.raw)}
            with self.assertRaises(ValueError):
                Connector(self.config)

    def test_broad_scope_rejected_and_isolated_bot_accepted(self):
        scopes = ['read_api']
        def fake(endpoint):
            if endpoint == 'user': return {'id': 3, 'username': 'project_1_bot_abc', 'external': True}
            if endpoint == 'personal_access_tokens/self':
                return {'id': 2, 'scopes': scopes, 'active': True, 'expires_at': '2026-11-01'}
            if '/members/' in endpoint: return {'access_level': 20}
            return {'id': 1}
        self.connector.api_get = fake
        self.assertEqual(self.connector.verify_credential()['status'], 'pass')
        scopes.append('api')
        with self.assertRaisesRegex(ValueError, 'readonly_project_token'):
            self.connector.verify_credential()

    def test_return_is_validated_idempotent_and_no_execution(self):
        response = json.dumps(BridgeTests.decision)
        first = self.connector.return_decision('sample', response)
        self.assertEqual(first, self.connector.return_decision('sample', response))
        self.assertFalse(first['training_dispatched'])
        self.assertEqual(len(list(Path(self.directory.name).rglob('analysis-response.json'))), 1)
        bad = {**BridgeTests.decision, 'request_digest': 'wrong'}
        with self.assertRaisesRegex(ValueError, 'analysis_request_mismatch'):
            self.connector.return_decision('sample', json.dumps(bad))

    def test_floating_revision_rejected(self):
        self.config['reviews']['sample']['commit'] = 'main'
        with self.assertRaisesRegex(ValueError, 'full_commit'):
            Connector(self.config)


if __name__ == '__main__':
    unittest.main()
