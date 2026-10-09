# Server mit Tags und Standorten organisieren

Ein HAProxy-Server kann mehrere Tags und einen Standort erhalten. Beispiele: `Prod`, `Dev`, `Staging`, `Kunde A` oder `Edge` als Tags und `Frankfurt · Rechenzentrum 1` als Standort. Weitere Zuordnungen wie Region oder Betreiber lassen sich ebenfalls als Tags verwenden.

Infrastrukturen wie Firma A oder Homelab lassen sich zusätzlich zu Tags und Standorten zuweisen. Die [Anleitung für mehrere Infrastrukturen](INFRASTRUCTURES.md) erklärt den globalen Serverfilter und die gezielte Zertifikatsverwaltung.

## Zuordnungen bearbeiten

Unter **Server** auf einer Serverkarte **Zuordnung bearbeiten** öffnen. Tags mit Komma trennen und den optionalen Standort eingeben. **Zuordnung speichern** übernimmt die Angaben sofort in die Verwaltung. Zum Entfernen einzelne Tags aus dem Feld löschen oder das jeweilige Feld leeren.

Es sind bis zu **20 Tags mit je 40 Zeichen** und ein Standort mit **120 Zeichen** möglich. Zusätzliche Leerzeichen werden entfernt und doppelte Tags unabhängig von Groß-/Kleinschreibung zusammengefasst. Tags dürfen selbst keine Kommas enthalten. `Prod`, `Dev` und `Staging` bekommen eigene Farben; alle weiteren Tags stehen ebenfalls zur Verfügung.

Die Änderung benötigt keinen Agent-Token und funktioniert auch bei einem nicht erreichbaren Server. Sie verändert keine HAProxy-Konfiguration und führt keinen Reload aus. Ändert jemand dieselbe Zuordnung parallel, wird ein älterer Entwurf abgelehnt; die Serverliste neu laden und die Angaben abgleichen.

Neue Server können ihre Tags und den Standort direkt im ersten Schritt des **Einrichtungsassistenten** erhalten. Die Felder stehen auch beim manuellen Verbinden eines bereits installierten Agenten und beim Bearbeiten einer Serververbindung zur Verfügung.

## Filtern und Anzeigen

In der Serverliste stehen **Alle Tags**, **Alle Standorte** und ein Suchfeld zur Verfügung. Die Filter lassen sich kombinieren; ein Server muss alle gewählten Bedingungen erfüllen. Die Suche findet Servername, Agent-Adresse, Profil, Tags und Standort. **Ohne Standort** zeigt noch nicht zugeordnete Server, **Filter zurücksetzen** zeigt wieder die vollständige Liste.

Tags und Standort werden außerdem in der Serverübersicht angezeigt. Der Standort steht zusätzlich bei der Instanzauswahl für Konfigurationen, Statistiken und Topologie. Administratoren ändern Zuordnungen; Operatoren und Viewer können sie ansehen und danach filtern.

## Update

Die Anwendung erstellt automatisch eine zusätzliche Tabelle in MariaDB. Bestehende Server bleiben erhalten und haben zunächst keine Tags und keinen Standort. Es werden keine neuen ENV-Variablen oder Schlüssel benötigt. Ältere API-Clients, die diese Felder beim Bearbeiten einer Verbindung nicht mitsenden, behalten vorhandene Zuordnungen bei.

## Bestehende Server bearbeiten

Im allgemeinen Serverdialog ist der Agent-Token beim Bearbeiten optional: leer lassen, um den gespeicherten verschlüsselten Token beizubehalten. Name, Tags, Standort, Infrastruktur und Notiz werden ohne Agent-Aufruf gespeichert und funktionieren auch bei einem offline Host. Änderungen an Agent-Adresse, Profil, HTTP-Freigabe oder Token prüfen weiterhin die Verbindung vor dem Speichern. Einen neuen Token nur bei einem tatsächlichen Tokenwechsel eintragen. Beim erstmaligen Verbinden bleibt der Token erforderlich.
