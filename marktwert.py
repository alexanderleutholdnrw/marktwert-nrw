#!/usr/bin/env python3
"""Automatischer Marktwert aus den Immobilienrichtwerten NRW (Teil von Rechenmodell V3).

Nur Standardbibliothek. Daten in marktdaten/ (jaehrlich mit aufbereitung_irw.py erzeugen).

Aufruf:
    python3 marktwert.py objekt.json          Bericht als Text
    python3 marktwert.py objekt.json --json   Ergebnis als JSON
    python3 marktwert.py --test               Selbsttest
Eingabe: dieselbe JSON-Datei wie fuer rechenmodell.py, zusaetzlich
    kommune, stadtteil, strasse, hausnummer, geschoss (0 = EG, -1 = Souterrain),
    geschosse (Vollgeschosse des Gebaeudes), aufzug (true/false),
    ausstattung ("einfach", "mittel", "gehoben"), modernisierung ("nicht", "teil", "voll"),
    erbbau (true/false). Unbekannte Merkmale weglassen: dann gilt der Wert des Normobjekts
    (Faktor 1) und das Merkmal steht als Annahme im Ergebnis.

Regeln (CLAUDE.md, Parameter 16 und Modus 0):
    * Ergebnis ist immer "automatisch, bitte manuell pruefen"; Rechnung damit vorlaeufig.
    * Mehrere moegliche Zonen (Hausnummer fehlt): Ampel und Rendite mit dem niedrigsten,
      Vorfilter mit dem hoechsten Wert.
    * Vorfilter: aussortieren, wenn Kaufpreis > 125 % des hoechsten Werts; trotz Faktor
      ueber 22 weiterleiten, wenn Kaufpreis <= 90 % des hoechsten Werts.
    * Koeln ohne Zone: Rueckfall auf GMB Koeln (Baujahresklasse ab 5 Faellen, sonst
      Weiterverkauf gesamt), vermietet mal 0,94.
    * Leverkusen ueber 5 Vollgeschosse: kein Richtwert, Werte fuer Grosswohnanlagen aus
      dem Tab Marktdaten (GMB Leverkusen 2026, S. 44).
"""

import gzip
import json
import math
import os
import re
import sys

JAHR = 2026
BASIS = os.path.dirname(os.path.abspath(__file__))
# Datenordner: Umgebungsvariable MARKTDATEN, sonst marktdaten/ neben dem Skript, sonst Arbeitsordner
_ORTE = [os.environ.get("MARKTDATEN", ""), os.path.join(BASIS, "marktdaten"), BASIS,
         os.path.join(os.getcwd(), "marktdaten"), "/home/claude/marktdaten"]
DATEN = next((p for p in _ORTE if p and os.path.exists(os.path.join(p, f"irw_{JAHR}_zonen.json"))),
             os.path.join(BASIS, "marktdaten"))
KENNZEICHEN = "⚠️ automatisch, bitte manuell prüfen"
QUELLE_UK = "LGDIR_1_05{gasl}_2026.pdf (boris.nrw.de)"

# Leverkusen, Grosswohnanlagen ueber 5 Vollgeschosse (Tab Marktdaten, GMB Leverkusen 2026, S. 44)
LEV_GROSS = {"einfach": 1750, "mittel": 1990, "gut": 2120}


# ------------------------------------------------------------ Hilfsfunktionen
def klasse(wert, grenzen):
    """grenzen: Liste (obergrenze_inklusive, koeffizient, bezeichnung)."""
    for grenze, uk, name in grenzen:
        if wert <= grenze:
            return uk, name
    return grenzen[-1][1], grenzen[-1][2]


def zahl(s):
    """Erste Zahl aus Normangaben wie '1-8', '13-65', '60', '70-89'."""
    m = re.search(r"\d+", str(s or ""))
    return int(m.group()) if m else None


def interp(x, punkte):
    """Lineare Interpolation, ausserhalb geklemmt. punkte: [(x, y)] aufsteigend."""
    if x <= punkte[0][0]:
        return punkte[0][1]
    for (x0, y0), (x1, y1) in zip(punkte, punkte[1:]):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return punkte[-1][1]


AUSST = {"einfach": "einfach", "schlecht": "einfach", "mittel": "mittel",
         "gehoben": "gehoben", "gut": "gehoben", "stark gehoben": "gehoben"}


