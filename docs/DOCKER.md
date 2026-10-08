# Docker-Installation

Dieses Paket enthält `docker-compose.yml`, `.env.example` und diese Anleitung. Das Image enthält die fertige Webanwendung für Linux/amd64. Eine externe MariaDB und Docker mit Compose werden benötigt. Die Datenbank und der Datenbankbenutzer müssen vor dem ersten Start existieren; die Anwendung erstellt ihre Tabellen selbst.

## Start

Archiv entpacken und in das Verzeichnis `haproxy-management` wechseln:

```bash
cp .env.example .env
chmod 600 .env
docker compose -f docker-compose.yml pull
```

Zufällige Werte für die drei Geheimnisse erzeugen; dafür wird keine lokale Python-Installation benötigt:

```bash
docker run --rm --network none --entrypoint python \
  ghcr.io/phillipunzen/haproxy-management-console:latest \
  -c 'import secrets; from cryptography.fernet import Fernet; print("ENCRYPTION_KEY=" + Fernet.generate_key().decode()); print("SESSION_SECRET=" + secrets.token_urlsafe(48)); print("ADMIN_PASSWORD=" + secrets.token_urlsafe(24))'
```

Die erzeugten Werte in `.env` übernehmen. MariaDB-Zugangsdaten eintragen und `APP_ORIGIN` auf die tatsächliche Browser-Adresse setzen, z. B. `http://192.168.10.70:8100`. Danach:

```bash
docker compose -f docker-compose.yml config --quiet
docker compose -f docker-compose.yml up -d
docker compose -f docker-compose.yml ps
```

Die Oberfläche unter `APP_ORIGIN` öffnen und mit `ADMIN_USERNAME` / `ADMIN_PASSWORD` anmelden. Diese Werte erzeugen den ersten Administrator nur bei einer leeren Benutzertabelle. Änderungen an diesen ENV-Werten setzen das Passwort bestehender Benutzer nicht zurück; das Passwort in der Weboberfläche ändern.

`.env` wird von Compose zur Variablenauflösung gelesen. Die benötigten Anwendungsvariablen werden explizit im Abschnitt `environment` an den Container übergeben. Für Passwörter mit `$` oder `#` einfache Anführungszeichen verwenden, z. B. `DB_PASSWORD='mein$pass#wort'`. Compose-Aufrufe immer aus diesem Verzeichnis ausführen.

## ENV-Variablen

| Variable | Bedeutung |
| --- | --- |
| `IMAGE_TAG` | Image-Version, standardmäßig `latest`; alternativ ein veröffentlichter `sha-…`-Tag. |
| `APP_BIND` | Host-IP für die Portfreigabe, standardmäßig `0.0.0.0`; für einen lokalen Reverse Proxy `127.0.0.1`. |
| `APP_PORT` | Host-Port der Weboberfläche, standardmäßig `8100`; Container-Port ist `8000`. |
| `APP_ORIGIN` | Vollständiger Ursprung der Browser-Adresse einschließlich Schema und Port, ohne Pfad oder abschließenden Schrägstrich. |
| `DB_HOST` | MariaDB-Hostname/IP, vom Container aus erreichbar. `localhost` bezeichnet den Container selbst. |
| `DB_PORT` | MariaDB-Port, standardmäßig `3306`. |
| `DB_NAME` | Name der bereits angelegten MariaDB-Datenbank. |
| `DB_USER` | MariaDB-Benutzer mit Zugriff auf diese Datenbank und Berechtigung zum Erstellen der Anwendungstabellen. |
| `DB_PASSWORD` | Passwort des MariaDB-Benutzers. |
| `ENCRYPTION_KEY` | Fernet-Schlüssel aus dem obigen Befehl für gespeicherte Agent-Tokens. Dauerhaft behalten und geschützt sichern. |
| `SESSION_SECRET` | Mindestens 32 zufällige Zeichen für Sitzungsschutz; dauerhaft behalten. |
| `ADMIN_USERNAME` | Name des ersten Administrators, standardmäßig `admin`. |
| `ADMIN_PASSWORD` | Zufälliges Startpasswort mit mindestens 14 Zeichen. |
| `COOKIE_SECURE` | `false` für HTTP im privaten LAN; `true` beim Zugriff über HTTPS. |
| `METRICS_INTERVAL` | Statistik-Abfrageintervall in Sekunden, standardmäßig `30`; mindestens `10` wird verwendet. |
| `AGENT_CA_FILE` | Optionaler Pfad zu einem CA-Bundle **im Container** für Agenten mit interner CA; sonst leer lassen. |

Für eine interne CA `agent-ca-bundle.pem` mit System-CAs und der internen CA neben die Compose-Datei legen, den kommentierten `volumes`-Abschnitt aktivieren und `AGENT_CA_FILE=/certs/agent-ca-bundle.pem` setzen.

Cloudflare-Tokens werden auf dem jeweiligen HAProxy-Agenten konfiguriert. Die Agent-Einrichtung für native HAProxy-Dienste und Docker ist im [Repository](https://github.com/phillipunzen/HaProxy-Management-Console/blob/main/docs/AGENT.md) beschrieben.

## Betrieb und Updates

```bash
# Logs
docker compose -f docker-compose.yml logs --tail 100 app

# Neues Image laden und Container aktualisieren
docker compose -f docker-compose.yml pull
docker compose -f docker-compose.yml up -d

# Stoppen
docker compose -f docker-compose.yml down
```

Nach Änderungen an `.env` ebenfalls `up -d` ausführen. Die Daten liegen in MariaDB; `.env` enthält den Schlüssel zum Entschlüsseln gespeicherter Agent-Tokens. MariaDB und `.env` separat sichern. Bei Updates keine neuen Schlüssel erzeugen.

Der Container läuft ohne Root-Rechte, mit schreibgeschütztem Dateisystem und ohne Docker-Socket. Das Image ist öffentlich über `ghcr.io/phillipunzen/haproxy-management-console` verfügbar.
