# HAProxy-Server anbinden: Nativ und Docker

Die Management-WebUI spricht mit einem **Agenten auf dem HAProxy-Host**. Dieser Agent läuft als systemd-Dienst auf Linux, auch wenn HAProxy selbst in Docker läuft. Er prüft und schreibt die Konfiguration, steuert den Dienst bzw. Container und liest Statistiken aus dem Runtime-Socket. Der Management-Container braucht weder Root-Rechte noch den Docker-Socket. Der Agent braucht keine MariaDB-Verbindung.

## Installation über den WebUI-Assistenten

Unter **Server → Server verbinden** Nativ oder Docker sowie **LAN** oder **Internet / andere Infrastruktur** wählen. Für einen Internet-Host bietet der Assistent **VPN**, **direktes HTTPS** oder **HTTPS über vorhandenen Reverseproxy**. Der Management-Server verbindet sich zum Agenten; die Management-WebUI muss dafür nicht veröffentlicht werden.

Die vier Schritte führen durch Installation, Host-Pfade, Vorbereitung/Download und den persönlichen Einzeiler mit Agent-Token und SHA-256-Prüfsummen. Bei Docker mit einer vorhandenen Compose-Datei die Dateistruktur **Compose-Ordner mit haproxy.cfg, certs, vhosts und dnsmapping.cfg** auswählen und z. B. `/srv/haproxy` angeben. Dann werden die Pfade, Mounts und Vorbereitung passend angezeigt. Anschließend den Installationsbefehl auf dem HAProxy-Host per SSH ausführen und im geöffneten Dialog **Verbindung prüfen & speichern** wählen.

Der Agent liegt unter `/opt/haproxy-control-agent`; Profile liegen unter `/etc/haproxy-control/agent.json`. Voraussetzungen: Debian/Ubuntu mit systemd (auch Debian 13), curl, sudo, eine laufende HAProxy-Instanz und Zugriff auf Download-Adresse sowie Paketquellen. Bei Docker müssen die angezeigten Verzeichnis-Mounts zuvor vorbereitet sein; der Installer verändert die Compose-Datei nicht. Bestehende Agent-Profile und Tokens bleiben erhalten; weitere Profile müssen denselben Agent-Endpunkt verwenden. Der Befehl enthält den Agent-Token und gehört nicht in öffentliche Tickets oder Protokolle.

Der Installer prüft auch die tatsächlich geladenen zusätzlichen `-f`-Dateien. Diese müssen ebenfalls über Verzeichnis-Mounts erreichbar sein, damit eine spätere Migration atomar schreiben und wiederherstellen kann. Bei HTTPS prüft er zunächst Zertifikat, passenden Schlüssel, Laufzeit und Agent-Hostname. Danach ergänzt er bei Bedarf den Runtime-Socket, prüft die komplette Konfiguration und lädt HAProxy neu. Bei einem Fehler wird die alte Konfiguration wiederhergestellt. Cloudflare- oder Hetzner-Token für HAProxy-Zertifikate anschließend im Zertifikatsdialog hinterlegen; LEGO wird mit installiert.

## Begonnene Einrichtung nach einem Management-Neustart fortsetzen

Sobald der Assistent den Installationsbefehl erzeugt, speichert er die begonnene Einrichtung samt Agent-Token **verschlüsselt in MariaDB**. Pro Administrator wird eine begonnene Einrichtung aufbewahrt. Beim erneuten Öffnen von **Server hinzufügen** wird derselbe Entwurf mit demselben Token geladen – auch nach Schließen des Browsers, erneutem Login oder Neustart des Management-Containers. Keine Zugangsdaten werden dafür in Browser-LocalStorage oder im Container-Dateisystem gespeichert.

Wurde der Installationsbefehl bereits auf dem Host ausgeführt, danach **Verbindung prüfen & speichern** wählen; der Befehl muss nicht erneut ausgeführt werden. Bei einem Verbindungsfehler bleibt die Einrichtung erhalten. Änderungen der öffentlichen Reverseproxy-Adresse oder der Zuordnung behalten den Token bei. Einen anderen Host, internen Port oder ein anderes Profil erst nach ausdrücklichem **Begonnene Einrichtung verwerfen** beginnen. Gleichzeitige Änderungen werden über Entwurfs-ID und Version abgeglichen.