class Merkmale:
    """Objektmerkmale; fehlende Werte bleiben None (dann gilt die Norm)."""

    def __init__(self, d):
        self.baujahr = d.get("baujahr")
        self.flaeche = d.get("wohnflaeche_kosten") or d.get("wohnflaeche_miete")
        self.einheiten = d.get("einheiten")
        self.zimmer = d.get("zimmer")
        self.geschoss = d.get("geschoss")
        self.geschosse = d.get("geschosse")
        self.aufzug = d.get("aufzug")
        a = d.get("ausstattung")
        self.ausstattung = AUSST.get(str(a).lower()) if a else None
        self.modernisierung = d.get("modernisierung")
        v = d.get("vermietet")
        self.vermietet = bool(d.get("miete_ist_monat")) if v is None else bool(v)
        self.erbbau = d.get("erbbau")


# --------------------------------------------- Umrechnung je Gutachterausschuss
# Jede Funktion liefert eine Liste von Zeilen:
#   (Merkmal, Objektangabe, Normangabe, UK Objekt, UK Norm, Annahme ja/nein)
# und eine Liste von Hinweisen.

def uk_koeln(z, o):
    """GA Koeln, LGDIR_1_0511400_2026, S. 3 f.; multiplikativ, Produkt Objekt / Produkt Norm."""
    n = z["norm"]
    bj = [(1920, 1.22, "1800 bis 1920"), (1940, 1.03, "1921 bis 1940"), (1965, 1.07, "1941 bis 1965"),
          (1980, 1.00, "1966 bis 1980"), (1990, 1.09, "1981 bis 1990"), (2000, 1.20, "1991 bis 2000"),
          (2010, 1.32, "2001 bis 2010"), (9999, 1.33, "2011 bis 2022")]
    gs = [(8, 1.00, "1 bis 8"), (999, 0.91, "ab 9")]
    we = [(6, 1.01, "2 bis 6"), (12, 1.01, "7 bis 12"), (30, 1.00, "13 bis 30"), (65, 0.98, "31 bis 65"),
          (99999, 0.94, "ab 66")]
    fl = [(40, 1.02, "25 bis 40 m²"), (80, 1.00, "41 bis 80 m²"), (120, 1.02, "81 bis 120 m²"),
          (999, 1.07, "121 bis 150 m²")]
    gl = [(0, 0.98, "EG"), (2, 1.00, "1. bis 2. OG"), (5, 1.03, "3. bis 5. OG"), (999, 1.00, "ab 6. OG")]
    au = {"einfach": 0.87, "mittel": 1.00, "gehoben": 1.23}
    norm_aus = "gehoben" if n.get("GSTAND") == "8" else "mittel"   # Annahme zur Kodierung GSTAND
    zeilen, hinw = [], []

    def k(name, tab, wert_o, wert_n):
        uo, no = klasse(wert_o if wert_o is not None else wert_n, tab)
        un, nn = klasse(wert_n, tab)
        zeilen.append((name, no, nn, uo, un, wert_o is None))

    k("Baujahr", bj, o.baujahr, zahl(n.get("BJ")))
    k("Geschosse im Objekt", gs, o.geschosse, zahl(n.get("ANZG")))
    k("Einheiten der Anlage", we, o.einheiten, zahl(n.get("WHNA")))
    k("Wohnfläche", fl, round(o.flaeche) if o.flaeche else None, zahl(n.get("WHNFL")))
    k("Geschosslage", gl, o.geschoss, 1)              # Norm GESLA 2 = 1. bis 2. OG (Annahme)
    zeilen.append(("Mietsituation", "vermietet" if o.vermietet else "unvermietet", "unvermietet",
                   0.94 if o.vermietet else 1.00, 1.00, False))
    ao = o.ausstattung or "mittel"
    zeilen.append(("Ausstattung", ao, norm_aus, au[ao], au[norm_aus], o.ausstattung is None))
    if o.baujahr and o.baujahr > 2022:
        hinw.append("Baujahr nach 2022 liegt ausserhalb des Kölner Modells.")
    hinw.append("Köln: Der BORIS-Rechner kann an Klassengrenzen (Baujahr, Fläche, Einheiten) "
                "von den Tabellenwerten abweichen.")
    return zeilen, hinw


