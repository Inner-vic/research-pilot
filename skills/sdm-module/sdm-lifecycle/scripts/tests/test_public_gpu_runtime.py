"""NVML selection invariants only; never import Torch or initialize a GPU."""
import importlib.util
from pathlib import Path
import os
from unittest.mock import patch
import pytest

path=Path(__file__).resolve().parents[2]/'assets/public-image-classification/gpu_runtime.py'
spec=importlib.util.spec_from_file_location('public_gpu_runtime',path)
gpu=importlib.util.module_from_spec(spec);spec.loader.exec_module(gpu)
ROWS=['0, GPU-free, NVIDIA A100-SXM4-40GB, 16000','1, GPU-full, NVIDIA A100-SXM4-40GB, 162',
      '2, GPU-other, NVIDIA V100, 30000']


def test_metadata_selects_free_authorized_type_without_touching_busy_gpu():
    selected,_=gpu.choose(ROWS)
    assert selected['uuid']=='GPU-free'


@pytest.mark.parametrize('inherited',['1','GPU-full','', '-1','all'])
def test_inherited_restriction_is_not_widened_to_a_free_peer(inherited):
    with pytest.raises(RuntimeError):gpu.choose(ROWS,inherited)


def test_uuid_prefix_and_numeric_visibility_are_respected():
    assert gpu.choose(ROWS,'0')[0]['uuid']=='GPU-free'
    assert gpu.choose(ROWS,'GPU-free')[0]['uuid']=='GPU-free'
    with pytest.raises(RuntimeError):gpu.choose(ROWS,'GPU-')


def test_visibility_is_restricted_before_caller_can_import_torch():
    with patch.dict(os.environ,{},clear=True),patch.object(gpu.subprocess,'check_output',return_value='\n'.join(ROWS)):
        result=gpu.restrict()
        assert os.environ['CUDA_VISIBLE_DEVICES']=='GPU-free'
        assert result['inherited_cuda_visible_devices'] is None
