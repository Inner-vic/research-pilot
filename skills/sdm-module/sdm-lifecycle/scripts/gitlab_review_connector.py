"""Allowlisted GitLab review transport; no model calls, Git writes or training."""
import base64
import json
import re
import os
import urllib.request
from pathlib import Path, PurePosixPath
from urllib.parse import quote
from model_review_bridge import sha, save, emit
from loop_analysis import validate
from task_pipeline import digest


class Connector:
    def __init__(self, config, fetch=None):
        self.config = config
        self.fetch = fetch or self.gitlab_file
        self.reviews = config['reviews']
        policy = config['policy']
        self.policy = policy
        if not policy.get('evidence_directory', '').startswith('experiments/'):
            raise ValueError('experiment_directory_required')
        for review in self.reviews.values():
            if (review['hostname'], review['project']) != (policy['hostname'], policy['project']):
                raise ValueError('other_project_forbidden')
            if not re.fullmatch(r'[0-9a-f]{40}', review['commit']):
                raise ValueError('full_commit_required')
            if not re.fullmatch(r'[A-Za-z0-9.-]+', review['hostname']):
                raise ValueError('invalid_hostname')
            if not re.fullmatch(r'[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+', review['project']):
                raise ValueError('invalid_project')
            for path, expected in review['files'].items():
                p = PurePosixPath(path)
                if p.is_absolute() or '..' in p.parts or '\\' in path or '%' in path or ':' in path or not re.fullmatch(r'sha256:[0-9a-f]{64}', expected):
                    raise ValueError('invalid_file_binding')
                if not path.startswith(policy['evidence_directory'].rstrip('/') + '/') or p.suffix not in ('.json', '.txt', '.md'):
                    raise ValueError('outside_approved_evidence_directory')
                approval = review.get('redaction_approvals', {}).get(path, {})
                if approval.get('sha256') != expected or approval.get('status') != 'approved' or not approval.get('reviewed_by'):
                    raise ValueError('redaction_approval_required')
            if review['request_path'] not in review['files']:
                raise ValueError('request_not_allowlisted')

    def api_get(self, endpoint):
        token = os.environ.get('BMAD_REVIEW_PROJECT_TOKEN')
        if not token:
            raise ValueError('dedicated_project_credential_required')
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                raise ValueError('credential_redirect_forbidden')
        request = urllib.request.Request('https://' + self.policy['hostname'] + '/api/v4/' + endpoint,
            headers={'PRIVATE-TOKEN': token}, method='GET')
        try:
            with urllib.request.build_opener(NoRedirect).open(request, timeout=30) as response:
                return json.load(response)
        except Exception:
            raise ValueError('dedicated_credential_read_failed') from None

    def verify_credential(self):
        identity = self.api_get('user')
        token = self.api_get('personal_access_tokens/self')
        project = self.api_get('projects/' + quote(self.policy['project'], safe=''))
        if project.get('id') != self.policy['project_id']:
            raise ValueError('project_identity_mismatch')
        if not identity.get('external') or not re.fullmatch('project_' + str(project['id']) + '_bot_[A-Za-z0-9]+', identity.get('username', '')):
            raise ValueError('external_project_bot_required')
        if (token.get('id') != self.policy['token_id'] or token.get('scopes') != ['read_api']
                or token.get('revoked') or not token.get('active') or not token.get('expires_at')):
            raise ValueError('readonly_project_token_required')
        member = self.api_get(f"projects/{project['id']}/members/{identity['id']}")
        if member.get('access_level') != 20:
            raise ValueError('reporter_role_required')
        return {'status': 'pass', 'project_id': project['id'], 'external_bot': True,
                'scope': 'read_api', 'role': 'Reporter', 'writes_allowed': False}

    def gitlab_file(self, review, path):
        endpoint = ('projects/' + quote(review['project'], safe='') + '/repository/files/'
                    + quote(path, safe='') + '?ref=' + review['commit'])
        self.verify_credential()
        payload = self.api_get(endpoint)
        if payload.get('commit_id') != review['commit'] or payload.get('encoding') != 'base64':
            raise ValueError('gitlab_revision_mismatch')
        return base64.b64decode(payload['content'], validate=True)

    def get_file(self, review_id, path, offset=0, limit=16000):
        review = self.reviews[review_id]
        if path not in review['files']:
            raise ValueError('file_not_allowlisted')
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 32000:
            raise ValueError('invalid_page')
        raw = self.fetch(review, path)
        if sha(raw) != review['files'][path]:
            raise ValueError('gitlab_file_integrity_failed')
        content = raw.decode('utf-8')
        self.check_redacted(content)
        return {'review_id': review_id, 'commit': review['commit'], 'path': path,
            'sha256': sha(raw), 'offset': offset, 'total_chars': len(content),
            'next_offset': offset + limit if offset + limit < len(content) else None,
            'content': content[offset:offset + limit], 'untrusted_evidence': True}

    @staticmethod
    def check_redacted(content):
        patterns = (r'glpat-[A-Za-z0-9_-]+', r'sk-[A-Za-z0-9_-]{16,}',
            r'-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----',
            r'(?i)(?:password|access_token|private_token|api_key)\s*[=:]\s*[\"\x27]?[A-Za-z0-9_/-]{6,}',
            r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}',
            r'(?i)(?:https?://[^\s\"<>]+|[A-Z]:[\\/]|/home/|/mnt/|/data/)')
        if any(re.search(pattern, content) for pattern in patterns):
            raise ValueError('sensitive_content_detected')

    def list_reviews(self):
        return [{'review_id': key, 'commit': row['commit'], 'files': list(row['files']),
            'preferred_model': 'ChatGPT Pro', 'automatic_model_fallback': False}
            for key, row in self.reviews.items()]

    def return_decision(self, review_id, response_json):
        review = self.reviews[review_id]
        # Verify the whole request, not the paginated tool return.
        raw = self.fetch(review, review['request_path'])
        if sha(raw) != review['files'][review['request_path']]:
            raise ValueError('request_integrity_failed')
        packet = json.loads(raw)
        response = json.loads(response_json)
        validate(response, packet)
        # Content-addressed outbox prevents retries overwriting other decisions.
        key = digest(response).split(':')[1]
        root = Path(self.config['outbox']).resolve() / key
        save(root, 'analysis-response.json', response_json.encode('utf-8'))
        receipt = {'schema': 'sdm-review-return/v1', 'review_id': review_id,
            'request_digest': digest(packet), 'decision_digest': digest(response),
            'source_commit': review['commit'], 'validation': 'pass',
            'model_identity': 'must-be-verified-by-chat-receipt',
            'training_dispatched': False, 'gitlab_written': False}
        emit(root, 'return-receipt.json', receipt)
        return receipt


def serve(config_path, transport='stdio', port=8765):
    from mcp.server.fastmcp import FastMCP
    connector = Connector(json.loads(Path(config_path).read_bytes()))
    connector.verify_credential()
    server = FastMCP('BMAD ML GitLab Review', host='127.0.0.1', port=port,
        stateless_http=True, json_response=True)

    @server.tool(annotations={'readOnlyHint': True, 'openWorldHint': False})
    def list_reviews() -> list[dict]:
        """List operator-approved immutable experiments. Prefer Pro for reasoning."""
        return connector.list_reviews()

    @server.tool(annotations={'readOnlyHint': True, 'openWorldHint': False})
    def read_evidence(review_id: str, path: str, offset: int = 0, limit: int = 16000) -> dict:
        """Read hash-verified GitLab evidence at a pinned commit. Follow next_offset to read all pages."""
        return connector.get_file(review_id, path, offset, limit)

    server.run(transport=transport)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--transport', choices=('stdio', 'streamable-http'), default='stdio')
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    serve(args.config, args.transport, args.port)