def uk_leverkusen(z, o):
    """GA Leverkusen, LGDIR_1_0511600_2026, S. 3; Norm fuer alle Zonen gleich (alle UK = 1,00)."""
    zeilen = []

    def k(name, tab, wert):
        uo, no = klasse(wert, tab) if wert is not None else (1.00, "wie Norm")
        zeilen.append((name, no, "Norm", uo, 1.00, wert is None))

    k("Baujahr", [(1979, 0.80, "vor 1980"), (1989, 0.91, "1980 bis 1989"), (1999, 1.00, "1990 bis 1999"),
                  (9999, 1.12, "ab 2000")], o.baujahr)
    k("Wohnungsgröße", [(69.99, 0.96, "unter 70 m²"), (89.99, 1.00, "70 bis 89 m²"), (999, 0.97, "ab 90 m²")],
      o.flaeche)
    k("Einheiten der Anlage", [(59, 1.00, "unter 60"), (99, 0.96, "60 bis 99"), (99999, 0.85, "ab 100")],
      o.einheiten)
    k("Vollgeschosse", [(3, 1.00, "unter 4"), (999, 0.98, "ab 4")], o.geschosse)
    st = {"einfach": 0.96, "mittel": 1.00, "gehoben": 1.14}
    zeilen.append(("Gebäudestandard", o.ausstattung or "mittel", "mittel", st[o.ausstattung or "mittel"],
                   1.00, o.ausstattung is None))
    mo = {"nicht": 1.00, "teil": 1.04, "voll": 1.09}
    m = o.modernisierung if o.modernisierung in mo else None
    zeilen.append(("Modernisierungsgrad", m or "nicht", "nicht", mo[m or "nicht"], 1.00, m is None))
    zeilen.append(("Vermietung", "vermietet" if o.vermietet else "unvermietet", "unvermietet",
                   0.92 if o.vermietet else 1.00, 1.00, False))
    return zeilen, ["Leverkusen: additive Verknüpfung laut Datensatz (BRECHV 1); einmal mit dem "
                    "BORIS-Rechner gegenprüfen."]


GL_KURVE = [(1960, 1.00), (1970, 1.11), (1980, 1.24), (1990, 1.38), (2000, 1.56), (2010, 1.82), (2015, 1.98)]


def uk_bergisch_gladbach(z, o):
    """GA Bergisch Gladbach, LGDIR_1_0520700_2026, S. 2 f.; nur Baujahr, interpoliert."""
    bn = zahl(z["norm"].get("BJ"))
    bo = o.baujahr
    hinw = ["Bergisch Gladbach: Wohnfläche, Geschoss, Einheiten und Vermietung ohne Koeffizienten."]
    if bo is not None and bo < 1960:
        hinw.append("Baujahr vor 1960: laut GA keine sinnvolle Umrechnung, mit 1960 gerechnet.")
    if bo is not None and bo > 2015:
        hinw.append("Baujahr nach 2015: mit 2015 gerechnet.")
    uo = interp(bo if bo is not None else bn, GL_KURVE)
    un = interp(bn, GL_KURVE)
    return [("Baujahr", bo if bo is not None else bn, bn, uo, un, bo is None)], hinw


def uk_rbk(z, o):
    """GA Rheinisch-Bergischer Kreis, LGDIR_1_0532300_2026, S. 3 (Diagramm); multiplikativ."""
    n = z["norm"]
    zeilen = []

    def k(name, tab, wert_o, wert_n):
        uo, no = klasse(wert_o if wert_o is not None else wert_n, tab)
        un, nn = klasse(wert_n, tab)
        zeilen.append((name, no, nn, uo, un, wert_o is None))

    k("Baujahr", [(1964, 0.97, "vor 1965"), (1974, 1.00, "1965 bis 1974"), (1984, 1.06, "1975 bis 1984"),
                  (1994, 1.20, "1985 bis 1994"), (1999, 1.21, "1995 bis 1999"), (9999, 1.32, "ab 2000")],
      o.baujahr, zahl(n.get("BJ")))
    k("Wohnfläche", [(49.99, 0.97, "unter 50 m²"), (74.99, 1.00, "50 bis unter 75 m²"),
                     (84.99, 1.02, "75 bis unter 85 m²"), (999, 1.02, "ab 85 m²")], o.flaeche, zahl(n.get("WHNFL")))
    k("Raumanzahl", [(2.99, 0.96, "unter 3"), (3.99, 1.00, "3 bis unter 4"), (99, 0.96, "ab 4")],
      o.zimmer, zahl(n.get("RANZ")))
    st = {"einfach": 0.96, "mittel": 1.00, "gehoben": 1.25}
    zeilen.append(("Gebäudestandard", o.ausstattung or "mittel", "mittel", st[o.ausstattung or "mittel"],
                   1.00, o.ausstattung is None))
    zeilen.append(("Aufzug", "ja" if o.aufzug else "nein", "nein", 1.01 if o.aufzug else 1.00,
                   1.00, o.aufzug is None))
    zeilen.append(("Vermietung", "vermietet" if o.vermietet else "unvermietet", "unvermietet",
                   0.94 if o.vermietet else 1.00, 1.00, False))
    return zeilen, ["Rhein.-Berg. Kreis: Koeffizienten aus einem Diagramm abgelesen; Raumanzahl = Zimmer "
                    "laut Exposé (ob Küche mitzählt, ist offen)."]


