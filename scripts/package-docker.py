#!/usr/bin/env python3
"""Create a minimal Docker deployment ZIP without local credentials."""
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

root = Path(__file__).resolve().parent.parent
output = root / 'downloads' / 'haproxy-management-docker.zip'
output.parent.mkdir(exist_ok=True)
files = {
    'docker-compose.yml': root / 'docker-compose.yml',
    '.env.example': root / '.env.example',
    'README.md': root / 'docs' / 'DOCKER.md',
}
with ZipFile(output, 'w', compression=ZIP_DEFLATED) as archive:
    for name, source in files.items():
        archive.write(source, f'haproxy-management/{name}')
print(output)
