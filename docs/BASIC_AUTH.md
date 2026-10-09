# Zentrale Basic-Auth-Benutzer für Websites

Unter **Basic Auth** verwaltest du Website-Benutzer und Benutzergruppen zentral in MariaDB. Diese Zugänge sind unabhängig von der Anmeldung an der Management-Oberfläche. Eine Gruppe kann mehrere Websites auf verschiedenen nativen oder Docker-HAProxy-Instanzen schützen; ein Benutzer kann mehreren Gruppen angehören.

## Website schützen

1. Als Administrator **Basic Auth → Gruppen → Gruppe hinzufügen** öffnen, z. B. „Intern“. Der **Realm** ist der Text im Browser-Anmeldedialog. Er erlaubt Buchstaben A–Z, Zahlen, Leerzeichen und `. : @ / _ -`.
2. Unter **Benutzer → Benutzer hinzufügen** Benutzername und Passwort vergeben (ohne Mindestlänge; auch kurze Passwörter sind möglich) und die gewünschte Gruppe auswählen. Benutzer ohne Gruppe erhalten keinen Site-Zugriff.
3. Die HAProxy-Instanz unter **Proxy Hosts** auswählen und die Website bearbeiten. Unter **Website-Zugang** die Gruppe auswählen und **Im Entwurf speichern** drücken. Bei importierten Konfigurationen unter **Übernommene Domain-Zuordnungen** den Einstellungsbutton neben der gewünschten Domain anklicken. Der Dialog **Domain-Zuordnung bearbeiten** zeigt den Zielserver und die Auswahl **Website-Zugang**; dort die Gruppe mit den gewünschten Benutzern zuweisen. Die aktuelle Zuordnung steht auch in der Spalte **Website-Zugang**.
4. **Konfiguration erzeugen** wählen. Im Konfigurationseditor vergleichen und **Prüfen & anwenden** ausführen. Der Agent prüft die Konfiguration mit dem HAProxy des Zielhosts und führt einen Reload aus.

Der Schutz gilt für die ausgewählte Website bzw. ihren Pfad. Andere Domains an einem gemeinsamen Backend behalten ihren bisherigen Website-Zugang. TCP-Pools unterstützen keine HTTP-Basic-Authentifizierung. HTTP-Challenges für Let's Encrypt bleiben bei vom grafischen Editor erzeugten Hosts erreichbar; HTTPS-Weiterleitungen erfolgen vor der Anmeldung. Für importierte Konfigurationen bleiben bestehende Challenge- und Redirect-Regeln maßgeblich.

## Änderungen auf Instanzen aktivieren

Passwort, Gruppenmitgliedschaften, Aktivierung und Realm lassen sich zentral ändern. Beim Bearbeiten eines Benutzers bedeutet ein **leeres Passwortfeld**, dass das bisherige Passwort erhalten bleibt. Deaktivierte oder gelöschte Benutzer erhalten nach Aktivierung der neuen HAProxy-Konfiguration keinen Zugriff mehr.

**Die Änderungen wirken erst nach Anwendung auf den betreffenden HAProxy-Instanzen.** Unter Basic Auth zeigt die Tabelle **Änderungen ausstehend**. Für jede betroffene Instanz **Konfiguration erzeugen → Prüfen & anwenden** ausführen. Die Anwendung lädt keine Dienste automatisch nach einer Benutzeränderung neu. Ältere erzeugte Entwürfe mit inzwischen veralteten Zugangsdaten werden beim Anwenden abgelehnt; dann erneut erzeugen.

Eine Gruppe ohne aktive Benutzer sperrt ihre zugewiesenen Websites weiterhin: Jede Anfrage erhält HTTP 401. Sie wird nicht automatisch öffentlich. Eine Gruppe kann erst gelöscht werden, wenn alle Website-Zuordnungen entfernt wurden, einschließlich deaktivierter Hosts. Zum Freigeben einer Website im Entwurf **Öffentlich · keine Basic Auth** auswählen und die Konfiguration anwenden.

Administratoren verwalten Benutzer und Gruppen. Operatoren können vorhandene Gruppen Websites zuordnen und Konfigurationen anwenden. Viewer haben keinen Zugriff auf die zentrale Benutzerverwaltung.

## Passwortspeicherung und Header

