"""Read-only installer checks before changing a running HAProxy instance."""
import json
import ssl
from datetime import datetime, timezone
from pathlib import Path

from cryptography import x509
from fastapi import HTTPException

from agent.config_bundle import read_bundle


def tls_options(setup):
    tls = setup.get('tls')
    if not tls:
        return ''
    cert, key = Path(tls['cert_file']), Path(tls['key_file'])
    try:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)
        certificate = x509.load_pem_x509_certificate(cert.read_bytes())
        now = datetime.now(timezone.utc)
        if not certificate.not_valid_before_utc <= now < certificate.not_valid_after_utc:
            raise ValueError('Zertifikat ist abgelaufen oder noch nicht gültig.')
        names = certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(x509.DNSName)
        hostname = tls['hostname'].lower()
        if not any(hostname == name.lower() or (name.startswith('*.') and hostname.split('.',1)[-1] == name[2:].lower() and hostname.count('.') == name.count('.')) for name in names):
            raise ValueError('Agent-Hostname fehlt im Zertifikat.')
        if any(c.isspace() or c in '%\"\'\\' for path in (str(cert), str(key)) for c in path):
            raise ValueError('TLS-Dateipfade ohne Leerzeichen oder systemd-Sonderzeichen verwenden.')
    except (OSError, ValueError, ssl.SSLError, x509.ExtensionNotFound) as error:
        raise SystemExit('Agent-TLS kann nicht eingerichtet werden: gültige PEM-Zertifikatskette und passenden Schlüssel auf dem Host bereitstellen; '+str(error)) from error
    return f' --ssl-certfile {cert} --ssl-keyfile {key}'


def docker_directories(p, run, sha):
    mounts = json.loads(run(['docker','inspect','--format','{{json .Mounts}}',p['container']]))
    def require(host_path, container_path, writable=False):
        # A nested file mount shadows the parent directory even when both exist.
        for mount in sorted(mounts,key=lambda m:len(m['Destination']),reverse=True):
            source, destination = Path(mount['Source']), Path(mount['Destination'])
            try: relative = Path(container_path).relative_to(destination)
            except ValueError: continue
            if source.is_dir() and source / relative == Path(host_path) and (not writable or mount.get('RW')):
                return
            break
        raise SystemExit(f'Docker-Verzeichnis-Mount fehlt oder wird von einem Datei-Mount überdeckt: {host_path} -> {container_path}. Compose wie im Assistenten vorbereiten, einzelne Datei-Mounts entfernen und Container neu erstellen; danach Befehl erneut ausführen.')
    require(p['config_path'],str(Path(p['container_config_dir'])/Path(p['config_path']).name))
    require(p['runtime_socket'],p['runtime_socket_config'],True)
    require(p['cert_dir'],p['cert_dir_config'])
    try: bundle = read_bundle(p,run,sha)
    except (OSError,ValueError,HTTPException) as error:
        raise SystemExit('Geladene Docker-Konfiguration nicht lesbar: '+str(getattr(error,'detail',error))) from error
    if not bundle['complete']:
        raise SystemExit('Geladene -f-Dateien nicht vollständig erkannt. Docker-Startbefehl oder config_sources im Agent-Profil prüfen.')
    for source in bundle['sources']:
        require(source['path'],source['container_path'])
    # Used for ProtectSystem=full when configuration lives under /etc or /usr.
    return {str(Path(source['path']).parent) for source in bundle['sources']}