# Kreis Mettmann, Baujahrmatrix S. 3: Zeile = Richtwert-Baujahr, Spalten = Objekt-Baujahr
ME_SP = [2020, 2015, 2010, 2005, 2000, 1995, 1990, 1985, 1980, 1975, 1970, 1965, 1960, 1955, 1950, 1945]
ME_M = {
    2020: [1.00, .92, .86, .79, .74, .69, .65, .62, .59, .57, .57],
    2015: [1.08, 1.00, .93, .86, .80, .75, .70, .67, .64, .62, .61, .61],
    2010: [1.17, 1.08, 1.00, .93, .86, .81, .76, .72, .69, .67, .66, .66, .66],
    2005: [1.26, 1.16, 1.08, 1.00, .93, .87, .82, .78, .74, .72, .71, .71, .71, .71],
    2000: [1.35, 1.25, 1.16, 1.07, 1.00, .93, .88, .83, .80, .78, .76, .76, .76, .76],
    1995: [1.45, 1.34, 1.24, 1.15, 1.07, 1.00, .94, .89, .86, .83, .82, .82, .82, .82, .82],
    1990: [1.54, 1.42, 1.32, 1.22, 1.14, 1.06, 1.00, .95, .91, .88, .87, .87, .87, .87, .87],
    1985: [1.62, 1.50, 1.39, 1.29, 1.20, 1.12, 1.05, 1.00, .96, .93, .92, .92, .92, .92, .92],
    1980: [1.69, 1.56, 1.45, 1.34, 1.25, 1.17, 1.10, 1.04, 1.00, .97, .96, .95, .95, .95, .95, .95],
    1975: [None, 1.61, 1.49, 1.38, 1.29, 1.20, 1.13, 1.07, 1.03, 1.00, .98, .98, .98, .98, .98, .98],
    1970: [None, None, 1.51, 1.41, 1.31, 1.22, 1.15, 1.09, 1.05, 1.02, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00],
    1965: [None, None, None, 1.41, 1.31, 1.22, 1.15, 1.09, 1.05, 1.02, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00],
    1960: [None, None, None, None, 1.29, 1.21, 1.13, 1.08, 1.03, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00],
}


def me_zelle(norm, obj):
    zeile = ME_M[norm] + [None] * (len(ME_SP) - len(ME_M[norm]))
    v = zeile[ME_SP.index(obj)]
    if v is None and ME_SP.index(obj) >= len(ME_M[norm]):        # aeltere Objekte: Tabelle flach
        v = [x for x in zeile if x is not None][-1]
    if v is None and obj in ME_M:                                  # neuere Objekte: Kehrwert
        v = 1 / me_zelle(obj, norm)
    return v


def me_baujahr(norm, obj):
    obj = min(max(obj, 1945), 2020)
    norm = min(max(norm, 1960), 2020)
    def zeile(nz):
        lo = max(s for s in ME_SP if s <= obj)
        hi = min(s for s in ME_SP if s >= obj)
        a, b = me_zelle(nz, lo), me_zelle(nz, hi)
        return a if lo == hi else a + (b - a) * (obj - lo) / (hi - lo)
    n0 = max(r for r in ME_M if r <= norm)
    n1 = min(r for r in ME_M if r >= norm)
    a, b = zeile(n0), zeile(n1)
    return a if n0 == n1 else a + (b - a) * (norm - n0) / (n1 - n0)


