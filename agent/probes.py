"""Reachability checks run on the HAProxy host.

``targets`` opens a TCP connection (and a TLS handshake for TLS targets) to backend
servers, exactly the path HAProxy itself will use. ``sites`` sends one request per
domain through the local HAProxy listener with the matching Host header and only
reports the status code. Neither check sends data from the management server to the
targets or returns response bodies.
"""

import http.client
import socket
import ssl
import time
from concurrent.futures import ThreadPoolExecutor

from pydantic import BaseModel, Field, field_validator

from backend.schemas import DOMAIN

HOST = r'^[a-zA-Z0-9.:_-]{1,253}$'


class Target(BaseModel):
    address: str = Field(pattern=HOST)
    port: int = Field(ge=1, le=65535)
    tls: bool = False


class TargetsIn(BaseModel):
    targets: list[Target] = Field(min_length=1, max_length=30)


class Site(BaseModel):
    domain: str = Field(max_length=253)
    path: str = Field(default='/', max_length=300, pattern=r'^/[^\s#]*$')
    port: int = Field(ge=1, le=65535)
    tls: bool = False

    @field_validator('domain')
    @classmethod
    def domain_ok(cls, value):
        value = value.lower()
        if value.startswith('*.') or not DOMAIN.fullmatch(value):
            raise ValueError('Gültige Domain ohne Platzhalter erforderlich.')
        return value


class SitesIn(BaseModel):
    sites: list[Site] = Field(min_length=1, max_length=50)


def _tls_context():
    # Reachability, not trust: certificates are checked by HAProxy and the TLS views.
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


def _error(error):
    if isinstance(error, socket.gaierror):
        return 'Name kann nicht aufgelöst werden'
    if isinstance(error, ConnectionRefusedError):
        return 'Verbindung abgelehnt'
    if isinstance(error, (TimeoutError, socket.timeout)):
        return 'Zeitüberschreitung'
    if isinstance(error, ssl.SSLError):
        return 'TLS-Handshake fehlgeschlagen'
    if isinstance(error, OSError) and error.strerror:
        return error.strerror
    return type(error).__name__


def check_target(target: Target, timeout=3.0):
    started = time.monotonic()
    try:
        with socket.create_connection((target.address.strip('[]'), target.port), timeout=timeout) as sock:
            if target.tls:
                server_name = (
                    None if target.address.replace('.', '').isdigit() or ':' in target.address else target.address
                )
                with _tls_context().wrap_socket(sock, server_hostname=server_name):
                    pass
        return {'ok': True, 'ms': round((time.monotonic() - started) * 1000)}
    except Exception as error:  # noqa: BLE001 - every failure is reported, never raised
        return {'ok': False, 'error': _error(error)}


def check_site(site: Site, timeout=5.0):
    started = time.monotonic()
    connection = http.client.HTTPConnection('127.0.0.1', site.port, timeout=timeout)
    try:
        if site.tls:
            # SNI selects the certificate exactly as for a real visitor.
            sock = socket.create_connection(('127.0.0.1', site.port), timeout=timeout)
            connection.sock = _tls_context().wrap_socket(sock, server_hostname=site.domain)
        connection.request('GET', site.path, headers={'Host': site.domain, 'User-Agent': 'HAProxy-Control-Check/1'})
        response = connection.getresponse()
        status = response.status
        location = response.getheader('Location') or ''
        return {'status': status, 'ms': round((time.monotonic() - started) * 1000), 'location': location[:300]}
    except Exception as error:  # noqa: BLE001
        return {'status': None, 'error': _error(error)}
    finally:
        connection.close()


def run(check, items):
    with ThreadPoolExecutor(max_workers=min(10, len(items))) as pool:
        return [item.model_dump() | result for item, result in zip(items, pool.map(check, items))]
