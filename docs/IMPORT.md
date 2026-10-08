# Vorhandene Konfiguration übernehmen

Die Migration liest vorhandene HAProxy-Dateien in einen grafischen Entwurf ein. Sie startet keinen Reload. Erst **Konfiguration erzeugen → Prüfen & anwenden** aktiviert Änderungen auf der gewählten Instanz.

## 1. Agent vorbereiten

Unter **Server** auf der jeweiligen Karte **Agent aktualisieren** (Download-Symbol) wählen. Den erzeugten Einzeiler auf dem **HAProxy-Host** ausführen. Der Befehl aktualisiert den vorhandenen systemd-Agenten, prüft Downloads mit SHA-256 und startet nur den Agenten neu. Profile, Tokens und HAProxy-Konfigurationen bleiben erhalten. Unterstützt werden Debian/Ubuntu und die Installationsverzeichnisse `/opt/haproxy-control-agent` sowie `/opt/haproxy-management`.

Bei einer manuellen Installation im abweichenden Verzeichnis die neuen Dateien `agent/`, `backend/__init__.py`, `backend/schemas.py` und `backend/haproxy_config.py` sowie `requirements.txt` aus dem Repository übernehmen, Abhängigkeiten im vorhandenen venv aktualisieren und den Agent-Dienst neu starten. `agent.json` beibehalten.

Der Agent erkennt die `-f`-Argumente bzw. Dateien nach `--` des laufenden nativen HAProxy-Masters oder den Docker-Containerbefehl. Verzeichnisse werden in Dateinamenreihenfolge geladen; nur nicht versteckte `.cfg`-Dateien werden übernommen. Alle Dateien und referenzierten Maps müssen für den Agenten lesbar sein; bei Docker müssen sie über lokale Mounts zugänglich sein.

Bei einem eigenen Entrypoint, relativen Pfaden oder einer nicht automatisch erkannten Dateiliste in `/etc/haproxy-control/agent.json` **alle** Quellen in ihrer HAProxy-Ladereihenfolge explizit setzen:

```json
"config_sources": [
  "/etc/haproxy/haproxy.cfg",
  "/etc/haproxy/conf.d"
]
```

Diese optionalen Profilwerte sind **Host-Pfade**, auch für Docker. `config_path` muss eine geladene Datei sein. Für native Maps außerhalb der Konfigurationsverzeichnisse zusätzlich erlaubte Verzeichnisse angeben:

```json
"map_dirs": ["/srv/shared-haproxy-maps"]
```

Nach Änderungen am Profil `sudo systemctl restart haproxy-control-agent` ausführen. Ein Import wird abgelehnt, wenn die geladene Dateiliste nicht vollständig ermittelt werden kann. Der Start eines gestoppten HAProxy-Dienstes ist weiterhin mit der Hauptdatei möglich; bei mehreren Dateien `config_sources` konfigurieren.

## 2. Einlesen und prüfen

1. Gewünschte Instanz auswählen und **Proxy Hosts** oder **Konfiguration** öffnen.
2. **Vorhandene Config einlesen** wählen.
3. **Aktive Dateien vom HAProxy-Agenten einlesen → Einlesen & Vorschau** wählen. Alternativ Hauptdatei, weitere `.cfg`-Dateien und Maps hochladen bzw. Text einfügen. Weitere hochgeladene Dateien werden alphabetisch nach der Hauptdatei angehängt. Der Map-Pfad muss genau dem Pfad in der Konfiguration entsprechen.
4. Erkannte Frontends, Backends, HTTP-/TCP-Dienste und Hinweise prüfen. **Als grafischen Entwurf übernehmen** ersetzt den bisherigen grafischen Entwurf dieser Instanz.
5. Domain-Zuordnungen und Backend-Ziele im Entwurf bearbeiten. **Konfiguration erzeugen** wählen und im vollständigen Konfigurationseditor mit dem aktiven Text vergleichen.
6. **Prüfen & anwenden** führt die Prüfung mit dem HAProxy-Binary des Zielhosts aus, sichert die vorherige Konfiguration und bestätigt den Reload anhand eines neuen Workers.