def uk_mettmann(z, o):
    """GA Kreis Mettmann, LGDIR_1_0531600_2026, S. 2 f.; nur die Merkmale des BORIS-Rechners."""
    n = z["norm"]
    bn = zahl(n.get("BJ"))
    zeilen = [("Baujahr", o.baujahr or bn, bn, me_baujahr(bn, o.baujahr or bn), 1.00, o.baujahr is None)]

    def k(name, tab, wert_o, wert_n):
        uo, no = klasse(wert_o if wert_o is not None else wert_n, tab)
        un, nn = klasse(wert_n, tab)
        zeilen.append((name, no, nn, uo, un, wert_o is None))

    k("Wohnfläche", [(40, 0.90, "bis 40 m²"), (80, 1.00, "41 bis 80 m²"), (120, 1.05, "81 bis 120 m²"),
                     (9999, 1.01, "über 120 m²")], round(o.flaeche) if o.flaeche else None, zahl(n.get("WHNFL")))
    k("Einheiten der Anlage", [(12, 1.05, "3 bis 12"), (65, 1.00, "13 bis 65"), (99999, 0.95, "über 65")],
      o.einheiten, zahl(n.get("WHNA")))
    au = {"einfach": 0.80, "mittel": 1.00, "gehoben": 1.10}
    zeilen.append(("Ausstattung", o.ausstattung or "mittel", "mittel", au[o.ausstattung or "mittel"],
                   1.00, o.ausstattung is None))
    zeilen.append(("Mietstatus", "vermietet" if o.vermietet else "nicht vermietet", "nicht vermietet",
                   0.91 if o.vermietet else 1.00, 1.00, False))
    hinw = ["Mettmann: 81 bis 120 m² mit Tabellenwert 1,05 (Rechenbeispiel im PDF nutzt 1,04); "
            "Geschoss, Balkon und Garten nicht im Rechner, nur sachverständig."]
    if o.erbbau:
        hinw.append("Erbbaurecht: Richtwert gilt nicht; laut GA sachverständig etwa mal 0,85.")
    return zeilen, hinw


UMRECHNUNG = {"11400": (uk_koeln, "multiplikativ"), "11600": (uk_leverkusen, "additiv"),
              "20700": (uk_bergisch_gladbach, "multiplikativ"), "32300": (uk_rbk, "multiplikativ"),
              "31600": (uk_mettmann, "multiplikativ")}


def umrechnen(zone, o):
    funk, art = UMRECHNUNG[zone["gasl"]]
    zeilen, hinw = funk(zone, o)
    if zone.get("brechv") == "1" or art == "additiv":
        faktor = 1 + sum(uo / un - 1 for _, _, _, uo, un, _ in zeilen)
    else:
        faktor = math.prod(uo for *_, uo, _, _ in zeilen) / math.prod(un for *_, un, _ in zeilen)
    if not 0.65 <= faktor <= 1.35:
        hinw.append(f"Faktor {faktor:.2f} ausserhalb 0,65 bis 1,35: Vergleichbarkeit prüfen, ggf. andere Zone.")
    return faktor, zeilen, hinw


# -------------------------------------------------------------- Adressen
KOMMUNE = {"koeln": "Koeln", "köln": "Koeln", "leverkusen": "Leverkusen", "bergischgladbach": "Bergisch Gladbach",
           "burscheid": "Burscheid", "leichlingen": "Leichlingen", "langenfeld": "Langenfeld"}


def norm_kommune(s):
    k = re.sub(r"\(.*?\)|[^a-zäöü]", "", str(s or "").lower())
    return KOMMUNE.get(k.replace("ö", "oe") if k not in KOMMUNE else k) or KOMMUNE.get(k)


def norm_strasse(s):
    s = str(s or "").lower().replace("ß", "ss")
    s = re.sub(r"str(asse|\.)", "str", s)
    return re.sub(r"[^a-z0-9äöü]", "", s)


def norm_hnr(s):
    m = re.match(r"\s*(\d+)\s*([a-zA-Z]?)", str(s or ""))
    return (m.group(1), m.group(2).lower()) if m else (None, "")


_ADR, _ZON, _GMB = None, None, None


def daten():
    global _ADR, _ZON, _GMB
    if _ZON is None:
        with open(os.path.join(DATEN, f"irw_{JAHR}_zonen.json"), encoding="utf-8") as fh:
            _ZON = json.load(fh)["zonen"]
        _ADR = {}
        with gzip.open(os.path.join(DATEN, f"irw_{JAHR}_adressen.tsv.gz"), "rt", encoding="utf-8") as fh:
            next(fh)
            for line in fh:
                kom, strasse, hnr, zus, zonen = line.rstrip("\n").split("\t")
                _ADR.setdefault((kom, norm_strasse(strasse)), []).append((hnr, zus.lower(), zonen, strasse))
        with open(os.path.join(DATEN, f"gmb_koeln_{JAHR}_stadtteile.json"), encoding="utf-8") as fh:
            _GMB = json.load(fh)
    return _ADR, _ZON, _GMB


def finde_zonen(kommune, strasse, hausnummer=None):
    """Rueckgabe: (Liste Zonen-IDs, Anteil Adressen ohne Zone, Ebene, Hinweise)."""
    adr, _, _ = daten()
    kom = norm_kommune(kommune)
    if not kom or not strasse:
        return [], None, "keine Adresse", ["Kommune oder Straße fehlt."]
    key = (kom, norm_strasse(strasse))
    eintraege = adr.get(key)
    hinw = []
    if not eintraege:   # Koeln: doppelte Strassennamen tragen ein Stadtteilkuerzel ("... Str. Po")
        kand = [k for k in adr if k[0] == kom and k[1].startswith(key[1]) and len(k[1]) - len(key[1]) <= 3]
        eintraege = [e for k in kand for e in adr[k]]
        if kand:
            hinw.append("Straßenname mehrdeutig: " + ", ".join(sorted({e[3] for e in eintraege})))
    if not eintraege:
        return [], None, "Straße nicht gefunden", [f"Straße '{strasse}' in {kom} nicht in den Gebäudereferenzen."]
    hnr, zus = norm_hnr(hausnummer)
    ebene = "Straße"
    if hnr:
        treffer = [e for e in eintraege if e[0] == hnr and e[1] == zus] or [e for e in eintraege if e[0] == hnr]
        if treffer:
            eintraege, ebene = treffer, "Hausnummer"
        else:
            hinw.append(f"Hausnummer {hausnummer} nicht gefunden, ganze Straße ausgewertet.")
    zonen = sorted({z for e in eintraege for z in e[2].split(",") if z})
    ohne = sum(1 for e in eintraege if not e[2]) / len(eintraege)
    return zonen, ohne, ebene, hinw


# --------------------------------------------------------- Rueckfall Koeln
def gmb_koeln(stadtteil, baujahr, vermietet):
    _, _, gmb = daten()
    st = str(stadtteil or "").lower().replace("köln-", "").replace("koeln-", "").strip()
    zeilen = [z for z in gmb["zeilen"] if st and (z["stadtteil"].lower() == st
              or st in [t.strip() for t in z["stadtteil"].lower().split("/")])]
    if not zeilen:
        return None
    gruppe = None
    if baujahr:
        gruppe = ("Baujahre vor 1941" if baujahr < 1941 else
                  "Baujahre 1941 - 1990" if baujahr <= 1990 else "Baujahre ab 1991")
    wahl = next((z for z in zeilen if z["gruppe"] == gruppe and z["n"] >= 5), None) \
        or next(z for z in zeilen if z["gruppe"] == "Weiterverkauf")
    f = 0.94 if vermietet else 1.0
    hinw = ["Rückfall GMB Köln: ungenormter Mittelwert, grob (etwa ±20 %)."]
    if wahl["n"] < 5:
        hinw.append(f"Nur {wahl['n']} Verkäufe.")
    return {"wert_qm": round(wahl["eur"] * f), "spanne_qm": (round(wahl["eur_min"] * f), round(wahl["eur_max"] * f)),
            "quelle": f"GMB Köln 2026 S. {wahl['seite']}: {wahl['stadtteil']}, {wahl['gruppe']}, "
                      f"{wahl['n']} Verkäufe, Ø {wahl['eur']} €/m²" + (" mal 0,94 vermietet" if vermietet else ""),
            "hinweise": hinw}


# --------------------------------------------------------------- Ergebnis
def ermittle(d):
    o = Merkmale(d)
    _, zon, _ = daten()
    kom = norm_kommune(d.get("kommune"))
    zonen, ohne, ebene, hinw = finde_zonen(d.get("kommune"), d.get("strasse"), d.get("hausnummer"))
    kand = []
    for zid in zonen:
        z = zon[zid]
        if z["norm"].get("OBJGR") == "1":
            hinw.append(f"Zone {z['wnum']} ({z['name']}) ist eine Erstverkaufszone (Neubau), nicht verwendet.")
            continue
        if z["gasl"] == "11600" and o.geschosse and o.geschosse > 5:
            hinw.append("Leverkusen: für Gebäude über 5 Vollgeschosse gibt es keine Richtwerte.")
            continue
        faktor, zeilen, h = umrechnen(z, o)
        kand.append({"art": "Richtwert", "zone": z["wnum"], "name": z["name"], "irw": z["irw"],
                     "faktor": round(faktor, 4), "wert_qm": round(z["irw"] * faktor), "zeilen": zeilen,
                     "quelle": f"Immobilienrichtwert Zone {z['wnum']} ({z['name']}) {z['irw']} €/m² zum "
                               f"{z['stichtag']}, Umrechnung {QUELLE_UK.format(gasl=z['gasl'])}",
                     "hinweise": h})
    if kom == "Koeln" and (not kand or (ohne or 0) > 0):
        g = gmb_koeln(d.get("stadtteil"), o.baujahr, o.vermietet)
        if g:
            kand.append({"art": "GMB-Stadtteil", **g})
        elif not kand:
            hinw.append(f"Köln: Stadtteil '{d.get('stadtteil')}' nicht in der Rückfalltabelle (nur rechtsrheinisch).")
    if kom == "Leverkusen" and o.geschosse and o.geschosse > 5:
        kand.append({"art": "Marktdaten", "wert_qm": LEV_GROSS["einfach"],
                     "spanne_qm": (LEV_GROSS["einfach"], LEV_GROSS["gut"]),
                     "quelle": "Tab Marktdaten, GMB Leverkusen 2026 S. 44, Großwohnanlagen einfach/mittel/gut "
                               "1.750/1.990/2.120 €/m²", "hinweise": []})
    hinw = list(dict.fromkeys(hinw))   # doppelte Hinweise entfernen
    r = {"kennzeichen": KENNZEICHEN, "ebene": ebene, "anteil_ohne_zone": ohne, "kandidaten": kand,
         "hinweise": hinw, "flaeche": o.flaeche}
    if not kand:
        r.update(status="kein Wert", marktwert=None, marktwert_max=None)
        r["hinweise"].append("Kein automatischer Wert: Marktbericht oder BORIS-Rechner manuell.")
        return r
    werte = [k["wert_qm"] for k in kand] + [k["spanne_qm"][1] for k in kand if k["art"] == "Marktdaten"]
    lo, hi = min(werte), max(werte)
    r.update(status="eindeutig" if len(kand) == 1 and kand[0]["art"] == "Richtwert" else "unsicher",
             wert_qm=lo, wert_qm_max=hi,
             marktwert=round(lo * o.flaeche) if o.flaeche else None,
             marktwert_max=round(hi * o.flaeche) if o.flaeche else None,
             annahmen=sorted({z[0] for k in kand for z in k.get("zeilen", []) if z[5]}),
             quelle="; ".join(k["quelle"] for k in kand) + f" ({KENNZEICHEN})")
    return r


def vorfilter(r, kaufpreis):
    """Modus 0, Stufe 1: Ergaenzung zum Faktor-Vorfilter (Entscheidung f)."""
    if not r.get("marktwert_max") or not kaufpreis:
        return "kein Urteil (Marktwert fehlt)"
    q = kaufpreis / r["marktwert_max"]
    if q > 1.25:
        return f"aussortieren: Kaufpreis {q - 1:+.0%} über dem höchsten möglichen Marktwert"
    if q <= 0.90:
        return f"weiterleiten trotz Faktor: Kaufpreis {q - 1:+.0%} unter dem höchsten möglichen Marktwert"
    return f"neutral: Kaufpreis {q - 1:+.0%} zum höchsten möglichen Marktwert"


# ------------------------------------------------------------------ Ausgabe
def fmt(x, n=0):
    return "n/a" if x is None else f"{x:,.{n}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def bericht(r, kaufpreis=None):
    out = [f"Marktwert automatisch ({r['ebene']}): {r['status']}"]
    for k in r["kandidaten"]:
        out.append(f"  {k['art']}: {fmt(k['wert_qm'])} €/m²  {k['quelle']}")
        for name, ob, nm, uo, un, ann in k.get("zeilen", []):
            out.append(f"    {name:<22} Objekt {str(ob):<16} UK {fmt(uo, 2)}  Norm {str(nm):<14} UK {fmt(un, 2)}"
                       + ("  (Annahme: wie Norm)" if ann else ""))
        if "faktor" in k:
            out.append(f"    Faktor {fmt(k['faktor'], 4)}")
        for h in k.get("hinweise", []):
            out.append("    Hinweis: " + h)
    if r.get("marktwert"):
        out.append(f"Marktwert für Ampel und Rendite: {fmt(r['marktwert'])} € ({fmt(r['wert_qm'])} €/m² x "
                   f"{fmt(r['flaeche'], 1)} m²), höchster möglicher Wert {fmt(r['marktwert_max'])} €")
    if kaufpreis:
        out.append("Vorfilter: " + vorfilter(r, kaufpreis))
    for h in r["hinweise"]:
        out.append("Hinweis: " + h)
    out.append(r["kennzeichen"])
    return "\n".join(out)


# --------------------------------------------------------------- Selbsttest
def test_zone(gasl, irw, **norm):
    return {"gasl": gasl, "irw": irw, "brechv": "1" if gasl == "11600" else "2", "norm": norm,
            "wnum": "test", "name": "test", "stichtag": "test"}


