# Proxy Hosts, Frontends und Backends

Die gewählte Infrastruktur grenzt die Serverauswahl ein. Alle Änderungen betreffen den angezeigten Zielserver und werden zunächst als Arbeitsentwurf gespeichert.

## Reverseproxy hinzufügen

Unter **Proxy Hosts → Proxy Host** Domain, optionalen Pfad und Zielserver eintragen. Das Tool erzeugt den Backend-Pool `backend_<Host-ID>`, dessen Healthchecks und die Weiterleitung im gewählten HTTP-Frontend automatisch. Ein neuer grafischer Entwurf enthält bereits den gemeinsamen Listener `public_http` für HTTP und optional HTTPS. Bei einer importierten Konfiguration ein vorhandenes HTTP-/HTTPS-Frontend auswählen; bestehende Header, Redirects, TCP-Dienste und globale Einstellungen bleiben erhalten.

Optional eine zentrale Basic-Auth-Gruppe und ein Zertifikat wählen. Beim gemeinsamen Listener eines neuen Entwurfs aktiviert die Zertifikatsauswahl HTTPS. Bei bestehenden oder selbst angelegten Frontends muss TLS am Frontend eingerichtet sein. Die Konfiguration bleibt ein Entwurf bis zum Anwenden.

## Mehrere Hostnamen für einen Reverseproxy

Im Host-Dialog die Hauptdomain unter **Domain** und weitere Namen unter **Weitere Hostnamen** eintragen, pro Zeile einen Namen oder mit Komma getrennt. Beispiel: `pc-wiki.de` als Domain und `www.pc-wiki.de` als weiterer Hostname. Bis zu 30 Namen insgesamt teilen sich einen Backend-Pool, Pfad, HTTPS-Redirect, Basic-Auth-Gruppe und Zertifikatsauswahl. Beide Namen liefern dieselbe Website; die Domain wird dadurch nicht automatisch auf den anderen Namen umgeleitet.

Das Feld gibt es auch beim Bearbeiten einer **übernommenen Domain-Zuordnung**. Normale Proxy Hosts unterstützen zusätzlich Wildcards; übernommene Domain-Zuordnungen verwenden exakte Namen. Doppelte Namen für denselben Pfad und dasselbe Frontend werden abgelehnt. In der Übersicht lassen sich zusätzliche Namen aufklappen; die Suche nach normalen Proxy Hosts berücksichtigt sie ebenfalls.

Das ausgewählte Produktionszertifikat muss alle Namen abdecken. Bei der Zertifikatsanforderung schlägt **Umfang → Alle Sites dieses Servers gemeinsam** auch zusätzliche Namen vor. DNS-Einträge für jeden Namen müssen auf den HAProxy zeigen. Bei bereits separat importierten Einträgen zuerst die zusätzliche Domain-Zuordnung entfernen und den Namen am gemeinsamen Eintrag hinterlegen; unterschiedliche Zugangsregeln oder Zertifikate bleiben beim Import als getrennte Einträge erhalten.

## Änderungen anwenden

Unter **Proxy Hosts**, **Frontends & Backends** und **Regeln** führt **Prüfen & anwenden** nach Bestätigung den ganzen Ablauf für den angezeigten Server aus: Konfiguration erzeugen, HAProxy-Prüfung, Version und vorherige Konfiguration sichern, anwenden und Reload bestätigen. Die Ansicht wird anschließend automatisch aktualisiert. Zum vorherigen Vergleich weiterhin **Konfiguration erzeugen** verwenden und im Konfigurationseditor prüfen und anwenden.

Nach erfolgreichem Anwenden werden Hauptdatei, geladene Dateien und Maps erneut gelesen. Die Import-Basis wird aktualisiert, während grafische Hosts, eigene Frontends/Backends, Zertifikats- und Basic-Auth-Zuordnungen erhalten bleiben. Ein erneuter manueller Import nach jeder Änderung ist nicht nötig. Bereits durch ältere Management-Versionen veraltete Import-Basen werden beim Öffnen automatisch repariert, wenn sie eindeutig dem zuletzt angewendeten unveränderten Entwurf entsprechen.

