"""Read-only runtime check for the public CIFAR-100 development-space workload."""
import importlib.metadata
import hashlib
import json
import pathlib
import shutil
import subprocess
import sys

spec = json.load(sys.stdin)
if not sys.platform.startswith('linux') or not pathlib.Path('/lpai').is_dir():
    raise RuntimeError('development_space_linux_required')
data_root=pathlib.Path(spec['data_cache'])/'cifar-100-python'
expected={'train':'16019d7e3df5f24257cddd939b257f8d','test':'f0ef6b0ae62326f3e7ffdfab6717acfc',
          'meta':'7973b15100ade9c7d40fb424638fde48'}
observed={}
for name,checksum in expected.items():
    path=data_root/name
    if not path.is_file():raise RuntimeError('public_data_preparation_required:'+name)
    with path.open('rb') as stream:observed[name]=hashlib.file_digest(stream,'md5').hexdigest()
    if observed[name]!=checksum:raise RuntimeError('official_data_member_checksum_mismatch:'+name)
versions = {k: importlib.metadata.version(k) for k in ('torch', 'torchvision', 'numpy', 'Pillow')}
rows = subprocess.check_output(['nvidia-smi', '--query-gpu=uuid,name,memory.free',
                                '--format=csv,noheader,nounits'], text=True).splitlines()
devices = [dict(zip(('uuid', 'name', 'free_mib'), [s.strip() for s in row.split(',')])) for row in rows]
if not any(int(d['free_mib']) >= 6144 and 'A100' in d['name'] for d in devices):
    raise RuntimeError('no_visible_gpu_with_6gib_free')
free = shutil.disk_usage('/lpai').free
if free < 3 * 1024**3:
    raise RuntimeError('insufficient_experiment_disk')
print(json.dumps({'versions': versions, 'gpu': devices, 'free_disk_bytes': free,
                  'data_member_md5':observed,'test_examples_decoded':False,
                  'epochs': spec['epochs'], 'batch_size': spec['batch_size'],
                  'gpu_compute_invoked': False, 'qualification': 'shared GPU visibility, not exclusivity'}))
