# Metrikspeicherung und Aufbewahrung

## Was gespeichert wird

Der Hintergrund-Collector erfasst standardmäßig alle 30 Sekunden einen Messpunkt pro HAProxy-Instanz. Nur er schreibt neue Verlaufswerte. Wiederholte Statistikaufrufe, zusätzliche Benutzer und Browser-Tabs erzeugen keine weiteren Messpunkte.

Die Datenbank enthält globale numerische Werte: Sessions, HTTP-Anfragerate, Traffic und Zähler. Die aktuelle Zusammenfassung einschließlich Version, Laufzeit und einer begrenzten Fehlermeldung liegt in `metric_latest`, genau eine Zeile pro Instanz. Vollständige Runtime-CSV-Zeilen, Konfigurationsinventar, Maps und die gesamte `show info`-Antwort werden nicht historisch gespeichert. Frontends, Backends und Zielserver sind weiterhin als Live-Details vom Agenten verfügbar.

`metric_buckets` speichert drei Zeitauflösungen. Jeder Messpunkt aktualisiert die entsprechenden Intervalle; ein persistenter Zeitfenster-Check verhindert doppelte Collector-Messungen innerhalb des Abfrageintervalls, auch nach einem Neustart.

| Auflösung | Standardaufbewahrung | Maximale Intervalle pro Instanz, inklusive Randintervall |
| --- | --- | --- |
| 30 Sekunden | 2 Stunden | 241 |
| 5 Minuten | 1 Tag | 289 |
| 1 Stunde | 7 Tage | 169 |
| Gesamt | | **699** |

Pro Intervall werden Summen und Anzahl gültiger Messungen, Spitzenwerte, Erreichbarkeit und zuletzt beobachtete Zähler gespeichert. Das Datenvolumen hängt von Anzahl der Instanzen und Aufbewahrungszeiten ab. Die Anzahl der Frontends, Backends und Zielserver erhöht die Größe der historischen Datensätze nicht.

Diagramme zeigen für Sessions und HTTP-Anfragerate den gewichteten Durchschnitt gültiger Online-Messungen. Der Tooltip zeigt den Spitzenwert und den Anteil erreichbarer Messpunkte. Nicht erreichbare Messpunkte gelten als unbekannte Anfragerate, nicht als gemessene Null. Ausfälle und Lücken in der Sammlung werden unterschieden: Ausfallintervalle haben Messpunkte und 0 % Erreichbarkeit; fehlende Intervalle haben keine Messpunkte. Lücken werden nicht durch eine durchgehende Kurve überbrückt. Erreichbarkeit ist ein Anteil der Beobachtungen, keine sekundengenaue SLA-Berechnung.

Die Zeitfenster wählen eine geeignete Auflösung und liefern höchstens 500 kompakte Punkte. Der Server lädt dafür eine indizierte Zeitreihe, keine großen historischen JSON-Antworten. Zähler wie Requests und Bytes bleiben kumulative HAProxy-Zähler; nach einem Worker-Neustart bzw. Reload können sie zurückgesetzt sein.

## Konfiguration über ENV

In `.env` stehen folgende Werte; `docker-compose.yml` gibt sie explizit an den Container weiter:

```dotenv
METRICS_INTERVAL=30
METRICS_RAW_HOURS=2
METRICS_FINE_DAYS=1
METRICS_RETENTION_DAYS=7
```

- `METRICS_INTERVAL`: Collector-Intervall, 10–3600 Sekunden. Die Live-Ansicht aktualisiert sich weiterhin unabhängig alle 30 Sekunden.
- `METRICS_RAW_HOURS`: Aufbewahrung der 30-Sekunden-Intervalle, 1–24 Stunden.
- `METRICS_FINE_DAYS`: Aufbewahrung der 5-Minuten-Intervalle, 1–30 Tage.
- `METRICS_RETENTION_DAYS`: Aufbewahrung der Stundenintervalle, 1–365 Tage. Muss mindestens `METRICS_FINE_DAYS` entsprechen. Die Oberfläche bietet weiterhin Zeitfenster bis sieben Tage an.

