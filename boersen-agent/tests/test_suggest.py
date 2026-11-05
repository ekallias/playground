import unittest
from types import SimpleNamespace

from boersen_agent.config import DEFAULT_RULES
from boersen_agent.suggest import Live, UniverseIndex, evaluate

CFG = SimpleNamespace(rule=lambda k: DEFAULT_RULES[k])


def live(**kw):
    base = dict(price=100.0, quote_time="2026-11-05", stop=80.0, stop_breach_confirmed=False,
                breach_weeks_ago=None, breach_level=None,
                stop_breach_provisional=False, dist_to_stop_pct=25.0, quarter_low=False, quarter_low_level=85.0,
                new_high=False, weeks_since_high=3, rs=115.0, day_change_pct=0.5)
    base.update(kw)
    return Live(**base)


def market(gesamt=(2, 1), safety=1, strict=False, season=True):
    return SimpleNamespace(gesamt_positive=gesamt[0] > gesamt[1], safety_effective=-1 if strict else safety,
                           safety_strict=strict, season_positive=season)


UNI = {"name": "Testwerk", "list": "X", "list_rank": 3, "rs": 130.0, "ht": "H", "kuv": 1.2, "kbv": 2.0, "global_rank": 12}


def ev(**kw):
    args = dict(key="K1", name="Testwerk", isin="CH0000000017", symbol="TW.SW", symbol_verified=True,
                in_portfolio=True, dispo=None, uni=UNI, uni_match=1.0, list_size=60, sect_kuv=None,
                live=live(), market=market(), cfg=CFG, newsletter_age=5)
    args.update(kw)
    return evaluate(**args)


class SuggestTests(unittest.TestCase):
    def test_confirmed_9_month_low_is_high_confidence_sell(self):
        s = ev(live=live(stop_breach_confirmed=True, breach_weeks_ago=0, breach_level=80.0, price=79))
        self.assertEqual(s.action, "SELL")
        self.assertGreaterEqual(s.confidence, 80)
        self.assertEqual(s.label, "high")

    def test_newsletter_sell_plus_live_breach_is_stronger(self):
        lv = dict(stop_breach_confirmed=True, breach_weeks_ago=1, breach_level=80.0, price=79)
        a = ev(live=live(**lv))
        b = ev(live=live(**lv), dispo={"rec": "V", "sector": ""})
        self.assertGreater(b.confidence, a.confidence)

    def test_intraweek_breach_is_watch_not_sell(self):
        s = ev(live=live(stop_breach_provisional=True, price=79))
        self.assertEqual(s.action, "WATCH")

    def test_quarter_rule_low_is_reduce(self):
        self.assertEqual(ev(live=live(quarter_low=True)).action, "REDUCE")

    def test_newsletter_buy_is_confirmed_and_lowered_in_bad_regime(self):
        good = ev(in_portfolio=False, dispo={"rec": "K", "sector": ""})
        bad = ev(in_portfolio=False, dispo={"rec": "K", "sector": ""}, market=market((1, 2), -1, season=False))
        self.assertEqual(good.action, "BUY")
        self.assertEqual(ev(dispo={"rec": "K", "sector": ""}).action, "ADD")
        self.assertEqual(good.verification, "confirmed")
        self.assertLess(bad.confidence, good.confidence - 25)

    def test_newsletter_buy_contradicted_by_live_data(self):
        s = ev(in_portfolio=False, dispo={"rec": "K", "sector": ""}, live=live(quarter_low=True))
        self.assertEqual(s.verification, "contradicted")
        self.assertEqual(s.action, "REDUCE")

    def test_holding_outside_top_200_is_review(self):
        s = ev(uni=dict(UNI, global_rank=450))
        self.assertEqual(s.action, "REVIEW")

    def test_missing_live_data_lowers_confidence(self):
        a = ev()
        b = ev(live=None)
        self.assertEqual(a.action, "HOLD")
        self.assertLess(b.confidence, a.confidence)
        self.assertTrue(any("no live price" in r["text"] for r in b.reasons))

    def test_confidence_is_clamped_and_reasons_explain_points(self):
        s = ev(in_portfolio=False, candidate=True, live=None, symbol=None, uni_match=0.8, newsletter_age=40,
               market=market((0, 3), -1, True, False))
        self.assertGreaterEqual(s.confidence, 5)
        self.assertTrue(all("pts" in r and "marker" in r for r in s.reasons))


class UniverseIndexTests(unittest.TestCase):
    def test_global_rank_and_fuzzy_match(self):
        idx = UniverseIndex([{"name": "Roche Hold.-GS.", "list": "CH", "list_rank": 1, "rs": 121.9, "ht": "H", "kuv": 5, "kbv": 9},
                             {"name": "Siemens Energy", "list": "DAX", "list_rank": 1, "rs": 150.0, "ht": "", "kuv": 2, "kbv": 5}])
        self.assertEqual(idx.rows[0]["name"], "Siemens Energy")
        r, m = idx.match("Roche Holding AG")
        self.assertEqual(r["global_rank"], 2)
        self.assertEqual(m, 1.0)
        self.assertIsNone(idx.match("Unknown Company")[0])


if __name__ == "__main__":
    unittest.main()