Nach erfolgreichem Verbinden werden die endgültigen Zugangsdaten in der Serververbindung gespeichert und der Einrichtungsentwurf gelöscht. **Verwerfen löscht ausschließlich den Einrichtungsentwurf im Management**, nicht den Agenten auf dem Host oder seinen Token. Für einen bereits installierten Agenten anschließend **Agent bereits installiert** mit dem vorhandenen Profil und Token verwenden.

Bei einer Installation mit einer älteren Version, deren Assistent noch nicht gespeichert wurde, lässt sich der verlorene Browser-Entwurf nicht nachträglich wiederherstellen. Der ursprüngliche Token bleibt aber auf dem HAProxy-Host unter `/etc/haproxy-control/agent.json` erhalten. Dort im benötigten Profil nachsehen und über **Agent bereits installiert** verbinden; den Token nicht durch erneute Generierung ersetzen und nicht in öffentliche Protokolle kopieren.

**`ENCRYPTION_KEY` und die MariaDB-Verbindung bei Container-Neustarts und Updates beibehalten.** Der Management-Schlüssel entschlüsselt sowohl gespeicherte Server-Tokens als auch begonnene Einrichtungen. Ein neuer Schlüssel macht die bisherigen Zugangsdaten unlesbar; die Anwendung meldet das jetzt gezielt, statt einen neuen Agent-Token anzulegen. Die zusätzliche Tabelle `agent_setup_drafts` wird beim Start automatisch angelegt; keine neuen ENV-Variablen sind erforderlich.

## Bestehende Compose unter /srv/haproxy vorbereiten

Bei folgender Struktur bleibt das externe Docker-Netzwerk `dockernet` unverändert:

```text
/srv/haproxy/
  docker-compose.yml          # tatsächlichen Dateinamen verwenden
  haproxy.cfg
  dnsmapping.cfg
  certs/
  vhosts/
```

Vor Änderungen Compose, Konfiguration, Zertifikate und Vhosts sichern. Im Compose-Verzeichnis ein eigenes Config-Verzeichnis anlegen und die beiden bisherigen einzelnen Dateien nach `config/` kopieren; Originale behalten. Bestehende abweichende Zielkopien zuerst vergleichen. UID/GID des laufenden Containers verwenden:

```bash
cd /srv/haproxy
sudo install -d -m 755 config config/certs config/vhosts
for file in haproxy.cfg dnsmapping.cfg; do
  if [ -e "config/$file" ]; then sudo cmp -- "$file" "config/$file" || exit 1;
  else sudo cp -p -- "$file" "config/$file"; fi
done
haproxy_uid=$(sudo docker exec haproxy id -u)
haproxy_gid=$(sudo docker exec haproxy id -g)
sudo install -d -m 750 -o "$haproxy_uid" -g "$haproxy_gid" run
```

Die leeren Verzeichnisse `config/certs` und `config/vhosts` dienen als Mount-Ziele für die bestehenden Verzeichnisse; ohne sie kann Docker keine verschachtelten Mounts im schreibgeschützten Config-Verzeichnis anlegen.

Den gesamten bisherigen `volumes`-Block ersetzen (keine zusätzlichen einzelnen Datei-Mounts darüberlegen):

```yaml
    volumes:
      - ./config:/usr/local/etc/haproxy:ro
      - ./certs:/usr/local/etc/haproxy/certs:ro
      - ./vhosts:/usr/local/etc/haproxy/vhosts:ro
      - ./run:/run/haproxy:rw
```

Die vollständige passende Vorlage liegt im ZIP unter `examples/haproxy-existing-compose.yml`. Image `haproxy:3.2-alpine`, Containername, Ports 80/443, Netzwerk und beide `-f`-Argumente bleiben erhalten. `dnsmapping.cfg` liegt dadurch weiter unter `/usr/local/etc/haproxy/dnsmapping.cfg`; Dateien werden weiterhin nur geladen, wenn sie im HAProxy-Startbefehl oder als referenzierte Datei verwendet werden. Ein Mount allein lädt keine Config-Datei.

Die Config- und Zertifikats-Mounts bleiben im Container schreibgeschützt. Der Agent läuft auf dem Host und schreibt dort; lediglich der gemeinsame Runtime-Socket benötigt einen beschreibbaren Container-Mount. Das Konfigurationsverzeichnis enthält weder die Compose-Datei noch deren `.env`.

Compose prüfen, die bestehende HAProxy-Konfiguration prüfen und Container neu erstellen (kurze Unterbrechung möglich; Dateinamen anpassen):

```bash
sudo docker compose -f docker-compose.yml config --quiet
sudo docker exec haproxy haproxy -c -f /usr/local/etc/haproxy/haproxy.cfg -f /usr/local/etc/haproxy/vhosts
sudo docker compose -f docker-compose.yml up -d --force-recreate haproxy
```

Danach im Assistenten die vorbereitete Struktur auswählen. Die erzeugten Agent-Pfade sind `/srv/haproxy/config/haproxy.cfg`, `/srv/haproxy/run/admin.sock`, `/srv/haproxy/certs`; die Zertifikatspfade im Container bleiben `/usr/local/etc/haproxy/certs`. Die Vhosts werden anhand der laufenden Container-Argumente erkannt. Beim ersten Einlesen **Aktive Dateien vom HAProxy-Agenten** verwenden: eine spätere bestätigte Migration konsolidiert die geladenen Dateien, sichert alle Originale und entfernt doppelte aktive Definitionen aus den alten Dateien. Ohne Prüfen & anwenden wird der laufende Dienst nicht durch einen grafischen Entwurf ersetzt.

## Internet-Host: Verbindung und Download getrennt vorbereiten

**VPN:** Zuerst WireGuard, Tailscale oder ein vorhandenes VPN zwischen den Standorten einrichten. Im Assistenten die tatsächlich erreichbare VPN-IP des HAProxy-Hosts verwenden; auch der Management-Container muss diese IP erreichen können. Private IPv4-, ULA-IPv6- und Tailscale-Adressen im Bereich `100.64.0.0/10` werden unterstützt. Der Agent bindet nur an diese IP; HTTP läuft innerhalb des verschlüsselten Tunnels. Agent-Port nur auf der VPN-Verbindung für das Management freigeben. Ohne Tunnel/Routen richtet der Einzeiler kein VPN ein.

**Direktes HTTPS:** Eine eigene Agent-Domain, z. B. `agent.example.de`, auf die öffentliche IP des Docker-Hosts zeigen lassen. Bei Cloudflare **DNS only** verwenden. Im Assistenten die Domain, eine lokal vorhandene Bind-IP, den eigenen Agent-Port und PEM-Pfade zu Zertifikatskette/Schlüssel angeben. Uvicorn bietet den Agenten selbst über TLS an; die Verbindung bleibt unabhängig von den verwalteten HAProxy-Frontends. Port 80/443 der HAProxy-Compose unverändert lassen. Der Management-Client prüft die Zertifikatskette und den Hostnamen; keine Abschaltung der Zertifikatsprüfung. Für interne CAs das CA-Bundle über `AGENT_CA_FILE` im Management bereitstellen.

Ein gültiges Zertifikat muss vor dem Installationsbefehl auf dem Host vorhanden sein. Ein bestehendes Wildcard-Zertifikat kann verwendet werden, wenn es die Agent-Domain abdeckt. Bei einer kombinierten HAProxy-PEM-Datei kann dieselbe Datei als Zertifikat und Schlüssel angegeben werden. Diese Dateien gehören auf dem Host vorzugsweise unter `/etc/haproxy-control/tls` oder in den bestehenden ACME-Store, nicht unter `/root` oder `/home` (systemd `ProtectHome`). Ein separat ausgestelltes Cloudflare-DNS-Zertifikat ist z. B. möglich:

```bash
sudo apt-get update
sudo apt-get install -y certbot python3-certbot-dns-cloudflare
sudo install -d -m 700 /etc/haproxy-control/tls
sudo install -m 600 /dev/null /etc/haproxy-control/tls/cloudflare.ini
sudo nano /etc/haproxy-control/tls/cloudflare.ini
# Inhalt: dns_cloudflare_api_token = DEIN_TOKEN
# DNS Edit und Zone Read nur für die benötigte Zone erlauben.
sudo certbot certonly --dns-cloudflare   --dns-cloudflare-credentials /etc/haproxy-control/tls/cloudflare.ini   --dns-cloudflare-propagation-seconds 60   --agree-tos --email DEINE_EMAIL --cert-name haproxy-control-agent   -d agent.example.de   --deploy-hook 'if systemctl --quiet is-active haproxy-control-agent; then systemctl restart haproxy-control-agent; fi'
```

