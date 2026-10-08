#!/usr/bin/env bash
# Called by the management UI with validated setup data and a package checksum.
set -euo pipefail
if [[ ${EUID} -ne 0 ]]; then
  echo 'Bitte den Befehl mit sudo ausführen.' >&2
  exit 1
fi
if [[ ${HAPROXY_AGENT_UPDATE_ONLY:-0} != 1 ]]; then
  : "${HAPROXY_AGENT_SETUP_B64:?Setup-Daten fehlen}"
fi
: "${HAPROXY_SOURCE_URL:?Management-Adresse fehlt}"
: "${HAPROXY_PACKAGE_SHA256:?Paket-Prüfsumme fehlt}"
command -v apt-get >/dev/null || { echo 'Der Installer unterstützt Debian/Ubuntu mit systemd.' >&2; exit 1; }
command -v systemctl >/dev/null || { echo 'systemd wird benötigt.' >&2; exit 1; }
haproxy_install_dir=/opt/haproxy-control-agent
if [[ ${HAPROXY_AGENT_UPDATE_ONLY:-0} == 1 ]]; then
  haproxy_install_dir=$(systemctl show --property=WorkingDirectory --value haproxy-control-agent)
  case "$haproxy_install_dir" in
    /opt/haproxy-control-agent|/opt/haproxy-management) ;;
    *) echo 'Vorhandener haproxy-control-agent.service fehlt oder nutzt ein anderes Arbeitsverzeichnis. Agent manuell nach Anleitung aktualisieren.' >&2; exit 1 ;;
  esac
  [[ -f /etc/haproxy-control/agent.json ]] || { echo 'Vorhandene Agent-Konfiguration fehlt.' >&2; exit 1; }
fi
haproxy_install_temp=$(mktemp -d)
trap 'rm -rf "$haproxy_install_temp"' EXIT
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y python3-venv curl certbot python3-certbot-dns-cloudflare
curl -fsSL "${HAPROXY_SOURCE_URL%/}/api/agent-package" -o "$haproxy_install_temp/package.zip"
printf '%s  %s\n' "$HAPROXY_PACKAGE_SHA256" "$haproxy_install_temp/package.zip" | sha256sum -c -
install -d -m 755 "$haproxy_install_dir"
python3 - "$haproxy_install_temp/package.zip" "$haproxy_install_dir" <<'PY'
import sys
from pathlib import Path
from zipfile import ZipFile
allowed = ['requirements.txt', 'backend/__init__.py', 'backend/schemas.py', 'backend/haproxy_config.py',
           'agent/__init__.py', 'agent/main.py', 'agent/config_bundle.py', 'agent/config.example.json']
with ZipFile(sys.argv[1]) as archive:
    for name in allowed:
        target = Path(sys.argv[2]) / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(archive.read('haproxy-management/' + name))
        target.chmod(0o644)
PY
python3 -m venv "$haproxy_install_dir/.venv"
"$haproxy_install_dir/.venv/bin/pip" install -r "$haproxy_install_dir/requirements.txt"
if [[ ${HAPROXY_AGENT_UPDATE_ONLY:-0} == 1 ]]; then
  systemctl restart haproxy-control-agent
  systemctl is-active haproxy-control-agent
  echo 'Agent aktualisiert. Profile, Tokens und HAProxy-Konfigurationen wurden beibehalten. In der WebUI erneut einlesen.'
  exit 0
fi
cd "$haproxy_install_dir"
.venv/bin/python - <<'PY'
import base64, json, os, re, shutil, socket, subprocess, tempfile, time
from pathlib import Path
from agent import main as agent

setup = json.loads(base64.b64decode(os.environ['HAPROXY_AGENT_SETUP_B64'], validate=True))
name, p = setup['profile_name'], setup['profile']
config_file = Path('/etc/haproxy-control/agent.json')
if not re.fullmatch(r'[a-zA-Z0-9_.-]{1,80}', name):
    raise SystemExit('Ungültiger Profilname.')
if config_file.exists():
    existing = json.loads(config_file.read_text())
else:
    existing = {'profiles': {}}
if name in existing['profiles']:
    raise SystemExit('Profil existiert bereits. Vorhandenen Agenten in der WebUI manuell verbinden; kein Token wird ersetzt.')
for current in existing['profiles'].values():
    if current['config_path'] == p['config_path'] or current['runtime_socket'] == p['runtime_socket']:
        raise SystemExit('Diese HAProxy-Datei bzw. dieser Socket wird bereits von einem Agent-Profil verwaltet.')

if p['kind'] == 'docker':
    mounts = json.loads(agent.run(['docker', 'inspect', '--format', '{{json .Mounts}}', p['container']]))
    def mounted(host_path, container_path, writable=False):
        for mount in mounts:
            source, dest = Path(mount['Source']), Path(mount['Destination'])
            if not source.is_dir():
                continue
            try:
                relative = Path(host_path).relative_to(source)
            except ValueError:
                continue
            if dest / relative == Path(container_path) and (not writable or mount.get('RW')):
                return True
        return False
    container_config = str(Path(p['container_config_dir']) / Path(p['config_path']).name)
    required = [(p['config_path'], container_config, False),
                (p['runtime_socket'], p['runtime_socket_config'], True),
                (p['cert_dir'], p['cert_dir_config'], False)]
    for source, destination, writable in required:
        if not mounted(source, destination, writable):
            raise SystemExit(f'Docker-Verzeichnis-Mount fehlt: {source} -> {destination}. Compose-Datei wie im Assistenten angezeigt anpassen und Container neu erstellen; danach Befehl erneut ausführen.')
    p['cert_uid'] = int(agent.run(['docker', 'exec', p['container'], 'id', '-u']))
    p['cert_gid'] = int(agent.run(['docker', 'exec', p['container'], 'id', '-g']))