Direkte Textänderungen oder Rollbacks werden nach dem Anwenden als neue aktive Import-Basis übernommen. Der Import erkennt die unterstützten Routen und Zielserver; komplexe Regeln bleiben vollständig im Texteditor erhalten. Wenn während des Anwendens ein anderer Benutzer den Entwurf oder jemand die aktiven Dateien verändert, bleibt dieser Stand erhalten und die Oberfläche zeigt den erforderlichen Abgleich an. Ein erfolgreicher Reload wird auch dann als erfolgreich ausgewiesen, wenn das anschließende Einlesen vorübergehend fehlschlägt.

## Frontends & Backends

Der eigene Navigationspunkt zeigt die Listener mit Bind-Adressen, HTTP-/TCP-Modus, TLS und den verbundenen Backend-Pools. Zielserver und umfangreiche Zuordnungen lassen sich aufklappen.

**Backend hinzufügen:** Name, HTTP oder TCP, Lastverteilung und einen oder mehrere Zielserver mit Port eingeben. Vorhandene importierte Pools lassen sich weiterhin in ihren erkannten Parametern bearbeiten. Automatische Proxy-Host-Pools über den zugehörigen Host bearbeiten.

**Frontend hinzufügen:** Name, Modus, Bind-IP und Port wählen. Für einen TCP-Dienst einen gleichartigen Backend-Pool als Standard zuweisen. Für HTTP können Proxy Hosts Domains/Pfade auf diesem Frontend verwalten; alternativ einen HTTP-Standardpool auswählen. TLS kann auf dem Frontend terminiert werden, sofern Zertifikate verfügbar sind. Bei TLS-Passthrough bleibt die TLS-Terminierung deaktiviert.

Beispiel MySQL: TCP-Backend `be_mysql` mit `192.168.10.235:3306`, danach TCP-Frontend `fe_mysql` auf `0.0.0.0:3306` mit Standardbackend `be_mysql`. Ports dürfen nicht mit bereits vorhandenen Listenern kollidieren. Bei Docker muss der entsprechende Containerport zusätzlich in der HAProxy-Compose-Datei veröffentlicht sein.

**Zertifikate** in einer TLS-Frontend-Zeile legt dessen explizite Produktionszertifikate fest. Eine Zertifikatsauswahl am Reverseproxy oder einer importierten Domain-Zuordnung hat für diese Domain Vorrang vor den Frontend-Zertifikaten. Eine zusätzliche Auswahl am Frontend ist nicht nötig. Die übrigen Domains behalten ihre bisherigen Zertifikate. Siehe [CERTIFICATES.md](CERTIFICATES.md).

Backend-Pools lassen sich unter **Frontends & Backends → Backends / Pools** über das Papierkorb-Symbol löschen, auch importierte HTTP-/TCP-Pools und Pools ohne statische Ziele. Bei **Proxy Hosts → Übernommene Backend-Pools** gibt es denselben Button. Die Bestätigung nennt betroffene Domains, Listener und Weiterleitungen. Zugehörige Proxy Hosts und Domain-Routen werden aus dem Entwurf entfernt; Standard- und bedingte Weiterleitungen auf den Pool werden ebenfalls entfernt. Ein importierter `listen`-Abschnitt wird einschließlich seines Listeners entfernt. Ein selbst angelegtes TCP-Frontend mit diesem Standardbackend wird ebenfalls entfernt; HTTP-Frontends bleiben ohne diese Standardzuordnung erhalten. Andere Pools und deren Optionen bleiben erhalten.