Domain und E-Mail ersetzen. Im HTTPS-Assistenten danach `/etc/letsencrypt/live/haproxy-control-agent/fullchain.pem` und `/etc/letsencrypt/live/haproxy-control-agent/privkey.pem` verwenden. Den Certbot-Erneuerungstimer auf dem Host prüfen (`systemctl status certbot.timer`) und aktivieren, sofern er noch nicht läuft. Nach Erneuerung müssen Agent-TLS-Zertifikate vom Agent-Dienst neu eingelesen werden: der Deploy-Hook startet ihn dafür neu. Bei bestehenden LEGO-/Cron-Aufträgen den Agent-Neustart in den erfolgreichen Deploy-Schritt aufnehmen. Die Zertifikatsverwaltung für Reverseproxy-Webseiten erneuert dieses separate Transportzertifikat nicht automatisch.

In **Host- und Provider-Firewall** den Agent-Port ausschließlich für die öffentliche Ausgangs-IP des Management-Standorts freigeben. Bei NAT diesen Port zusätzlich auf die lokale Bind-IP weiterleiten. IPv4 und IPv6 separat berücksichtigen. Der Agent braucht keine eingehende Verbindung zur LAN-Management-WebUI. Beispiel für eine schon aktive UFW-Firewall (Platzhalter ersetzen):

```bash
sudo ufw allow from MANAGEMENT_AUSGANGS_IP to AGENT_BIND_IP port 9101 proto tcp
```

### HTTPS über einen vorhandenen Reverseproxy

Im Assistenten **Internet / andere Infrastruktur → HTTPS über vorhandenen Reverseproxy** wählen. Zwei getrennte Adressen eintragen:

| Zweck | Beispiel |
| --- | --- |
| Interne Bind-IP des Agenten auf dem HAProxy-Host | `172.18.0.1` (tatsächlich vorhandene private Host-/Bridge-IP verwenden) |
| Interner Agent-Port | `9091` (oder `9101`) |
| Öffentliche HTTPS-Adresse des Agenten | `https://agent.example.de` = Port **443** |
| Abweichender öffentlicher Proxy-Port | `https://agent.example.de:8443` |

Der Installationsbefehl startet einen HTTP-Agenten ausschließlich auf der internen Bind-IP und dem internen Port. Er benötigt hierfür keine TLS-Dateien. Das Management speichert dagegen die öffentliche **HTTPS**-Adresse; die HTTP-Freigabe der gespeicherten Management-Verbindung bleibt deaktiviert. Das Zertifikat liegt am Reverseproxy und muss die öffentliche Agent-Domain abdecken. `https://agent.example.de:443` ist ebenfalls gültig. Keine `/profiles/…`-Pfade in die Basis-URL schreiben: der Management-Client ergänzt sie automatisch.

Ein nativer Reverseproxy auf demselben Host kann den Agenten über `127.0.0.1:9091` ansprechen. Bei HAProxy in Docker bezeichnet `127.0.0.1` dagegen den Container selbst. Für dein externes `dockernet` eine vorhandene private Host-IP oder die tatsächlich konfigurierte Netzwerk-Gateway-IP verwenden; z. B. mit `docker network inspect dockernet --format '{{json .IPAM.Config}}'` prüfen. Den Agent-Port nur für den Reverseproxy freigeben und nicht öffentlich weiterleiten. Der HTTP-Abschnitt muss lokal bzw. in einem vertrauenswürdigen privaten Netz liegen; über ein fremdes Netzwerk hierfür ebenfalls TLS oder VPN verwenden.

Auf dem HTTPS-Frontend die Agent-Domain zu einem eigenen HTTP-Backend routen. Die folgenden Direktiven in die **vorhandene** Konfiguration integrieren (Domain/Bind-IP ersetzen), das bestehende TLS-Frontend mit Zertifikat beibehalten:

