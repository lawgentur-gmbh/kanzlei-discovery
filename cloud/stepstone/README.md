# Privates StepStone-Archiv

Reparaturstand 30.10.: `large_run_enabled=false`. Der große manuelle Lauf und
der große Sonntagsabruf sind bis zur bestandenen Cloud-Abnahme technisch gesperrt.
Der vorhandene Zeitplan bleibt konfiguriert; seine Ausführung startet bei gesetzter
Sperre keinen Crawl. Kleine gezielt gestartete Tests sind separat begrenzt.

Seit Version 3 werden Kandidaten und Such-Checkpoints nach jeder Seite atomar
in `kr_portals.stepstone_candidates` / `search_runs` gespeichert. Jeder Bereich
verarbeitet seine Texte vor dem nächsten Bereich. `repair_resume` verarbeitet
die dauerhaft gespeicherte Warteschlange ohne erneute Suchlistenabrufe.
Strikt sequenziell: zwei Sekunden Abstand zwischen Seiten, 45 Sekunden zwischen
Bereichen; vorheriger Browser wird vor dem nächsten geschlossen, Sitzung bleibt
erhalten. Zwei aufeinanderfolgende Netzwerkfehler stoppen den Test frühzeitig.
Ein Neustart nach Netzwerkfehlern erhält Cookies und IP und ist begrenzt.
Ein einzelner erfolgreicher Bereich gilt ausdrücklich nicht als Abnahme.

Nutzerauftrag 30.10.2026. Ausschließlich Erfassung und interne Aufbewahrung.
Keine Reverse-Suche, kein Arbeitgeber-Crawl, keine STEREJO-Veröffentlichung und
kein Import der 170 zuvor zugeordneten Arbeitgeber-Originale.

- Sonntags 05:00 UTC: 06:00 Uhr Berlin im Winter, 07:00 Uhr im Sommer.
- GitHub Actions, unabhängig vom Benutzerrechner, höchstens ein aktiver Lauf.
- 30 geprüfte Beruf-/Branchensuchen und ihre Filter; alle belegten Ergebnisseiten.
  Branchen-Suchen liefern auch Business Services; Suchtreffer sind keine garantierte
  fachliche Zuordnung. Der Bestand bleibt privat und braucht Matching-Regeln.
- Detailseiten: vollständige JobPosting-Beschreibung bzw. abgegrenzter Anzeigentext.
  Suchlisten-Snippets gelten nicht als Volltext. Fehlende Texte bleiben offen.
- Datenbank: KanzleiRadar, ausschließlich privates Schema `kr_portals`.
  Separater Schreibzugang ohne Rechte für STEREJO/CRM und ohne Löschrecht.
- Identität: Portal-ID. Gleicher Text liegt über SHA-256 nur einmal vor.
  Zustandsversionen speichern Referenzen, Zeitpunkte und Beobachtungszähler.
  Exakte Wiederveröffentlichungen (Arbeitgeber, Titel, Ort, Text) werden in der
  internen Ansicht `deduplicated_jobs` zusammengeführt; Portal-IDs bleiben belegt.
- Keine Entfernung historischer Anzeigen. Ein fehlender Suchtreffer schließt keine Stelle.
  `last_seen` und Detail-Prüfergebnis zeigen Aktualität und Abrufgrenzen.
- Vier Stunden Abrufbudget je Lauf, innerhalb des fünfstündigen Workflowlimits.
  Bei Budgetende bleiben Ergebnisse erhalten und der Rest wird als Rückstand
  gemeldet; keine Vollständigkeitszusage. Neue/fehlende Texte vor fehlerhaften
  Aktualisierungen und vorhandenen Texten, vorhandene nach ältestem Prüfzeitpunkt.
- Warnung: über 30 Prozent UND mindestens 20 Anzeigen Abweichung je Suche gegen
  den Median der letzten vier technisch erfolgreichen vergleichbaren Suchläufe;
  außerdem Filter-/Paginationfehler oder weniger als 90 Prozent bestätigte Volltexte.
- Erst begrenzter Wiederholungsversuch mit frischem Client und geprüften Parsern.
  Keine automatisch generierten Codeänderungen, entfernten Filter oder Umgehung
  von Zugriffssperren. Unbekannte Layoutänderungen bleiben als Störung sichtbar.
- Danach eine zugewiesene GitHub-Störungsmeldung pro Kalenderwoche. E-Mail-Zustellung
  hängt von den GitHub-Einstellungen des Empfängers ab. Keine Anzeigentexte, Personen-
  oder Zugangsdaten in öffentlichen Logs, Issues oder Artefakten.

Der alte Windows-Wochenlauf, Octoparse-Export und alte GitHub-Pipeline sind
kein zusätzlicher StepStone-Erfassungsweg. Produktiver Job Monitor unverändert.
Cloud-Aktivierung am 30.10.2026 nach ausdrücklicher Secret-Freigabe und erfolgreichem
Datenbank-Pilot: drei Anzeigen mit vollständigem Text, drei Textblöcke, drei
Zustandsversionen, unabhängig in der Datenbank bestätigt. Warnweg ebenfalls getestet.
Seit dem Cloudvergleich vom 30.10. nutzt der Lauf normale Chromium-Navigation
mit HTTP/1.1 ab Beginn für Listen und Details. Im Vergleich lief requests in
Timeouts; Standard-Chromium scheiterte nach der ersten Navigation. HTTP/1.1
lieferte auf beiden Kontrollsuchen HTTP200 und jeweils 25 Treffer.
Die erweiterte Stichprobe las alle 30 Suchen über zwei Seiten und verarbeitete
58 Anzeigen mit Originaltext. Eine weitere Prüfung las alle 30 angebotenen
Notarfachangestellten-Seiten. Dabei wich die native ID-Menge von der Trefferzahl
der Quelle ab; solche Widersprüche bleiben als Teilbefund sichtbar.
Seit der zweiten Korrektur wird nach der Hauptantwort auf die eigentlichen
Such-/Jobdaten statt auf das gesamte DOM-Ladeereignis gewartet. Unvollständig
geladene Seiten gelten weiterhin als Fehler, niemals als Nullbestand.
Der erste planmäßige Vollumfangslauf steht noch aus; Stichproben belegen keine
pauschale Gesamtmarktabdeckung oder aktuelle Vollständigkeit aller Suchen.

Plattformgrenze: GitHub kann öffentliche Repository-Zeitpläne nach 60 Tagen ohne
Repository-Aktivität deaktivieren. Ein zusätzliches automatisches Wartungsschreiben
wurde von der Freigabeprüfung abgelehnt und ist nicht eingerichtet. Kein verborgenes
Keepalive oder weiterer Zeitplan. GitHub-Laufzeiten können gegenüber der Sollzeit
abweichen. Dokumentation: https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule
