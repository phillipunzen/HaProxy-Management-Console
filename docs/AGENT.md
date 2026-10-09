# HAProxy-Server anbinden: Nativ und Docker

Die Management-WebUI spricht mit einem **Agenten auf dem HAProxy-Host**. Dieser Agent läuft als systemd-Dienst auf Linux, auch wenn HAProxy selbst in Docker läuft. Er prüft und schreibt die Konfiguration, steuert den Dienst bzw. Container und liest Statistiken aus dem Runtime-Socket. Der Management-Container braucht weder Root-Rechte noch den Docker-Socket. Der Agent braucht keine MariaDB-Verbindung.

## Installation über den WebUI-Assistenten

Unter **Server → Server verbinden** Nativ oder Docker wählen, Host-IP und Pfade angeben. Der Assistent erstellt einen individuellen Einzeiler mit Token, Agent-Adresse und SHA-256-Prüfsummen. Auf dem HAProxy-Host per SSH ausführen und anschließend im geöffneten Dialog **Verbindung prüfen & speichern** wählen. Der automatisch installierte Agent liegt unter `/opt/haproxy-control-agent`; seine Profile liegen wie bei der manuellen Installation unter `/etc/haproxy-control/agent.json`. Der Installer ergänzt bei Bedarf den Runtime-Socket, prüft vor dem HAProxy-Reload und stellt bei einem Fehler die alte Konfiguration wieder her. Bestehende Agent-Profile werden erhalten; ein vorhandenes Profil wird nicht durch einen neuen Token ersetzt.

Voraussetzungen: Debian/Ubuntu mit systemd, curl, sudo, eine laufende HAProxy-Instanz und Netzwerkzugriff auf die Management-Adresse sowie Paketquellen. Bei Docker benötigt der Container die im Assistenten gezeigten Verzeichnis-Mounts; der Installer verändert die Containerdefinition nicht. Den Agent-Port nur für den Management-Host freigeben. Cloudflare-Zugangsdaten anschließend wie unten beschrieben eintragen. Der Befehl enthält den Agent-Token und gehört nicht in öffentliche Tickets oder Protokolle.

Die folgenden Schritte erklären die **manuelle Alternative** und die Vorbereitung der HAProxy-Instanz. Bei einem bereits vorhandenen Agenten im Assistenten **Agent bereits installiert** wählen.

Beispiel: Management auf `192.168.10.70:8100`, HAProxy-Host auf `192.168.10.71`. Die IPs, Pfade, Dienst- und Containernamen an die eigene Installation anpassen. **Alle Installationsbefehle in Schritt 1–4 auf dem HAProxy-Host ausführen.**

## 1. Agent-Dateien und Python installieren

Voraussetzungen: Linux mit systemd, Python 3.11+, bereits installiertes HAProxy; für Docker außerdem die Docker CLI auf dem Host. Die folgenden Paketbefehle gelten für Debian/Ubuntu. HAProxy 3.0 und 3.2 wurden getestet.

Das Docker-ZIP enthält bereits `agent/`, `backend/schemas.py`, `requirements.txt` und diese Anleitung. Auf einem neuen HAProxy-Host das Paket nach `/opt` entpacken; dadurch entsteht `/opt/haproxy-management`. Auf einem Host mit vorhandenem Projekt dessen Verzeichnis verwenden. Alternativ aus GitHub beziehen:

```bash
sudo apt-get update
sudo apt-get install -y git python3-venv certbot python3-certbot-dns-cloudflare
sudo git clone https://github.com/phillipunzen/HaProxy-Management-Console.git /opt/haproxy-management
cd /opt/haproxy-management
sudo python3 -m venv .venv
sudo .venv/bin/pip install -r requirements.txt
sudo install -d -m 700 /etc/haproxy-control /var/lib/haproxy-control
```

Bei Verwendung des ZIP den `git clone`-Befehl überspringen. Node.js wird auf dem Agent-Host nicht benötigt.

## 2. Agent-Profil und Token erstellen

Pro HAProxy-Instanz genau ein Profil anlegen. Für einen nativen Dienst `PROFILE=native` setzen, für Docker `PROFILE=docker-edge`. Dieser Befehl erstellt **eine neue** Agent-Konfiguration aus der Vorlage und erzeugt einen zufälligen Token:

