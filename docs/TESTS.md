# Validierung am Entwicklungsserver

Stand: 8. Oktober 2026.

- Frontend-Produktionsbuild mit TypeScript erfolgreich.
- **160 Unit-Tests bestanden**, zusätzlich vier aktuelle Basic-Auth-Laufzeittests und zwei Migrationstests mit echten nativen und Docker-HAProxy-Instanzen. Die zuvor geprüften sechs Integrationstests für allgemeine Authentifizierung und Dienstaktionen sind unten beschrieben.
- Unit-Tests für Konfigurationsgenerator, Eingabevalidierung, Agent-Authentifizierung, Konflikterkennung, lokale Wiederherstellung bei Reload-Fehlern, Zertifikat-/Schlüsselvergleich, Certbot-Aufrufe, Erneuerung geänderter Produktionszertifikate und HTTP-Challenge-Webroot.
- Integrationstests an der angegebenen MariaDB und zwei isolierten echten HAProxy-Installationen: offizieller Docker-Container HAProxy 3.2.25 und natives HAProxy 3.0.11 mit eigener temporärer systemd-Unit. Prüfung generierter Hosts/Wildcards/ACLs/Redirects; Anwendung und Wiederherstellung von Revisionen; erfolgreicher Reload mit neuem Worker; Stop, Start, Restart; Login, CSRF, Rollenrechte, obligatorischer Passwortwechsel, Versionskonflikte.
- Migrationstests: mehrere automatisch erkannte `-f`-Dateien und Verzeichnisse, Host-Map-Konvertierung, Erhaltung zusätzlicher Direktiven, Änderung einer Domain und eines Backend-Ports, echte HTTPS-Anfragen, TCP-Forwarding und Wiederherstellung der vorherigen Konfiguration. Externe Änderungen an weiteren Dateien bzw. Maps werden vor Übernahme und Anwendung erkannt; lokale Reload-Fehler stellen sämtliche Originaldateien wieder her.
- Aktuelle Chromium-Prüfung auf Desktop und 390-Pixel-Mobilansicht: getrennte Frontend-/Backend-Statistiken, HTTP-/TCP-Filter, Suche nach Zielservern, Importvorschau, Übernahme ohne Reload, Backend- und Domain-Bearbeitung, Domain hinzufügen, Erzeugung des Entwurfs und Agent-Aktualisierungsbefehl.
- Chromium-Browsertest an der laufenden Docker-Webanwendung: erster Login und Passwortwechsel, Serververbindung, Hosts und Regeln speichern, Listener bearbeiten, Konfiguration erzeugen/prüfen/speichern, Zertifikatsformular, echte Runtime-Statistiken, Navigation, mobile Ansicht und Logout. Testbenutzer und Testinstanzen anschließend entfernt.

Die Test-HAProxy-Instanzen sind ausschließlich für Validierung bestimmt; bestehende Dienste wurden nicht verändert. Automatisierte Tests für Certbot verwenden kontrollierte Ersatzaufrufe. Eine echte Let's-Encrypt-Ausstellung bzw. DNS-Änderung wurde ohne echte Ziel-Domains und Cloudflare-Zugangstoken nicht ausgeführt. Agent-TLS muss auf den jeweiligen Zielhosts eingerichtet werden; LAN-HTTP der Management-WebUI entspricht dem gewünschten ersten Betrieb per IP.

Die zusätzlichen Migrationstests in `tests/test_import_integration.py` benötigen eine explizite `HAPROXY_IMPORT_LAB_CONFIG` mit getrennten temporären HAProxy-Profilen (`native-import`, `docker-import`) und lokalen HTTP-/TCP-Testzielen. Sie führen echte Reloads und Dateimigrationen aus und sind ohne diese Labor-Konfiguration deaktiviert. CI führt die Unit-Tests einschließlich `tests/test_config_import.py` aus.

## Kompakter Metrikspeicher

21 zusätzliche Unit-Tests prüfen kompakte erlaubte Felder, ungültige Messwerte, große ganzzahlige Zähler, persistente Vermeidung doppelter Messpunkte, gewichtete Durchschnitte und Spitzen, Erreichbarkeit, Sammlungslücken, Zeitauflösungen und Aufbewahrung, atomare/idempotente Alt-Datenmigration, FK-Löschungen, schreibfreie Statistikaufrufe, Rollenrechte und Schutz vor zu früher Tabellenoptimierung.

In einer isolierten MariaDB 11.8.6 wurden 300 synthetische Alt-Snapshots mit **107,36 MB JSON-Nutzdaten** übernommen. Ergebnis: **276 kompakte Intervalle**, 37,96 KB für deren JSON-Zählerdaten inklusive aktueller Zusammenfassung und **180,22 KB belegter Metriktabellen einschließlich Indizes** laut MariaDB-Statistik. Migration ca. 5,79 Sekunden in diesem Test. Spitzenwerte, Online-/Offline-Zählung, große Zähler und persistente Vermeidung doppelter Messungen geprüft. Zwölf Statistikaufrufe änderten die Zahl historischer Zeilen nicht. Die explizite Tabellenoptimierung erhielt die neuen Zeitreihen und verkleinerte die alte .ibd-Datei von 113,25 MB auf 98,3 KB. Alle Testcontainer und Testvolumes wurden entfernt. Diese Messung ist ein synthetisches Beispiel, keine Größenmessung der Nutzerinstallation.