Ändern sich Hauptdatei, weitere geladene Dateien, Maps oder der grafische Entwurf zwischen Vorschau und Übernahme, wird die Übernahme abgelehnt. Externe Datei-/Map-Änderungen nach dem Import werden auch vor Erzeugung bzw. Anwendung erkannt. Danach erneut einlesen und Änderungen abgleichen.

## Was wird grafisch bearbeitbar?

- Statische Server in `backend`- und `listen`-Abschnitten: Zieladresse, Port, Gewicht (auch 0) sowie Round Robin, Least Connections und Source. HTTP/TCP-Modus, Servernamen, TLS und zusätzliche Serveroptionen bleiben erhalten.
- Einfache Host-Maps mit exakten kleingeschriebenen Domains, beispielsweise:

  ```haproxy
  use_backend %[req.hdr(host),lower,map(/etc/haproxy/maps/vhosts.map,be_default)]
  ```

  ```text
  app.example.com be_app
  www.example.com be_app
  ```

  Diese Zuordnung wird in explizite Host-ACLs und Backend-Routen überführt. Der Fallback bleibt erhalten. Die ursprüngliche Map-Datei wird nicht überschrieben; ihre Einträge steuern die konvertierten Routen nach dem Anwenden nicht mehr. Domain-Zuordnungen danach im Entwurf bearbeiten.

- Zusammenhängende einfache Paare aus `acl NAME hdr(host) -i domain.example` und `use_backend POOL if NAME`. Dazu zählen auch die vom Import erzeugten Domain-Routen, die erneut eingelesen werden können.

`global`, `defaults`, Header, Redirects, Zertifikatspfade, `userlist`, Prometheus, Statistik-Listener, TCP-Pools und unbekannte Direktiven bleiben im Konfigurationstext erhalten. TLS-Zertifikate und Schlüssel werden durch den Config-Import weder kopiert noch ersetzt.

Komplexe Bedingungen, Wildcard-/Regex-Maps, mehrdeutige Map-Einträge, dynamische `server-template`-Ziele, dynamische Ports, Unix-Ziele und nicht unterstützte Algorithmen werden erhalten und im Texteditor bearbeitet. Neue Backend-Pools, Listener, Server und komplexe Regeln ebenfalls im Texteditor ergänzen; anschließend die aktive Konfiguration erneut importieren. Grenzen: 200 geladene Dateien, insgesamt 1 MB Konfiguration, 100 Maps mit je höchstens 256 KB und zusammen höchstens 1 MB im Entwurf.

## Mehrere Dateien und Wiederherstellung

Beim ersten Anwenden des importierten Entwurfs werden **alle erkannten geladenen Abschnitte in `config_path` zusammengeführt**. Die übrigen geladenen `.cfg`-Dateien werden zu Kommentar-Dateien. Die bestehenden `-f`-Argumente und Verzeichnisse bleiben verwendbar; dadurch werden die Abschnitte nicht doppelt geladen. Das gilt auch beim Import eines hochgeladenen Ersatztexts. Diese Änderung der Dateistruktur steht in der Importvorschau.

Alle Originalinhalte und Maps werden auf dem Agenten in einem geschützten `bundle.json` unter `/var/lib/haproxy-control/backups/` gesichert. Scheitert Schreiben oder Reload, stellt der Agent alle Originaldateien samt Dateirechten wieder her und lädt die vorherige Konfiguration erneut.

Die Versionshistorie enthält außerdem die vorherige **zusammengeführte Konfiguration**. **Als Entwurf laden → Prüfen & anwenden** stellt deren Verhalten wieder her; die Dateien bleiben dabei zusammengeführt. Zum Wiederherstellen der ursprünglichen Aufteilung auf dem Host die Originalinhalte aus der Agent-Sicherung an ihre gespeicherten Pfade zurückspielen, die vollständige Dateiliste mit HAProxy prüfen und erst danach neu laden. Vorhandene Konfigurationsdateien können Zugangsdaten enthalten; die Agent-Sicherungen entsprechend schützen.
