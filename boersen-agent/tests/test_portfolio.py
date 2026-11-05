import tempfile
import unittest
from datetime import date
from pathlib import Path

from boersen_agent.portfolio import holdings, import_csv, record_trade
from boersen_agent.store import Store

CSV = """isin,symbol,name,quantity,avg_price,currency,buy_date
CH0000000017,TW.SW,Testwerk,10,50,CHF,2025-12-05
,BSP,Beispiel Corp,5,100,USD,2026-01-16
,CASH,Cash,1000,1,EUR,
"""


class PortfolioTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "a.db")
        p = Path(self.tmp.name) / "p.csv"
        p.write_text(CSV)
        self.n = import_csv(self.store, p, date(2026, 11, 5))

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_import(self):
        self.assertEqual(self.n, 3)
        hs = {h.key: h for h in holdings(self.store)}
        self.assertEqual(hs["CH0000000017"].quantity, 10)
        self.assertEqual(hs["CASH:EUR"].quantity, 1000)
        self.assertEqual(self.store.symbol_for_isin("CH0000000017")["symbol"], "TW.SW")

    def test_double_import_is_refused(self):
        p = Path(self.tmp.name) / "p.csv"
        with self.assertRaises(ValueError):
            import_csv(self.store, p, date(2026, 11, 5))

    def test_trades_update_cost_and_cash(self):
        record_trade(self.store, "buy", 10, 70, "CHF", date(2026, 11, 5), isin="CH0000000017")
        record_trade(self.store, "sell", 5, 80, "CHF", date(2026, 11, 6), isin="CH0000000017")
        h = {x.key: x for x in holdings(self.store)}["CH0000000017"]
        self.assertEqual(h.quantity, 15)
        self.assertAlmostEqual(h.avg_price, 60)
        self.assertAlmostEqual(h.realized, 100)

    def test_overselling_is_refused(self):
        with self.assertRaises(ValueError):
            record_trade(self.store, "sell", 50, 1, "USD", date(2026, 11, 5), symbol="BSP")

    def test_buy_in_cash_currency_reduces_cash(self):
        record_trade(self.store, "buy", 2, 100, "EUR", date(2026, 11, 5), symbol="XYZ.DE")
        self.assertEqual({x.key: x for x in holdings(self.store)}["CASH:EUR"].quantity, 800)


if __name__ == "__main__":
    unittest.main()


BROKER = ("﻿Bezeichnung;WKN;ISIN;Währung;Einstandskurs;Bestand\r\n"
          "BEISPIEL AG NAMENS-AKTIEN O.N.;123456;DE0000000001;EUR;120,54321;40\r\n"
          "TEST WORLD 1C FUNDS;A1B2C3;IE0000000002;EUR;88,12345;1.250,00\r\n"
          "1,0% BUNDESANLEIHE TEST 15.2.30;999999;DE0000000003;EUR;99,25;25.000,00\r\n")


class BrokerImportTests(unittest.TestCase):
    def test_broker_export_is_detected_and_classified(self):
        from boersen_agent.portfolio import classify  # noqa: F401
        with tempfile.TemporaryDirectory() as d:
            st = Store(Path(d) / "a.db")
            p = Path(d) / "b.csv"
            p.write_text(BROKER, encoding="utf-8")
            self.assertEqual(import_csv(st, p, date(2026, 11, 5)), 3)
            hs = {h.key: h for h in holdings(st)}
            self.assertEqual(hs["DE0000000001"].quantity, 40)
            self.assertAlmostEqual(hs["DE0000000001"].avg_price, 120.54321)
            self.assertEqual(hs["IE0000000002"].asset, "fund")
            self.assertEqual(hs["IE0000000002"].quantity, 1250)
            bond = hs["DE0000000003"]
            self.assertEqual((bond.asset, bond.quantity), ("bond", 25000))
            self.assertTrue(bond.cash_like)
            self.assertIn("buy date unknown", st.transactions()[0]["note"])
            st.close()

    def test_bond_valued_at_cost_in_percent_of_nominal(self):
        from types import SimpleNamespace

        from boersen_agent.portfolio import Holding, valuation
        bond = Holding("B", "DE0000000003", None, "Bund", 100000, 97.5, "EUR", "2026-11-05", asset="bond")
        fund = Holding("F", "IE0000000002", "F.DE", "Fund", 10, 100, "EUR", "2026-11-05", asset="fund")
        mkt = SimpleNamespace(quote=lambda s: SimpleNamespace(price=110.0, currency="EUR") if s == "F.DE" else None)
        v = valuation([bond, fund], mkt, SimpleNamespace(symbols={}), lambda h: (h.symbol, True))
        self.assertAlmostEqual(v["rows"][0]["value_eur"], 97500)
        self.assertEqual(v["at_cost"], ["Bund"])
        self.assertAlmostEqual(v["equity_share"], 1100 / 98600 * 100)
