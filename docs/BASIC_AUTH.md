# Zentrale Basic-Auth-Benutzer für Websites

Unter **Basic Auth** verwaltest du Website-Benutzer und Benutzergruppen zentral in MariaDB. Diese Zugänge sind unabhängig von der Anmeldung an der Management-Oberfläche. Eine Gruppe kann mehrere Websites auf verschiedenen nativen oder Docker-HAProxy-Instanzen schützen; ein Benutzer kann mehreren Gruppen angehören.

## Website schützen

1. Als Administrator **Basic Auth → Gruppen → Gruppe hinzufügen** öffnen, z. B. „Intern“. Der **Realm** ist der Text im Browser-Anmeldedialog. Er erlaubt Buchstaben A–Z, Zahlen, Leerzeichen und `. : @ / _ -`.
2. Unter **Benutzer → Benutzer hinzufügen** Benutzername und Passwort mit **mindestens 10 Zeichen** vergeben und die gewünschte Gruppe auswählen. Benutzer ohne Gruppe erhalten keinen Site-Zugriff.
3. Die HAProxy-Instanz unter **Proxy Hosts** auswählen und die Website bearbeiten. Unter **Website-Zugang** die Gruppe auswählen und **Im Entwurf speichern** drücken. Bei importierten Konfigurationen steht diese Auswahl in den bearbeitbaren Domain-Zuordnungen zur Verfügung.
4. **Konfiguration erzeugen** wählen. Im Konfigurationseditor vergleichen und **Prüfen & anwenden** ausführen. Der Agent prüft die Konfiguration mit dem HAProxy des Zielhosts und führt einen Reload aus.

Der Schutz gilt für die ausgewählte Website bzw. ihren Pfad. Andere Domains an einem gemeinsamen Backend bleiben öffentlich. TCP-Pools unterstützen keine HTTP-Basic-Authentifizierung. HTTP-Challenges für Let's Encrypt bleiben bei vom grafischen Editor erzeugten Hosts erreichbar; HTTPS-Weiterleitungen erfolgen vor der Anmeldung. Für importierte Konfigurationen bleiben bestehende Challenge- und Redirect-Regeln maßgeblich.

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

Vorhandene `userlist`-Abschnitte und eigene Authentifizierungsregeln bleiben beim Import im Text erhalten. Sie werden nicht automatisch zu zentralen Benutzern konvertiert. Ist eine Domain-Zuordnung grafisch bearbeitbar, kann sie anschließend eine zentrale Gruppe erhalten. Bereits vorhandene `http-request auth`-Regeln im betroffenen Frontend oder Backend müssen vorher im Texteditor abgestimmt werden; die Anwendung lehnt eine doppelte Authentifizierung ab.

Nicht sicher kombinierbare geerbte `http-request`-Regeln in `defaults` werden bei der Erzeugung mit einem Hinweis abgelehnt. Diese Regeln bei Bedarf in die konkreten Frontend-/Backend-Abschnitte verschieben und erneut prüfen. Von dieser Anwendung erzeugter Basic-Auth-Schutz bleibt beim erneuten Einlesen erhalten, auch wenn einzelne Pfadrouten nur im Text bearbeitbar sind. Die Anwendung ersetzt ausschließlich ihre markierten Benutzerlisten und Authentifizierungsblöcke.

Das Update erstellt die zusätzlichen Basic-Auth-Tabellen automatisch. Es benötigt **keine neuen ENV-Variablen oder Schlüssel**. Bestehende Management-Benutzer und Konfigurationen erhalten ohne ausdrückliche Website-Zuordnung keinen Basic-Auth-Schutz. Anleitung und aktuelle Compose-Datei sind auch im Docker-ZIP enthalten.