```bash
cd /opt/haproxy-management
PROFILE=native
# Für Docker stattdessen: PROFILE=docker-edge
sudo .venv/bin/python - "$PROFILE" <<'PY'
import json, os, secrets, sys
from pathlib import Path
name = sys.argv[1]
target = Path('/etc/haproxy-control/agent.json')
if target.exists():
    raise SystemExit('agent.json existiert bereits: vorhandene Profile erhalten und manuell ergänzen.')
profile = json.loads(Path('agent/config.example.json').read_text())['profiles'][name]
profile['token'] = secrets.token_urlsafe(48)
# Private Datei mit O_EXCL anlegen; bestehende Konfiguration nie überschreiben.
with os.fdopen(os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as handle:
    json.dump({'profiles': {name: profile}}, handle, indent=2)
print('Profil:', name)
print('Agent-Token:', profile['token'])
PY
sudo nano /etc/haproxy-control/agent.json
```

Den ausgegebenen **Agent-Token** für Schritt 5 aufbewahren. Er ist unabhängig von Login-Passwort, `ENCRYPTION_KEY` und `SESSION_SECRET`; Agent-Tokens benötigen weiterhin mindestens 32 Zeichen. Einen eigenen Token pro Profil verwenden. Bei vorhandener `agent.json` Profile manuell ergänzen und neue Tokens mit `python3 -c 'import secrets; print(secrets.token_urlsafe(48))'` erzeugen. Die WebUI bietet unter **Server → Profilvorlage mit neuen Tokens** ebenfalls eine Vorlage an; nur benötigte Profile übernehmen.

In der Konfiguration die Werte für die gewählte Installation prüfen:

| Agent-Feld | Nativ (`native`) | Docker (`docker-edge`) |
| --- | --- | --- |
| `kind` | `native` | `docker` |
| `service` / `container` | systemd-Dienst `haproxy` | Containername `haproxy` |
| `config_path` | `/etc/haproxy/haproxy.cfg` | Host-Pfad `/opt/haproxy/config/haproxy.cfg` |
| `container_config_dir` | Nicht erforderlich | `/usr/local/etc/haproxy` im Container |
| `runtime_socket` | `/run/haproxy/admin.sock` | Host-Pfad `/opt/haproxy/run/admin.sock` |
| `runtime_socket_config` | `/run/haproxy/admin.sock` | `/run/haproxy/admin.sock` im Container |
| `cert_dir` | `/etc/haproxy/certs` | Host-Pfad `/opt/haproxy/certs` |
| `cert_dir_config` | `/etc/haproxy/certs` | `/etc/haproxy/certs` im Container |

`config_path`, `runtime_socket` und `cert_dir` sind immer aus Sicht des **Agent-Hosts**. Die Felder mit `_config` werden in die HAProxy-Konfiguration geschrieben und müssen aus Sicht des **HAProxy-Prozesses** stimmen. Ein Agent kann mehrere Profile im Objekt `profiles` verwalten. Pro Konfigurationsdatei und Socket nur ein Profil anlegen.

## 3a. HAProxy nativ vorbereiten

Diesen Abschnitt für `kind=native` verwenden. Dienstnamen und Binary-Pfad gegebenenfalls im Profil anpassen:

```bash
sudo systemctl status haproxy
sudo systemctl cat haproxy
sudo install -d -m 755 /run/haproxy /etc/haproxy/certs
sudo cp -a /etc/haproxy/haproxy.cfg /etc/haproxy/haproxy.cfg.before-management
sudo nano /etc/haproxy/haproxy.cfg
```

Im vorhandenen `global`-Abschnitt den Runtime-Socket ergänzen bzw. einen bereits vorhandenen Socket mit dem Agent-Profil abgleichen. Nicht den gesamten Abschnitt oder bestehende Frontends/Backends ersetzen:

```haproxy
global
    stats socket /run/haproxy/admin.sock mode 660 level admin
```

Die bisherige Konfiguration bleibt erhalten. Der systemd-Dienst muss im Master-Worker-Modus laufen und Reload unterstützen; übliche Distributionseinheiten verwenden `-Ws`. Vor dem Reload prüfen:

```bash
sudo /usr/sbin/haproxy -c -f /etc/haproxy/haproxy.cfg
sudo systemctl reload haproxy
sudo ls -l /run/haproxy/admin.sock
```

Für eigene Units Master-Worker-Modus und `ExecReload` gemäß der HAProxy-Installation einrichten. Bei chroot und abweichenden Socket-Pfaden den tatsächlichen Host-Pfad im Agent-Profil verwenden. Zertifikate müssen vom HAProxy-Prozess beim Start lesbar sein; `cert_uid`, `cert_gid`, `cert_mode` entsprechend anpassen. Die native Vorlage verwendet root und 0600.

Danach mit Schritt 4 fortfahren.

## 3b. HAProxy in Docker vorbereiten

Diesen Abschnitt für `kind=docker` verwenden. Der Agent bleibt auf dem **Docker-Host**; dort muss `sudo docker ps` funktionieren. Für bestehende Container zuerst Namen und Mounts prüfen:

```bash
sudo docker ps --format 'table {{.Names}}\t{{.Image}}'
sudo docker inspect haproxy --format '{{json .Mounts}}'
sudo docker exec haproxy id
```

**Das Konfigurationsverzeichnis mounten, keine einzelne `haproxy.cfg`.** Der Agent tauscht Dateien atomar aus; ein einzelner Datei-Mount würde am alten Dateiinhalt hängen bleiben. Auch das Runtime-Verzeichnis muss mit dem Host geteilt sein.

Beispiel für das getestete offizielle Image; bei bestehenden Installationen die eigene Compose-Datei anpassen und die bisherige Konfiguration erhalten:

```yaml
services:
  haproxy:
    image: haproxy:3.2.25
    restart: unless-stopped
    container_name: haproxy
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - /opt/haproxy/config:/usr/local/etc/haproxy:ro
      - /opt/haproxy/run:/run/haproxy
      - /opt/haproxy/certs:/etc/haproxy/certs:ro
    sysctls:
      net.ipv4.ip_unprivileged_port_start: 0
```

Für dieses Image die Host-Verzeichnisse anlegen; der Containerbenutzer verwendet UID/GID 99. Bei eigenen Images die mit `id` ermittelten Werte verwenden und auch `cert_uid`/`cert_gid` im Agent-Profil ändern:

```bash
sudo install -d -m 755 /opt/haproxy/config /opt/haproxy/certs
sudo install -d -m 750 -o 99 -g 99 /opt/haproxy/run
```

Die bestehende Konfiguration nach `/opt/haproxy/config/haproxy.cfg` übernehmen und für den Containerbenutzer lesbar machen. Im `global`-Abschnitt denselben Socket wie im Container-Pfad des Profils einstellen:

```haproxy
global
    stats socket /run/haproxy/admin.sock mode 660 level admin
```

Für eine **neue Testinstanz ohne vorhandene Konfiguration** genügt zum Verbindungsaufbau folgende vollständige Datei; produktive Frontends/Backends danach passend konfigurieren:

```haproxy
global
    stats socket /run/haproxy/admin.sock mode 660 level admin

defaults
    mode http
    timeout connect 5s
    timeout client 30s
    timeout server 30s

frontend healthcheck
    bind :80
    http-request return status 200 content-type text/plain string HAProxy-ready
```

Bei einer neuen Instanz die gezeigte Compose-Datei als `/opt/haproxy/docker-compose.yml` speichern, die Konfiguration bereitstellen und starten. Bei bestehenden Instanzen den tatsächlichen Pfad der eigenen Compose-Datei verwenden:

```bash
sudo chmod 644 /opt/haproxy/config/haproxy.cfg
sudo docker compose -f /opt/haproxy/docker-compose.yml up -d
sudo docker exec haproxy haproxy -c -f /usr/local/etc/haproxy/haproxy.cfg
sudo ls -l /opt/haproxy/run/admin.sock
```

Mount-Änderungen erfordern eine Neuerstellung des Containers und können den Proxy kurz unterbrechen. Der offizielle Entrypoint startet HAProxy im Master-Worker-Modus; das Profil verwendet `reload_signal=USR2`. Bei eigenen Images das passende Signal und den Startmodus prüfen. `cert_mode` ist dezimal: 384 = 0600, 416 = 0640; die Docker-Vorlage verwendet 0640.

