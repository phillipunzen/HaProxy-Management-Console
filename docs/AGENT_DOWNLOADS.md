# Separater Download-Port für Agenten

Der optionale Compose-Dienst **agent-downloads** liefert ausschließlich zwei öffentliche Dateien aus:

| Pfad | Datei |
| --- | --- |
| `/api/agent-installer` | Agent-Installationsskript |
| `/api/agent-package` | Docker-/Agent-ZIP mit öffentlichen Vorlagen und Anleitungen |

Er akzeptiert nur **GET** und **HEAD**. Es gibt keine WebUI, Login-, Konfigurations-, Profil-, Token- oder Verwaltungs-API, keine API-Dokumentation, Dateisuche oder Uploads. Andere Pfade liefern 404. Zugangsdaten für ein konkretes Profil entstehen weiterhin ausschließlich im angemeldeten Management-Assistenten und gehören nicht zu den ausgelieferten Dateien.

## Aktivieren

Die aktuelle `docker-compose.yml` und `.env.example` aus dem [Docker-ZIP](https://github.com/phillipunzen/HaProxy-Management-Console/raw/refs/heads/main/downloads/haproxy-management-docker.zip) verwenden. Vorhandene `.env`, Datenbank und Schlüssel erhalten. Folgende Variablen in der eigenen `.env` ergänzen:

```dotenv
# Hinter einem lokalen HTTPS-Reverseproxy nur auf localhost veröffentlichen.
AGENT_DOWNLOAD_BIND=127.0.0.1
AGENT_DOWNLOAD_PORT=8101
# Öffentliche Adresse, die der HAProxy-Host zum Herunterladen erreicht.
AGENT_DOWNLOAD_ORIGIN=https://agent-downloads.example.com
```

`APP_ORIGIN` bleibt die bisherige Adresse der Management-WebUI. `AGENT_DOWNLOAD_ORIGIN` ist eine Basisadresse ohne Pfad, Zugangsdaten, Query oder Fragment. Ohne diese Variable verwenden Installations- und Update-Befehle weiterhin `APP_ORIGIN`. Die Variable startet keinen Dienst; dafür das Compose-Profil aktivieren:

```bash
docker compose -f docker-compose.yml --profile agent-downloads pull
docker compose -f docker-compose.yml --profile agent-downloads up -d
```

Das Profil startet einen **zusätzlichen Container aus demselben Image und Tag**. Er führt nur `backend.agent_downloads:app` auf Container-Port 8001 aus. Er bekommt weder `.env` noch Datenbankpasswort, Agent-Tokens, Management-Schlüssel oder Host-Verzeichnis-Mounts. Er verwendet ein eigenes Docker-Netz, ein schreibgeschütztes Dateisystem und den unprivilegierten Image-Benutzer. Container-Speicher, Prozesse, CPU, gleichzeitige Verbindungen und Protokollgröße sind begrenzt. Dieses Netzwerk ist kein VPN und keine Firewall für ausgehende Verbindungen.

Für einen Reverseproxy auf einem anderen LAN-Host statt localhost die **LAN-IP des Control Hosts** als `AGENT_DOWNLOAD_BIND` setzen, z. B. `192.168.10.70`. Der Reverseproxy leitet die öffentliche Download-Domain ausschließlich zu dieser IP auf Port 8101. Für eine direkte Router-Portweiterleitung ebenfalls diese LAN-IP verwenden und ausschließlich den Download-Port weiterleiten. Die Management-WebUI auf Port 8100 bleibt im LAN. Der Container bietet selbst HTTP an; HTTPS am vorgeschalteten Reverseproxy terminieren. Kein Fallback zum Management-Port einrichten. Ein vorgeschalteter Reverseproxy im Docker-Netz benötigt ausdrücklich Zugang zum Download-Netz oder zum veröffentlichten Host-Port.

Für einen Test im privaten LAN ist `AGENT_DOWNLOAD_ORIGIN=http://192.168.10.70:8101` möglich. Über das Internet HTTPS verwenden. Nach einer Änderung der Download-Adresse den App-Container mit `up -d` aktualisieren und den Installations-/Update-Befehl in der WebUI **neu erzeugen**. Bereits kopierte Befehle behalten ihre frühere Adresse.

## Prüfen und aktualisieren

```bash
curl -I https://agent-downloads.example.com/api/agent-installer
curl -I https://agent-downloads.example.com/api/agent-package
# Erwartet: 404, keine Verwaltungsoberfläche.
curl -i https://agent-downloads.example.com/api/instances
curl -i https://agent-downloads.example.com/
```

Installer und ZIP werden weiterhin gegen die SHA-256-Prüfsummen des angemeldeten Management-Assistenten geprüft, bevor Dateien installiert oder ausgeführt werden. **App und Download-Container immer gemeinsam auf denselben Tag aktualisieren**, sonst können die Prüfsummen abweichen. Bei aktiviertem Download-Profil für Updates beide obigen Compose-Befehle mit `--profile agent-downloads` verwenden. Der Download-Service-Healthcheck prüft den Installer mit HEAD und benötigt keine Datenbank. Paketinstallationen auf dem HAProxy-Host benötigen weiterhin Debian/Ubuntu-Paketquellen und GitHub für LEGO.

Zum Abschalten:

```bash
docker compose -f docker-compose.yml --profile agent-downloads stop agent-downloads
```

Danach `AGENT_DOWNLOAD_ORIGIN` leeren und den App-Container aktualisieren, wenn Befehle wieder über die Management-Adresse herunterladen sollen.

## Grenzen

Der Port dient nur zum Herunterladen. Die eigentliche Verwaltung läuft weiterhin vom Control Server zum Agenten und benötigt separat **HTTPS oder einen verschlüsselten VPN-Tunnel**. Der Download-Port richtet weder Agent-TLS noch einen automatischen Rückkanal ein. Der bestehende Einzeiler-Assistent bleibt für private, erreichbare Agent-IP-Adressen ausgelegt; ein Internet-Agent mit HTTPS wird über **Agent bereits installiert** verbunden.

Ein öffentlicher Download-Dienst gewährt keine Verwaltungsrechte, bleibt aber eine öffentlich erreichbare HTTP-Anwendung. Er kann beispielsweise mit Anfragen belastet werden. Die Trennung reduziert die Angriffsfläche; sie ist keine Garantie, dass keine Sicherheitslücke existieren kann. Host und Image aktuell halten; nur diesen Dienst veröffentlichen. Architektur der Compose-Netze: [Docker-Dokumentation](https://docs.docker.com/reference/compose-file/networks/).
