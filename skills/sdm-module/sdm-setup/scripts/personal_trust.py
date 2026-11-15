#!/usr/bin/env python3
"""Fresh, explicitly personal Ed25519 trust; no platform evidence is produced."""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile


def sha(data):
    return 'sha256:' + hashlib.sha256(data).hexdigest()


def run(argv):
    return subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, check=True, timeout=15)


def safe_path(value):
    path = Path(value)
    if not path.is_absolute() or any(part == '..' for part in path.parts):
        raise ValueError('paths must be absolute without parent traversal')
    for node in [path, *path.parents]:
        if node.is_symlink():
            raise ValueError('linked paths are not allowed')
    return path


def self_check(openssl, key, public):
    with tempfile.TemporaryDirectory(prefix='sdm-personal-proof-') as directory:
        root = Path(directory)
        message = root / 'challenge'
        signature = root / 'signature'
        pub = root / 'public.pem'
        message.write_bytes(b'sdm-personal-trust-self-check/v1:' + os.urandom(32))
        pub.write_bytes(public)
        run([openssl, 'pkeyutl', '-sign', '-rawin', '-inkey', str(key), '-in', str(message), '-out', str(signature)])
        verify = [openssl, 'pkeyutl', '-verify', '-rawin', '-pubin', '-inkey', str(pub), '-in', str(message), '-sigfile', str(signature)]
        run(verify)
        message.write_bytes(message.read_bytes() + b'tampered')
        rejected = subprocess.run(verify, stdin=subprocess.DEVNULL, capture_output=True, timeout=15)
        if rejected.returncode == 0:
            raise ValueError('tampered challenge unexpectedly verified')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['init', 'validate'])
    parser.add_argument('--trust-root', required=True)
    parser.add_argument('--private-root', required=True)
    parser.add_argument('--principal', required=True)
    args = parser.parse_args()
    try:
        if sys.platform != 'linux' or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9._:@-]{0,127}', args.principal):
            raise ValueError('requires Linux and an explicit stable personal principal')
        trust, private = safe_path(args.trust_root), safe_path(args.private_root)
        if trust == private or private.is_relative_to(trust) or trust.is_relative_to(private):
            raise ValueError('public and private roots must be separate')
        openssl = Path(shutil.which('openssl') or '/unavailable').resolve(strict=True)
        info = openssl.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or not os.access(openssl, os.X_OK):
            raise ValueError('OpenSSL must be a single-link regular executable')
        os.umask(0o077)
        key = private / 'personal-ed25519.pem'
        snapshot_file = trust / 'trust-snapshot.json'
        if args.command == 'init':
            if trust.exists() or private.exists():
                raise ValueError('refuse existing trust or private root; validate or reconcile instead')
            private.mkdir(parents=True, mode=0o700)
            run([str(openssl), 'genpkey', '-algorithm', 'ED25519', '-out', str(key)])
            key.chmod(0o600)
            public = run([str(openssl), 'pkey', '-in', str(key), '-pubout']).stdout
            self_check(str(openssl), key, public)
            now = datetime.now(timezone.utc).replace(microsecond=0)
            timestamp = lambda value: value.isoformat().replace('+00:00', 'Z')
            snapshot = {'schema': 'sdm-trust-snapshot/v1',
                        'verifier': {'path': str(openssl), 'digest': sha(openssl.read_bytes())},
                        'keys': [{'principal_id': args.principal, 'key_digest': sha(public),
                                  'public_key_pem': public.decode('ascii'),
                                  'roles': ['platform-producer', 'research-owner'],
                                  'valid_from': timestamp(now - timedelta(seconds=1)),
                                  'valid_until': timestamp(now + timedelta(days=90))}],
                        'revocations': []}
            trust.mkdir(parents=True, mode=0o700)
            with snapshot_file.open('x', encoding='utf-8') as stream:
                json.dump(snapshot, stream, sort_keys=True, separators=(',', ':'))
            with (trust / 'personal-scope.json').open('x', encoding='utf-8') as stream:
                json.dump({'schema': 'sdm-personal-scope/v1', 'scope': 'personal-experiment',
                           'principal_id': args.principal, 'company_authority': False,
                           'independent_review_authorized': False}, stream, sort_keys=True)
        for public_file in [snapshot_file, trust / 'personal-scope.json']:
            public_info = public_file.lstat()
            if not stat.S_ISREG(public_info.st_mode) or public_info.st_nlink != 1:
                raise ValueError('public trust files must be single-link regular files')
        snapshot = json.loads(snapshot_file.read_text(encoding='utf-8'))
        scope = json.loads((trust / 'personal-scope.json').read_text(encoding='utf-8'))
        if scope.get('scope') != 'personal-experiment' or scope.get('principal_id') != args.principal:
            raise ValueError('personal scope/principal mismatch')
        key_info = key.lstat()
        if not stat.S_ISREG(key_info.st_mode) or key_info.st_nlink != 1 or key_info.st_mode & 0o077:
            raise ValueError('private key permissions/type invalid')
        public = run([str(openssl), 'pkey', '-in', str(key), '-pubout']).stdout
        rows = snapshot.get('keys', [])
        if len(rows) != 1 or rows[0].get('principal_id') != args.principal or rows[0].get('public_key_pem') != public.decode('ascii') or rows[0].get('key_digest') != sha(public):
            raise ValueError('public trust/key identity mismatch')
        if rows[0].get('roles') != ['platform-producer', 'research-owner']:
            raise ValueError('personal initialization must not grant independent reviewer roles')
        now = datetime.now(timezone.utc)
        parse_time = lambda value: datetime.fromisoformat(value.replace('Z', '+00:00'))
        if not parse_time(rows[0]['valid_from']) <= now < parse_time(rows[0]['valid_until']):
            raise ValueError('personal key is outside its validity interval')
        if any(row.get('key_digest') == sha(public) and parse_time(row['effective_at']) <= now for row in snapshot.get('revocations', [])):
            raise ValueError('personal key is revoked')
        if snapshot.get('verifier') != {'path': str(openssl), 'digest': sha(openssl.read_bytes())}:
            raise ValueError('pinned verifier changed')
        self_check(str(openssl), key, public)
        print(json.dumps({'command': 'personal-trust.' + args.command, 'status': 'pass',
                          'scope': 'personal-experiment', 'principal_id': args.principal,
                          'key_digest': sha(public), 'trust_root': str(trust),
                          'signature_verified': True, 'tamper_rejected': True,
                          's13_emitted': False, 'independent_review_authorized': False}, sort_keys=True))
        return 0
    except (ValueError, OSError, subprocess.SubprocessError, json.JSONDecodeError):
        print(json.dumps({'command': 'personal-trust.' + args.command, 'status': 'blocked',
                          'error': 'personal_trust_configuration_invalid', 's13_emitted': False}))
        return 3


if __name__ == '__main__':
    sys.exit(main())