## 4. Agent-Adresse freigeben und Dienst starten

Die mitgelieferte Agent-Unit bindet zunächst nur an `127.0.0.1:9101`. Für die Verbindung aus dem Management-Container eine erreichbare **private Host-IP** verwenden. Folgendes Beispiel bindet auf `192.168.10.71`:

```bash
cd /opt/haproxy-management
sudo install -m 644 agent/haproxy-control-agent.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl edit haproxy-control-agent
```

Diesen Inhalt als systemd-Override eintragen; IP anpassen:

```ini
[Service]
ExecStart=
ExecStart=/opt/haproxy-management/.venv/bin/uvicorn agent.main:app --host 192.168.10.71 --port 9101 --workers 1 --no-proxy-headers --no-access-log
```

Der Agent läuft als root, damit er systemd, Docker, Konfigurationsdateien und Zertifikate verwalten kann. Ein Profil-Token erlaubt diese Verwaltung. Den Agent-Port über die bestehende Firewall ausschließlich für den Management-Host freigeben; bei Docker-NAT ist das normalerweise dessen Host-IP. HTTP nur im privaten Netz verwenden. Für HTTPS einen Reverse Proxy davor setzen oder Uvicorn mit `--ssl-certfile` und `--ssl-keyfile` starten; die Management-Anwendung prüft TLS-Zertifikate und benötigt für interne CAs ein vertrauenswürdiges CA-Bundle.

Beispiel für eine **bereits aktive UFW-Firewall**, IPs anpassen:

```bash
sudo ufw allow from 192.168.10.70 to 192.168.10.71 port 9101 proto tcp
```

Bei anderen Firewalls die entsprechende Regel verwenden. Für den Agenten ist keine Router-Portweiterleitung ins Internet erforderlich.

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now haproxy-control-agent
sudo systemctl status haproxy-control-agent
sudo ss -ltnp | grep ':9101'
```

Bei Änderungen an `agent.json` den Agenten mit `sudo systemctl restart haproxy-control-agent` neu laden.

## 5. In der WebUI verbinden

Als Administrator anmelden und **Server → Server verbinden** wählen:

| Feld | Beispiel Nativ | Beispiel Docker |
| --- | --- | --- |
| Servername | `HAProxy Nativ` | `HAProxy Docker` |
| Agent-Adresse | `http://192.168.10.71:9101` | `http://192.168.10.71:9101` |
| Profilname | `native` | `docker-edge` |
| Agent-Token | Wert aus `profiles.native.token` | Wert aus `profiles.docker-edge.token` |
| HTTP-Verbindung im privaten Netz zulassen | Aktivieren für dieses HTTP-Beispiel | Aktivieren für dieses HTTP-Beispiel |

**Verbindung prüfen & speichern** klicken. Keine HAProxy-Portnummer wie 80/443 und keinen Pfad `/profiles/…` in die Agent-Adresse eintragen. `kind` wird aus dem Profil automatisch erkannt. Laufen mehrere Instanzen auf demselben Host, können sie dieselbe Agent-Adresse mit unterschiedlichen Profilnamen und Tokens verwenden.

Anschließend die Instanz auswählen: **Statistiken** zeigt die Runtime-Daten, **Konfiguration** lädt die vorhandene HAProxy-Datei. Diese zuerst lesen, bevor im grafischen Editor eine neue vollständige Konfiguration erzeugt wird.

## Verbindung prüfen und Fehler beheben

Auf dem Management-Host die Erreichbarkeit testen (ersetzt keine Profil-Authentifizierung):

```bash
curl -i http://192.168.10.71:9101/profiles/native
# Ohne Token ist HTTP 401 die erwartete Antwort eines erreichbaren Agenten.
# Für Docker den Pfad /profiles/docker-edge verwenden.
```

Um genau das Netzwerk des Management-Containers zu prüfen, aus dem Verzeichnis seiner Compose-Datei:

```bash
docker compose -f docker-compose.yml exec app python -c 'import httpx; r=httpx.get("http://192.168.10.71:9101/profiles/native", timeout=5); print(r.status_code)'
```

| Symptom | Prüfen |
| --- | --- |
| Verbindung abgelehnt / Timeout | Agent läuft, private IP stimmt, Port 9101 freigegeben, Unit bindet nicht nur an localhost. |
| 401 / Agent-Zugriff verweigert | Profilname und Token stimmen exakt mit `agent.json` überein; Agent nach Änderungen neu gestartet. |
| TLS-Fehler | Hostname/IP im Zertifikat und vertrauenswürdiges CA-Bundle im Management-Container. |
| Profil verbunden, Statistiken offline | HAProxy läuft; `stats socket` vorhanden; Host-Pfad und Mount des Runtime-Sockets stimmen. |
| Docker-Konfigurationsprüfung schlägt fehl | Containername, Verzeichnis-Mount, `container_config_dir` und Leserechte prüfen. |
| Reload meldet keinen neuen Worker | Master-Worker-Modus, systemd-Reload bzw. Docker-Signal und Runtime-Socket prüfen. |

Agent-Logs: `sudo journalctl -u haproxy-control-agent -n 100 --no-pager`. Management-Logs: `docker compose -f docker-compose.yml logs --tail 100 app`.

## Cloudflare DNS-01

Cloudflare API-Token mit **Zone:DNS:Edit** und **Zone:Zone:Read**, auf die benötigten Zonen beschränkt. Ein Token kann mehrere Zonen erlauben; alternativ unterschiedliche Agent-Profile/Zugangsdaten verwenden.

```ini
# /etc/haproxy-control/cloudflare.ini
dns_cloudflare_api_token = DEIN_CLOUDFLARE_API_TOKEN
```

```bash
sudo chmod 600 /etc/haproxy-control/cloudflare.ini
```

Im Profil:

```json
"dns_providers": {
  "cloudflare": {
    "credentials_file": "/etc/haproxy-control/cloudflare.ini",
    "propagation_seconds": 60
  }
}
```

Der Pluginname `dns-cloudflare` muss in `certbot plugins` verfügbar sein. In der WebUI Domains einzeln oder gemeinsam eingeben, z. B. `example.com`, `*.example.com`, `example.net`. Alle Domains müssen durch das Token und Cloudflare validierbar sein. Wildcards benötigen DNS-01.

Zunächst Staging testen; Staging-Zertifikate liegen in `.staging/` und werden nicht für produktive TLS-Listener genutzt. Für Produktion Staging deaktivieren. Certbot erzeugt eine eigene Lineage je Profil, Zertifikatsname und Umgebung. Ein Produktionszertifikat wird nach Schlüsselprüfung installiert und HAProxy validiert und neu geladen. Die Aktivierung muss erfolgreich sein, damit die Anforderung als erfolgreich gilt.

## HTTP-01

Profilfeld `acme_webroot`, z. B. `/var/lib/haproxy-control/webroot`, setzen. Webroot-Dienst mit der bereitgestellten Unit starten:

```bash
sudo mkdir -p /var/lib/haproxy-control/webroot
sudo install -m 644 agent/haproxy-control-webroot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now haproxy-control-webroot
```

Diese Unit bindet auf 127.0.0.1:8888. Im grafischen Editor **Listener → HTTP-01** aktivieren, den aus Sicht des HAProxy-Prozesses erreichbaren Webroot-Host setzen und Konfiguration anwenden. Bei Docker die private Host-IP verwenden und den Webroot-Dienst gezielt an diese Adresse binden; localhost im Container ist nicht der Host. Nur Challenge-Pfade öffentlich weiterleiten. Port 80 muss von Let's Encrypt erreichbar sein, auch wenn andere Anfragen zu HTTPS umgeleitet werden. A-/AAAA-Records müssen auf den richtigen Proxy zeigen. Bei mehreren HAProxy-Knoten muss jede Challenge-Anfrage den Webroot erreichen, auf dem Certbot den Token schreibt.

## Erneuerung und Sicherungen

