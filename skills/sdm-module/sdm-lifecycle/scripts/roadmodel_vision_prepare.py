"""Install a pinned wheel from platform inputs; runtime never downloads inputs."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

WHEEL_NAME = 'timm-1.0.22-py3-none-any.whl'
WHEEL_SHA = '888981753e65cbaacfc07494370138b1700a27b1f0af587f4f9b47bc024161d0'
WEIGHT_SHA = 'ed8a1265847086c913535fe7d84c0156a68dc98c0edd52298b8bb4bb7bdcbbb1'


def checked_input(path, expected_sha, expected_bytes):
    path = Path(path).resolve(strict=True)
    if not path.is_file() or path.stat().st_size != expected_bytes:
        raise ValueError('offline_input_size_mismatch:' + path.name)
    with path.open('rb') as stream:
        actual = hashlib.file_digest(stream, 'sha256').hexdigest()
    if actual != expected_sha:
        raise ValueError('offline_input_digest_mismatch:' + path.name)
    print('VISION_PINNED_INPUT ' + json.dumps({'name': path.name, 'sha256': actual,
          'bytes': expected_bytes, 'source': 'platform-mounted-offline'}), flush=True)
    return path


def prepare(directory, checkpoint, wheel_directory):
    directory = Path(directory)
    root = Path(wheel_directory).resolve(strict=True)
    candidates = [p for p in (root / WHEEL_NAME, root / 'dependency' / WHEEL_NAME) if p.is_file()]
    if len(candidates) != 1:
        raise ValueError('exactly_one_mounted_wheel_required')
    wheel = checked_input(candidates[0], WHEEL_SHA, 2530238)
    checkpoint = checked_input(checkpoint, WEIGHT_SHA, 342624338)
    vendor = directory / 'vision-vendor'
    subprocess.run([sys.executable, '-m', 'pip', '--isolated', 'install',
                    '--no-index', '--no-deps', '--no-cache-dir', '--disable-pip-version-check',
                    '--target', str(vendor), str(wheel)], check=True, timeout=120)
    return str(vendor), str(checkpoint)


if __name__ == '__main__':
    vendor, checkpoint = prepare(*sys.argv[1:])
    print(json.dumps({'vendor': vendor, 'checkpoint': checkpoint}))
