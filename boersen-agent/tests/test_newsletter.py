"""Parser tests on synthetic text that mimics the issue layout (no real newsletter content)."""
import unittest

from boersen_agent.newsletter import name_key, num, parse_calls, parse_dashboard, parse_dispo, parse_meta, parse_universe

DASH = """Hausse-Baisse-Signale – Gesamtsystem: Negativ 1:2
1) Zinsstruktur Trend Positiv! Testsatz (aktuell 0,47).
2) Index-Trend (Nasdaq-DowUtility-DAX) Negativ! Testsatz.
3) Übrige Indikatoren positiv 2:3
Anleihezinsen Negativ! a. Ölpreis Negativ! b. CRB-Index Negativ! c.
US-Dollar-Euro Positiv! d. Saisonfaktor Positiv! e.
Kurzfristige Trend-Methoden: negativ!
9-Monats-Hoch-Tief-Methode: 39 Hoch zu 114 Tief (Vorwoche 31:135)
32-Wochen-Indizes-Methode: 1 Hoch zu 10 Tief (Vorwoche 1:8)
Indizes-Quartal-Methode (25 Aktien-Indizes): 0 Hoch zu 5 Tief (Vorwoche 1:7)
Sicherheitssystem: negativ!
Fazit: Test fazit www.example.org"""

DISPO = """                 Dispo-Liste aller offenen Positionen
Wertentwicklung der ganzen Liste (Börsensignale-Index), Stand am 23.10.26: 559,68 (31.12.99=100).
    CH    CH0000000017        Testwerk          Pharma        05.12.25    58,12      79,59     47     60,58        K
  USA     US0000000009    Beispiel Corp     Elektronik     16.01.2026   1646,00    2790,00    57 1690,00      H
 ETC-Gold DE0000000000        Zertifikat           Gold $        12.09.25   211,10      248,15                    H
    E     ES0000000001       Neuwert SA        Energie       23.10.26    21,90      Neu!      26     18,36        V
 LU0000000002        Some Fund - EUR                  173,35
Depot: 20 Cash-Positionen in Form der Anleihe (53%), 17 Aktien (44%), 1 Fonds (3%)."""

UNI = """Testland
Nr.   Titel                          RS      H/T   KUV     KBV             Nr.   Titel                          RS    H/T   KUV     KBV
 1    Alpha Werke                  239,83          0,67     1,83           3    Gamma Bank                 108,02          k.A.    1,91
 2    Beta Systems                 167,38     H    3,57     2,73           4    Delta 3D                    95,14    T     0,33    1,52
Branchen
Nr. Titel                      RS      KUV    KBV                 Nr.   Titel                       RS      KUV     KBV
 1   Computerhardware         118,66   2,10   6,51                2    Software                   104,83   3,70    5,13"""


class ParserTests(unittest.TestCase):
    def test_numbers(self):
        self.assertEqual(num("1.234,56"), 1234.56)
        self.assertIsNone(num("k.A."))
        self.assertIsNone(num("Neu!"))

    def test_meta(self):
        self.assertEqual(parse_meta("Text\nAusgabe 22/2026\n26.10.2026\n"), (22, 2026, "2026-10-26"))

    def test_dashboard(self):
        db, warn = parse_dashboard(DASH)
        self.assertEqual(db["gesamt"]["score"], [1, 2])
        self.assertEqual((db["zinsstruktur"], db["index_trend"]), (1, -1))
        self.assertEqual([db[k] for k in ("anleihezinsen", "oel", "crb", "dollar", "saison")], [-1, -1, -1, 1, 1])
        self.assertEqual(db["other5_score"], [2, 3])  # word says 'positiv', numbers decide
        self.assertEqual(db["sicherheit"], -1)
        self.assertEqual(db["m9"], [39, 114])
        self.assertEqual(db["zinsstruktur_value"], 0.47)
        self.assertEqual(warn, [])

    def test_dashboard_inconsistency_is_reported(self):
        db, warn = parse_dashboard(DASH.replace("Negativ 1:2", "Positiv 3:0"))
        self.assertTrue(any("does not match" in w for w in warn))

    def test_dispo(self):
        rows, depot, warn = parse_dispo(DISPO)
        self.assertEqual([r["rec"] for r in rows], ["K", "H", "H", "V"])
        a, b, gold, new = rows
        self.assertEqual((a["isin"], a["name"], a["sector"], a["stop_9m"]), ("CH0000000017", "Testwerk", "Pharma", 60.58))
        self.assertEqual(b["rec_date"], "2026-01-16")
        self.assertEqual(b["stop_9m"], 1690.0)
        self.assertIsNone(gold["kgv"])
        self.assertTrue(new["new"])
        self.assertEqual(depot, {"index_level": 559.68, "cash_pct": 53, "stocks_pct": 44})
        self.assertEqual(warn, [])

    def test_universe(self):
        rows, sectors, warn = parse_universe([UNI])
        self.assertEqual([r["name"] for r in rows], ["Alpha Werke", "Gamma Bank", "Beta Systems", "Delta 3D"])
        beta = rows[2]
        self.assertEqual((beta["ht"], beta["kuv"], beta["list"]), ("H", 3.57, "Testland"))
        self.assertIsNone(rows[1]["kuv"])
        self.assertEqual(rows[3]["ht"], "T")
        self.assertEqual([s["name"] for s in sectors], ["Computerhardware", "Software"])
        self.assertTrue(warn)  # fewer than 500 rows is flagged

    def test_calls(self):
        c = parse_calls("Inhalt Kaufen: Alpha Werke, Beta Systems 4 Gesamtsystem ... Verkauf: Delta 3D Obwohl die Aktie")
        self.assertEqual(c["buy"], ["Alpha Werke", "Beta Systems"])
        self.assertEqual(c["sell"], ["Delta 3D"])

    def test_name_key(self):
        self.assertEqual(name_key("Roche Hold.-GS."), name_key("Roche Holding"))
        self.assertEqual(name_key("Société Générale"), "societe generale")


if __name__ == "__main__":
    unittest.main()