Änderungen mit `docker compose -f docker-compose.yml up -d` aktivieren. Nicht mit `restart`: geänderte Container-Umgebungswerte benötigen die Neuerstellung. Eine längere Aufbewahrung erhöht die maximale Anzahl Intervalle entsprechend. Die Bereinigung entfernt abgelaufene Intervalle in kleinen Transaktionen; bei stark verkürzter Aufbewahrung kann die vollständige Bereinigung mehrere Collector-Durchläufe dauern.

Unter **Einstellungen → Metrikspeicher** sehen Administratoren die aktive Aufbewahrung, die Anzahl Intervalle, den geschätzten von MariaDB belegten Tabellenplatz und den Fortschritt der Übernahme alter Daten.

## Update einer bestehenden Installation

```bash
docker compose -f docker-compose.yml pull
docker compose -f docker-compose.yml up -d
```

Beim Start erstellt die Anwendung `metric_latest` und `metric_buckets`. Die Datenbank benötigt dafür die bereits bei der Erstinstallation erforderlichen Tabellenrechte. Vor dem Update die üblichen Datenbanksicherungen beibehalten. Die bestehende Tabelle `metrics` wird automatisch in kleinen, atomaren Transaktionen migriert:

1. Nur benötigte globale Werte werden durch SQL aus den alten JSON-Datensätzen gelesen. Große Runtime-Arrays werden nicht in die Anwendung geladen.
2. Werte innerhalb der Aufbewahrung werden zu den neuen Zeitintervallen zusammengefasst. Jüngste Datensätze werden zuerst übernommen; ältere Diagrammbereiche vervollständigen sich während der Migration.
3. Alte Zeilen werden in derselben Transaktion erst nach der Übernahme entfernt. Ein abgebrochener Vorgang kann fortgesetzt werden, ohne Messungen doppelt zu zählen. Bereits abgelaufene Daten werden gemäß Aufbewahrung bereinigt.

Aktuelle Zusammenfassungen werden durch alte Messungen nicht überschrieben. Einstellungen, Benutzer, HAProxy-Konfigurationen und Revisionen werden nicht verändert. Bei Fehlern bleiben die betroffenen alten Daten erhalten; im Container-Log steht der Fehler. Während des Updates nur eine aktive App-Instanz mit Collector betreiben; eine alte App-Version darf anschließend keine großen Snapshots mehr schreiben. Die Anwendung ist weiterhin für einen App-Worker ausgelegt.

## Bereits belegten Speicher zurückgeben

Nach der Migration kann die leere Tabelle `metrics` weiterhin großen Dateispeicher belegen. Unter **Einstellungen → Metrikspeicher → Speicher freigeben** lässt sie sich gezielt optimieren. Die Aktion ist nur für Administratoren und erst nach vollständiger Übernahme verfügbar. Sie führt `OPTIMIZE TABLE metrics WAIT 5` aus, erhält Datensätze und protokolliert die Aktion. Die Optimierung benötigt passende MariaDB-Rechte und kann kurzzeitig auf Datenbanksperren warten. Es erfolgt kein `TRUNCATE` oder Löschen der neuen Zeitreihen.

Alternativ nach Kontrolle, dass die alte Tabelle keine Zeilen mehr enthält, als Datenbankadministrator in der richtigen Anwendungsdatenbank ausführen:

```sql
SELECT COUNT(*) FROM metrics;
OPTIMIZE TABLE metrics;
```

Wie viel Dateispeicher zurückgegeben wird, hängt von Storage Engine und Tablespace-Einstellungen ab. [MariaDB beschreibt OPTIMIZE TABLE und seine Voraussetzungen](https://mariadb.com/docs/server/ha-and-performance/optimization-and-tuning/optimizing-tables/optimize-table). Die Platzanzeige in der Oberfläche beruht auf MariaDB-Tabellenstatistiken und ist eine Schätzung; sie umfasst keine Binlogs, Backups oder andere Datenbanken.