Löschen ändert zunächst den grafischen Entwurf. Danach **Prüfen & anwenden**. Ohne passende Route erhalten HTTP-Anfragen die HAProxy-Standardantwort; TCP-Verbindungen ohne Ziel werden beendet. Unterstützte Host-Maps werden berücksichtigt, einschließlich des Fallback-Pools. Nicht auflösbare dynamische Backend-Auswahl, Sample-Ausdrücke oder Server-Tracking mit Bezug auf den Pool sperren die Löschung mit einem Hinweis: Diese Regeln zuerst im Konfigurationseditor anpassen und erneut einlesen. Automatische System-Pools für Fehlerantworten und ACME werden durch ihre Funktionen verwaltet.

Neue Frontends können ebenfalls im grafischen Entwurf bearbeitet und entfernt werden. Vor dem Entfernen ihre Host-Zuordnungen ändern. Übernommene Listener lassen sich in ihren Zertifikaten bearbeiten; Bind-Adressen, komplexe ACLs oder weitere ursprüngliche Parameter weiterhin im Konfigurationseditor ändern. Konfigurationen mit dynamischen Regeln werden nicht pauschal in ein vereinfachtes Modell umgeschrieben.

Die Übersicht zeigt den Arbeitsentwurf. Ein noch unvollständiger Entwurf meldet den Grund; die aktive Konfiguration bleibt bis zur erfolgreichen Prüfung und Anwendung unverändert.

## Zertifikat direkt am Reverseproxy auswählen

Unter **Proxy Hosts** die Website bzw. die übernommene Domain-Zuordnung bearbeiten und **Zertifikat für HTTPS** auswählen. Das Frontend benötigt einen TLS-Bind, muss dieses Zertifikat aber nicht nochmals auswählen. Danach **Konfiguration erzeugen → Prüfen & anwenden**.

Die Anwendung erzeugt eine domainbezogene `crt-list`: Ein gewähltes Zertifikat wird für die zugewiesene Domain geladen, auch wenn das Frontend-Zertifikat dieselbe Domain, ein Wildcard oder zusätzliche Einzel-Domains enthält. Der Agent stellt die Liste unter `.control-tls` im Zertifikatsverzeichnis bereit. Bei der Prüfung bleibt sie temporär; erst erfolgreiches Anwenden hält sie vor. Bei einem fehlgeschlagenen Reload werden Konfiguration und Listen wiederhergestellt. Erneuerungen und erneutes Einlesen behalten die Domain-Zuordnung. Ohne zentrale Auswahl gelten wieder die ursprünglichen Frontend-Zertifikate.

TLS erfolgt vor der URL-Auswertung. Für dieselbe Domain am selben Frontend kann deshalb nur ein Zertifikat ausgewählt werden; verschiedene Zertifikate für `/` und `/admin` werden abgelehnt.

Für diese Funktion sowohl den Management-Container als auch den Agenten aktualisieren. Den Agent-Befehl findest du unter **Server → Agent aktualisieren**. Alte Agenten werden bei der Erzeugung mit einer verständlichen Meldung erkannt. Es sind keine neuen ENV-Variablen oder Tokens erforderlich. Eigene `crt-list`-/`crt-store`-Definitionen bleiben weiterhin manuell verwaltet. Erzeugte Konfigurationen verwenden zusätzlich die vom Agenten bereitgestellten Listen; bei manuellen Dateiexporten diese Dateien ebenfalls übernehmen.


## TLS zum Backend und Insecure verify

TLS am Zielserver verschlüsselt die Verbindung von HAProxy zum Backend. Bei Proxy Hosts und eigenen Backend-Pools erscheint nach Aktivieren von **TLS** bzw. **Ziel-TLS** die Option **Zertifikat nicht prüfen (Insecure verify)**. Sie gilt pro Zielserver und erzeugt `ssl verify none`, beispielsweise für interne Dienste mit selbstsignierten Zertifikaten. Ohne diese Auswahl bleibt `verify required` mit dem System-CA-Bundle aktiv. Bei Hostnamen wird SNI auch ohne Zertifikatsprüfung gesendet.

