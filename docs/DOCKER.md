# Docker-Installation

Server lassen sich mit frei wählbaren Tags und einem Standort organisieren und filtern. Unter **Server → Tags & Standort** bearbeiten oder beim Verbinden direkt angeben. Die [Anleitung für Server-Zuordnungen](https://github.com/phillipunzen/HaProxy-Management-Console/blob/main/docs/SERVERS.md) liegt im ZIP unter `docs/SERVERS.md`. Keine zusätzlichen ENV-Variablen erforderlich.

Für zentral verwaltete Website-Zugänge unter **Basic Auth** Benutzer und Gruppen anlegen, unter **Proxy Hosts → Website-Zugang** zuordnen und die Konfiguration auf jeder betroffenen Instanz prüfen und anwenden. Die [Basic-Auth-Anleitung](https://github.com/phillipunzen/HaProxy-Management-Console/blob/main/docs/BASIC_AUTH.md) beschreibt Passwortwechsel, Aktivierung und importierte Konfigurationen. Keine zusätzlichen ENV-Variablen erforderlich. Die Anleitung liegt im Docker-ZIP unter `docs/BASIC_AUTH.md`.

Dieses Paket enthält `docker-compose.yml`, `.env.example`, diese Anleitung sowie die Agent-Dateien und `docs/AGENT.md` zum Verbinden nativer und Docker-basierter HAProxy-Server. Das Image enthält die fertige Webanwendung für Linux/amd64. Eine externe MariaDB und Docker mit Compose werden benötigt. Die Datenbank und der Datenbankbenutzer müssen vor dem ersten Start existieren; die Anwendung erstellt ihre Tabellen selbst.

## Start

Archiv entpacken und in das Verzeichnis `haproxy-management` wechseln:

```bash
cp .env.example .env
chmod 600 .env
docker pull ghcr.io/phillipunzen/haproxy-management-console:latest
```

## Schlüssel und Startpasswort erzeugen

Für eine **neue Installation** zufällige Werte für `ENCRYPTION_KEY`, `SESSION_SECRET` und `ADMIN_PASSWORD` erzeugen; dafür wird keine lokale Python-Installation benötigt:

```bash
docker run --rm --network none --entrypoint python \
  ghcr.io/phillipunzen/haproxy-management-console:latest \
  -c 'import secrets; from cryptography.fernet import Fernet; print("ENCRYPTION_KEY=" + Fernet.generate_key().decode()); print("SESSION_SECRET=" + secrets.token_urlsafe(48)); print("ADMIN_PASSWORD=" + secrets.token_urlsafe(24))'
```

Die drei ausgegebenen Zeilen **anstelle der vorhandenen Platzhalter** in `.env` übernehmen. Der Befehl zeigt neue Werte an und ändert keine Dateien. `ENCRYPTION_KEY` ist ein gültiger Fernet-Schlüssel, `SESSION_SECRET` hat mindestens 32 Zeichen und `ADMIN_PASSWORD` mindestens 10 Zeichen. `.env` geschützt sichern; bei Updates oder Wiederverwendung der Datenbank die vorhandenen Schlüssel behalten. Ohne den ursprünglichen `ENCRYPTION_KEY` lassen sich gespeicherte Agent-Tokens nicht mehr entschlüsseln.

MariaDB-Zugangsdaten eintragen und `APP_ORIGIN` auf die tatsächliche Browser-Adresse setzen, z. B. `http://192.168.10.70:8100`.

## Container starten

Danach:

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
| `ADMIN_PASSWORD` | Zufälliges Startpasswort mit mindestens 10 Zeichen. |
| `COOKIE_SECURE` | `false` für HTTP im privaten LAN; `true` beim Zugriff über HTTPS. |
| `METRICS_INTERVAL` | Collector-Intervall in Sekunden, standardmäßig `30`; erlaubt sind `10`–`3600`. Browser-Aufrufe speichern keine weiteren Messpunkte. |
| `METRICS_RAW_HOURS` | Aufbewahrung der 30-Sekunden-Intervalle in Stunden; Standard `2`, erlaubt `1`–`24`. |
| `METRICS_FINE_DAYS` | Aufbewahrung der 5-Minuten-Intervalle in Tagen; Standard `1`, erlaubt `1`–`30`. |
| `METRICS_RETENTION_DAYS` | Aufbewahrung der Stundenwerte in Tagen; Standard `7`, erlaubt `1`–`365`, mindestens `METRICS_FINE_DAYS`. |
| `AGENT_CA_FILE` | Optionaler Pfad zu einem CA-Bundle **im Container** für Agenten mit interner CA; sonst leer lassen. |

Für eine interne CA `agent-ca-bundle.pem` mit System-CAs und der internen CA neben die Compose-Datei legen, den kommentierten `volumes`-Abschnitt aktivieren und `AGENT_CA_FILE=/certs/agent-ca-bundle.pem` setzen.

## HAProxy-Server verbinden: Nativ oder Docker

In der WebUI **Server → Server verbinden** öffnen. Der Assistent fragt nach Installationstyp, privater Host-IP, Dienst- bzw. Containername und den Pfaden. Er erzeugt einen zugeschnittenen Einzeiler mit einem neuen Agent-Token. Den Befehl per SSH auf dem HAProxy-Host ausführen, den Agent-Port für den Management-Host freigeben und im selben Dialog **Verbindung prüfen & speichern** wählen. Der Installer unterstützt Debian/Ubuntu mit systemd und eine bereits laufende HAProxy-Instanz. Bei Docker müssen die im Dialog angezeigten Verzeichnis-Mounts vorhanden sein; fehlende Mounts werden als Fehler mit Hinweis gemeldet. Bereits installierte Agenten über **Agent bereits installiert** verbinden.

Auf dem jeweiligen **HAProxy-Host** den mitgelieferten Agenten installieren – auch wenn HAProxy in Docker läuft. Der Agent verwaltet den lokalen systemd-Dienst bzw. die lokalen Container. Seine Konfiguration liegt unter `/etc/haproxy-control/agent.json` und enthält pro Instanz einen Profilnamen und einen eigenen Token. Er benötigt keine Datenbankverbindung.

Die [Schritt-für-Schritt-Anleitung im Paket](docs/AGENT.md) zeigt alle Befehle für die Installation, native Runtime-Sockets, Docker-Mounts und die Netzwerkadresse des Agenten. Die Beispiele verwenden `native` für den systemd-Dienst und `docker-edge` für einen Container.

Anschließend in der WebUI **Server → Server verbinden**:

| Feld | Eintrag |
| --- | --- |
| Servername | Frei wählbar, z. B. `Edge Proxy`. |
| Agent-Adresse | URL des Agenten, z. B. `http://192.168.10.71:9101`; ohne Profilpfad. |
| Profilname | `native` oder `docker-edge`, genau wie in `agent.json`. |
| Agent-Token | Der Wert `token` des gewählten Profils. |
| HTTP-Verbindung im privaten Netz zulassen | Für das HTTP-Beispiel aktivieren; bei HTTPS deaktiviert lassen. |

**Verbindung prüfen & speichern** wählen. Der Installationstyp wird automatisch erkannt. Cloudflare-Tokens werden auf dem jeweiligen HAProxy-Agenten konfiguriert; auch das ist in der Agent-Anleitung beschrieben.

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

## Vorhandene HAProxy-Konfiguration importieren

Nach dem Verbinden des Hosts unter **Proxy Hosts → Vorhandene Config einlesen** den Agenten als Quelle wählen oder Konfigurationsdateien und Maps hochladen. Der Import erstellt einen Entwurf; erst Prüfen und Anwenden aktiviert ihn. Bei bestehenden Agenten zuvor unter **Server → Agent aktualisieren** den Befehl erstellen und auf dem HAProxy-Host ausführen. Die beiliegende [Import-Anleitung](IMPORT.md) beschreibt mehrere Dateien, Domain-Maps, TCP-Pools und Sicherungen.

## Metrikspeicher bei Updates

Neue Messungen speichern nur kompakte globale Werte. Die alte Tabelle `metrics` wird automatisch in Zeitintervalle übernommen; unter **Einstellungen → Metrikspeicher** lässt sich der Fortschritt prüfen und danach freier Platz zurückgeben. Die [Metrik-Anleitung](METRICS.md) beschreibt die Aufbewahrung, Durchschnitt/Spitzenwerte, Ausfälle und MariaDB-Optimierung.

## Live-Topologie

Unter **Topologie** stehen Sites, TCP-Dienste, Backend-Pools und Zielserver als interaktives Diagramm bereit. Animierte Verbindungen zeigen gemessene Aktivität bzw. aktive Sessions, mit Suche, Filtern und Details. Die Ansicht speichert keine zusätzlichen Metriken. Messgrenzen und Bedienung: [TOPOLOGY.md](TOPOLOGY.md).

## Mehrere Infrastrukturen

Unter **Infrastrukturen** Gruppen anlegen und Server unter **Server → Zuordnung bearbeiten** zuweisen. Zertifikate und Konfigurationen gehören zur ausgewählten HAProxy-Instanz. Die [Anleitung im Paket](docs/INFRASTRUCTURES.md) beschreibt getrennte Zertifikatsverzeichnisse, Cloudflare-Konten und die Auswahl des Zielservers. [Anleitung auf GitHub](https://github.com/phillipunzen/HaProxy-Management-Console/blob/main/docs/INFRASTRUCTURES.md). Bestehende Installationen benötigen keine neuen ENV-Variablen.

## Zertifikate und Proxy-Editor

[LEGO-Aufträge übernehmen und Erneuerung steuern](docs/CERTIFICATES.md) sowie [HTTP-/TCP-Frontends und Backend-Pools aufbauen](docs/PROXIES.md). Nach einem Image-Update den Agenten auf bestehenden HAProxy-Hosts unter Server aktualisieren, um die neuen Zertifikatsfunktionen zu nutzen. LEGO und DNS-Zugangsdaten liegen auf dem HAProxy-Host, nicht im Management-Container.
