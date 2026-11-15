"""Generic transport keeps source/SSH boundaries without project policy imports."""
import importlib.util
import json
from pathlib import Path
import pytest

FILE=Path(__file__).parents[5]/'.agents/skills/lpai-s12-platform/scripts/devspace_transport.py'
spec=importlib.util.spec_from_file_location('generic_devspace_transport',FILE)
transport=importlib.util.module_from_spec(spec);spec.loader.exec_module(transport)


@pytest.mark.parametrize('address',['host.example:2200','ssh://host.example:2200','ssh -p 2200 root@host.example','ssh -p 2200 root@host.example -t bash'])
def test_advertised_endpoints_are_parsed_as_data(address):
    assert transport.ssh_endpoint(address,'root')==('host.example',2200)


@pytest.mark.parametrize('address',['ssh -p 22 other@host.example','ssh -p 0 root@host.example',
    'ssh -p 22 root@host.example; echo bad','ssh -p 22 root@host.example -t sh','host.example:99999'])
def test_untrusted_shell_or_wrong_identity_is_rejected(address):
    with pytest.raises(ValueError):transport.ssh_endpoint(address,'root')


@pytest.mark.parametrize('url',['https://gitlab.example/team/forecast.git','https://gitlab.example/user/classifier.git'])
def test_project_identity_is_supplied_not_hardcoded(tmp_path,url):
    value={'status':'transferred','source_url':url,'branch':'experiment','commit':'a'*40,'remote_path':'/persistent/source'}
    path=tmp_path/'receipt.json';path.write_text(json.dumps(value))
    assert transport.source_receipt(path,url,'experiment','a'*40,'/persistent/source')==value
    with pytest.raises(ValueError,match='anchor_mismatch'):
        transport.source_receipt(path,url,'other','a'*40,'/persistent/source')


def test_credential_url_is_rejected_and_identity_bytes_are_not_read(tmp_path):
    with pytest.raises(ValueError,match='credential_free'):
        transport.source_receipt(tmp_path/'absent','https://user:secret@gitlab.example/team/repo.git','b','a'*40,'/source')
    key=tmp_path/'existing-identity';key.write_text('synthetic fixture')
    from unittest.mock import patch
    with patch.object(Path,'read_bytes',side_effect=AssertionError('No private key reads')):
        argv=transport.ssh_argv('host.example',2200,'root',key)
    assert argv[-1]=='root@host.example' and 'StrictHostKeyChecking=yes' in argv and str(key) in argv
