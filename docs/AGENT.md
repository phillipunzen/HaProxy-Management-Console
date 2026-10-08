# HAProxy-Server anbinden

Die Webanwendung benötigt weder Root-Rechte noch den Docker-Socket. Auf jedem HAProxy-Host läuft ein Agent. Ein Agent kann mehrere native Dienste und Docker-Container verwalten. Die Agent-Konfiguration legt erlaubte Profile, Pfade und Container lokal fest. Ein Agent-Token berechtigt zur Verwaltung des gesamten Profils; nur die Management-Anwendung darf den Agent erreichen.

## Installation als systemd-Dienst

Voraussetzungen: Linux, Python 3.11+, HAProxy mit Runtime-Socket; für Docker-Profile Docker CLI und ein HAProxy-Container im Master-Worker-Modus. Die installierte HAProxy-Version wird zur Prüfung verwendet. HAProxy 3.0 und 3.2 wurden im Entwicklungslabor getestet.

Kopiere dieses Projekt nach `/opt/haproxy-management` auf dem HAProxy-Host. Node.js wird für den Agenten nicht benötigt.

```bash
sudo apt-get install python3-venv certbot python3-certbot-dns-cloudflare
cd /opt/haproxy-management
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
sudo install -d -m 700 /etc/haproxy-control /var/lib/haproxy-control
sudo install -m 600 agent/config.example.json /etc/haproxy-control/agent.json
python3 -c 'import secrets; print(secrets.token_urlsafe(48))'
```

Bearbeite `/etc/haproxy-control/agent.json`: benötigte Profile behalten, für jedes Profil einen eigenen zufälligen Token setzen und Pfade anpassen. Beispielwerte niemals als echte Tokens verwenden. `cert_uid`, `cert_gid` und `cert_mode` müssen zum Benutzer passen, mit dem HAProxy Zertifikate liest. Die Beispielwerte sind für das offizielle Docker-Image mit UID/GID 99; eigene Images können andere IDs verwenden. `cert_mode` ist die dezimale Darstellung des Dateimodus: 384 = 0600, 416 = 0640.

Der Agent wird hier als root betrieben, weil er systemd, Docker, Zertifikate und Konfigurationsdateien verwaltet. Er nimmt keine frei wählbaren Shell-Kommandos aus der WebUI entgegen. Konfigurationseditor und Operator-Rolle sind dennoch vertrauenswürdige Administrationszugänge: vollständige HAProxy-Konfigurationen können das Verhalten des Dienstes grundlegend ändern. Agent-Zugriff per Firewall auf die Management-IP begrenzen.

```bash
sudo install -m 644 agent/haproxy-control-agent.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now haproxy-control-agent
sudo systemctl status haproxy-control-agent
```

Die mitgelieferte Unit bindet standardmäßig **127.0.0.1:9101**. Für entfernte Management-Server einen HTTPS-Reverse-Proxy davor setzen, oder die Unit gezielt auf eine private IP ändern und den Zugriff per Firewall auf die Management-IP beschränken. Bei direktem TLS unterstützt Uvicorn `--ssl-certfile` und `--ssl-keyfile`. Selbstsignierte Zertifikate brauchen eine vertrauenswürdige CA im Management-Container; TLS-Prüfung wird nicht abgeschaltet. Bei HTTP im privaten Netz muss die Freigabe im Serverformular explizit gesetzt werden.

Danach in der WebUI unter **Server → Server hinzufügen** Adresse, Profilname und Token eingeben. Die Verbindung wird vor dem Speichern geprüft. Tokens werden mit Fernet verschlüsselt in MariaDB gespeichert und nicht in API-Antworten zurückgegeben.

## Native Installation

Bestehende Konfiguration erhalten und Runtime-Socket im `global`-Abschnitt ergänzen:

```haproxy
global
    stats socket /run/haproxy/admin.sock mode 660 level admin
```