else:
    agent.run(['systemctl', 'is-active', p['service']])

haproxy_config = Path(p['config_path'])
if not haproxy_config.is_file():
    raise SystemExit(f'HAProxy-Konfiguration fehlt: {haproxy_config}. Der Installer benötigt eine vorhandene Instanz.')
cert_dir = Path(p['cert_dir']); cert_dir.mkdir(parents=True, exist_ok=True)
runtime_dir = Path(p['runtime_socket']).parent; runtime_dir.mkdir(parents=True, exist_ok=True)
if p['kind'] == 'docker':
    os.chown(runtime_dir, p['cert_uid'], p['cert_gid'])
    os.chmod(runtime_dir, 0o750)
old = haproxy_config.read_text()
if not re.search(r'^\s*stats\s+socket\s+' + re.escape(p['runtime_socket_config']) + r'(?:\s|$)', old, re.M):
    socket_line = '    stats socket ' + p['runtime_socket_config'] + ' mode 660 level admin\n'
    if re.search(r'^global\s*(?:#.*)?$', old, re.M):
        new = re.sub(r'^(global\s*(?:#.*)?\n)', lambda m: m.group(1) + socket_line, old, count=1, flags=re.M)
    else:
        new = 'global\n' + socket_line + '\n' + old
    agent.validate(p, new)
    backup = haproxy_config.with_name(haproxy_config.name + '.before-agent-' + str(time.time_ns()))
    shutil.copy2(haproxy_config, backup)
    attributes = haproxy_config.stat()
    def write(content):
        agent.atomic(haproxy_config, content.encode(), attributes.st_mode & 0o777, attributes.st_uid, attributes.st_gid)
    def reload():
        if p['kind'] == 'docker':
            agent.run(['docker', 'kill', '--signal', p.get('reload_signal', 'USR2'), p['container']])
        else:
            agent.run(['systemctl', 'reload', p['service']])
    try:
        write(new); reload()
        for attempt in range(60):
            try:
                if agent.info(p).get('Pid'):
                    break
            except OSError:
                pass
            time.sleep(.25)
        else:
            raise RuntimeError('Runtime-Socket nach Reload nicht erreichbar.')
    except Exception:
        write(old)
        try:
            reload()
        finally:
            print('Vorherige HAProxy-Konfiguration wiederhergestellt; Dienststatus prüfen.')
        raise
else:
    agent.validate(p, old)
    agent.info(p)

config_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
Path('/var/lib/haproxy-control').mkdir(parents=True, exist_ok=True, mode=0o700)
existing['profiles'][name] = p
agent.atomic(config_file, json.dumps(existing, indent=2).encode(), 0o600, 0, 0)
unit = f'''[Unit]
Description=HAProxy Control Agent
After=network-online.target
Wants=network-online.target
[Service]
Type=simple
WorkingDirectory=/opt/haproxy-control-agent
Environment=AGENT_CONFIG=/etc/haproxy-control/agent.json
Environment=AGENT_STATE_DIR=/var/lib/haproxy-control
ExecStart=/opt/haproxy-control-agent/.venv/bin/uvicorn agent.main:app --host {setup['bind']} --port {setup['port']} --workers 1 --no-proxy-headers --no-access-log
Restart=on-failure
RestartSec=5
UMask=0077
PrivateTmp=true
ProtectHome=true
ProtectSystem=full
ReadWritePaths=-/etc/haproxy -/etc/letsencrypt
[Install]
WantedBy=multi-user.target
'''
target = Path('/etc/systemd/system/haproxy-control-agent.service')
if target.exists():
    shutil.copy2(target, target.with_name(target.name + '.before-setup-' + str(time.time_ns())))
agent.atomic(target, unit.encode(), 0o644, 0, 0)
override = Path('/etc/systemd/system/haproxy-control-agent.service.d/99-management-setup.conf')
override.parent.mkdir(parents=True, exist_ok=True)
agent.atomic(override, ('[Service]\nExecStart=\nExecStart=' + unit.split('ExecStart=', 1)[1].split('\n', 1)[0] + '\n').encode(), 0o644, 0, 0)
print('Agent-Profil eingerichtet. Cloudflare-Zugangsdaten bei Bedarf in /etc/haproxy-control/cloudflare.ini hinterlegen (chmod 600).')
PY
systemctl daemon-reload
systemctl enable haproxy-control-agent
systemctl restart haproxy-control-agent
systemctl is-active haproxy-control-agent
echo 'Agent installiert. Port für den Management-Host in der Firewall freigeben und in der WebUI Verbindung prüfen & speichern wählen.'