Die Anwendung speichert ausschließlich gesalzene SHA-512-crypt-Passworthashes mit 50.000 Runden. Passwörter und Hashes werden in den Benutzer-APIs nicht zurückgegeben; Klartextpasswörter stehen weder in erzeugten Konfigurationen noch im Änderungsprotokoll. HAProxy benötigt die Hashes in seiner Konfiguration; deshalb Konfigurationsversionen, Exporte, Agent-Sicherungen und Datenbank-Backups geschützt aufbewahren. Das Ziel-HAProxy muss SHA-512-crypt über seine Systembibliothek unterstützen; der HAProxy-Check vor dem Anwenden prüft die erzeugte Datei.

Basic Auth überträgt Zugangsdaten mit jeder Anfrage. Geschützte Websites über **HTTPS** betreiben. Nach erfolgreicher Prüfung entfernt HAProxy standardmäßig den `Authorization`-Header vor Weiterleitung an das Backend. Nur wenn die Zielanwendung ihn benötigt, **Authorization-Header an das Backend weitergeben** aktivieren.

Die Passwortprüfung kostet Rechenzeit pro Anfrage. Bei stark frequentierten geschützten Diensten die Auslastung des Ziel-HAProxy prüfen. Hinweise zum HAProxy-Verhalten stehen in der [offiziellen Basic-Auth-Anleitung](https://www.haproxy.com/documentation/haproxy-configuration-tutorials/security/authentication/basic-authentication/).

## Vorhandene Konfigurationen und Update

Vorhandene `userlist`-Abschnitte und eigene Authentifizierungsregeln bleiben beim Import im Text erhalten. Sie werden nicht automatisch zu zentralen Benutzern konvertiert. Ist eine Domain-Zuordnung grafisch bearbeitbar, kann sie anschließend eine zentrale Gruppe erhalten. Für bestehende Backend-Regeln bietet der Dialog **Domain-Zuordnung bearbeiten** eine ausdrückliche Umstellung:

1. Unter **Proxy Hosts → Übernommene Domain-Zuordnungen** die betroffene Domain bearbeiten.
2. Unter **Website-Zugang** die zentrale Gruppe auswählen. Der Dialog zeigt die vorhandenen Backend-Regeln an.
3. **Vorhandene Backend-Anmeldung für diese Domain ersetzen** bestätigen und den Entwurf speichern.
4. **Konfiguration erzeugen → Prüfen & anwenden** ausführen.

Die zentrale Gruppe ersetzt die bisherigen Backend-Anmelderegeln ausschließlich für diese Domain am gewählten Frontend. Andere Domains und Frontends am selben Backend behalten die ursprünglichen Regeln, einschließlich ihrer Bedingungen und Reihenfolge. Bestehende Benutzer werden nicht automatisch in die zentrale Gruppe übernommen. Ohne Bestätigung bleibt die Erzeugung bei einem Konflikt gesperrt; die Meldung unter **Frontends & Backends** führt zu den Domain-Zuordnungen.

Die ursprünglichen Regeln bleiben reversibel in markierten Blöcken erhalten. Nach erneutem Einlesen bleibt auch die bestätigte Umstellung erhalten. Entfernst du später die zentrale Gruppe, werden die ursprünglichen Backend-Anmelderegeln für diese Domain wieder wirksam; „keine zentrale Gruppe“ bedeutet bei solchen Imports daher nicht automatisch öffentlichen Zugang.

Eigene Authentifizierungsregeln im **Frontend** sowie geerbte Regeln in **defaults** benötigen weiterhin eine manuelle Abstimmung. Änderungen an der ursprünglichen importierten Basis vornehmen und diese erneut einlesen. Änderungen nur im erzeugten Textentwurf ändern die gespeicherte Importbasis nicht.

Nicht sicher kombinierbare geerbte `http-request`-Regeln in `defaults` werden bei der Erzeugung mit einem Hinweis abgelehnt. Diese Regeln bei Bedarf in die konkreten Frontend-/Backend-Abschnitte verschieben und erneut prüfen. Von dieser Anwendung erzeugter Basic-Auth-Schutz bleibt beim erneuten Einlesen erhalten, auch wenn einzelne Pfadrouten nur im Text bearbeitbar sind. Die Anwendung ersetzt ausschließlich ihre markierten Benutzerlisten und Authentifizierungsblöcke.

Das Update erstellt die zusätzlichen Basic-Auth-Tabellen automatisch. Es benötigt **keine neuen ENV-Variablen oder Schlüssel**. Bestehende Management-Benutzer und Konfigurationen erhalten ohne ausdrückliche Website-Zuordnung keinen Basic-Auth-Schutz. Anleitung und aktuelle Compose-Datei sind auch im Docker-ZIP enthalten.
