#!/usr/bin/env python3
"""Package Docker deployment and agent installation files without credentials."""
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

root = Path(__file__).resolve().parent.parent
output = root / 'downloads' / 'haproxy-management-docker.zip'
output.parent.mkdir(exist_ok=True)
files = {
    'docker-compose.yml': root / 'docker-compose.yml',
    '.env.example': root / '.env.example',
    'README.md': root / 'docs' / 'DOCKER.md',
    'docs/CERTIFICATES.md': root / 'docs' / 'CERTIFICATES.md',
    'docs/PROXIES.md': root / 'docs' / 'PROXIES.md',
    'docs/AGENT.md': root / 'docs' / 'AGENT.md',
    'docs/IMPORT.md': root / 'docs' / 'IMPORT.md',
    'docs/METRICS.md': root / 'docs' / 'METRICS.md',
    'docs/TOPOLOGY.md': root / 'docs' / 'TOPOLOGY.md',
    'docs/BASIC_AUTH.md': root / 'docs' / 'BASIC_AUTH.md',
    'docs/INFRASTRUCTURES.md': root / 'docs' / 'INFRASTRUCTURES.md',
    'docs/SERVERS.md': root / 'docs' / 'SERVERS.md',
    'requirements.txt': root / 'requirements.txt',
    'backend/__init__.py': root / 'backend' / '__init__.py',
    'backend/proxy_options.py': root / 'backend' / 'proxy_options.py',
    'backend/schemas.py': root / 'backend' / 'schemas.py',
    'backend/haproxy_config.py': root / 'backend' / 'haproxy_config.py',
    'backend/basic_auth.py': root / 'backend' / 'basic_auth.py',
    'backend/tls_bindings.py': root / 'backend' / 'tls_bindings.py',
}
for name in ('__init__.py', 'main.py', 'certificates.py', 'challenge.py', 'tls_bindings.py', 'config_bundle.py', 'config.example.json',
             'haproxy-control-agent.service', 'haproxy-control-webroot.service'):
    files[f'agent/{name}'] = root / 'agent' / name
with ZipFile(output, 'w', compression=ZIP_DEFLATED) as archive:
    for name, source in files.items():
        archive.write(source, f'haproxy-management/{name}')
print(output)
