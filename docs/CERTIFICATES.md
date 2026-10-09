# Zertifikate und automatische Erneuerung

Zuerst den Zielserver bzw. seine Infrastruktur auswählen. Zertifikate und Aufträge gehören ausschließlich zu diesem Agent-Profil. Private Schlüssel und DNS-Zugangsdaten bleiben auf dem HAProxy-Host.

## Zeitplan und manuelle Erneuerung

Unter **Zertifikate → Zeitplan** automatische Erneuerung aktivieren oder pausieren. Entweder ein Stundenintervall oder täglich eine Uhrzeit mit Zeitzone einstellen, beispielsweise **03:15 / Europe/Berlin**. Der Zeitplan läuft auf dem Agenten auch bei ausgeschalteter Management-Oberfläche. Eine verpasste tägliche Prüfung wird nach dem Agent-Start nachgeholt; fehlgeschlagene Prüfungen werden beim nächsten geplanten Termin erneut versucht. Der Agent kontrolliert die Fälligkeit etwa jede Minute. Nächste Prüfung, letzte erfolgreiche Prüfung und Fehler sind sichtbar.

Je Produktionszertifikat lässt sich die Automatik separat pausieren. **Erneuerung prüfen** prüft ein Zertifikat; **Alle Erneuerungen prüfen** auch die manuell pausierten Produktionsaufträge. Der ACME-Client entscheidet anhand seiner Laufzeit-/ACME-Regeln, ob ein neues Zertifikat nötig ist. **Jetzt erneuern** fordert nach Bestätigung unabhängig von der Fälligkeit ein neues Zertifikat an. Das kann Ausstellungslimits verbrauchen; diese Aktion gezielt einsetzen.

Geänderte Zertifikate werden atomar als PEM installiert, mit ihrem privaten Schlüssel abgeglichen und mit der aktiven HAProxy-Konfiguration geprüft. Danach folgt ein kontrollierter Reload: systemd bei nativen Installationen, das konfigurierte Reload-Signal bei Docker. Bei einem Fehler wird die vorherige PEM-Datei wiederhergestellt. Unveränderte PEM-Dateien lösen keinen Reload aus. Ein fehlgeschlagener Auftrag verhindert die Prüfung anderer Zertifikate nicht.

Staging bleibt getrennt und wird nicht automatisch im Produktionslistener aktiviert. Importierte eigene PEM-Dateien haben keinen ACME-Auftrag. Wird ein bisher automatisch verwaltetes Zertifikat manuell per PEM ersetzt, wird sein alter Erneuerungsauftrag entfernt, damit er das neue PEM nicht überschreibt.

Für diese Funktionen einen aktuellen Agenten verwenden: **Server → Agent aktualisieren**, angezeigten Befehl auf dem HAProxy-Host ausführen. Profile und Tokens bleiben erhalten.

## Bestehenden LEGO-Auftrag übernehmen

Unterstützt wird **LEGO v5** mit `lego run`, `--env-file`, `--cert.name` und `--renew-force`. Das entspricht dem gezeigten Cron-Befehl. LEGO bleibt auf dem HAProxy-Host installiert; im Management-Container wird es nicht benötigt. Vor einem Wechsel von LEGO v4 die offizielle [v5-Migrationsanleitung](https://go-acme.github.io/lego/migration/cli/) beachten. Das Tool führt keine automatische Migration oder LEGO-Aktualisierung aus.

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

3. Die vorhandene Env-Datei weiterverwenden; sie enthält z. B. `CF_DNS_API_TOKEN` und optional `CF_ZONE_API_TOKEN`. Zugangsdaten nicht in die WebUI kopieren. Datei nur für root lesbar machen und Agent neu starten:

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

**Zertifikate → Zertifikat anfordern** bietet den ACME-Client sowie drei Arten der Domain-Auswahl:

- Eine einzelne Reverseproxy-Site.
- Alle Sites des ausgewählten Servers gemeinsam; die angezeigte Liste lässt sich ergänzen, etwa um Root-Domains oder Wildcards.
- Eine eigene Liste aus bis zu 30 Domains, etwa `example.com` und `*.example.com` oder mehrere unabhängige Domains.

Ein gemeinsames Zertifikat benötigt ein Cloudflare-Token, das alle betroffenen Zonen validieren darf. Für eigene Zertifikate pro Domain getrennte Aufträge mit unterschiedlichen PEM-Namen anlegen. Neu ausgestellte LEGO-Zertifikate verwenden eigene Verzeichnisse unter `<lego.path>/control/<Profilkennung>/`; so kollidieren Aufträge mit denselben Domainnamen nicht mit dem übernommenen Sammelauftrag. Staging und Produktion haben getrennte Verzeichnisse. Ein bestehender LEGO-Auftrag kann nur einem Agent-Profil bzw. PEM-Namen zugewiesen sein.

HTTP-01 und Wildcards lassen sich nicht kombinieren. Für HTTP-01 den Webroot-Dienst und die Challenge-Weiterleitung einrichten; siehe [AGENT.md](AGENT.md). Certbot und sein Cloudflare-Plugin bleiben als alternative ACME-Engine unterstützt.

## Eigene PEM-Dateien und Listener-Zuweisung

**PEM importieren** erwartet Zertifikatskette und passenden unverschlüsselten privaten Schlüssel in einer Datei. Eigene interne CA-Zertifikate sind möglich; Browser müssen der CA vertrauen.

Beim Proxy Host bzw. der übernommenen Domain-Zuordnung lässt sich ein Produktionszertifikat auswählen. Es wird zusätzlich auf dessen TLS-Frontend geladen; ein bisheriges Sammelzertifikat bleibt dabei erhalten. Unter **Frontends & Backends → Zertifikate** lässt sich die vollständige explizite Zertifikatsauswahl eines TLS-Frontends festlegen. Eine solche Auswahl ersetzt dessen bisherige `crt`-Zuweisung; daher alle weiterhin benötigten Zertifikate auswählen. Ohne explizite Auswahl bleibt die ursprüngliche Zuweisung erhalten. Eigene `crt-list`/`crt-store`-Definitionen weiter im Konfigurationseditor verwalten.

HAProxy wählt beim TLS-Verbindungsaufbau per SNI aus den geladenen Zertifikaten. Zertifikate sind an Domains/Listener gebunden, nicht an URL-Pfade. Für getrennte Zertifikate pro Domain sollten die SAN-Listen nicht überlappen. Der Generator prüft, ob gewählte Zertifikate auf dem Zielserver vorhanden, gültig und für die ausgewählte Domain passend sind. Danach **Konfiguration erzeugen → Prüfen → Anwenden**.

Referenzen: [LEGO v5-Befehle](https://go-acme.github.io/lego/references/ref-flags/), [Cloudflare mit LEGO](https://go-acme.github.io/lego/dns/cloudflare/), [Certbot-Erneuerung](https://eff-certbot.readthedocs.io/en/stable/using.html#renewing-certificates), [HAProxy TLS und SNI](https://www.haproxy.com/documentation/haproxy-configuration-tutorials/security/ssl-tls/basics-enable-tls/).
