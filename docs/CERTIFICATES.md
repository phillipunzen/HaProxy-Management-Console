# Zertifikate und automatische Erneuerung

Zuerst den Zielserver bzw. seine Infrastruktur auswählen. Zertifikate und Aufträge gehören ausschließlich zu diesem Agent-Profil. Private Schlüssel und DNS-Zugangsdaten bleiben auf dem HAProxy-Host.

## Zertifikat anfordern: HTTP oder DNS

1. Zielserver auswählen und **Zertifikate → Zertifikat anfordern** öffnen.
2. **DNS-Challenge** oder **HTTP-Challenge** wählen. Die Anwendung verwendet automatisch LEGO für DNS und Certbot für HTTP; eine ACME-Client-Auswahl ist nicht erforderlich. Bestehende Certbot- und LEGO-Aufträge werden weiterhin mit ihrem bisherigen Client erneuert.
3. Bei DNS **Cloudflare** oder **Hetzner Cloud** auswählen und den API-Token direkt im verdeckten Feld eingeben. Optional einen bereits gespeicherten Zugang **dieses Servers** auswählen. Ein neuer Auftrag kann einen eigenen Token erhalten, etwa für ein anderes Kundenkonto. Provider-Wechsel leert die Token-Felder.
4. PEM-Name, Domains und ACME-E-Mail eingeben. Erst mit Staging testen; für ein vertrauenswürdiges Produktionszertifikat anschließend Staging deaktivieren. Wildcards benötigen DNS.
5. Das Produktionszertifikat beim Proxy Host bzw. HTTPS-Frontend zuweisen und die erzeugte Konfiguration prüfen und anwenden. Der Erneuerungszeitplan verwendet den gespeicherten Zugang automatisch.

**Cloudflare:** API-Token mit **Zone → DNS → Bearbeiten** und **Zone → Zone → Lesen**, auf alle angegebenen Zonen beschränkt. Optional einen zweiten Zone-Token im Dialog angeben, wenn die Leserechte aufgeteilt sind. Die Anwendung übergibt `CF_DNS_API_TOKEN` und optional `CF_ZONE_API_TOKEN` an LEGO.

