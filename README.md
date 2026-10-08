# HAProxy Control

Deutsche Management-Oberfläche für mehrere HAProxy-Server mit nativer Installation oder Docker. Die Webanwendung läuft in einem unprivilegierten Docker-Container und speichert ausschließlich in MariaDB. Ein Agent pro HAProxy-Host übernimmt erlaubte lokale Verwaltungsaufgaben.

## Docker-Image starten

Image: `ghcr.io/phillipunzen/haproxy-management-console:latest` (Linux/amd64).

Eine fertige `docker-compose.yml` mit expliziten ENV-Variablen liegt im Repository. Das [Docker-Paket als ZIP herunterladen](https://github.com/phillipunzen/HaProxy-Management-Console/raw/refs/heads/main/downloads/haproxy-management-docker.zip): enthalten sind Compose-Datei, `.env.example`, Startanleitung und Agent-Dateien mit Anleitung für Nativ und Docker. Die [Docker-Anleitung](docs/DOCKER.md) beschreibt jede Variable und die Erzeugung der Schlüssel. Das ZIP wird mit `python3 scripts/package-docker.py` unter `downloads/haproxy-management-docker.zip` neu erstellt. Im vollständigen [Repository-Archiv](https://github.com/phillipunzen/HaProxy-Management-Console/archive/refs/heads/main.zip) sind diese Dateien ebenfalls enthalten. Für die fertige Compose-Datei immer `docker compose -f docker-compose.yml …` verwenden; `compose.yaml` ist für den lokalen Build vorgesehen.

```bash
git clone https://github.com/phillipunzen/HaProxy-Management-Console.git
cd HaProxy-Management-Console
cp .env.example .env
chmod 600 .env
docker pull ghcr.io/phillipunzen/haproxy-management-console:latest
```

### Schlüssel und Startpasswort erzeugen

Für eine **neue Installation** erzeugt dieser Befehl `ENCRYPTION_KEY`, `SESSION_SECRET` und `ADMIN_PASSWORD`. Er verwendet Python aus dem fertigen Image; auf dem Host muss nur Docker installiert sein:

```bash
docker run --rm --network none --entrypoint python \
  ghcr.io/phillipunzen/haproxy-management-console:latest \
  -c 'import secrets; from cryptography.fernet import Fernet; print("ENCRYPTION_KEY=" + Fernet.generate_key().decode()); print("SESSION_SECRET=" + secrets.token_urlsafe(48)); print("ADMIN_PASSWORD=" + secrets.token_urlsafe(24))'
```

Die drei ausgegebenen Zeilen **anstelle der vorhandenen Platzhalter** in `.env` eintragen. Der Befehl zeigt neue Werte an und ändert keine Dateien. Zusätzlich MariaDB-Zugangsdaten und `APP_ORIGIN` ausfüllen.

- `ENCRYPTION_KEY`: gültiger Fernet-Schlüssel für die Verschlüsselung gespeicherter Agent-Tokens.
- `SESSION_SECRET`: zufälliger Sitzungsschlüssel; mindestens 32 Zeichen.
- `ADMIN_PASSWORD`: zufälliges Passwort des ersten Administrators; mindestens 10 Zeichen.

`.env` geschützt sichern und die Schlüssel bei Updates oder Neuinstallation mit derselben Datenbank beibehalten. Ohne den ursprünglichen `ENCRYPTION_KEY` lassen sich gespeicherte Agent-Tokens nicht mehr entschlüsseln. `ADMIN_PASSWORD` erzeugt nur den ersten Benutzer; bestehende Passwörter werden in der Weboberfläche geändert.

Anschließend starten:

```bash
docker compose -f docker-compose.yml config --quiet
docker compose -f docker-compose.yml up -d
```

Die Oberfläche ist standardmäßig unter `http://<server-ip>:8100` erreichbar. `APP_ORIGIN` in `.env` muss auf genau diese Adresse zeigen. Mit `ADMIN_USERNAME` und `ADMIN_PASSWORD` anmelden und beim ersten Login ein persönliches Passwort setzen. Voraussetzungen und Agent-Einrichtung stehen unten.

Falls das GHCR-Paket privat ist, vor dem Pull mit deinem GitHub-Benutzernamen und einem Token mit `read:packages` bei `ghcr.io` anmelden (`docker login ghcr.io -u <github-user> --password-stdin`). Das Token über stdin zuführen und nicht in Repository-Dateien speichern.

Alternativ lokal bauen: `docker compose up -d --build`. Logs: `docker compose -f compose.registry.yaml logs --tail 100 app`. Der Container startet nach Docker-/Host-Neustarts automatisch. Die Daten bleiben in der externen MariaDB.

## Image-Veröffentlichung

GitHub Actions führt die Unit-Tests aus, baut Frontend und Backend im mehrstufigen Docker-Build und veröffentlicht das Image in der GitHub Container Registry. Pushes auf `main` erzeugen `latest` und einen Commit-Tag (`sha-…`); Versions-Tags wie `v0.1.0` erzeugen zusätzlich `0.1.0` und `0.1`. Pull Requests prüfen den Build ohne Veröffentlichung. Der Workflow benötigt keine eigenen Registry-Secrets: er verwendet das temporäre `GITHUB_TOKEN` mit `packages:write`.

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
- Runtime-Statistiken mit getrennten Frontends, Backend-Pools und Zielservern; HTTP/HTTPS, TCP/TLS-Passthrough, Prometheus und Statistikdienst, Protokollfilter und Suche. Sessions, HTTP-Raten/Fehler und Traffic live. Kompakter Verlauf mit Durchschnitt, Spitzenwert und Erreichbarkeit: 30-Sekunden-Intervalle für 2 Stunden, 5-Minuten-Intervalle für 1 Tag und Stundenwerte für 7 Tage. Ausschließlich der Collector schreibt Messpunkte; Statistikaufrufe erzeugen keine zusätzlichen Datenbankeinträge.
- Live-Topologie: Frontend → Site/TCP-Dienst → Backend-Pool → Zielserver mit Suche, Protokoll-/Frontend-Filter, Zoom und anklickbaren Details. Animierte Aktivität und aktive Sessions; gemeinsame Backend-Zähler ausdrücklich gekennzeichnet. Keine zusätzlichen Datenbankeinträge. [Anleitung und Messgrenzen](docs/TOPOLOGY.md).
- Import vorhandener Konfigurationen einschließlich mehrerer geladener Dateien und Host-Maps: Vorschau, grafisch bearbeitbare Domain-Zuordnungen und HTTP-/TCP-Backend-Ziele; unbekannte Direktiven bleiben im Text erhalten. Gemeinsame Dateisicherung und Wiederherstellung bei Fehlern.
- Aktivitätsprotokoll für Anmeldung und Änderungen.

## Metriken ohne große Datenbank

Der Hintergrund-Collector speichert nur globale Messwerte. Vollständige Frontend-/Backend-Zeilen werden live geladen und nicht mehr in jeder historischen Zeile abgelegt. Die Standardaufbewahrung begrenzt den Verlauf auf höchstens ungefähr 700 kompakte Intervalle pro Instanz plus eine aktuelle Zusammenfassung. Alte `metrics`-Datensätze werden beim Update automatisch und schrittweise übernommen. Unter **Einstellungen → Metrikspeicher** stehen Fortschritt und Tabellenbelegung; danach lässt sich der Platz der alten Tabelle über **Speicher freigeben** zurückgeben. Details, ENV-Variablen und Grenzen stehen in [METRICS.md](docs/METRICS.md).

## Neues Deployment

Voraussetzungen: Docker mit Compose und eine erreichbare MariaDB (11.x getestet).

1. `.env.example` nach `.env` kopieren, Zugangsdaten und `APP_ORIGIN` setzen. Schlüssel und Startpasswort mit dem Befehl unter [Schlüssel und Startpasswort erzeugen](#schlüssel-und-startpasswort-erzeugen) erstellen und die Platzhalter ersetzen. `.env` auf Modus 0600 setzen.
2. Datenbank vorab erstellen. Der Benutzer benötigt Zugriff auf die Anwendungstabellen sowie bei Erststart `CREATE`/`INDEX`/`REFERENCES`. Die Anwendung führt keine Drops oder Änderungen an fremden Tabellen aus. Eine eigene Datenbank wird empfohlen.
3. `docker compose -f compose.registry.yaml up -d` für das veröffentlichte Image oder `docker compose up -d --build` für einen lokalen Build.
4. Mit dem Bootstrap-Benutzer anmelden und Passwort ändern. Bootstrap-Werte erzeugen einen Benutzer nur, wenn noch kein Benutzer existiert.
5. Agenten auf den HAProxy-Hosts nach [Agent-Anleitung](docs/AGENT.md) einrichten und unter **Server** verbinden.

`APP_PORT` ist der veröffentlichte Host-Port. `APP_BIND` kann für vorgeschaltete Proxys auf `127.0.0.1` gesetzt werden. `APP_ORIGIN` muss genau dem im Browser verwendeten Ursprung entsprechen, einschließlich Port. Bei Wechsel von IP zu Domain anpassen und Container mit `docker compose up -d` neu erstellen. `COOKIE_SECURE=false` ist nur für den gewünschten HTTP-Zugriff im privaten Netz gesetzt; für HTTPS `COOKIE_SECURE=true` setzen. Die App akzeptiert keine beliebigen Cross-Origin-Schreibanfragen und vertraut standardmäßig keinen Proxy-Headern.

Für Agenten mit interner CA: CA-PEM nur lesbar in den Container mounten und `AGENT_CA_FILE` auf ein Bundle aus System-CAs und interner CA setzen. Die App prüft Agent-TLS-Zertifikate immer; Redirects werden nicht verfolgt. HTTP muss im Serverformular ausdrücklich freigegeben werden. Die Webanwendung benötigt **keinen Docker-Socket**.

## HAProxy-Server verbinden

Auf jedem **HAProxy-Host** den mitgelieferten Agenten als systemd-Dienst installieren. Er verwaltet entweder einen nativen HAProxy-Dienst oder die lokalen Docker-Container. Der Agent läuft in beiden Fällen auf dem Host; er benötigt keine MariaDB-Zugangsdaten. Ein Agent kann mehrere Profile bereitstellen, je eines pro HAProxy-Instanz.

Am einfachsten über den **Einrichtungsassistenten**: unter **Server** auf **Server verbinden** klicken, Nativ oder Docker wählen und Host-IP, Dienst-/Containername sowie Pfade angeben. Die Oberfläche erzeugt einen individuellen Einzeiler. Diesen per SSH auf dem HAProxy-Host ausführen, danach im geöffneten Dialog **Verbindung prüfen & speichern** wählen. Der Installer unterstützt Debian/Ubuntu mit systemd, ergänzt bei Bedarf einen Runtime-Socket, prüft die HAProxy-Konfiguration vor dem Reload und sichert die bisherige Datei. Download und Paket werden anhand von SHA-256 geprüft. Bei Docker müssen die angezeigten Verzeichnis-Mounts bereits vorhanden sein; der Installer erstellt keine Container neu. Ein vorhandener Agent lässt sich über **Agent bereits installiert** direkt verbinden.

Alternativ manuell installieren:

1. Agent-Dateien aus dem ZIP oder dem Repository auf den HAProxy-Host kopieren und Python-Abhängigkeiten installieren.
2. Für **Nativ** das Profil `native` verwenden: Dienst `haproxy`, Konfiguration `/etc/haproxy/haproxy.cfg`, Runtime-Socket `/run/haproxy/admin.sock`.
3. Für **Docker** das Profil `docker-edge` verwenden: Containername und Host-Pfade setzen; Konfigurations-, Socket- und Zertifikatsverzeichnisse in den HAProxy-Container mounten. Der Agent braucht die lokale Docker CLI.
4. Für das Profil einen eigenen zufälligen Agent-Token erzeugen und den Agenten unter einer privaten, vom Management-Container erreichbaren IP starten, z. B. `192.168.10.71:9101`.
5. In der WebUI **Server → Server verbinden** wählen und die folgende Zuordnung verwenden:

| Feld | Beispiel für Nativ | Beispiel für Docker |
| --- | --- | --- |
| Servername | `HAProxy Nativ` | `HAProxy Docker` |
| Agent-Adresse | `http://192.168.10.71:9101` | `http://192.168.10.72:9101` |
| Profilname | `native` | `docker-edge` |
| Agent-Token | Token aus `profiles.native.token` | Token aus `profiles.docker-edge.token` |
| HTTP zulassen | Für diese HTTP-Beispiele im privaten LAN aktivieren | Für diese HTTP-Beispiele im privaten LAN aktivieren |

Mit **Verbindung prüfen & speichern** verbinden. Den Installationstyp erkennt die Anwendung aus dem Agent-Profil. In der Agent-Adresse weder einen HAProxy-Listener-Port noch `/profiles/…` eintragen. `127.0.0.1` bezeichnet aus dem Management-Container heraus diesen Container, nicht den entfernten HAProxy-Host.

Die [vollständige Schritt-für-Schritt-Anleitung](docs/AGENT.md) enthält Installationsbefehle, getrennte Beispiele für Nativ und Docker, Netzwerkfreigabe, Verbindungstest und Fehlerbehebung. Sie liegt zusammen mit den Agent-Dateien auch im ZIP und ist in der WebUI unter **Server → Einrichtungsanleitung** sowie im Verbindungsformular verlinkt.

## Konfiguration bearbeiten

Bei bestehenden HAProxy-Servern **Proxy Hosts → Vorhandene Config einlesen** wählen. Der aktuelle Agent liest die geladenen Konfigurationsdateien und referenzierten Host-Maps. Die Vorschau unterscheidet HTTP-Reverse-Proxys, TCP-Pools, Prometheus und HAProxy-Stats. **Als grafischen Entwurf übernehmen** aktiviert noch keine Änderung am Dienst.

Domain-Zuordnungen aus einfachen Host-Maps und Host-ACLs sowie statische Backend-Ziele werden grafisch bearbeitbar. Globale Einstellungen, Header, Redirects, Authentifizierung und Sonderregeln bleiben erhalten. **Konfiguration erzeugen** setzt die Änderungen in den eingelesenen Text ein. Im Editor vergleichen, prüfen und anwenden. Bei mehreren geladenen Dateien werden die Abschnitte beim Anwenden in der Hauptdatei zusammengeführt; alle Originaldateien werden gesichert. Die [Import-Anleitung](docs/IMPORT.md) beschreibt Voraussetzungen, Grenzen und Wiederherstellung.

Einen bestehenden Agenten unter **Server → Agent aktualisieren** aktualisieren und den erzeugten Befehl auf dem HAProxy-Host ausführen. Bei eigener Dateistruktur gegebenenfalls `config_sources` und `map_dirs` im Agent-Profil setzen.

Für eine neue Konfiguration ohne Import erzeugt der grafische Editor die vollständige Datei aus Hosts, Regeln und Listenern. Manuelle Einstellungen werden in diesem Modus nicht übernommen; ein Dialog weist darauf hin. HTTPS erst einschalten, wenn im Zertifikatsverzeichnis ein gültiges Produktionszertifikat liegt. Der HAProxy-Check blockiert fehlerhafte Konfigurationen.

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
.venv/bin/python -m pytest tests/test_generator.py tests/test_agent.py tests/test_certificates.py tests/test_auth.py tests/test_agent_setup.py tests/test_agent_installer.py tests/test_config_import.py tests/test_metrics.py -q
.venv/bin/uvicorn backend.main:app --host 127.0.0.1 --port 8100
```

Für UI-Entwicklung: `npm run dev --prefix frontend`; Vite leitet `/api` an `127.0.0.1:8100` weiter. `APP_ORIGIN` dafür auf den Vite-Ursprung setzen. Alle Frontend-Abhängigkeiten liegen in `frontend/package-lock.json`; Python-Abhängigkeiten sind gepinnt.

`tests/test_integration.py` verwendet nur isolierte HAProxy-Testprofile und temporäre Benutzer/Instanzen, aber die in `.env` ausgewählte MariaDB. Es werden keine Tabellen gelöscht. Zum Ausführen zwei **separate Testinstanzen** mit den Profilnamen `docker-lab` und `native-lab` auf einem Testagenten unter `http://127.0.0.1:9101` bereitstellen, dann `HAPROXY_LAB_CONFIG=/path/to/lab/agent.json .venv/bin/python -m pytest tests/test_integration.py -q`. Diese Tests führen echte Reloads, Stops, Starts und Neustarts aus; nie Produktionsprofile dafür verwenden.

## Grenzen der ersten Version

Es gibt keine automatische Konfigurationssynchronisation oder Zertifikatsverteilung zwischen Agent-Hosts, keine vollständige grafische Abbildung beliebiger HAProxy-Syntax und keine Formularverwaltung neuer TCP-/UDP-Listener. Der Import übernimmt unterstützte Domain-Routen und HTTP-/TCP-Backend-Ziele; übrige Syntax bleibt im Text erhalten. Der Konfigurationseditor unterstützt eigene HAProxy-Syntax; Validierung erfolgt auf dem Zielserver. Zertifikatsausstellung braucht echte Domain-/DNS-Voraussetzungen und den Cloudflare-Token auf dem Agenten. Automatische Erneuerung gilt für mit dieser Anwendung ausgestellte Produktionszertifikate; importierte PEMs müssen extern erneuert werden. Fehler der automatischen Agent-Erneuerung stehen im Agent-Journal; eine zentrale Benachrichtigungsintegration ist noch nicht vorhanden.
