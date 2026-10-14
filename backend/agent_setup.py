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
from urllib.parse import urlsplit


class AgentSetupIn(InstanceMetadataIn):
    setup_id: str | None = Field(default=None,pattern=r'^[a-f0-9]{32}$')
    setup_version: int | None = Field(default=None,ge=1)
    name: str = Field(min_length=1, max_length=120)
    kind: Literal['native', 'docker']
    host: str = Field(max_length=45)
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
    connection: Literal['lan', 'vpn', 'https', 'reverse_proxy'] = 'lan'
    public_agent_url: str = Field(default='',max_length=500)
    agent_hostname: str = Field(default='',max_length=253)
    tls_cert_file: str = '/etc/haproxy-control/tls/fullchain.pem'
    tls_key_file: str = '/etc/haproxy-control/tls/privkey.pem'
    download_origin: str = Field(default='',max_length=500)

    @field_validator('host')
    @classmethod
    def host_ip(cls, value):
        address = ipaddress.ip_address(value)
        if address.is_unspecified or address.is_link_local or address.is_multicast:
            raise ValueError('Eine erreichbare Host-IP verwenden; kein localhost oder 0.0.0.0.')
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
        address = ipaddress.ip_address(self.host)
        private = any(address in ipaddress.ip_network(net) for net in ('10.0.0.0/8','172.16.0.0/12','192.168.0.0/16','100.64.0.0/10','fc00::/7') if address.version == ipaddress.ip_network(net).version)
        if address.is_loopback and self.connection != 'reverse_proxy':
            raise ValueError('Loopback nur für einen Reverseproxy auf demselben Host verwenden.')
        if self.connection == 'reverse_proxy':
            if not (private or address.is_loopback):
                raise ValueError('Internen HTTP-Agenten nur an eine private Host-IP oder Loopback binden.')
            if not self.allow_http:
                raise ValueError('HTTP für den internen Abschnitt zwischen Reverseproxy und Agent ausdrücklich zulassen.')
            url = urlsplit(self.public_agent_url)
            if any(ord(c)<=32 or ord(c)>=127 for c in self.public_agent_url) or any(c in self.public_agent_url for c in ('\\','"',"'")) or url.scheme != 'https' or not url.hostname or url.username is not None or url.password is not None or url.query or url.fragment or url.path not in ('','/'):
                raise ValueError('Öffentliche Agent-Adresse als HTTPS-Basis-URL ohne Zugangsdaten oder Profilpfad angeben.')
            try: public_port = url.port
            except ValueError: raise ValueError('Ungültiger öffentlicher HTTPS-Port.')
            if public_port is not None and not 1 <= public_port <= 65535: raise ValueError('Ungültiger öffentlicher HTTPS-Port.')
            self.public_agent_url = self.public_agent_url.rstrip('/')
        elif self.connection != 'https':
            if not private:
                raise ValueError('HTTP nur über eine private LAN- oder VPN-IP verwenden. Für öffentliche Adressen HTTPS wählen.')
            if not self.allow_http:
                raise ValueError('HTTP im privaten LAN oder VPN ausdrücklich zulassen.')
        else:
            self.tls_cert_file = self.absolute_path(self.tls_cert_file)
            self.tls_key_file = self.absolute_path(self.tls_key_file)
            if self.allow_http:
                raise ValueError('Für HTTPS die HTTP-Freigabe deaktivieren.')
            self.agent_hostname = self.agent_hostname.strip().lower()
            if not re.fullmatch(r'(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', self.agent_hostname):
                raise ValueError('Für HTTPS einen DNS-Hostnamen angeben, der im Agent-Zertifikat enthalten ist.')
        if self.connection == 'https' and any(PurePosixPath(value).parts[1] in ('root','home') for value in (self.tls_cert_file,self.tls_key_file)):
            raise ValueError('Agent-TLS-Dateien außerhalb von /root und /home bereitstellen; systemd ProtectHome verhindert dort den Zugriff.')
        if self.download_origin:
            if any(ord(c)<=32 or ord(c)>=127 for c in self.download_origin) or any(c in self.download_origin for c in ('\\','"',"'")): raise ValueError('Download-Adresse ohne Leerzeichen angeben.')
            self.download_origin = self.download_origin.rstrip('/')
            url = urlsplit(self.download_origin)
            if url.scheme not in ('https','http') or not url.hostname or url.username or url.password or url.query or url.fragment or url.path:
                raise ValueError('Download-Adresse als HTTP(S)-Basis-URL ohne Pfad oder Zugangsdaten angeben.')
            try: port = url.port
            except ValueError: raise ValueError('Ungültiger Port der Download-Adresse.')
            if port is not None and not 1 <= port <= 65535: raise ValueError('Ungültiger Port der Download-Adresse.')
            if url.scheme == 'http':
                try: download_ip = ipaddress.ip_address(url.hostname)
                except ValueError: raise ValueError('Für Download-Domains HTTPS verwenden.')
                if not any(download_ip in ipaddress.ip_network(net) for net in ('10.0.0.0/8','172.16.0.0/12','192.168.0.0/16','100.64.0.0/10','fc00::/7') if download_ip.version == ipaddress.ip_network(net).version):
                    raise ValueError('Öffentliche Downloads benötigen HTTPS.')
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


def build_plan(body: AgentSetupIn, origin: str, root: Path, token: str | None = None):
    template = json.loads((root / 'agent/config.example.json').read_text())
    profile = template['profiles']['native' if body.kind == 'native' else 'docker-edge'].copy()
    profile.update(token=token or secrets.token_urlsafe(48), config_path=body.config_path,
                   runtime_socket=body.runtime_socket, runtime_socket_config=body.runtime_socket_config,
                   cert_dir=body.cert_dir, cert_dir_config=body.cert_dir_config)
    if body.kind == 'native':
        profile['service'] = body.service
    else:
        profile['container'] = body.container
        profile['container_config_dir'] = body.container_config_dir
    setup = {'profile_name': body.profile, 'profile': profile, 'bind': body.host, 'port': body.port}
    if body.connection == 'https':
        setup['tls'] = {'hostname':body.agent_hostname, 'cert_file':body.tls_cert_file, 'key_file':body.tls_key_file}
    encoded = base64.b64encode(json.dumps(setup).encode()).decode()
    command=installation_command(body.download_origin or origin,root,'HAPROXY_AGENT_SETUP_B64='+shlex.quote(encoded))
    host = body.agent_hostname if body.connection == 'https' else (f'[{body.host}]' if ':' in body.host else body.host)
    scheme = 'https' if body.connection == 'https' else 'http'
    agent_url = body.public_agent_url if body.connection == 'reverse_proxy' else f'{scheme}://{host}:{body.port}'
    return {'command': command, 'instance': {'name': body.name, 'agent_url': agent_url,
            'profile': body.profile, 'token': profile['token'], 'allow_http': body.connection in ('lan','vpn'), 'notes': '',
            'tags':body.tags,'location':body.location,'infrastructure_id':body.infrastructure_id}}
