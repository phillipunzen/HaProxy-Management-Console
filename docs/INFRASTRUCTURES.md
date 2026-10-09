# Mehrere Infrastrukturen verwalten

Eine Infrastruktur ist eine organisatorische Gruppe, z. B. **Firma A**, **Firma B** oder **Homelab**. Jede HAProxy-Instanz gehört optional genau einer Infrastruktur an. Tags wie `Prod` und `Dev` sowie der Standort bleiben unabhängig davon verfügbar. Eine Instanz entspricht einem Agent-Profil für einen nativen Dienst oder einen Docker-Container.

## Gruppen und Server zuordnen

1. Als Administrator **Infrastrukturen → Infrastruktur hinzufügen** öffnen und Name sowie optional eine Beschreibung speichern.
2. Unter **Server → Zuordnung bearbeiten** die Infrastruktur eines bestehenden Servers auswählen und speichern. Das funktioniert auch ohne Agent-Verbindung oder Token und führt keinen HAProxy-Reload aus.
3. Neue Server bekommen die Zuordnung im Einrichtungsassistenten oder beim manuellen Verbinden. Wenn oben eine Infrastruktur ausgewählt ist, wird sie für neue Server vorausgewählt.
4. Über die Auswahl **Infrastruktur** oben auf der Seite zwischen **Alle Infrastrukturen**, **Ohne Zuordnung** und einer bestimmten Gruppe wechseln. Serverkarten, Übersichtszähler und die Auswahl der HAProxy-Instanz berücksichtigen diesen Filter.

Infrastrukturen lassen sich umbenennen und beschreiben. Zum Löschen zuerst alle Server-Zuordnungen entfernen oder die Server anderen Gruppen zuordnen. Beim Entfernen einer Serververbindung bleibt die Infrastruktur bestehen. Parallele Änderungen werden durch Versionsprüfung abgefangen.

Die Gruppierung ändert keine Agent-Adresse, Zugangstokens, Konfigurationen, Zertifikate oder laufenden Dienste. Sie verschiebt keine Dateien zwischen Servern.

## Zertifikate gezielt installieren

**Zertifikate** öffnen, die gewünschte **HAProxy-Instanz** auswählen und den angezeigten **Zielserver**, die Infrastruktur und das Profil prüfen. Danach ein Let's-Encrypt-Zertifikat anfordern oder ein vorhandenes PEM samt passendem privaten Schlüssel importieren. Die Aktion wird ausschließlich an dieses Agent-Profil gesendet; Produktionszertifikate werden dort geprüft, installiert und durch Reload aktiviert. Staging-Zertifikate bleiben getrennt.

Die Zertifikatsliste zeigt das Zertifikatsverzeichnis dieses Profils. Es gibt keine automatische Verteilung an sämtliche Server oder an alle Mitglieder einer Infrastruktur. Soll ein bestehendes PEM auf einem zweiten Server verfügbar sein, dort ausdrücklich den zweiten Zielserver auswählen und das PEM erneut importieren. Private Schlüssel verbleiben auf den Agent-Hosts; die Verwaltung bietet keinen zentralen Schlüssel-Export oder Zertifikats-Tresor.

Cloudflare- und Hetzner-Cloud-Zugangsdaten werden im Zertifikatsdialog des ausgewählten Servers hinterlegt. Für unterschiedliche Konten eigene Tokens pro Auftrag verwenden; gespeicherte Zugänge können nur innerhalb desselben Agent-Profils und Providers wiederverwendet werden. Mehrere Domains und Wildcards pro Zertifikat werden unterstützt, solange sie mit demselben Provider-Zugang validiert werden können. Bestehende Certbot-Aufträge behalten ihre lokal konfigurierten Credential-Dateien. [Einrichtung und Provider-Rechte](CERTIFICATES.md).

### Mehrere Profile auf demselben Host

Für jede Instanz eigene Host-Pfade für `config_path`, `runtime_socket` und **`cert_dir`** verwenden. Beispielsweise:

| Profil | Konfiguration auf dem Host | Zertifikate auf dem Host |
| --- | --- | --- |
| firma-a | `/opt/firma-a/config/haproxy.cfg` | `/opt/firma-a/certs` |
| homelab | `/opt/homelab/config/haproxy.cfg` | `/opt/homelab/certs` |

Bei Docker die jeweiligen Verzeichnisse in den passenden Container mounten. Die Container dürfen intern beide `/etc/haproxy/certs` verwenden; entscheidend sind getrennte Verzeichnisse und Mount-Quellen auf dem Host. In jedem Profil muss `cert_dir_config` dem tatsächlichen HAProxy-Pfad entsprechen. Bestehende eigene `crt`-Pfade in Konfigurationen ebenfalls prüfen.

Der aktuelle Installationsassistent lehnt gemeinsam genutzte oder ineinander liegende Zertifikatsverzeichnisse mehrerer Profile ab. Der aktuelle Agent erkennt solche vorhandenen Konstellationen, zeigt sie über die Oberfläche an und verhindert Ausstellung, Import und Erneuerung, bis die Profile getrennte Verzeichnisse haben. Die vorhandenen HAProxy-Dienste laufen weiter; Verzeichnisse und Konfigurationen werden dabei nicht automatisch umgeschrieben. Bestehende Agenten unter **Server → Agent aktualisieren** auf die neue Version bringen. Die WebUI allein kann Dateisystem-Pfade eines alten entfernten Agenten nicht auf gemeinsame Nutzung prüfen.

## Andere Einstellungen gezielt verwalten

Proxy Hosts, Redirects, ACLs, HTTP-/HTTPS-Listener, importierte Konfigurationen und Versionshistorien gehören jeweils zur ausgewählten HAProxy-Instanz. Der Zielserver steht auf der Seite und in den Bearbeitungsdialogen. Während ein Dialog, eine Bestätigung oder eine laufende Aktion offen ist, bleibt der Serverwechsel gesperrt. Nach einem Wechsel werden Bearbeitungsfunktionen erst mit den Daten des neuen Servers freigegeben.

Grafische Änderungen zunächst speichern, **Konfiguration erzeugen**, den Entwurf für den angezeigten Zielserver prüfen und unter **Konfiguration → Prüfen & anwenden** aktivieren. Dadurch werden andere Server nicht automatisch geändert.

Basic-Auth-Benutzer und Gruppen sind zentral. Ihre Anwendung erfolgt ausdrücklich durch Gruppenzuordnung zu einzelnen Sites und anschließendes Anwenden der betreffenden Serverkonfiguration. Für voneinander unabhängige Website-Zugänge separate Gruppen und Benutzer verwenden. Verwaltungskonten, Basic-Auth-Verzeichnis und Aktivitätsprotokoll gelten für die gesamte Management-Oberfläche; die Infrastruktur-Auswahl filtert diese zentralen Ansichten nicht.

## Update bestehender Installationen

Die Anwendung erstellt zwei zusätzliche MariaDB-Tabellen beim Start. Vorhandene Server, Tags, Standorte, Konfigurationen und Zertifikate bleiben erhalten; bestehende Server starten **Ohne Zuordnung**. Es werden keine neuen ENV-Variablen und keine neuen Schlüssel benötigt. Ältere API-Clients behalten bestehende Infrastruktur-Zuordnungen bei, wenn sie das Feld `infrastructure_id` beim Bearbeiten nicht mitsenden. Ein explizites `null` entfernt die Zuordnung.

Die Gruppen dienen der Organisation und gezielten Verwaltung. Die bestehenden Admin-/Operator-/Viewer-Rollen gelten weiterhin über alle Infrastrukturen; es gibt keine Zugriffsrechte je Infrastruktur.