```haproxy
# Im bestehenden HTTPS-Frontend:
    acl management_agent_host hdr(host) -i agent.example.de agent.example.de:443
    use_backend management_agent if management_agent_host

backend management_agent
    mode http
    timeout server 330s
    server agent 172.18.0.1:9091 check
```

Alle HTTP-Methoden (`GET`, `POST`, `PUT`, `DELETE`), Pfade und den `Authorization: Bearer …`-Header unverändert weiterreichen. Keine zusätzliche Basic-Auth-Anmeldung oder Login-Weiterleitung davorschalten; der Agent prüft bereits den Profil-Token. Bei Proxy-Software mit Größenlimits mindestens 2 MiB zulassen. Für Zertifikatsaktionen ausreichend lange Timeouts vorsehen (z. B. 330 Sekunden); auch vorgezogene Timeouts anderer Proxys berücksichtigen. Den HTTPS-Zugang auf die öffentliche Ausgangs-IP des Management-Standorts begrenzen; bei Cloudflare den DNS-Record auf **DNS only** setzen.

Ohne Token antwortet `https://agent.example.de/profiles/DEIN_PROFIL` mit HTTP 401. Nach Ausführung des Installationsbefehls im Assistenten **Verbindung prüfen & speichern** wählen: hierfür wird die öffentliche HTTPS-Adresse benutzt, nicht `http://INTERNE_IP:9091`. Bei einem bereits installierten Agenten kann dieselbe HTTPS-Adresse unter **Agent bereits installiert** bzw. **Server bearbeiten** eingetragen werden; Profil und vorhandenen Token beibehalten.

Wenn der verwaltete HAProxy gleichzeitig diesen Agent-Zugang weiterleitet, fällt der Zugang beim Stoppen des Proxys aus. Für den Wiederstart deshalb einen unabhängigen Weg wie SSH, VPN oder einen separaten Management-Proxy bereithalten. Den vorhandenen Agenten nicht erneut als neues Profil mit anderem Bind-Port installieren; bestehende Profile behalten ihren Endpunkt. Zum Wechsel einer bestehenden HTTP-/HTTPS-Agent-Bindung den vorhandenen systemd-Endpunkt und ggf. `listener`-Metadaten in `agent.json` bewusst anpassen und den Agenten neu starten.

Der **Download** ist die Gegenrichtung: der Internet-Host lädt einmalig Installer/ZIP und bei Updates erneut. Eine LAN-Adresse wie `http://192.168.10.70:8101` funktioniert ohne VPN nicht. Im Assistenten daher eine vom Host erreichbare Download-Basis-URL angeben, z. B. `https://agent-download.example.de`; alternativ die vorkonfigurierte Adresse über das VPN nutzen. Öffentlich nur den [Download-Dienst](AGENT_DOWNLOADS.md) hinter HTTPS veröffentlichen, nicht die Management-API. Download-Port allein stellt keine Verbindung zum Agenten her. Die individuelle Download-Adresse im Assistenten gilt für den erzeugten Installationsbefehl; für spätere Update-Befehle die erreichbare Standard-Adresse über `AGENT_DOWNLOAD_ORIGIN` einstellen.

Die folgenden Schritte erklären die **manuelle Alternative** und die Vorbereitung der HAProxy-Instanz. Bei einem bereits vorhandenen Agenten im Assistenten **Agent bereits installiert** wählen.

Beispiel: Management auf `192.168.10.70:8100`, HAProxy-Host auf `192.168.10.71`. Die IPs, Pfade, Dienst- und Containernamen an die eigene Installation anpassen. **Alle Installationsbefehle in Schritt 1–4 auf dem HAProxy-Host ausführen.**

## Download ohne Veröffentlichung der Management-WebUI

Für HAProxy-Hosts außerhalb des LANs kann das optionale Compose-Profil **agent-downloads** ausschließlich Installer und Agent-ZIP auf einem separaten Port ausliefern. `AGENT_DOWNLOAD_ORIGIN` setzt die Adresse für neu erzeugte Installations-/Update-Befehle. Die Management-WebUI und ihre API bleiben im LAN; die eigentliche Agent-Verbindung benötigt unabhängig davon HTTPS oder VPN. [Download-Port einrichten](AGENT_DOWNLOADS.md).

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