Das Verzeichnis muss existieren. Der Agent muss auf den Socket zugreifen können. Für geprüfte Reloads ist der systemd-Dienst im HAProxy-Master-Worker-Modus erforderlich, wie bei üblichen Distributionseinheiten. Agent verwendet `haproxy -c -f <tempfile>` und `systemctl reload <service>`. Ein Reload gilt erst als erfolgreich, wenn ein neuer Worker über den Runtime-Socket erreichbar ist.

Zertifikatsverzeichnis vor Verwendung anlegen, z. B. `/etc/haproxy/certs`. Bei chroot/nicht privilegierter Ausführung die beim Start verfügbaren Pfade und Berechtigungen prüfen.

## Docker-Installation

**Verzeichnisse mounten, keine einzelne haproxy.cfg.** Atomare Dateiwechsel benötigen einen Verzeichnis-Mount; ein einzelner Datei-Mount behält sonst die alte Datei. Auch das Runtime-Verzeichnis muss zwischen Container und Agent-Host geteilt sein.

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

Die drei Host-Verzeichnisse zuvor anlegen; `/opt/haproxy/run` muss für den HAProxy-Containerbenutzer beschreibbar sein. Konfiguration vor dem ersten Start bereitstellen. Konfiguration muss für den Containerbenutzer lesbar sein. Für das offizielle 3.2-Image ist `USR2` der Reload im Master-Worker-Modus. Andere Images können abweichende Signale benötigen; `reload_signal` lokal anpassen. Der Agent validiert laufende Container per `docker exec`; gestoppte Container mit einem temporären Container desselben Images und dessen Mounts. Start und Neustart werden ebenfalls validiert.

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

Der Agent prüft verwaltete Produktionszertifikate 60 Sekunden nach Start und danach alle 12 Stunden mit `certbot renew`. Nur geänderte Zertifikate lösen einen HAProxy-Reload aus. Fehlgeschlagene Erneuerungen stehen in `journalctl -u haproxy-control-agent`; in der WebUI werden Ablaufdaten sichtbar. **Erneuerung prüfen** führt denselben Prozess manuell aus. Importierte PEM-Dateien werden nicht automatisch durch Certbot erneuert.

Agent-State unter `/var/lib/haproxy-control` enthält Zertifikat-Zuordnungen, Locks und lokale Konfigurationssicherungen. Vor jedem Config-Wechsel wird gesichert; bei Reload-Fehlern wird die alte Datei wiederhergestellt und erneut geladen. Externe Änderungen werden durch SHA-256-Vergleich erkannt und nicht überschrieben. Bei einer unterbrochenen Netzwerkverbindung kann der Status unklar sein; aktive Serverkonfiguration kontrollieren und einen neuen Entwurf erstellen. Alte Backups können nach eigenem Aufbewahrungsplan entfernt werden.

## Hinweise für mehrere Server

Zertifikate werden auf dem jeweiligen Agent-Host ausgestellt und gespeichert. Es gibt keinen automatischen Zertifikatsschlüssel-Transfer zwischen unabhängigen Agent-Hosts. Für denselben Hostnamen auf mehreren HAProxy-Servern entweder auf jedem Host ausstellen oder Zertifikate gezielt per PEM importieren. DNS-01 ist meist einfacher für HA-Setups. Agent-Profile für Container oder Dienste, die eine gemeinsame Konfigurationsdatei verwenden, sind nicht unterstützt: pro Datei und Socket genau ein Profil anlegen.

Referenzen: [HAProxy Runtime API](https://www.haproxy.com/documentation/haproxy-runtime-api/), [offizieller Docker-Entrypoint für HAProxy 3.2](https://github.com/docker-library/haproxy/blob/master/3.2/docker-entrypoint.sh), [Certbot-Benutzerhandbuch](https://eff-certbot.readthedocs.io/en/stable/using.html), [Cloudflare-Plugin](https://certbot-dns-cloudflare.readthedocs.io/en/stable/).
