import json
import re
import subprocess
import unittest
from datetime import datetime

from boersen_agent import i18n

SAMPLES = [
    "weekly close broke the 9-month low 24.68 (last week); the newsletter's latest exit point",
    "weekly close broke the 9-month low 134.98 (1 weeks ago); the newsletter's latest exit point",
    "weekly close below all closes of this and last quarter (1277.00)",
    "price 79.00 is below the stop 80.00; sell if this week closes below",
    "only 2.2% above the 9-month stop (14.27)",
    "RS rank 342 is outside the top 200", "RS rank 205 outside the top 200", "RS rank 82 within the top 200",
    "no new 9-month high for 60 weeks", "bottom third of its list (40/58) in a rising market",
    "newsletter Dispo-Liste: Kaufen (K)", "newsletter Dispo-Liste: Verkaufen (V)",
    "meets the stock-list criteria (high RS, low KUV, new high)", "RS 127, global rank 96",
    "new 9-month high (H)", "KUV 3.90 above sector average (3.62)", "KUV 0.90 at or below normal range (2.00)",
    "KBV 1.84 within 4.0", "live data: no stop or quarter-rule breach", "live RS 129 (newsletter list: 127)",
    "no sell or review rule triggered", "no live price data: rules checked on newsletter data only",
    "symbol SIE.DE found by automatic lookup; please verify", "newsletter data is 20 days old",
    "Gesamtsystem negative: newsletter advises caution", "Sicherheitssystem negative (2 of 3 warnings)",
    "Saisonfaktor negative (May-Oct): the newsletter does not add risk",
    "fund/ETF: only the price rules apply (no newsletter stock data)",
    "smoothed spread 0.52 (newsletter: 0.44); US+DE averaging [I]",
    "last 117.6; negative on 6-week high [V], positive on 6-week low [U]",
    "last 0.8942; EUR per USD; buy on 15-week high [I], sell on 15-week low [U]",
    "from newsletter (no free CRB data; rule needs the index)",
]
ENGLISH = re.compile(r"\b(the|weekly|below|above|outside|within|newsletter data|no sell|please|only|rule)\b")


class GermanTests(unittest.TestCase):
    def tearDown(self):
        i18n.set_lang("en")

    def test_every_reason_pattern_is_translated(self):
        i18n.set_lang("de")
        for s in SAMPLES:
            out = i18n.reason(s)
            self.assertNotEqual(out, s, s)
            self.assertIsNone(ENGLISH.search(out.replace("Newsletter", "")), f"{s!r} -> {out!r}")

    def test_english_is_untouched(self):
        self.assertEqual(i18n.reason(SAMPLES[0]), SAMPLES[0])
        self.assertEqual(i18n._("Daily brief"), "Daily brief")
        self.assertEqual(i18n.num(1234.5, 2), "1 234.50")

    def test_numbers_dates_actions(self):
        i18n.set_lang("de")
        self.assertEqual(i18n.num(950684.89, 2), "950.684,89")
        self.assertEqual(i18n.num(2.9, 1, "%"), "2,9 %")
        self.assertEqual(i18n.date_long(datetime(2026, 11, 5, 7, 41)), "Donnerstag, 05. November 2026 · 07:41")
        self.assertEqual(i18n.date_short("2026-10-26"), "26.10.2026")
        self.assertEqual(i18n.action("SELL"), "VERKAUFEN")
        self.assertIn("German", i18n.ai_language_rule())
        with self.assertRaises(ValueError):
            i18n.set_lang("fr")


if __name__ == "__main__":
    unittest.main()
