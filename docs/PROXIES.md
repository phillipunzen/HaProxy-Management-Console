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

**Zertifikate** in einer TLS-Frontend-Zeile legt dessen explizite Produktionszertifikate fest. Eine Zertifikatsauswahl am Reverseproxy oder einer importierten Domain-Zuordnung hat für diese Domain Vorrang vor den Frontend-Zertifikaten. Eine zusätzliche Auswahl am Frontend ist nicht nötig. Die übrigen Domains behalten ihre bisherigen Zertifikate. Siehe [CERTIFICATES.md](CERTIFICATES.md).

Neue Frontends und Pools können im grafischen Entwurf bearbeitet und entfernt werden. Vor dem Entfernen ihre Host-/Domain-Zuordnungen ändern. Übernommene Listener lassen sich in ihren Zertifikaten bearbeiten; Bind-Adressen, komplexe ACLs oder weitere ursprüngliche Parameter weiterhin im Konfigurationseditor ändern. Konfigurationen mit dynamischen Regeln werden nicht pauschal in ein vereinfachtes Modell umgeschrieben.

Die Übersicht zeigt den Arbeitsentwurf. Ein noch unvollständiger Entwurf meldet den Grund; die aktive Konfiguration bleibt bis zur erfolgreichen Prüfung und Anwendung unverändert.

## Zertifikat direkt am Reverseproxy auswählen

Unter **Proxy Hosts** die Website bzw. die übernommene Domain-Zuordnung bearbeiten und **Zertifikat für HTTPS** auswählen. Das Frontend benötigt einen TLS-Bind, muss dieses Zertifikat aber nicht nochmals auswählen. Danach **Konfiguration erzeugen → Prüfen & anwenden**.

Die Anwendung erzeugt eine domainbezogene `crt-list`: Ein gewähltes Zertifikat wird für die zugewiesene Domain geladen, auch wenn das Frontend-Zertifikat dieselbe Domain, ein Wildcard oder zusätzliche Einzel-Domains enthält. Der Agent stellt die Liste unter `.control-tls` im Zertifikatsverzeichnis bereit. Bei der Prüfung bleibt sie temporär; erst erfolgreiches Anwenden hält sie vor. Bei einem fehlgeschlagenen Reload werden Konfiguration und Listen wiederhergestellt. Erneuerungen und erneutes Einlesen behalten die Domain-Zuordnung. Ohne zentrale Auswahl gelten wieder die ursprünglichen Frontend-Zertifikate.

TLS erfolgt vor der URL-Auswertung. Für dieselbe Domain am selben Frontend kann deshalb nur ein Zertifikat ausgewählt werden; verschiedene Zertifikate für `/` und `/admin` werden abgelehnt.

Für diese Funktion sowohl den Management-Container als auch den Agenten aktualisieren. Den Agent-Befehl findest du unter **Server → Agent aktualisieren**. Alte Agenten werden bei der Erzeugung mit einer verständlichen Meldung erkannt. Es sind keine neuen ENV-Variablen oder Tokens erforderlich. Eigene `crt-list`-/`crt-store`-Definitionen bleiben weiterhin manuell verwaltet. Erzeugte Konfigurationen verwenden zusätzlich die vom Agenten bereitgestellten Listen; bei manuellen Dateiexporten diese Dateien ebenfalls übernehmen.
