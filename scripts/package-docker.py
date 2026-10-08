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
    'docs/AGENT.md': root / 'docs' / 'AGENT.md',
    'docs/IMPORT.md': root / 'docs' / 'IMPORT.md',
    'requirements.txt': root / 'requirements.txt',
    'backend/__init__.py': root / 'backend' / '__init__.py',
    'backend/schemas.py': root / 'backend' / 'schemas.py',
    'backend/haproxy_config.py': root / 'backend' / 'haproxy_config.py',
}
for name in ('__init__.py', 'main.py', 'config_bundle.py', 'config.example.json',
             'haproxy-control-agent.service', 'haproxy-control-webroot.service'):
    files[f'agent/{name}'] = root / 'agent' / name
with ZipFile(output, 'w', compression=ZIP_DEFLATED) as archive:
    for name, source in files.items():
        archive.write(source, f'haproxy-management/{name}')
print(output)
