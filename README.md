# HAProxy Control

Deutsche Management-Oberfläche für mehrere HAProxy-Server mit nativer Installation oder Docker. Die Webanwendung läuft in einem unprivilegierten Docker-Container und speichert ausschließlich in MariaDB. Ein Agent pro HAProxy-Host übernimmt erlaubte lokale Verwaltungsaufgaben.

## Docker-Image starten

Image: `ghcr.io/phillipunzen/haproxy-management-console:latest` (Linux/amd64).

Eine fertige `docker-compose.yml` mit expliziten ENV-Variablen liegt im Repository. Das [Docker-Paket als ZIP herunterladen](https://github.com/phillipunzen/HaProxy-Management-Console/raw/refs/heads/main/downloads/haproxy-management-docker.zip): enthalten sind Compose-Datei, `.env.example` und Startanleitung. Die [Docker-Anleitung](docs/DOCKER.md) beschreibt jede Variable und die Erzeugung der Schlüssel. Das ZIP wird mit `python3 scripts/package-docker.py` unter `downloads/haproxy-management-docker.zip` neu erstellt. Im vollständigen [Repository-Archiv](https://github.com/phillipunzen/HaProxy-Management-Console/archive/refs/heads/main.zip) sind diese Dateien ebenfalls enthalten. Für die fertige Compose-Datei immer `docker compose -f docker-compose.yml …` verwenden; `compose.yaml` ist für den lokalen Build vorgesehen.

```bash
git clone https://github.com/phillipunzen/HaProxy-Management-Console.git
cd HaProxy-Management-Console
cp .env.example .env
# .env mit eigener MariaDB-Verbindung, Schlüsseln und Startpasswort ausfüllen.
chmod 600 .env
docker compose -f compose.registry.yaml pull
docker compose -f compose.registry.yaml up -d
```

Die Oberfläche ist standardmäßig unter `http://<server-ip>:8100` erreichbar. `APP_ORIGIN` in `.env` muss auf genau diese Adresse zeigen. Mit `ADMIN_USERNAME` und `ADMIN_PASSWORD` anmelden und beim ersten Login ein persönliches Passwort setzen. Voraussetzungen und Agent-Einrichtung stehen unten.

Falls das GHCR-Paket privat ist, vor dem Pull mit deinem GitHub-Benutzernamen und einem Token mit `read:packages` bei `ghcr.io` anmelden (`docker login ghcr.io -u <github-user> --password-stdin`). Das Token über stdin zuführen und nicht in Repository-Dateien speichern.

Alternativ lokal bauen: `docker compose up -d --build`. Logs: `docker compose -f compose.registry.yaml logs --tail 100 app`. Der Container startet nach Docker-/Host-Neustarts automatisch. Die Daten bleiben in der externen MariaDB.

## Image-Veröffentlichung

GitHub Actions führt die 22 Unit-Tests aus, baut Frontend und Backend im mehrstufigen Docker-Build und veröffentlicht das Image in der GitHub Container Registry. Pushes auf `main` erzeugen `latest` und einen Commit-Tag (`sha-…`); Versions-Tags wie `v0.1.0` erzeugen zusätzlich `0.1.0` und `0.1`. Pull Requests prüfen den Build ohne Veröffentlichung. Der Workflow benötigt keine eigenen Registry-Secrets: er verwendet das temporäre `GITHUB_TOKEN` mit `packages:write`.

Für Updates `docker compose -f compose.registry.yaml pull` und danach `docker compose -f compose.registry.yaml up -d` ausführen. Über `IMAGE_TAG` in `.env` lässt sich eine bestimmte Version bzw. ein Commit-Tag verwenden. Die Paket-Sichtbarkeit lässt sich in den GitHub-Paketeinstellungen ändern. [GitHub-Dokumentation zur Container Registry](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry).

## Funktionen

- Login mit Argon2id-Passwörtern, serverseitigen Sitzungen, HttpOnly-Cookie, CSRF-Prüfung und Anmeldebegrenzung; Passwortwechsel invalidiert sämtliche Sitzungen.
- Benutzerrollen: Admin (alle Rechte), Operator (Konfiguration, Zertifikate, Reload/Start/Restart), Viewer (Statistiken und Zertifikatsmetadaten).
- Mehrere Agent-Profile mit verschlüsselten Zugangstokens; native systemd-Dienste und Docker-Container.
- Grafischer Entwurf für Domains/Wildcards, Pfadrouting, mehrere Backends, Gewichte, Healthchecks, Round Robin/Least Connections/Source und geprüfte TLS-Verbindungen zum Backend.
- HTTP-/HTTPS-Listener, HTTPS-Weiterleitungen, ACLs für Host, Pfad, IP-Netze und Methoden, feste URL-Redirects und Request-Header.
- Vollständiger Konfigurationseditor mit Vergleich zur aktiven Datei, Export, Validierung, Entwürfen, Versionshistorie und Wiederherstellung als neuer Entwurf.
- Geprüftes Anwenden: `haproxy -c`, externe Änderungen per Hash erkennen, alte Version sichern, Datei atomar wechseln, Reload und neuen Worker bestätigen. Fehler lösen eine Wiederherstellung aus; Netzwerkfehler werden als unklarer Status dokumentiert.
- Let's Encrypt über Certbot: HTTP-01, Cloudflare DNS-01, mehrere Domains und Wildcards; getrennte Staging-Zertifikate; PEM-Import mit Schlüsselvergleich; Erneuerungsprüfung alle 12 Stunden auf dem Agenten.
- Runtime-Statistiken, Frontends/Backends, Sessions, HTTP-Raten/Fehler, Traffic und Messwertverlauf mit 7 Tagen Aufbewahrung; Sammlung alle 30 Sekunden.
- Aktivitätsprotokoll für Anmeldung und Änderungen.

## Neues Deployment

Voraussetzungen: Docker mit Compose und eine erreichbare MariaDB (11.x getestet).

1. `.env.example` nach `.env` kopieren, Zugangsdaten und `APP_ORIGIN` setzen. Passwörter und Schlüssel zufällig erzeugen. `ENCRYPTION_KEY` ist ein Fernet-Key; `SESSION_SECRET` mindestens 32 zufällige Zeichen; `ADMIN_PASSWORD` mindestens 14 Zeichen. `.env` auf Modus 0600 setzen.
2. Datenbank vorab erstellen. Der Benutzer benötigt Zugriff auf die Anwendungstabellen sowie bei Erststart `CREATE`/`INDEX`/`REFERENCES`. Die Anwendung führt keine Drops oder Änderungen an fremden Tabellen aus. Eine eigene Datenbank wird empfohlen.
3. `docker compose -f compose.registry.yaml up -d` für das veröffentlichte Image oder `docker compose up -d --build` für einen lokalen Build.
4. Mit dem Bootstrap-Benutzer anmelden und Passwort ändern. Bootstrap-Werte erzeugen einen Benutzer nur, wenn noch kein Benutzer existiert.
5. Agenten auf den HAProxy-Hosts nach [Agent-Anleitung](docs/AGENT.md) einrichten und unter **Server** verbinden.

`APP_PORT` ist der veröffentlichte Host-Port. `APP_BIND` kann für vorgeschaltete Proxys auf `127.0.0.1` gesetzt werden. `APP_ORIGIN` muss genau dem im Browser verwendeten Ursprung entsprechen, einschließlich Port. Bei Wechsel von IP zu Domain anpassen und Container mit `docker compose up -d` neu erstellen. `COOKIE_SECURE=false` ist nur für den gewünschten HTTP-Zugriff im privaten Netz gesetzt; für HTTPS `COOKIE_SECURE=true` setzen. Die App akzeptiert keine beliebigen Cross-Origin-Schreibanfragen und vertraut standardmäßig keinen Proxy-Headern.

Für Agenten mit interner CA: CA-PEM nur lesbar in den Container mounten und `AGENT_CA_FILE` auf ein Bundle aus System-CAs und interner CA setzen. Die App prüft Agent-TLS-Zertifikate immer; Redirects werden nicht verfolgt. HTTP muss im Serverformular ausdrücklich freigegeben werden. Die Webanwendung benötigt **keinen Docker-Socket**.

## Konfiguration bearbeiten

Bei bestehenden HAProxy-Servern zuerst **Konfiguration** öffnen: die aktive Konfiguration wird vollständig geladen. Dort Änderungen direkt vornehmen, prüfen, als Version speichern und anwenden.

Der grafische Editor ist ein eigener, persistenter Entwurf. Er importiert vorhandene komplexe Konfigurationen nicht automatisch. **Konfiguration erzeugen** erstellt die vollständige Konfiguration aus Hosts, Regeln und Listenern; dabei werden manuelle Einstellungen nicht übernommen. Ein Dialog weist darauf hin. Im Konfigurationseditor beide Fassungen vergleichen und erst anschließend anwenden. HTTPS erst einschalten, wenn im Zertifikatsverzeichnis ein gültiges Produktionszertifikat liegt. Der HAProxy-Check blockiert fehlerhafte Konfigurationen.

Rollback: vorherige Version in der Historie **Als Entwurf laden**, prüfen und anwenden. Dadurch werden auch zwischenzeitliche externe Änderungen erkannt. Bei unbekanntem Status zuerst aktive Konfiguration und Dienst prüfen; nicht blind erneut anwenden.

## Daten und Sicherungen

MariaDB enthält Benutzer, verschlüsselte Agent-Tokens, Sitzungen, Instanzentwürfe, Konfigurationsversionen, Statistiken und Audit-Einträge. Raw-Konfigurationen können anwendungsspezifische Geheimnisse enthalten; Datenbankzugriff entsprechend beschränken.

- MariaDB mit üblichen `mariadb-dump`-/Server-Backups sichern.
- `.env` bzw. `ENCRYPTION_KEY` und `SESSION_SECRET` separat geschützt sichern. Ohne ursprünglichen Fernet-Key können Agent-Tokens nicht entschlüsselt werden.
- Auf Agent-Hosts `/etc/letsencrypt`, Zertifikatsverzeichnisse, Agent-Konfiguration und `/var/lib/haproxy-control` sichern. Private Zertifikatsschlüssel liegen nicht in der Management-Datenbank.
- Messwerte werden nach 7 Tagen entfernt; Konfigurationsversionen und Audit-Einträge bleiben erhalten. Lokale Agent-Backups nach eigenem Aufbewahrungsplan rotieren.
- Eine Docker-Neuinstallation verliert keine Daten, solange MariaDB und die Schlüssel erhalten bleiben.

## Entwicklung und Tests

Backend: Python/FastAPI/SQLAlchemy/PyMySQL. Frontend: React/TypeScript/Vite. Ein App-Worker, weil die Anmeldebegrenzung im Prozess geführt wird; Sitzungen und Datenspeicherung liegen in MariaDB. Eine horizontale Skalierung benötigt eine zentrale Anmeldebegrenzung und einen einzelnen Collector.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
npm ci --prefix frontend
npm run build --prefix frontend
.venv/bin/python -m pytest tests/test_generator.py tests/test_agent.py tests/test_certificates.py -q
.venv/bin/uvicorn backend.main:app --host 127.0.0.1 --port 8100
```

Für UI-Entwicklung: `npm run dev --prefix frontend`; Vite leitet `/api` an `127.0.0.1:8100` weiter. `APP_ORIGIN` dafür auf den Vite-Ursprung setzen. Alle Frontend-Abhängigkeiten liegen in `frontend/package-lock.json`; Python-Abhängigkeiten sind gepinnt.

`tests/test_integration.py` verwendet nur isolierte HAProxy-Testprofile und temporäre Benutzer/Instanzen, aber die in `.env` ausgewählte MariaDB. Es werden keine Tabellen gelöscht. Zum Ausführen zwei **separate Testinstanzen** mit den Profilnamen `docker-lab` und `native-lab` auf einem Testagenten unter `http://127.0.0.1:9101` bereitstellen, dann `HAPROXY_LAB_CONFIG=/path/to/lab/agent.json .venv/bin/python -m pytest tests/test_integration.py -q`. Diese Tests führen echte Reloads, Stops, Starts und Neustarts aus; nie Produktionsprofile dafür verwenden.

## Grenzen der ersten Version

Es gibt keine automatische Konfigurationssynchronisation oder Zertifikatsverteilung zwischen Agent-Hosts, kein Parsing beliebiger HAProxy-Konfigurationen zurück in den grafischen Editor und keine automatische Übernahme von TCP-/UDP-Frontends in Formularen. Der Konfigurationseditor unterstützt eigene HAProxy-Syntax; Validierung erfolgt auf dem Zielserver. Zertifikatsausstellung braucht echte Domain-/DNS-Voraussetzungen und den Cloudflare-Token auf dem Agenten. Automatische Erneuerung gilt für mit dieser Anwendung ausgestellte Produktionszertifikate; importierte PEMs müssen extern erneuert werden. Fehler der automatischen Agent-Erneuerung stehen im Agent-Journal; eine zentrale Benachrichtigungsintegration ist noch nicht vorhanden.
