# Live-Topologie

Unter **Topologie** zeigt die Oberfläche die aktive Konfiguration der ausgewählten HAProxy-Instanz. Die Ansicht ist auch mit der Rolle Viewer zugänglich und verändert weder Konfiguration noch Dienst.

Der Weg läuft von links nach rechts: **Frontend → Site / Dienst → Backend-Pool → Zielserver**. HTTP-/HTTPS-Sites, TCP-Listener und TLS-Passthrough werden anhand des HAProxy-Modus unterschieden. HTTP-Redirects, Prometheus und Statistikdienste, die direkt am Frontend antworten, erhalten einen eigenen Dienstknoten. Nicht zugeordnete Backends bleiben sichtbar. Ein `listen`-Abschnitt mit Servern zeigt seine Frontend- und Backend-Seite. Runtime-Adressen ergänzen konfigurierte Ziele; dynamische `server-template`-Instanzen werden aus der Runtime übernommen.

Domain-Routen stammen aus einfachen Host-ACLs, den vom Editor erzeugten Host-/Pfad-ACLs oder exakten Host-Maps mit `req.hdr(host),lower,map(...)` bzw. `map_str(...)`. Wildcard-ACLs des Editors werden erkannt. Komplexe Bedingungen bleiben als Regeln sichtbar; dynamische Backend-Ausdrücke erhalten einen Hinweis statt erfundener Zielverbindungen. Fehlende Maps, uneindeutige Map-Schlüssel und fehlende Backends werden gemeldet. Beliebige Inline-ACL-Werte und vollständige Konfigurationen werden nicht im Topologie-JSON ausgegeben.

## Bedienung

- Instanz über die vorhandene Serverauswahl wechseln.
- Domain, Proxy oder Zieladresse suchen; nach HTTP/HTTPS, TCP/TLS oder Frontend filtern.
- Einen Knoten anklicken: zugehörige Wege werden hervorgehoben und Details erscheinen unter dem Diagramm.
- Zoomen und im Diagramm horizontal/vertikal scrollen. Bei sehr großen Konfigurationen werden maximal 240 Knoten dargestellt; die Ansicht meldet weitere Knoten. Suche und Frontend-Filter durchsuchen weiterhin den vollständigen Graphen.
- Animation pausieren oder wieder starten. Die Betriebssystemeinstellung für reduzierte Bewegung wird berücksichtigt.

## Was die Animation bedeutet

**Gestrichelte Linien** zeigen ausschließlich konfigurierte Zuordnungen. Die Runtime liefert keine Zähler pro Frontend-Backend-Verbindung oder Domain. Daher animiert die Ansicht diese Zuordnungen nicht als vermeintlichen Domain-Traffic.

**Bewegte Punkte zwischen Backend und Zielserver** zeigen gemessene Aktivität des jeweiligen Zielservers. Dichte, Geschwindigkeit und Linienbreite steigen logarithmisch mit der Sessionsrate beziehungsweise dem Durchsatz. Ein Punkt entspricht keinem einzelnen Request. Ausgefallene bzw. deaktivierte Ziele werden rot markiert und erhalten keine Aktivitätsanimation.

**Langsam pulsierende Linien** zeigen aktive Sessions, wenn keine aktuelle Aktivitätsrate gemessen wurde. Eine bestehende TCP-Verbindung kann ruhen. HAProxy kann Byte-Zähler erst beim Verbindungsende aktualisieren; dann zeigt der berechnete Durchsatz die im Messintervall verbuchten Bytes. Das ist keine paketgenaue Messung des laufenden Datenstroms.

HTTP-Frontends zeigen HAProxys `req_rate` (Anfragen/s). Für HTTP-Backends wird die Änderung von `req_tot` zwischen zwei erfolgreichen Abfragen durch die verstrichene Zeit geteilt. Zielserver und TCP-Pools zeigen `rate` als **Sessions/s**; dieser Wert wird nicht als HTTP-Anfragenrate bezeichnet. Ein- und ausgehende Bytes werden ebenfalls als Intervallmittel berechnet. Nach einem Neustart, rückläufigen Zählern, längerer Messlücke oder beim ersten Abruf erscheinen fehlende Intervallwerte als „—“.

Wenn mehrere Sites oder Frontends denselben Pool verwenden, zeigt die Detailansicht ausdrücklich **Backend gesamt**. Diese Werte dürfen nicht als Nutzung einer einzelnen Domain interpretiert werden. Die Liste aktiver Pools sortiert zunächst nach gemessener Aktivität, dann nach vorhandenen Sessions; ihre Balken zeigen einen relativen Aktivitätsindikator, keine Auslastung in Prozent.

Die Runtime-Felder sind im [HAProxy Management Guide](https://docs.haproxy.org/3.2/management.html#9.1) dokumentiert.

## Aktualisierung und Speicher

Live-Messwerte werden alle **10 Sekunden** abgefragt, solange die Ansicht geöffnet und der Browser-Tab sichtbar ist. Parallele Abfragen derselben Ansicht werden verhindert. Die aktive Konfiguration und Maps werden im Management-Prozess höchstens 30 Sekunden zwischengespeichert; Änderungen werden daher spätestens beim folgenden Abruf nach Ablauf dieses Caches sichtbar. Der Cache ist auf 128 Instanzen begrenzt, und geänderte Agent-Verbindungsdaten invalidieren ihn.

Topologie und Detailzähler erzeugen **keine Datenbankeinträge**. Im Browser bleibt nur der aktuelle und vorherige Graph für die Berechnung der Intervallraten. Die bestehende kompakte Metrikaufbewahrung bleibt bestehen. Bei Offline-Meldung, Abruffehler oder veralteten Messwerten stoppt die Aktivitätsanimation. Nach einem Fehler bleibt der letzte Graph mit einem deutlichen Hinweis sichtbar.

Endpunkt: `GET /api/instances/{id}/topology`, nur mit gültiger Sitzung. Bestehende Agenten verwenden ihre vorhandenen Statistik- und Konfigurationsendpunkte; bei nicht lesbarer Konfiguration werden nur bekannte Runtime-Knoten und Backend-Server-Verbindungen angezeigt.
