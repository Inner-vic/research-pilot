from copy import deepcopy
import pytest
from canonical import digest
from contracts import (ContractError, canonical_runtime_environment_manifest,
    runtime_profile_from_manifest, canonical_runtime_environment_plan,
    validate_runtime_environment_against_r01)


def manifest():
    return {'schema':'sdm-devspace-runtime-environment/v1',
        'environment_instance_identity':'lpai-personal-uid',
        'container_image_digest':'not-provided','container_image_signature_digest':'not-provided',
        'image_provenance':{'backend':'lpai-devspace','purpose':'diagnostic-only',
            'namespace':'personal-project','space_uid':'personal-uid','image_reference':'registry/personal/image',
            'platform_spec_digest':digest({'actual-spec':1}),'content_status':'not-provided','signature_status':'not-provided'},
        'operating_system':{'name':'Linux','version':'test','architecture':'x86_64'},
        'driver':{'vendor':'NVIDIA','version':'test'},'cuda':{'toolkit_version':'test','runtime_version':'test'},
        'libraries':[{'name':'torch','version':'test','artifact_digest':digest({'installed-bytes':1})}],
        'gpu':{'vendor':'NVIDIA','model':'L20X','count':1,'memory_bytes':1024,'compute_capability':'9.0'},
        'cpu':{'architecture':'x86_64','model':'test','logical_cores':1},
        'kernel':{'name':'Linux','release':'test'},
        'distributed_topology':{'backend':'single-process','node_count':1,'world_size':1,'processes_per_node':1,'interconnect':'local'},
        'dependency_lock_digest':digest({'lock':1}),'environment_variables':[]}


def test_devspace_unknown_image_is_explicit_and_profile_remains_exact():
    actual=canonical_runtime_environment_manifest(manifest())
    profile=runtime_profile_from_manifest(actual)
    plan={'schema':'sdm-runtime-environment-plan/v1','environment_instance_identity':actual['environment_instance_identity'],
          'expected_runtime_image_digest':'not-provided','expected_runtime_profile':profile,'expected_runtime_profile_digest':digest(profile)}
    assert canonical_runtime_environment_plan(plan)==plan
    validate_runtime_environment_against_r01(actual,plan)
    for field in ('libraries','image_provenance'):
        changed=deepcopy(actual)
        if field=='libraries': changed[field][0]['artifact_digest']=digest({'different-installed-bytes':2})
        else: changed[field]['space_uid']='another-space'
        with pytest.raises(ContractError,match='actual runtime profile differs'):
            validate_runtime_environment_against_r01(changed,plan)


@pytest.mark.parametrize('change',[lambda m:m.pop('image_provenance'),
    lambda m:m['image_provenance'].__setitem__('purpose','production'),
    lambda m:m.__setitem__('container_image_digest',digest({'image-name':1})),
    lambda m:m.__setitem__('dependency_lock_digest','not-provided')])
def test_devspace_cannot_skip_real_environment_or_substitute_name_hash(change):
    actual=manifest(); change(actual)
    with pytest.raises(ContractError): canonical_runtime_environment_manifest(actual)


def test_legacy_signed_image_manifest_cannot_use_devspace_markers():
    actual=manifest(); actual['schema']='sdm-runtime-environment/v1'; actual.pop('image_provenance')
    with pytest.raises(ContractError): canonical_runtime_environment_manifest(actual)