Bei übernommenen Backend-Pools wird die vorhandene Zertifikatsprüfung aus `server`, `default-server` und `ssl-server-verify` erkannt. Bei bestehenden TLS-Zielen lässt sie sich im Backend-Dialog ändern; zusätzliche Serveroptionen und Kommentare bleiben erhalten. TLS selbst und komplexe TLS-Parameter einer übernommenen Verbindung weiterhin im Texteditor ändern. Ältere gespeicherte Import-Entwürfe behalten ihre ursprüngliche Prüfung. Diese Option betrifft die Backend-Verbindung; die HTTPS-Zertifikate der Website werden weiter separat verwaltet.


Beim erneuten Einlesen einer vom Tool erzeugten Konfiguration bleiben erstellte Hosts reguläre **Proxy Hosts**. IDs, Alias-Namen, Pfade, Backend-TLS einschließlich Insecure verify, zentrale Basic-Auth-Gruppen und Zertifikate werden wiedererkannt. Neue Konfigurationen merken sich zusätzlich den vollständigen grafischen Aufbau einschließlich deaktivierter Hosts und eigener Frontends/Backends. Die Vorschau nennt die wiedererkannten Tool-Hosts. Siehe [IMPORT.md](IMPORT.md).

## Live-Healthchecks der Reverseproxys

Unter **Proxy Hosts** steht bei jedem eigenen Proxy Host und jeder übernommenen Domain-Zuordnung ein **Healthcheck**. Die Anzeige liest alle zehn Sekunden die aktuellen HAProxy-Runtime-Daten und die geladenen Konfigurationsdateien des ausgewählten Servers:

- **Grün – Erreichbar:** HAProxy meldet alle geprüften Ziele als verfügbar.
- **Gelb – Teilweise erreichbar:** Mindestens ein Ziel ist verfügbar; andere sind ausgefallen, fehlen oder nehmen keine neuen Verbindungen an.
- **Rot – Ausgefallen:** Alle Ziele sind DOWN. Ein gestopptes Frontend wird gesondert angezeigt.
- **Wartung / Drain**, **Ohne Healthcheck**, **Lokale Antwort** und **Unbekannt** unterscheiden administrative Zustände, ungeprüfte Ziele, lokale HAProxy-Antworten und fehlende Daten.
- **Noch nicht angewendet:** Domain, Pfad oder Backend-Ziele des Entwurfs stimmen noch nicht mit der aktiven Konfiguration überein. Die Verfügbarkeit alter Ziele wird dann nicht dem neuen Entwurf zugeschrieben.

Die Anzeige lässt sich aufklappen: Sie zeigt pro Ziel Adresse, Backup-Status, HAProxy-Checkstatus, gegebenenfalls HTTP-Statuscode und Checkdauer. Mehrere Domains mit demselben Backend-Pool teilen dessen Zustand. Deaktivierung im Entwurf und bereits angewendete Deaktivierung werden getrennt erkannt. Nach Speichern oder Anwenden wird der Zustand erneut abgefragt, ohne offene Formulare oder den Entwurf zu überschreiben.

Automatische Proxy-Host-Pools verwenden zunächst einen TCP-Verbindungscheck (`check`). Dieser bestätigt die Verbindung zum Zielport. Ein vollständiger Webseiten-Aufruf über öffentliche DNS-Auflösung, Frontend-TLS und Anmeldung wird damit nicht geprüft. Vorhandene HTTP-, TLS- oder eigene HAProxy-Checks werden unverändert verwendet; zusätzliche Check-Optionen können im Konfigurationseditor gesetzt werden.

Abfragen schreiben keine Metrikdatensätze und keine zusätzliche Historie in MariaDB. Bei fehlender Verbindung werden alte grüne Werte ausgeblendet; nach 45 Sekunden gelten nicht erneuerte Werte als veraltet. Im Hintergrund ausgeblendete Browser-Tabs pausieren die Abfragen und aktualisieren beim Wiederöffnen. Für diese Funktion genügt ein Update des Management-Containers; es sind keine neuen Agent-ENV-Variablen, Tokens oder Agent-Endpunkte erforderlich.

## Proxy-Optionen pro Reverseproxy

