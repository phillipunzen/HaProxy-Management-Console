# Validierung am Entwicklungsserver

Stand: 8. Oktober 2026.

- Frontend-Produktionsbuild mit TypeScript erfolgreich.
- **28 automatisierte Tests bestanden** (22 Unit-Tests und 6 Integrationstests).
- Unit-Tests für Konfigurationsgenerator, Eingabevalidierung, Agent-Authentifizierung, Konflikterkennung, lokale Wiederherstellung bei Reload-Fehlern, Zertifikat-/Schlüsselvergleich, Certbot-Aufrufe, Erneuerung geänderter Produktionszertifikate und HTTP-Challenge-Webroot.
- Integrationstests an der angegebenen MariaDB und zwei isolierten echten HAProxy-Installationen: offizieller Docker-Container HAProxy 3.2.25 und natives HAProxy 3.0.11 mit eigener temporärer systemd-Unit. Prüfung generierter Hosts/Wildcards/ACLs/Redirects; Anwendung und Wiederherstellung von Revisionen; erfolgreicher Reload mit neuem Worker; Stop, Start, Restart; Login, CSRF, Rollenrechte, obligatorischer Passwortwechsel, Versionskonflikte.
- Chromium-Browsertest an der laufenden Docker-Webanwendung: erster Login und Passwortwechsel, Serververbindung, Hosts und Regeln speichern, Listener bearbeiten, Konfiguration erzeugen/prüfen/speichern, Zertifikatsformular, echte Runtime-Statistiken, Navigation, mobile Ansicht und Logout. Testbenutzer und Testinstanzen anschließend entfernt.

Die Test-HAProxy-Instanzen sind ausschließlich für Validierung bestimmt; bestehende Dienste wurden nicht verändert. Automatisierte Tests für Certbot verwenden kontrollierte Ersatzaufrufe. Eine echte Let's-Encrypt-Ausstellung bzw. DNS-Änderung wurde ohne echte Ziel-Domains und Cloudflare-Zugangstoken nicht ausgeführt. Agent-TLS muss auf den jeweiligen Zielhosts eingerichtet werden; LAN-HTTP der Management-WebUI entspricht dem gewünschten ersten Betrieb per IP.