Der Agent prüft verwaltete Produktionszertifikate standardmäßig 60 Sekunden nach Start und danach alle 12 Stunden. Unter **Zertifikate → Zeitplan** lassen sich Intervall, tägliche Uhrzeit, Zeitzone und Aktivierung einstellen. Pro Zertifikat kann die Automatik pausiert werden. Einzelprüfung, Sammelprüfung und sofortige Erneuerung sind im Tool verfügbar. Certbot und **LEGO v5** werden unterstützt; vorhandene LEGO-Aufträge lassen sich übernehmen. Nur geänderte Zertifikate lösen einen geprüften HAProxy-Reload aus. Fehler und Zeitplan werden im Tool angezeigt; ausführliche Logs stehen in `journalctl -u haproxy-control-agent`. Importierte PEM-Dateien haben keine automatische Erneuerung. [Vollständige Einrichtung, LEGO-Profil und Cron-Übernahme](CERTIFICATES.md).

Agent-State unter `/var/lib/haproxy-control` enthält Zertifikat-Zuordnungen, Locks und lokale Konfigurationssicherungen. Vor jedem Config-Wechsel wird gesichert; bei Reload-Fehlern wird die alte Datei wiederhergestellt und erneut geladen. Externe Änderungen werden durch SHA-256-Vergleich erkannt und nicht überschrieben. Bei einer unterbrochenen Netzwerkverbindung kann der Status unklar sein; aktive Serverkonfiguration kontrollieren und einen neuen Entwurf erstellen. Alte Backups können nach eigenem Aufbewahrungsplan entfernt werden.

## Hinweise für mehrere Server

Zertifikate werden auf dem jeweiligen Agent-Host ausgestellt und gespeichert. Es gibt keinen automatischen Zertifikatsschlüssel-Transfer zwischen unabhängigen Agent-Hosts. Für denselben Hostnamen auf mehreren HAProxy-Servern entweder auf jedem Host ausstellen oder Zertifikate gezielt per PEM importieren. DNS-01 ist meist einfacher für HA-Setups. Agent-Profile für Container oder Dienste, die eine gemeinsame Konfigurationsdatei verwenden, sind nicht unterstützt: pro Datei und Socket genau ein Profil anlegen.

Referenzen: [HAProxy Runtime API](https://www.haproxy.com/documentation/haproxy-runtime-api/), [offizieller Docker-Entrypoint für HAProxy 3.2](https://github.com/docker-library/haproxy/blob/master/3.2/docker-entrypoint.sh), [Certbot-Benutzerhandbuch](https://eff-certbot.readthedocs.io/en/stable/using.html), [Cloudflare-Plugin](https://certbot-dns-cloudflare.readthedocs.io/en/stable/).

## Bestehende Dateien und Agent-Updates

Die WebUI bietet unter **Server → Agent aktualisieren** einen checksum-geprüften Aktualisierungsbefehl für bestehende systemd-Agenten. Tokens und Profile bleiben erhalten. Für den Config-Import müssen `agent/config_bundle.py` und `backend/haproxy_config.py` neben den bisherigen Dateien vorhanden sein.

Mehrere `-f`-Dateien und Verzeichnisse erkennt der Agent automatisch aus dem laufenden Dienst bzw. Docker-Containerbefehl. Für eigene Entrypoints oder relative Pfade `config_sources` mit sämtlichen Host-Pfaden in Ladereihenfolge im Profil setzen. Native Maps außerhalb der Konfigurationsverzeichnisse über `map_dirs` freigeben. Nach Profiländerungen den Agenten neu starten. Details, Einlesevorschau, Zusammenführung mehrerer Dateien und Wiederherstellung stehen in [IMPORT.md](IMPORT.md).

## Mehrere Infrastrukturen und Zertifikatsverzeichnisse

In der WebUI lässt sich jedes Profil einer Infrastruktur zuordnen. Für mehrere Profile auf demselben Host separate Konfigurationsdateien, Runtime-Sockets und Zertifikatsverzeichnisse verwenden. Der aktuelle Installer lehnt gemeinsam genutzte oder ineinander liegende `cert_dir`-Pfade ab. Der Agent meldet vorhandene gemeinsame Verzeichnisse in seinen Capabilities und blockiert dort Zertifikatsschreibzugriffe einschließlich automatischer Erneuerung. Für unabhängige Cloudflare-Konten je Profil eigene Credential-Dateien konfigurieren. [Vollständige Anleitung](INFRASTRUCTURES.md). Bestehende Agenten über **Server → Agent aktualisieren** aktualisieren.