**Hetzner Cloud:** In der [Hetzner Console](https://console.hetzner.cloud/) das Projekt mit den DNS-Zonen öffnen und einen API-Token mit **Lesen & Schreiben** erzeugen. Diesen im Dialog hinterlegen. Unterstützt wird die aktuelle Cloud-DNS-API, nicht ein alter API-Key aus `dns.hetzner.com`. LEGO v5.5.2 verwendet dafür den Provider `hetzner` mit `HETZNER_API_TOKEN`.

**Einrichtung:** Unter **Server → Agent aktualisieren** den Einzeiler auf jedem HAProxy-Host ausführen. Der Installer installiert LEGO **v5.5.2** für Linux amd64/arm64 nach `<Agent-Verzeichnis>/bin/lego`, prüft den Download gegen eine fest hinterlegte SHA-256-Prüfsumme und erhält vorhandene Profile und Tokens. Ein global installiertes LEGO wird nicht ersetzt. Für neue DNS-Aufträge muss kein `lego`-Objekt oder DNS-Plugin von Hand eingerichtet werden. Bei manueller Agent-Installation das passende offizielle LEGO-Binary nach `bin/lego` im Arbeitsverzeichnis legen und ausführbar machen; Download-Prüfsummen mit dem Installer abgleichen.

**Speicherung:** DNS-Zugangsdaten werden über die authentifizierte Agent-Verbindung an den ausgewählten Host geschickt. Sie liegen dort in eigenen Dateien unter `/var/lib/haproxy-control/dns-credentials/<Profilkennung>/`, mit Verzeichnisrechten **0700** und Dateirechten **0600**. Die Dateien enthalten die Tokens im Klartext und müssen wie private Schlüssel gesichert werden. Die Management-Datenbank, ACME-Auftragsdateien, API-Leseresultate und Audit-Einträge enthalten keine DNS-Tokens. Ein gespeicherter Zugang lässt sich ausschließlich im selben Agent-Profil und für denselben Provider wiederverwenden. Neue Tokens werden erst bei einem erfolgreichen Auftrag behalten. Bei HTTP werden keine DNS-Zugangsdaten übertragen. Auch Agent-Verbindungen über HTTP im privaten LAN verschlüsseln den Transport nicht; für verschlüsselten Token-Transport Agent-TLS verwenden, siehe [AGENT.md](AGENT.md).

**HTTP:** Port 80, die öffentliche DNS-Auflösung und die Weiterleitung von `/.well-known/acme-challenge/` zum Webroot müssen eingerichtet sein. Die [HTTP-Anleitung](AGENT.md#http-01) gilt für native und Docker-Instanzen. HTTP braucht keinen Provider-Token und unterstützt keine Wildcards.

## Vom Staging-Test zum Produktionszertifikat

Ein erfolgreiches Staging-Zertifikat bestätigt die Challenge-Einrichtung. Seine Zertifikatskette wird von normalen Browsern nicht als vertrauenswürdig anerkannt ([Let’s Encrypt](https://letsencrypt.org/docs/staging-environment/)). Es bleibt im getrennten `.staging/`-Verzeichnis und wird nicht auf den produktiven HTTPS-Listener geladen.

Die Zertifikatsauswahl im Proxy Host zeigt Staging-Einträge mit dem Hinweis **zuerst Produktion anfordern**; sie sind dort nicht auswählbar. Unter **Zertifikate → Produktion anfordern** am Testzertifikat wird eine neue Anforderung geöffnet. PEM-Name und Domains sind vorbelegt. Der aktuelle Agent liefert auch E-Mail, Challenge, Provider, Automatik und den Verweis auf den gespeicherten DNS-Zugang, soweit diese im Auftrag vorhanden sind; die Tokens werden dabei nicht zurückgegeben. Bei älteren Agenten fehlende Angaben im Dialog ergänzen oder den Agenten aktualisieren.

Vor dem Absenden den PEM-Namen prüfen: Gibt es auf diesem Server bereits ein Produktionszertifikat mit diesem Namen, wird es nach erfolgreicher Prüfung ersetzt und HAProxy neu geladen. Der Dialog weist darauf hin. Für ein zusätzliches Zertifikat einen anderen Namen verwenden. **Produktionszertifikat anfordern** stellt ein neues Zertifikat über die Produktions-CA aus; ein Staging-Zertifikat wird nicht durch Umbenennen oder Kopieren vertrauenswürdig. Das ursprüngliche Testzertifikat bleibt erhalten. Anschließend das Produktionszertifikat im Proxy Host bzw. HTTPS-Frontend auswählen und den Konfigurationsentwurf prüfen und anwenden.

Alternativ **Zertifikat anfordern** öffnen, dieselben Domains und den gespeicherten DNS-Zugang wählen und **Staging verwenden** deaktivieren.

## Zeitplan und manuelle Erneuerung

Unter **Zertifikate → Zeitplan** automatische Erneuerung aktivieren oder pausieren. Entweder ein Stundenintervall oder täglich eine Uhrzeit mit Zeitzone einstellen, beispielsweise **03:15 / Europe/Berlin**. Der Zeitplan läuft auf dem Agenten auch bei ausgeschalteter Management-Oberfläche. Eine verpasste tägliche Prüfung wird nach dem Agent-Start nachgeholt; fehlgeschlagene Prüfungen werden beim nächsten geplanten Termin erneut versucht. Der Agent kontrolliert die Fälligkeit etwa jede Minute. Nächste Prüfung, letzte erfolgreiche Prüfung und Fehler sind sichtbar.

Je Produktionszertifikat lässt sich die Automatik separat pausieren. **Erneuerung prüfen** prüft ein Zertifikat; **Alle Erneuerungen prüfen** auch die manuell pausierten Produktionsaufträge. Der ACME-Client entscheidet anhand seiner Laufzeit-/ACME-Regeln, ob ein neues Zertifikat nötig ist. **Jetzt erneuern** fordert nach Bestätigung unabhängig von der Fälligkeit ein neues Zertifikat an. Das kann Ausstellungslimits verbrauchen; diese Aktion gezielt einsetzen.

Geänderte Zertifikate werden atomar als PEM installiert, mit ihrem privaten Schlüssel abgeglichen und mit der aktiven HAProxy-Konfiguration geprüft. Danach folgt ein kontrollierter Reload: systemd bei nativen Installationen, das konfigurierte Reload-Signal bei Docker. Bei einem Fehler wird die vorherige PEM-Datei wiederhergestellt. Unveränderte PEM-Dateien lösen keinen Reload aus. Ein fehlgeschlagener Auftrag verhindert die Prüfung anderer Zertifikate nicht.

Staging bleibt getrennt und wird nicht automatisch im Produktionslistener aktiviert. Importierte eigene PEM-Dateien haben keinen ACME-Auftrag. Wird ein bisher automatisch verwaltetes Zertifikat manuell per PEM ersetzt, wird sein alter Erneuerungsauftrag entfernt, damit er das neue PEM nicht überschreibt.

Für diese Funktionen einen aktuellen Agenten verwenden: **Server → Agent aktualisieren**, angezeigten Befehl auf dem HAProxy-Host ausführen. Profile und Tokens bleiben erhalten.

## Bestehenden LEGO-Auftrag übernehmen

Unterstützt wird **LEGO v5** mit `lego run`, `--env-file`, `--cert.name` und `--renew-force`. Das entspricht dem gezeigten Cron-Befehl. LEGO bleibt auf dem HAProxy-Host installiert; im Management-Container wird es nicht benötigt. Vor einem Wechsel von LEGO v4 die offizielle [v5-Migrationsanleitung](https://go-acme.github.io/lego/migration/cli/) beachten. Eine Migration bestehender v4-Daten wird nicht automatisch durchgeführt; das Agent-Update installiert ein eigenes v5.5.2-Binary für neue Aufträge.

1. Auf dem HAProxy-Host `lego --version` prüfen.
2. In `/etc/haproxy-control/agent.json` im betreffenden Profil zusätzlich folgendes `lego`-Objekt einfügen. Vorhandene Profilfelder einschließlich Token erhalten. `binary` an die Ausgabe von `command -v lego` anpassen.

```json
"lego": {
  "binary": "/usr/local/bin/lego",
  "path": "/etc/lego",
  "env_file": "/etc/lego/dns_api.env",
  "resolvers": ["1.1.1.1:53", "8.8.8.8:53"],
  "propagation_wait": "5s"
}
```

3. Die vorhandene Env-Datei weiterverwenden; sie enthält z. B. `CF_DNS_API_TOKEN` und optional `CF_ZONE_API_TOKEN`. Die bestehende Datei kann im Übernahmedialog mit **Vorhandene LEGO-Zugangsdaten auf dem Host verwenden** weiter genutzt werden. Alternativ einen neuen Token im Dialog hinterlegen. Datei nur für root lesbar machen und Agent neu starten:

```bash
sudo chmod 600 /etc/lego/dns_api.env
sudo systemctl restart haproxy-control-agent
```

4. Im Tool den richtigen Server auswählen, **Zertifikate → LEGO übernehmen** öffnen.
5. **PEM-Name:** `cert`, falls der bestehende Bind `/etc/haproxy/certs/cert.pem` lädt. **Vorhandener LEGO-Zertifikatsname:** `phillipunzen.de`. **E-Mail:** die bisherige ACME-E-Mail. **Challenge:** Cloudflare DNS-01.
6. **Alle SAN-Domains** des vorhandenen Zertifikats eintragen, einschließlich Wildcards. Das Tool prüft die Liste gegen das Zertifikat. Für den gezeigten Sammelauftrag:

```text
phillipunzen.de
*.phillipunzen.de
phlene.de
*.phlene.de
marlenenitsch.de
*.marlenenitsch.de
pc-wiki.de
*.pc-wiki.de
survival-life.eu
*.survival-life.eu
phil-un.de
*.phil-un.de
hof-zentrale.de
*.hof-zentrale.de
```

Die Übernahme liest `.crt` und `.key` aus `/etc/lego/certificates/`, installiert das PEM mit geprüftem Reload und registriert die Erneuerung; sie stellt dabei kein neues Zertifikat aus. Die Erneuerung verwendet denselben LEGO-Pfad und denselben Zertifikatsnamen, auch wenn der HAProxy-PEM-Name `cert` ist.

**Erst nach erfolgreicher Übernahme und Kontrolle den bisherigen Cronjob deaktivieren**, damit nicht zwei Zeitsteuerungen gleichzeitig denselben LEGO-Auftrag ausführen. Anschließend im Tool **Zeitplan → täglich 03:15 → Europe/Berlin** einstellen. Der Agent übernimmt das Kopieren, Dateirechte, Konfigurationsprüfung und Reload. Bestehende Dateien werden nicht allein durch Auswahl eines Zeitplans übernommen.

## Neue Zertifikate: pro Site, pro Domain oder gemeinsam

**Zertifikate → Zertifikat anfordern** bietet die Challenge-Auswahl sowie drei Arten der Domain-Auswahl:

- Eine einzelne Reverseproxy-Site.
- Alle Sites des ausgewählten Servers gemeinsam; die angezeigte Liste lässt sich ergänzen, etwa um Root-Domains oder Wildcards.
- Eine eigene Liste aus bis zu 30 Domains, etwa `example.com` und `*.example.com` oder mehrere unabhängige Domains.

Ein gemeinsames DNS-Zertifikat benötigt einen Zugang beim gewählten Provider, der alle betroffenen Zonen validieren darf. Domains verschiedener Provider auf separate Zertifikatsaufträge verteilen. Für eigene Zertifikate pro Domain getrennte Aufträge mit unterschiedlichen PEM-Namen anlegen. Neu ausgestellte LEGO-Zertifikate verwenden eigene Verzeichnisse unter `<lego.path>/control/<Profilkennung>/` bzw. standardmäßig `/var/lib/haproxy-control/acme/control/<Profilkennung>/`; so kollidieren Aufträge mit denselben Domainnamen nicht mit dem übernommenen Sammelauftrag. Staging und Produktion haben getrennte Verzeichnisse. Ein bestehender LEGO-Auftrag kann nur einem Agent-Profil bzw. PEM-Namen zugewiesen sein.

HTTP-01 und Wildcards lassen sich nicht kombinieren. Für HTTP-01 den Webroot-Dienst und die Challenge-Weiterleitung einrichten; siehe [AGENT.md](AGENT.md). Bereits vorhandene Certbot-Aufträge und ihre lokal konfigurierten Plugins bleiben unterstützt.

## Eigene PEM-Dateien und Listener-Zuweisung

**PEM importieren** erwartet Zertifikatskette und passenden unverschlüsselten privaten Schlüssel in einer Datei. Eigene interne CA-Zertifikate sind möglich; Browser müssen der CA vertrauen.

Beim Proxy Host bzw. der übernommenen Domain-Zuordnung lässt sich ein Produktionszertifikat auswählen. Es wird zusätzlich auf dessen TLS-Frontend geladen; ein bisheriges Sammelzertifikat bleibt dabei erhalten. Unter **Frontends & Backends → Zertifikate** lässt sich die vollständige explizite Zertifikatsauswahl eines TLS-Frontends festlegen. Eine solche Auswahl ersetzt dessen bisherige `crt`-Zuweisung; daher alle weiterhin benötigten Zertifikate auswählen. Ohne explizite Auswahl bleibt die ursprüngliche Zuweisung erhalten. Eigene `crt-list`/`crt-store`-Definitionen weiter im Konfigurationseditor verwalten.

HAProxy wählt beim TLS-Verbindungsaufbau per SNI aus den geladenen Zertifikaten. Zertifikate sind an Domains/Listener gebunden, nicht an URL-Pfade. Für getrennte Zertifikate pro Domain sollten die SAN-Listen nicht überlappen. Der Generator prüft, ob gewählte Zertifikate auf dem Zielserver vorhanden, gültig und für die ausgewählte Domain passend sind. Danach **Konfiguration erzeugen → Prüfen → Anwenden**.

Referenzen: [LEGO v5-Befehle](https://go-acme.github.io/lego/references/ref-flags/), [Cloudflare mit LEGO](https://go-acme.github.io/lego/dns/cloudflare/), [Hetzner mit LEGO](https://go-acme.github.io/lego/dns/hetzner/), [Hetzner Cloud-DNS](https://docs.hetzner.com/networking/dns/migration-to-hetzner-console/features-and-differences/), [Certbot-Erneuerung](https://eff-certbot.readthedocs.io/en/stable/using.html#renewing-certificates), [HAProxy TLS und SNI](https://www.haproxy.com/documentation/haproxy-configuration-tutorials/security/ssl-tls/basics-enable-tls/).

## Auswahl pro Reverseproxy statt nur am Frontend

Die Auswahl **Zertifikat für HTTPS** am Reverseproxy bzw. der importierten Domain-Zuordnung hat Vorrang für diese Domain. Eine zusätzliche Zuweisung am Frontend ist nicht nötig. Das Frontend muss TLS aktiviert haben. Auch bei überlappenden SANs oder Wildcards verwendet der Dienst das gewählte Zertifikat für die zugewiesene Domain; andere Domains behalten ihre Frontend-Zertifikate. **Konfiguration erzeugen → Prüfen & anwenden** aktiviert die Auswahl.

Management-Container und HAProxy-Agent aktualisieren; unter **Server** ist der Agent-Update-Befehl verfügbar. Der Agent erstellt die benötigten SNI-Listen unter `.control-tls` im Zertifikatsverzeichnis und berücksichtigt sie bei Prüfung, Reload, Rollback und Zertifikatserneuerung. Beim erneuten Import bleibt die Auswahl erhalten. Einzelheiten: [PROXIES.md](PROXIES.md).
