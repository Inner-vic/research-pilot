#!/usr/bin/env python3
"""Seal a complete producer result using the installed coordinator's verifier."""
import argparse
import base64
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from personal_trust import safe_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('lifecycle-root', 'request', 'result', 'trust-root', 'private-root', 'principal', 'output'):
        parser.add_argument('--' + name, required=True)
    args = parser.parse_args()
    try:
        lifecycle = safe_path(args.lifecycle_root)
        sys.path.insert(0, str(lifecycle / 'scripts'))
        from authority import platform_result_subject
        from canonical import canonical_bytes, digest
        from contracts import AssetBundle, validate_schema
        from handoffs import _validate_result
        bundle = AssetBundle.load(lifecycle / 'assets')
        request_raw = Path(args.request).read_bytes()
        request = json.loads(request_raw)
        if request_raw != canonical_bytes(request):
            raise ValueError('request_not_canonical')
        result = json.loads(Path(args.result).read_bytes())
        if result.get('terminal_status') not in {'successful','failed','cancelled'}:
            raise ValueError('unsupported_terminal_status')
        if any(name in result for name in ('producer_attestation', 'receipt_digest')):
            raise ValueError('refuse_already_sealed_result')
        _, contract, _ = bundle.operation_contract(request['operation'])
        if digest(contract) != request['adapter_contract_digest']:
            raise ValueError('pinned_contract_mismatch')
        expected = contract['required_evidence']
        evidence = result.get('evidence', [])
        if not isinstance(evidence, list) or any(not isinstance(item, dict) for item in evidence):
            raise ValueError('evidence_array_required')
        types = [item.get('type') for item in evidence]
        if set(types) != set(expected):
            raise ValueError('complete_operation_evidence_required')
        for item in evidence:
            if set(item) != {'type', 'value'} or validate_schema(item['value'], contract['evidence_value_schemas'][item['type']]):
                raise ValueError('evidence_schema_invalid')
        if result.get('producer_principal_id') != args.principal:
            raise ValueError('producer_principal_mismatch')
        trust, private = safe_path(args.trust_root), safe_path(args.private_root)
        check = subprocess.run([sys.executable, '-B', str(Path(__file__).with_name('personal_trust.py')),
            'validate', '--trust-root', str(trust), '--private-root', str(private), '--principal', args.principal],
            capture_output=True, timeout=60)
        if check.returncode:
            raise ValueError('personal_trust_invalid')
        snapshot = json.loads((trust / 'trust-snapshot.json').read_text())
        row = snapshot['keys'][0]
        now = datetime.now(timezone.utc).replace(microsecond=0)
        stamp = lambda value: value.isoformat().replace('+00:00', 'Z')
        subject = dict(platform_result_subject(request, result), action='platform.result',
            key_digest=row['key_digest'], issued_at=stamp(now), expires_at=stamp(now + timedelta(minutes=10)),
            nonce=os.urandom(24).hex(), expected_revision=request['workspace_revision'])
        os.umask(0o077)
        with tempfile.TemporaryDirectory(prefix='sdm-personal-result-') as directory:
            message, signature = Path(directory) / 'subject', Path(directory) / 'signature'
            message.write_bytes(canonical_bytes(subject))
            subprocess.run([snapshot['verifier']['path'], 'pkeyutl', '-sign', '-rawin',
                '-inkey', str(private / 'personal-ed25519.pem'), '-in', str(message), '-out', str(signature)],
                capture_output=True, check=True, timeout=15)
            result['producer_attestation'] = {'schema': 'sdm-authority-attestation/v1', 'subject': subject,
                'signature': base64.b64encode(signature.read_bytes()).decode().rstrip('='),
                'public_key_pem': row['public_key_pem'], 'trust_snapshot': snapshot, 'trust_snapshot_digest': digest(snapshot)}
        result['receipt_digest'] = digest(result)
        _validate_result(request, result, bundle, trusted_snapshot=snapshot,
            trusted_snapshot_digest=digest(snapshot), consumption_time=stamp(now))
        output = safe_path(args.output)
        with output.open('xb') as stream:
            stream.write(canonical_bytes(result))
        print(json.dumps({'status': 'pass', 'result_id': result['result_id'], 'receipt_digest': result['receipt_digest'],
            'scope': 'personal-experiment', 's13_emitted': True, 's13_accepted': False}))
        return 0
    except Exception as exc:
        # Never echo subprocess stderr, private material, or untrusted payloads.
        reasons = {'request_not_canonical', 'unsupported_terminal_status',
            'refuse_already_sealed_result', 'pinned_contract_mismatch', 'evidence_array_required',
            'complete_operation_evidence_required', 'evidence_schema_invalid',
            'producer_principal_mismatch', 'personal_trust_invalid'}
        reason = str(exc) if type(exc) is ValueError and str(exc) in reasons else getattr(exc, 'code', type(exc).__name__)
        print(json.dumps({'status': 'blocked', 'reason': reason,
            's13_emitted': False, 's13_accepted': False}))
        return 3


if __name__ == '__main__':
    sys.exit(main())
