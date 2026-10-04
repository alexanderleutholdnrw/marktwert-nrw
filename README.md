# marktwert-nrw

Automatischer Marktwert für Eigentumswohnungen aus den Immobilienrichtwerten NRW, für die Region Köln rechtsrheinisch, Leverkusen, Bergisch Gladbach, Burscheid, Leichlingen und Langenfeld (Stichtag 01.01.2026).

Das Ergebnis ist eine grobe, automatische Schätzung (etwa ±10 bis 20 %), kein Verkehrswert und kein Ersatz für den Immobilien-Preis-Kalkulator auf boris.nrw.de oder ein Gutachten.

## Inhalt

| Datei | Inhalt |
|---|---|
| marktwert.py | Zuordnung Adresse zu Richtwertzone, Umrechnungskoeffizienten der Gutachterausschüsse, Rückfall auf Stadtteilwerte in Köln. Nur Python-Standardbibliothek. Selbsttest: `python3 marktwert.py --test` |
| marktdaten/irw_2026_zonen.json | Richtwertzonen Teilmarkt Eigentumswohnungen mit Normobjekt |
| marktdaten/irw_2026_adressen.tsv.gz | Adresse (Kommune, Straße, Hausnummer) zu Richtwertzone, vorberechnet |
| marktdaten/gmb_koeln_2026_stadtteile.json | Weiterverkaufspreise der rechtsrheinischen Kölner Stadtteile und Deutz, Grundstücksmarktbericht Köln 2026, S. 126 und 138 bis 144 |

## Quellen und Lizenz

* Immobilienrichtwerte NRW, Stichtag 01.01.2026, und Gebäudereferenzen NRW: Geobasis NRW / Gutachterausschüsse für Grundstückswerte in NRW, opengeodata.nrw.de, Datenlizenz Deutschland Zero, Version 2.0 (dl-de/zero-2-0).
* Umrechnungskoeffizienten: Örtliche Fachinformationen der Gutachterausschüsse Köln, Leverkusen, Bergisch Gladbach, Rheinisch-Bergischer Kreis und Kreis Mettmann (boris.nrw.de, LGDIR_1_05{GASL}_2026.pdf), mit Seitenangabe im Code.
* Stadtteilwerte Köln: Grundstücksmarktbericht 2026 des Gutachterausschusses für Grundstückswerte in der Stadt Köln (boris.nrw.de).

Die Dateien in marktdaten werden einmal im Jahr nach dem neuen Stichtag neu erzeugt.