Unter **Proxy Hosts → Bearbeiten → Proxy-Optionen** können HAProxy-Direktiven zeilenweise ergänzt, verändert oder gelöscht werden. Der HTTP-Modus wird automatisch gesetzt; ein mitkopiertes `mode http` ist erlaubt und wird beim Speichern aus dem Optionsfeld entfernt. Eigene Proxy Hosts verwalten diese Optionen in ihrem automatisch erzeugten Backend-Pool. Bei übernommenen Domain-Zuordnungen zeigt der gleiche Editor die lokalen Optionen des zugeordneten Backend-Pools. Änderungen daran gelten für alle Domains und sonstigen Listener, die diesen Pool nutzen; die zugeordneten Hostnamen werden angezeigt. HTTP-Pools lassen sich außerdem unter **Frontends & Backends → Backend bearbeiten** bzw. **Übernommene Backend-Pools → Bearbeiten** konfigurieren.

Beispiel für eine Anwendung unter einem anderen Zielpfad:

```haproxy
http-request set-path /reset-password%[path]
http-request set-header Host %[req.hdr(host)]
http-request set-header X-Forwarded-Host %[req.hdr(host)]
http-request set-header X-Forwarded-Proto https
http-request set-header X-Forwarded-Port 443
option forwardfor header X-Forwarded-For
```

Damit wird `/login?token=abc` zum Zielpfad `/reset-password/login?token=abc`. Domain-/Pfad-Routing und zentrale Basic Auth werden davor ausgewertet. Die Rewrite-Regel ändert den an den Backend-Server gesendeten Pfad.

**Forwarded-Header hinzufügen** ergänzt die üblichen Header, ohne vorhandene Werte derselben Direktive zu ersetzen oder doppelt anzulegen. Die Vorlage ermittelt Protokoll und Frontend-Port dynamisch (`ssl_fc` und `dst_port`); feste Werte wie im Beispiel sind ebenfalls möglich. **Pfad-Präfix als Beispiel** fügt die erste Beispielzeile hinzu. Bestehende Pfad-Umschreibungen werden dabei beibehalten. Einzelne Optionen durch Löschen ihrer Zeile entfernen, oder **Alle Optionen entfernen** verwenden.

Unterstützt werden HTTP-Pfad-/URI-/Query-Umschreibungen, Request-/Response-/After-Response-Header, Variablen, `option`, `no option` und Backend-Timeouts. Reihenfolge, HAProxy-Ausdrücke, Anführungszeichen und Inline-Kommentare bleiben erhalten; leere Zeilen und reine Kommentarzeilen werden nicht als Optionen gespeichert. Optionen aus `defaults` bleiben geerbt. Zum Deaktivieren einer geerbten Option beispielsweise `no option forwardfor` verwenden. Listener, Serverdefinitionen, allgemeines Routing und Authentifizierung werden über ihre eigenen Einstellungen oder den vollständigen Konfigurationseditor bearbeitet.

Bei Imports werden unterstützte lokale Optionen aus dem ursprünglichen Backend übernommen; bereits gespeicherte Import-Entwürfe erhalten diese Informationen beim Laden. Ein ausdrücklich geleertes Optionsfeld entfernt sie. Nicht bearbeitete Konfigurationszeilen, zusätzliche Server-/Healthcheck-Parameter und bestehende Authentifizierungsregeln bleiben erhalten. Erneutes Einlesen erkennt gespeicherte Optionen wieder. Die endgültige Syntax prüft der HAProxy-Prozess des Zielservers vor dem Anwenden; erst **Im Entwurf speichern → Prüfen & anwenden** aktiviert die Änderung.

Für diese Funktion genügt ein Update des Management-Containers. Das Docker-Archiv und der Installer enthalten das zusätzliche Schema-Hilfsmodul für künftige Agent-Installationen.


## IP-Zugriff pro Reverseproxy

