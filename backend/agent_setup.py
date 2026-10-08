"""Build a validated, administrator-requested agent installation command."""
import base64
import hashlib
import ipaddress
import json
import re
import secrets
import shlex
from pathlib import Path, PurePosixPath

from pydantic import Field, field_validator, model_validator
from typing import Literal
from backend.schemas import InstanceMetadataIn


class AgentSetupIn(InstanceMetadataIn):
    name: str = Field(min_length=1, max_length=120)
    kind: Literal['native', 'docker']
    host: str
    port: int = Field(default=9101, ge=1024, le=65535)
    profile: str = Field(pattern=r'^[a-zA-Z0-9_.-]{1,80}$')
    service: str = Field(default='haproxy', pattern=r'^[a-zA-Z0-9_.@-]{1,100}$')
    container: str = Field(default='haproxy', pattern=r'^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,99}$')
    config_path: str
    runtime_socket: str
    cert_dir: str
    container_config_dir: str = '/usr/local/etc/haproxy'
    runtime_socket_config: str = '/run/haproxy/admin.sock'
    cert_dir_config: str = '/etc/haproxy/certs'
    allow_http: bool = False

    @field_validator('host')
    @classmethod
    def private_host(cls, value):
        address = ipaddress.ip_address(value)
        if not address.is_private or address.is_loopback or address.is_unspecified or address.is_link_local:
            raise ValueError('Eine private, erreichbare Host-IP verwenden; kein localhost oder 0.0.0.0.')
        return str(address)

    @field_validator('config_path', 'runtime_socket', 'cert_dir', 'container_config_dir',
                     'runtime_socket_config', 'cert_dir_config')
    @classmethod
    def absolute_path(cls, value):
        path = PurePosixPath(value)
        if not re.fullmatch(r'/[a-zA-Z0-9_./-]+', value) or '..' in value.split('/') or len(path.parts) < 3:
            raise ValueError('Absoluten Pfad ohne Leerzeichen oder .. verwenden.')
        return str(path)

    @model_validator(mode='after')
    def valid_setup(self):
        if not self.allow_http:
            raise ValueError('HTTP im privaten Netz für diesen Installationsassistenten ausdrücklich zulassen.')
        if not self.config_path.endswith('.cfg') or not self.runtime_socket.endswith('.sock'):
            raise ValueError('Konfiguration muss auf .cfg und Runtime-Socket auf .sock enden.')
        if len(PurePosixPath(self.runtime_socket).parts) < 4:
            raise ValueError('Ein eigenes Runtime-Verzeichnis verwenden, z. B. /run/haproxy/admin.sock.')
        if self.kind == 'native':
            self.runtime_socket_config = self.runtime_socket
            self.cert_dir_config = self.cert_dir
        return self


def installation_command(origin: str, root: Path, environment: str):
    origin=origin.rstrip('/')
    installer_hash=hashlib.sha256((root/'scripts/install-agent.sh').read_bytes()).hexdigest()
    package_hash=hashlib.sha256((root/'downloads/haproxy-management-docker.zip').read_bytes()).hexdigest()
    q=shlex.quote
    return (
        '( set -e; haproxy_setup_dir=$(mktemp -d); '
        'trap \'rm -rf "$haproxy_setup_dir"\' EXIT; '
        f'curl -fsSL {q(origin + "/api/agent-installer")} -o "$haproxy_setup_dir/install.sh"; '
        f'printf \'%s  %s\\n\' {q(installer_hash)} "$haproxy_setup_dir/install.sh" | sha256sum -c -; '
        f'sudo env {environment} HAPROXY_SOURCE_URL={q(origin)} '
        f'HAPROXY_PACKAGE_SHA256={q(package_hash)} bash "$haproxy_setup_dir/install.sh" )'
    )


def build_update_command(origin: str, root: Path):
    return {'command':installation_command(origin,root,'HAPROXY_AGENT_UPDATE_ONLY=1')}


def build_plan(body: AgentSetupIn, origin: str, root: Path):
    template = json.loads((root / 'agent/config.example.json').read_text())
    profile = template['profiles']['native' if body.kind == 'native' else 'docker-edge'].copy()
    profile.update(token=secrets.token_urlsafe(48), config_path=body.config_path,
                   runtime_socket=body.runtime_socket, runtime_socket_config=body.runtime_socket_config,
                   cert_dir=body.cert_dir, cert_dir_config=body.cert_dir_config)
    if body.kind == 'native':
        profile['service'] = body.service
    else:
        profile['container'] = body.container
        profile['container_config_dir'] = body.container_config_dir
    setup = {'profile_name': body.profile, 'profile': profile, 'bind': body.host, 'port': body.port}
    encoded = base64.b64encode(json.dumps(setup).encode()).decode()
    command=installation_command(origin,root,'HAPROXY_AGENT_SETUP_B64='+shlex.quote(encoded))
    host = f'[{body.host}]' if ':' in body.host else body.host
    return {'command': command, 'instance': {'name': body.name, 'agent_url': f'http://{host}:{body.port}',
            'profile': body.profile, 'token': profile['token'], 'allow_http': True, 'notes': '',
            'tags':body.tags,'location':body.location,'infrastructure_id':body.infrastructure_id}}