def selbsttest():
    ok = True

    def pruef(name, ist, soll, tol):
        nonlocal ok
        gut = ist is not None and abs(ist - soll) <= tol
        ok &= gut
        print(f"{'OK  ' if gut else 'FEHL'} {name:<52} ist {ist} soll {soll} +/- {tol}")

    # Rechenbeispiele aus den PDFs (unabhaengige Pruefwerte)
    z = test_zone("11400", 3800, BJ="1955", WHNFL="35", WHNA="40", ANZG="1-8", GSTAND="5")
    o = Merkmale({"baujahr": 1995, "wohnflaeche_miete": 130, "einheiten": 8, "geschosse": 5, "geschoss": 0})
    f, _, _ = umrechnen(z, o)
    pruef("Köln PDF S. 5 (PDF rundet auf 1,27/1,07)", round(3800 * f), 4510, 10)
    pruef("Köln PDF S. 5: Immobilienpreis", round(3800 * f * 130, -3), 586000, 1000)
    z = test_zone("20700", 3500, BJ="1970")
    pruef("Bergisch Gladbach PDF S. 6: Baujahr 1970 auf 2000", round(umrechnen(z, Merkmale({"baujahr": 2000,
          "wohnflaeche_miete": 140}))[0], 2), 1.41, 0.01)
    pruef("BG Gutachten 34 K 36/25 S. 31: 1971 auf 1973 in %",
          round((interp(1973, GL_KURVE) / interp(1971, GL_KURVE) - 1) * 100, 1), 2.2, 0.2)
    z = test_zone("31600", 2800, BJ="1970", WHNFL="41-80", WHNA="13-65")
    o = Merkmale({"baujahr": 1980, "wohnflaeche_miete": 90, "ausstattung": "gehoben", "einheiten": 70,
                  "vermietet": True})
    pruef("Mettmann PDF S. 5 (Beispiel nutzt 1,04 statt 1,05)", round(2800 * umrechnen(z, o)[0]), 2912, 30)
    pruef("Mettmann Matrix 1960 auf 1985", me_baujahr(1960, 1985), 1.08, 0.001)
    # Objekte mit Rechnerwert aus der Uebersicht, ueber die Adresse
    r = ermittle({"kommune": "Bergisch Gladbach", "strasse": "Marienhöhe", "hausnummer": "1",
                  "baujahr": 1960, "wohnflaeche_kosten": 86})
    pruef("Marienhöhe Rechnerwert", r["marktwert"], 166840, 1)
    r = ermittle({"kommune": "Bergisch Gladbach", "strasse": "Reginharstr.", "hausnummer": "32",
                  "baujahr": 1971, "wohnflaeche_kosten": 87})
    pruef("Reginharstraße 32 Rechnerwert je m²", r["wert_qm"], 1570, 0)
    r = ermittle({"kommune": "Köln", "stadtteil": "Finkenberg", "strasse": "", "baujahr": 1969,
                  "wohnflaeche_miete": 78, "miete_ist_monat": 687})
    pruef("Finkenberg Rückfall GMB (Übersicht 154.674)", r["marktwert"], 154674, 50)
    r = ermittle({"kommune": "Bergisch Gladbach", "strasse": "Giselbertstraße", "baujahr": 1971,
                  "wohnflaeche_kosten": 80})
    pruef("Giselbertstraße ohne Nr.: niedrigster Wert je m²", r["wert_qm"], 1570, 0)
    pruef("Giselbertstraße ohne Nr.: höchster Wert je m²", r["wert_qm_max"], 2744, 3)
    pruef("Vorfilter: 30 % über höchstem Wert", int(vorfilter({"marktwert_max": 100000}, 130000)
                                                     .startswith("aussortieren")), 1, 0)
    pruef("Vorfilter: 10 % unter höchstem Wert", int(vorfilter({"marktwert_max": 100000}, 90000)
                                                     .startswith("weiterleiten")), 1, 0)
    print("Selbsttest Marktwert bestanden" if ok else "Selbsttest Marktwert FEHLGESCHLAGEN")
    return ok


def main(argv):
    if "--test" in argv:
        sys.exit(0 if selbsttest() else 1)
    pfade = [a for a in argv if not a.startswith("--")]
    if not pfade:
        print(__doc__)
        return
    with open(pfade[0], encoding="utf-8") as fh:
        d = json.load(fh)
    r = ermittle(d)
    if "--json" in argv:
        r["vorfilter"] = vorfilter(r, d.get("kaufpreis"))
        print(json.dumps(r, indent=2, ensure_ascii=False, default=str))
    else:
        print(bericht(r, d.get("kaufpreis")))


if __name__ == "__main__":
    main(sys.argv[1:])