Unter **Proxy Hosts → Bearbeiten → IP-Zugriff** (auch bei übernommenen Domain-Zuordnungen) **Zugriff auf erlaubte IP-Adressen und Netze beschränken** aktivieren. Erlaubte IPv4-/IPv6-Adressen oder CIDR-Netze eintragen, z. B. `192.168.10.0/24`, `10.8.0.12` und `2001:db8::/32`. Ein Eintrag pro Zeile oder durch Kommas getrennt. CIDR-Netze werden auf ihre Netzadresse normalisiert. Bei aktivierter Beschränkung muss mindestens eine Adresse bzw. ein Netz angegeben sein.

**Geschützte Pfad-Präfixe** leer lassen, um den gesamten Eintrag mit allen Hostnamen und seinem Proxy-Host-Pfad einzuschränken. Für einzelne Bereiche absolute Präfixe wie `/admin` oder `/internal` eintragen. Andere Pfade bleiben ohne diese IP-Beschränkung erreichbar. `/admin` umfasst auch `/administrator`; `/admin/` umfasst nur das Präfix mit dem abschließenden Schrägstrich. Bei einem Proxy-Host-Pfad `/api` müssen geschützte Präfixe ebenfalls unter `/api` liegen. Mehrere passende Beschränkungen gelten gemeinsam, auch bei überlappenden Wildcard-Domains oder Proxy-Host-Pfaden.

Anfragen außerhalb der erlaubten Netze erhalten **HTTP 403**. Die Prüfung erfolgt am HTTP-Frontend vor lokalen Weiterleitungen, Basic Auth, Header- und Backend-Pfadänderungen und gilt nur für die jeweilige Domain einschließlich ihrer zusätzlichen Hostnamen. Andere Domains am selben Backend-Pool behalten ihre Einstellungen. Geprüft werden der ursprüngliche URL-Pfad und eine einmal URL-dekodierte Form. Anwendungsinterne alternative URLs, mehrfaches Dekodieren oder Pfadnormalisierung können andere Pfade abbilden: entsprechende Präfixe aufnehmen bzw. den ganzen Eintrag einschränken; die Anwendung sollte sensible Funktionen selbst absichern.

Es gilt die Client-IP, die HAProxy sieht (`fc_src`), nicht ein vom Besucher gesetzter `X-Forwarded-For`-Header. Bei CDN, vorgeschaltetem Proxy oder NAT kann daher dessen Adresse maßgeblich sein. HTTP-Regeln mit `set-src` verändern diese ursprüngliche Verbindungsadresse nicht. Bereits vorhandene Änderungen der Verbindungsadresse durch TCP-Regeln bzw. PROXY-Protokoll benötigen eine verlässliche Vertrauensgrenze. `set-src` in TCP-Regeln des Frontends und HTTP-Regeln in geerbten `defaults` werden für zentral geschützte Einträge mit einem konkreten Hinweis abgelehnt, da sie vor der zentralen Prüfung ausgeführt werden könnten. Die Regeln werden dabei nicht automatisch entfernt.

IP-Liste und zentrale Basic Auth gelten gemeinsam: Auch eine erlaubte Adresse muss sich anmelden, wenn eine Gruppe zugewiesen ist. Die explizit aktivierte HTTP-Challenge des eigenen `public_http`-Listeners bleibt für ACME erreichbar. Eigene importierte Challenge-Regeln entsprechend in der Pfadauswahl berücksichtigen.

Nach **Im Entwurf speichern → Prüfen & anwenden** wird die Einschränkung aktiv. Deaktivieren des Schalters entfernt die zentralen IP-Regeln; ursprüngliche manuelle Zugriffsbeschränkungen bleiben erhalten. Die Einstellungen werden beim erneuten Einlesen wiederhergestellt. Markierte IP-Regelblöcke und ihre Metadaten müssen zusammenpassen; manuell veränderte Blöcke werden nicht stillschweigend überschrieben. Diese Funktion benötigt ein Update des Management-Containers, keine neuen ENV-Variablen und keinen Agent-Update.

Syntax und Regelreihenfolge: [HAProxy-Konfigurationshandbuch](https://docs.haproxy.org/3.2/configuration.html#4.2-http-request).
