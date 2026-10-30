# Privates StepStone-Archiv

Nutzerauftrag 30.10.2026. Ausschließlich Erfassung und interne Aufbewahrung.
Keine Reverse-Suche, kein Arbeitgeber-Crawl, keine STEREJO-Veröffentlichung und
kein Import der 170 zuvor zugeordneten Arbeitgeber-Originale.

- Sonntags 05:00 UTC: 06:00 Uhr Berlin im Winter, 07:00 Uhr im Sommer.
- GitHub Actions, unabhängig vom Benutzerrechner, höchstens ein aktiver Lauf.
- Bestehende sieben Berufssuchen und ihre Filter; alle belegten Ergebnisseiten.
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
Cloud-Aktivierung erst nach Prüfung des neuen Geheimnisses und einem echten Pilotlauf.
