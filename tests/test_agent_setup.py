import base64
import json
import re
import shlex
from pathlib import Path

import pytest
from pydantic import ValidationError
from backend.agent_setup import AgentSetupIn, build_plan

ROOT = Path(__file__).resolve().parent.parent


def values(kind='native'):
    return dict(name='Edge',kind=kind,host='192.168.10.71',profile='edge',allow_http=True,
                config_path='/etc/haproxy/haproxy.cfg',runtime_socket='/run/haproxy/admin.sock',
                cert_dir='/etc/haproxy/certs')


@pytest.mark.parametrize('kind',['native','docker'])
def test_generated_command_contains_exact_profile_and_checksums(kind):
    plan=build_plan(AgentSetupIn(**values(kind)), 'http://192.168.10.70:8100', ROOT)
    args=shlex.split(plan['command'])
    encoded=next(arg.split('=',1)[1] for arg in args if arg.startswith('HAPROXY_AGENT_SETUP_B64='))
    setup=json.loads(base64.b64decode(encoded))
    assert setup['profile']['token']==plan['instance']['token']
    assert setup['profile']['kind']==kind
    assert setup['profile_name']=='edge'
    assert setup['bind']=='192.168.10.71'
    assert len(plan['instance']['token'])>=32
    assert plan['instance']['agent_url']=='http://192.168.10.71:9101'
    assert re.search(r'HAPROXY_PACKAGE_SHA256=[a-f0-9]{64}',plan['command'])
    assert 'sha256sum -c -' in plan['command']
    assert build_plan(AgentSetupIn(**values(kind)), 'http://192.168.10.70:8100', ROOT)['instance']['token']!=plan['instance']['token']


@pytest.mark.parametrize('field,value',[
    ('host','127.0.0.1'),('host','0.0.0.0'),('host','8.8.8.8'),
    ('profile','bad;command'),('service','haproxy;touch /tmp/bad'),
    ('config_path','/etc/../etc/shadow.cfg'),('config_path','/tmp/a$(id).cfg'),
    ('allow_http',False),('port',80),('runtime_socket','/run/haproxy/not-a-socket'),
    ('runtime_socket','/run/admin.sock'),
])
def test_invalid_setup_rejected(field,value):
    data=values();data[field]=value
    with pytest.raises(ValidationError):AgentSetupIn(**data)


def test_native_socket_paths_match_and_ipv6_url_is_bracketed():
    data=values();data.update(host='fd00::71',runtime_socket='/run/custom/admin.sock',cert_dir='/etc/custom/certs')
    body=AgentSetupIn(**data)
    assert body.runtime_socket_config==body.runtime_socket
    assert body.cert_dir_config==body.cert_dir
    assert build_plan(body,'http://192.168.10.70:8100',ROOT)['instance']['agent_url']=='http://[fd00::71]:9101'


def test_update_command_preserves_existing_profile_credentials():
    from backend.agent_setup import build_update_command
    root=Path(__file__).resolve().parent.parent
    command=build_update_command('http://192.168.10.70:8100',root)['command']
    assert 'HAPROXY_AGENT_UPDATE_ONLY=1' in command
    assert 'HAPROXY_AGENT_SETUP_B64' not in command
    assert 'sha256sum -c' in command


def test_https_public_host_has_verified_transport_and_independent_download():
    data=values('docker')|dict(host='203.0.113.10',connection='https',allow_http=False,
        agent_hostname='agent.example.de',download_origin='https://download.example.de')
    plan=build_plan(AgentSetupIn(**data),'http://192.168.10.70:8101',ROOT)
    args=shlex.split(plan['command'])
    encoded=next(arg.split('=',1)[1] for arg in args if arg.startswith('HAPROXY_AGENT_SETUP_B64='))
    setup=json.loads(base64.b64decode(encoded))
    assert setup['tls']['hostname']=='agent.example.de'
    assert setup['bind']=='203.0.113.10'
    assert plan['instance']['agent_url']=='https://agent.example.de:9101'
    assert plan['instance']['allow_http'] is False
    assert 'https://download.example.de/api/agent-installer' in plan['command']
    assert '192.168.10.70' not in plan['command']
    assert ' -k ' not in plan['command']


@pytest.mark.parametrize('host',['10.100.0.2','100.90.1.2','fdab::2'])
def test_vpn_ips_supported_without_public_http(host):
    body=AgentSetupIn(**(values()|dict(host=host,connection='vpn')))
    assert 'tls' not in json.loads(base64.b64decode(next(arg.split('=',1)[1] for arg in shlex.split(build_plan(body,'https://download.example.de',ROOT)['command']) if arg.startswith('HAPROXY_AGENT_SETUP_B64='))))


@pytest.mark.parametrize('changes',[
    dict(connection='vpn',host='1.1.1.1'),
    dict(connection='https',host='1.1.1.1',allow_http=True,agent_hostname='agent.example.de'),
    dict(connection='https',allow_http=False,agent_hostname='bad;command'),
    dict(connection='https',allow_http=False,agent_hostname=''),
    dict(download_origin='http://1.1.1.1:8101'),
    dict(download_origin='http://download.example.de'),
    dict(download_origin='https://user:pass@download.example.de'),
    dict(download_origin='https://download.example.de/path'),
    dict(download_origin='https://download.example.de:99999'),
    dict(download_origin='https://download.example.de?token=secret'),
])
def test_transport_and_download_fail_closed(changes):
    with pytest.raises(ValidationError):AgentSetupIn(**(values()|changes))
