import ipaddress
import re
from typing import Literal
from urllib.parse import urlsplit
from pydantic import BaseModel, Field, field_validator, model_validator

TOKEN = re.compile(r'^[a-zA-Z0-9_.-]+$')
DOMAIN = re.compile(r'^(?:\*\.)?(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9-]{2,63}$')

def single(value: str):
    if any(c.isspace() for c in value) or any(c in value for c in '#;\\\x00'):
        raise ValueError('Keine Leerzeichen, Zeilenumbrüche oder Steuerzeichen erlaubt.')
    return value

class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=200)

class PasswordIn(BaseModel):
    current_password: str = Field(max_length=200)
    password: str = Field(min_length=10, max_length=200)

class UserIn(BaseModel):
    username: str = Field(pattern=r'^[a-zA-Z0-9_.-]{3,80}$')
    password: str = Field(min_length=10, max_length=200)
    role: Literal['admin', 'operator', 'viewer']

class InstanceIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    agent_url: str = Field(max_length=500)
    profile: str = Field(pattern=r'^[a-zA-Z0-9_.-]{1,80}$')
    token: str = Field(min_length=32, max_length=500)
    allow_http: bool = False
    notes: str = Field(default='', max_length=2000)

    @model_validator(mode='after')
    def valid_url(self):
        u = urlsplit(self.agent_url)
        if not u.hostname or u.username or u.password or u.query or u.fragment or u.path not in ('', '/'):
            raise ValueError('Agent-URL muss eine Basis-URL ohne Zugangsdaten oder Pfad sein.')
        if u.scheme not in ('https', 'http') or (u.scheme == 'http' and not self.allow_http):
            raise ValueError('HTTPS erforderlich; HTTP nur nach ausdrücklicher Freigabe im privaten Netz.')
        self.agent_url = self.agent_url.rstrip('/')
        return self

class BackendServer(BaseModel):
    address: str = Field(min_length=1, max_length=253)
    port: int = Field(ge=1, le=65535, default=80)
    weight: int = Field(ge=1, le=256, default=100)
    tls: bool = False

    @field_validator('address')
    @classmethod
    def address_ok(cls, v):
        single(v)
        try:
            ipaddress.ip_address(v)
            return v
        except ValueError:
            if not re.fullmatch(r'[a-zA-Z0-9](?:[a-zA-Z0-9.-]*[a-zA-Z0-9])?', v):
                raise ValueError('Ungültige IP-Adresse oder Hostname.')
        return v

class Host(BaseModel):
    id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,40}$')
    domain: str = Field(max_length=253)
    path: str = Field(default='/', max_length=300)
    enabled: bool = True
    force_https: bool = False
    balance: Literal['roundrobin', 'leastconn', 'source'] = 'roundrobin'
    servers: list[BackendServer] = Field(min_length=1, max_length=30)

    @field_validator('domain')
    @classmethod
    def domain_ok(cls, v):
        v = v.lower().strip()
        if not DOMAIN.fullmatch(v):
            raise ValueError('Bitte einen gültigen Domainnamen eingeben.')
        return v

    @field_validator('path')
    @classmethod
    def path_ok(cls, v):
        single(v)
        if not v.startswith('/') or '{' in v or '}' in v:
            raise ValueError('Pfad muss mit / beginnen.')
        return v

class Rule(BaseModel):
    id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,40}$')
    name: str = Field(min_length=1, max_length=120)
    enabled: bool = True
    match: Literal['host', 'path_prefix', 'source_ip', 'method'] = 'path_prefix'
    value: str = Field(min_length=1, max_length=300)
    action: Literal['deny', 'redirect', 'set_header'] = 'deny'
    target: str = Field(default='', max_length=500)
    code: Literal[301, 302, 307, 308] = 301

    @model_validator(mode='after')
    def valid_rule(self):
        single(self.value)
        if self.match == 'source_ip':
            ipaddress.ip_network(self.value, strict=False)
        if self.match == 'method' and self.value not in ('GET','POST','PUT','DELETE','PATCH','OPTIONS','HEAD'):
            raise ValueError('Ungültige HTTP-Methode.')
        if self.match == 'host' and not DOMAIN.fullmatch(self.value.lower()):
            raise ValueError('Ungültiger Hostname.')
        if self.match == 'path_prefix' and not self.value.startswith('/'):
            raise ValueError('Pfad muss mit / beginnen.')
        if self.action == 'redirect':
            single(self.target)
            if urlsplit(self.target).scheme not in ('http','https') or not urlsplit(self.target).hostname:
                raise ValueError('Redirect-Ziel muss eine HTTP(S)-URL sein.')
        if self.action == 'set_header':
            if ':' not in self.target:
                raise ValueError('Header im Format Name: Wert eingeben.')
            name, value = self.target.split(':',1)
            if not re.fullmatch(r'[a-zA-Z0-9-]+',name) or not value.strip():
                raise ValueError('Ungültiger Header.')
            single(value.strip())
        return self

class Document(BaseModel):
    http_port: int = Field(default=80, ge=1, le=65535)
    https_port: int = Field(default=443, ge=1, le=65535)
    tls_enabled: bool = False
    acme_enabled: bool = False
    acme_address: str = '127.0.0.1'
    acme_port: int = Field(default=8888, ge=1, le=65535)
    maxconn: int = Field(default=4096, ge=100, le=1000000)
    hosts: list[Host] = Field(default_factory=list, max_length=200)
    rules: list[Rule] = Field(default_factory=list, max_length=200)
    version: int = 0

    @field_validator('acme_address')
    @classmethod
    def acme_ok(cls,v):
        return BackendServer(address=v).address

    @model_validator(mode='after')
    def unique_ids(self):
        for items in (self.hosts, self.rules):
            if len({x.id for x in items}) != len(items):
                raise ValueError('IDs müssen eindeutig sein.')
        if self.tls_enabled and self.http_port == self.https_port:
            raise ValueError('HTTP und HTTPS benötigen verschiedene Ports.')
        if not self.tls_enabled and any(h.enabled and h.force_https for h in self.hosts):
            raise ValueError('HTTPS-Weiterleitungen benötigen einen aktiven HTTPS-Listener.')
        return self

class DraftIn(BaseModel):
    config: str = Field(min_length=1, max_length=1024*1024)
    base_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
    message: str = Field(default='', max_length=500)

class CertificateIn(BaseModel):
    name: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,80}$')
    domains: list[str] = Field(min_length=1, max_length=30)
    email: str = Field(max_length=200)
    challenge: Literal['http', 'dns'] = 'http'
    provider: str = Field(default='', pattern=r'^[a-z0-9-]*$')
    staging: bool = True

    @model_validator(mode='after')
    def check(self):
        if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',self.email):
            raise ValueError('Gültige E-Mail-Adresse erforderlich.')
        self.domains = [d.lower().strip() for d in self.domains]
        if any(not DOMAIN.fullmatch(d) for d in self.domains):
            raise ValueError('Ungültige Domain.')
        if self.challenge == 'http' and any(d.startswith('*.') for d in self.domains):
            raise ValueError('Wildcard-Zertifikate benötigen DNS-Challenges.')
        if self.challenge == 'dns' and not self.provider:
            raise ValueError('DNS-Anbieter erforderlich.')
        return self