## DNS-01: Cloudflare und Hetzner Cloud

Nach dem Agent-Update unter **Zertifikate → Zertifikat anfordern → DNS-Challenge** den Provider auswählen und den API-Token im verdeckten Feld hinterlegen. Für Cloudflare **Zone:DNS:Edit** und **Zone:Zone:Read** auf allen benötigten Zonen erlauben. Für Hetzner Cloud einen **Lesen & Schreiben**-Projekt-Token aus der Hetzner Console verwenden; die DNS-Zonen müssen in diesem Projekt liegen. LEGO v5.5.2 wird vom Installer mit SHA-256-Prüfung in `bin/lego` installiert. Profile und globale LEGO-Installationen bleiben erhalten.

Gespeicherte Zugänge können für weitere Zertifikate desselben Profils und Providers wiederverwendet werden. Separate Tokens pro Auftrag sind möglich. Die Dateien liegen unter `/var/lib/haproxy-control/dns-credentials/` und sind nur für den Agent-Benutzer lesbar (0600, Verzeichnis 0700). Das Verzeichnis und die ACME-Daten unter `/var/lib/haproxy-control/acme/` sicher mit sichern; hier liegen Zugangsdaten und private Schlüssel. Im Management werden keine DNS-Tokens gespeichert oder zurückgegeben.

[Zertifikatsdialog, Provider-Rechte und LEGO-Übernahme](CERTIFICATES.md). Zunächst Staging testen; für Produktion Staging deaktivieren. Wildcards benötigen DNS-01. Bestehende Certbot-Aufträge verwenden weiterhin die bisherigen Credential-Dateien, etwa `dns_providers.cloudflare.credentials_file`, und brauchen weiterhin ihr Certbot-Plugin.

## HTTP-01

Profilfeld `acme_webroot`, z. B. `/var/lib/haproxy-control/webroot`, setzen. Webroot-Dienst mit der bereitgestellten Unit starten:

```bash
sudo mkdir -p /var/lib/haproxy-control/webroot
sudo install -m 644 agent/haproxy-control-webroot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now haproxy-control-webroot
```

Diese Unit bindet auf 127.0.0.1:8888. Wenn der Assistent nach `/opt/haproxy-control-agent` installiert hat, in der Webroot-Unit `WorkingDirectory` und den Pfad in `ExecStart` entsprechend anpassen; das tatsächliche Arbeitsverzeichnis zeigt `systemctl show --property=WorkingDirectory --value haproxy-control-agent`. Im grafischen Editor **Listener → HTTP-01** aktivieren, den aus Sicht des HAProxy-Prozesses erreichbaren Webroot-Host setzen und Konfiguration anwenden. Bei Docker die private Host-IP verwenden und den Webroot-Dienst gezielt an diese Adresse binden; localhost im Container ist nicht der Host. Nur Challenge-Pfade öffentlich weiterleiten. Port 80 muss von Let's Encrypt erreichbar sein, auch wenn andere Anfragen zu HTTPS umgeleitet werden. A-/AAAA-Records müssen auf den richtigen Proxy zeigen. Bei mehreren HAProxy-Knoten muss jede Challenge-Anfrage den Webroot erreichen, auf dem Certbot den Token schreibt.

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

In der WebUI lässt sich jedes Profil einer Infrastruktur zuordnen. Für mehrere Profile auf demselben Host separate Konfigurationsdateien, Runtime-Sockets und Zertifikatsverzeichnisse verwenden. Der aktuelle Installer lehnt gemeinsam genutzte oder ineinander liegende `cert_dir`-Pfade ab. Der Agent meldet vorhandene gemeinsame Verzeichnisse in seinen Capabilities und blockiert dort Zertifikatsschreibzugriffe einschließlich automatischer Erneuerung. Für unabhängige DNS-Konten eigene Tokens im Zertifikatsdialog hinterlegen; gespeicherte Zugänge sind pro Profil isoliert. [Vollständige Anleitung](INFRASTRUCTURES.md). Bestehende Agenten über **Server → Agent aktualisieren** aktualisieren.
