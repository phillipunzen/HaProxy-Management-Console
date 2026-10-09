# Proxy Hosts, Frontends und Backends

Die gewählte Infrastruktur grenzt die Serverauswahl ein. Alle Änderungen betreffen den angezeigten Zielserver und werden zunächst als Arbeitsentwurf gespeichert.

## Reverseproxy hinzufügen

Unter **Proxy Hosts → Proxy Host** Domain, optionalen Pfad und Zielserver eintragen. Das Tool erzeugt den Backend-Pool `backend_<Host-ID>`, dessen Healthchecks und die Weiterleitung im gewählten HTTP-Frontend automatisch. Ein neuer grafischer Entwurf enthält bereits den gemeinsamen Listener `public_http` für HTTP und optional HTTPS. Bei einer importierten Konfiguration ein vorhandenes HTTP-/HTTPS-Frontend auswählen; bestehende Header, Redirects, TCP-Dienste und globale Einstellungen bleiben erhalten.

Optional eine zentrale Basic-Auth-Gruppe und ein Zertifikat wählen. Beim gemeinsamen Listener eines neuen Entwurfs aktiviert die Zertifikatsauswahl HTTPS. Bei bestehenden oder selbst angelegten Frontends muss TLS am Frontend eingerichtet sein. Die Konfiguration bleibt ein Entwurf bis **Konfiguration erzeugen → Prüfen → Anwenden**.

## Frontends & Backends

Der eigene Navigationspunkt zeigt die Listener mit Bind-Adressen, HTTP-/TCP-Modus, TLS und den verbundenen Backend-Pools. Zielserver und umfangreiche Zuordnungen lassen sich aufklappen.

**Backend hinzufügen:** Name, HTTP oder TCP, Lastverteilung und einen oder mehrere Zielserver mit Port eingeben. Vorhandene importierte Pools lassen sich weiterhin in ihren erkannten Parametern bearbeiten. Automatische Proxy-Host-Pools über den zugehörigen Host bearbeiten.

**Frontend hinzufügen:** Name, Modus, Bind-IP und Port wählen. Für einen TCP-Dienst einen gleichartigen Backend-Pool als Standard zuweisen. Für HTTP können Proxy Hosts Domains/Pfade auf diesem Frontend verwalten; alternativ einen HTTP-Standardpool auswählen. TLS kann auf dem Frontend terminiert werden, sofern Zertifikate verfügbar sind. Bei TLS-Passthrough bleibt die TLS-Terminierung deaktiviert.

Beispiel MySQL: TCP-Backend `be_mysql` mit `192.168.10.235:3306`, danach TCP-Frontend `fe_mysql` auf `0.0.0.0:3306` mit Standardbackend `be_mysql`. Ports dürfen nicht mit bereits vorhandenen Listenern kollidieren. Bei Docker muss der entsprechende Containerport zusätzlich in der HAProxy-Compose-Datei veröffentlicht sein.

**Zertifikate** in einer TLS-Frontend-Zeile legt dessen explizite Produktionszertifikate fest. Domain-Zuordnungen können zusätzliche Zertifikate beisteuern; die Auswahl erfolgt per SNI. Siehe [CERTIFICATES.md](CERTIFICATES.md).

Neue Frontends und Pools können im grafischen Entwurf bearbeitet und entfernt werden. Vor dem Entfernen ihre Host-/Domain-Zuordnungen ändern. Übernommene Listener lassen sich in ihren Zertifikaten bearbeiten; Bind-Adressen, komplexe ACLs oder weitere ursprüngliche Parameter weiterhin im Konfigurationseditor ändern. Konfigurationen mit dynamischen Regeln werden nicht pauschal in ein vereinfachtes Modell umgeschrieben.

Die Übersicht zeigt den Arbeitsentwurf. Ein noch unvollständiger Entwurf meldet den Grund; die aktive Konfiguration bleibt bis zur erfolgreichen Prüfung und Anwendung unverändert.
