# Vorhandene Konfiguration übernehmen

Grafisch bearbeitbaren HTTP-Domain-Zuordnungen lässt sich unter **Website-Zugang** eine zentrale Basic-Auth-Gruppe zuweisen. Bestehende eigene Benutzerlisten bleiben im Text erhalten; sie werden nicht automatisch in die zentrale Verwaltung übernommen. Vom Programm erzeugte Authentifizierungszuordnungen bleiben beim erneuten Import erhalten. Details: [Zentrale Basic-Auth-Benutzer](BASIC_AUTH.md).

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

## Importfehler beheben

Wird im Dialog nur `[object Object]` angezeigt, die Management-WebUI auf das aktuelle Image aktualisieren und die Seite neu laden (bei Bedarf mit `Strg+F5`). Die Fehlerantwort enthält wieder Klartext auch für ältere, bereits geladene Oberflächen. Die aktuelle Oberfläche zeigt zusätzlich den Update-Befehl bei fehlender Agent-Funktion. HTML wird beim erneuten Laden auf Änderungen geprüft. Danach die tatsächliche Fehlermeldung anhand der folgenden Hinweise beheben.

Dass **Aktive Konfiguration** angezeigt wird, bestätigt nur den Zugriff auf die Hauptdatei (`/config`). Für den Import aller geladenen Dateien und Maps wird zusätzlich `/config-bundle` benötigt. Fehlt dieser Endpunkt beim Agenten, bietet der Importdialog **Update-Befehl anzeigen** an (Administrator erforderlich). Den Befehl auf dem dort genannten HAProxy-Host ausführen und anschließend **Nach Update erneut einlesen** wählen. Die Aktualisierung der Management-WebUI allein aktualisiert keinen entfernten Agenten.

Bei anderen Fehlern zeigt der Dialog die Ursache des Agenten an:

- `config_path gehört nicht zu den geladenen Dateien` oder unvollständige Dateiliste: `config_path` und sämtliche `config_sources` im passenden Agent-Profil prüfen; bei Docker Host-Pfade verwenden.
- Datei außerhalb der Docker-Mounts: Konfiguration und Maps auf dem HAProxy-Host mounten und die Container-/Host-Pfade im Profil abgleichen.
- Datei nicht vorhanden, fehlende Leserechte oder ungültige Kodierung: genannte Datei auf dem HAProxy-Host prüfen. Konfigurationen und Maps müssen für den Agent-Dienst als UTF-8 lesbar sein.
- Agent nicht erreichbar oder Zugriff verweigert: Agent-Adresse, TLS, Firewall, Profilname und Agent-Token der Serververbindung prüfen.
- Bereits laufende Änderung oder Zeitüberschreitung: laufende Dienstaktion abwarten und erneut einlesen; bei wiederholten Fehlern `sudo journalctl -u haproxy-control-agent -n 100 --no-pager` auf dem HAProxy-Host prüfen.

Bei einem alten Agenten lässt sich hochgeladener Text weiterhin in der Vorschau ansehen. Die Übernahme bleibt bis zur Agent-Aktualisierung gesperrt, damit keine unbekannten zusätzlichen Dateien beim späteren Anwenden übergangen werden. Dateifehler lassen sich durch manuelles Hochladen nicht umgehen.

## 2. Einlesen und prüfen

1. Gewünschte Instanz auswählen und **Proxy Hosts** oder **Konfiguration** öffnen.
2. **Vorhandene Config einlesen** wählen.
3. **Aktive Dateien vom HAProxy-Agenten einlesen → Einlesen & Vorschau** wählen. Alternativ Hauptdatei, weitere `.cfg`-Dateien und Maps hochladen bzw. Text einfügen. Weitere hochgeladene Dateien werden alphabetisch nach der Hauptdatei angehängt. Der Map-Pfad muss genau dem Pfad in der Konfiguration entsprechen.
4. Erkannte Frontends, Backends, HTTP-/TCP-Dienste und Hinweise prüfen. **Als grafischen Entwurf übernehmen** ersetzt den bisherigen grafischen Entwurf dieser Instanz.
5. Unter **Proxy Hosts** den Einstellungsbutton neben einer Domain oder einem Backend-Pool anklicken. Die Bearbeitung öffnet sich direkt als Dialog mit dem gewählten Zielserver. Bei der Domain unter **Website-Zugang** eine zentrale Basic-Auth-Gruppe zuweisen; die zugehörigen Benutzer unter **Basic Auth** verwalten. **Im Entwurf speichern** speichert die Änderung. **Konfiguration erzeugen** wählen und im vollständigen Konfigurationseditor mit dem aktiven Text vergleichen.
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

### Vorhandene Basic-Auth-Regeln umstellen

Wenn ein importierter Backend-Pool bereits `http-request auth` verwendet, unter **Proxy Hosts** die Domain-Zuordnung bearbeiten, eine zentrale Gruppe auswählen und **Vorhandene Backend-Anmeldung für diese Domain ersetzen** bestätigen. Andere Domains und Frontends behalten ihre bisherigen Regeln. Anschließend **Konfiguration erzeugen → Prüfen & anwenden**. Die Umstellung und ursprünglichen Regeln bleiben beim erneuten Einlesen erhalten. Einzelheiten stehen in [BASIC_AUTH.md](BASIC_AUTH.md).