Aktuelle Browserprüfung für den Metrikspeicher auf Desktop und 390-Pixel-Mobilansicht: Migrationsfortschritt, Aktualisierung unter Einstellungen, geschützte Speicherfreigabe erst nach Migration sowie Diagramm-Tooltip mit Durchschnitt, Spitzenwert und Erreichbarkeit.

## Live-Topologie

17 zusätzliche Unit-Tests prüfen Host-Maps und gemeinsame Pools ohne erfundene Domain-Zähler, TCP-Ziele und nicht zugeordnete Pools, direkte Frontend-Dienste, generierte Host-/Pfad-/Wildcard-ACLs, komplexe und dynamische Regeln, fehlende Maps/Backends, Runtime-Fallback, dynamische Server, aktuelle Zieladressen, geheime Inline-ACL-Werte und Zahlenvalidierung. API-Prüfung: Login erforderlich, Viewer-Zugriff, 12 Aufrufe ohne Metrik-Schreibzugriffe, zeitlich/betragsmäßig begrenzter Konfigurationscache und Invalidierung bei Agent-Profilwechsel.

Chromium auf Desktop und 390-Pixel-Mobilansicht: Graph und Pfadhervorhebung, gemeinsame Backend-Details, Domain-/Protokoll-/Frontend-Filter, Animation und Pausierung, TCP-Intervallwerte, reduzierte Bewegung, Offline-Zustand und kein Seitenüberlauf oder JavaScript-Fehler. Isolierte echte native und Docker-HAProxy-Prozesse: 35 HTTP-Anfragen über zwei Domains an einen gemeinsamen Pool, tatsächliche Frontend-/Backend-Zähler, TCP-Echo über eine offene Session und verbuchte Byte-Zähler nach Verbindungsende. Testprozesse und Container anschließend entfernt.

## Zentrale Basic Auth

30 zusätzliche Unit-Tests prüfen mindestens zehn Passwortzeichen, Hashes mit zufälligem Salt, Schutz vor Konfigurationsinjektion, eigene und importierte Domain-/Pfadrouten, gemeinsame Backends, leere Gruppen, Header-Weitergabe, wiederholten Import und Erhaltung eigener Regeln. API-Tests prüfen Rollen, CSRF, konkurrierende Versionen, Mitgliedschaften, Passwortbeibehaltung, Löschschutz, fehlende Gruppen, ausstehende Aktivierung und Ablehnung veralteter Passwörter in Revisionen. Die Benutzer-API und das Audit enthalten keine Passwörter oder Hashes; API-Aufrufe erzeugen keine Metrikzeilen.

Vier echte Laufzeittests prüfen jeweils native und Docker-HAProxy-Prozesse mit generierter und importierter Konfiguration: HTTP 401 ohne/falsche Zugangsdaten, Zugriff für passende Gruppen, öffentliche Domains am selben Backend, leere Gruppen, HTTPS-Redirects, ACME-Ausnahme, Authorization-Header und unverändertes TCP-Forwarding. Ein echter Reload aktiviert ein neues Passwort und weist das alte anschließend ab. Aktivierung: `HAPROXY_BASIC_AUTH_LAB=1 python -m pytest tests/test_basic_auth_runtime.py -q`; benötigt Docker mit dem Testimage und ein lokales HAProxy-Binary (optional `HAPROXY_BASIC_AUTH_BINARY`). Testprozesse und Container werden entfernt.

Chromium auf Desktop und 390-Pixel-Mobilansicht prüft Gruppen-/Benutzerverwaltung, Passwortbeibehaltung, Deaktivierung, eigene und importierte Website-Zuordnungen, ausstehende Verteilung, Erzeugung ohne automatisches Anwenden und Operatorrechte.

Zusätzliche API-Prüfung an einer isolierten MariaDB 11.8.6: fünf Szenarien für Benutzerverwaltung, Rollen, Gruppen, Versionskonflikte und Aktivierungsstatus bestanden. Zwei parallele Gruppenanlagen ergeben einmal HTTP 201 und einmal HTTP 409. Die Sperre beginnt mit einer frischen Transaktion, um veraltete Authentifizierungs-Snapshots bei MariaDB zu vermeiden. Testcontainer und Datenvolumes entfernt.

## Server-Tags und Standorte

16 zusätzliche Unit-Tests prüfen Unicode-/Leerzeichennormalisierung, doppelte Tags, Längen- und Mengengrenzen, ungültige Werte, bestehende Server ohne Zuordnung, Bearbeitung ohne Agent-Aufruf, Versionskonflikte, Rollen, CSRF, FK-Löschung und Kompatibilität mit älteren Clients. Der Einrichtungsassistent übernimmt die Zuordnungen in die Verbindungsdaten; sie stehen nicht im Installationsbefehl. Änderungen erzeugen keine Metrikzeilen und ändern weder Agent-Zugang noch HAProxy-Entwurf.

Chromium auf Desktop und 390-Pixel-Mobilansicht: kombinierte Tag-/Standortfilter, Suche, Server ohne Standort, leere Trefferliste, Filter zurücksetzen, Zuordnung ohne Token, manuelle Texteingabe mehrerer Tags, Übernahme im Einrichtungsassistenten und Filter mit Viewerrechten.

In einer isolierten MariaDB 11.8.6 bestanden fünf API-Szenarien, die Erweiterung eines Schemas mit vorhandenen Servern sowie zwei gleichzeitige Zuordnungsänderungen (HTTP 200 und HTTP 409). Vorhandene Notizen und Server blieben beim Erstellen der neuen Tabelle erhalten. Testcontainer und Datenvolumes wurden entfernt.
