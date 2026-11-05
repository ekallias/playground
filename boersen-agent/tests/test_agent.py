"""End-to-end: fake Paperless + CSV prices + synthetic rates -> both reports."""
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest import mock

from boersen_agent import agent as agent_mod
from boersen_agent import config, marketdata
from boersen_agent.agent import Agent
from boersen_agent.marketdata import CSVProvider, Market
from boersen_agent.newsletter import Issue
from boersen_agent.portfolio import import_csv
from boersen_agent.store import Store
from tests.helpers import write_csv

END = date(2026, 11, 5)


def back_series(days, f):
    """Business days ending on END, value f(i) with i=0 oldest."""
    ds, d = [], END
    while len(ds) < days:
        if d.weekday() < 5:
            ds.append(d)
        d -= timedelta(days=1)
    ds.reverse()
    return [(x.isoformat(), float(f(i))) for i, x in enumerate(ds)]


def dashboard():
    return {"gesamt": {"positive": True, "score": [2, 1]}, "zinsstruktur": 1, "index_trend": 1,
            "anleihezinsen": -1, "oel": -1, "crb": -1, "dollar": 1, "saison": -1, "other5_score": [2, 3],
            "short_term": -1, "sicherheit": -1, "m9": [97, 57], "w32": [0, 4], "q25": [0, 8],
            "zinsstruktur_value": 0.44, "fazit": "Test"}


def issue(number=22, when="2026-10-26"):
    return Issue(number, 2026, when, dashboard(),
                 dispo=[{"isin": "CH0000000017", "name": "Testwerk", "list": "CH", "sector": "Pharma",
                         "rec_date": "2025-12-05", "rec_price": 58.0, "price_today": 79.6, "kgv": 47.0,
                         "stop_9m": 60.6, "rec": "K", "new": False},
                        {"isin": "US0000000009", "name": "Beispiel Corp", "list": "USA", "sector": "Elektronik",
                         "rec_date": "2026-01-16", "rec_price": 100.0, "price_today": 60.0, "kgv": 20.0,
                         "stop_9m": 90.0, "rec": "H", "new": False}],
                 depot={"cash_pct": 53, "stocks_pct": 44, "index_level": 559.68},
                 universe=[{"list": "CH", "list_rank": i + 1, "name": n, "rs": rs, "ht": ht, "kuv": kuv, "kbv": 2.0}
                           for i, (n, rs, ht, kuv) in enumerate([("Testwerk", 127.0, "H", 1.5), ("Kandidat AG", 160.0, "H", 0.9),
                                                                  ("Teuer AG", 150.0, "H", 9.0), ("Beispiel Corp", 70.0, "T", 1.0)])],
                 sectors=[{"name": "Pharma, Kosmetik", "rs": 114.0, "kuv": 3.6, "kbv": 3.4}],
                 calls={"buy": ["Testwerk"], "sell": []})


class FakePaperless:
    def __init__(self):
        self.uploaded = []

    def list_correspondents(self):
        return [{"id": 9, "name": "Börsen Signale"}]

    def iter_documents(self, **kw):
        return iter([{"id": 1, "created": "2026-10-26"}, {"id": 2, "created": "2026-10-26"}])  # duplicate upload

    def download_document(self, doc_id, dest, original=False):
        Path(dest).write_bytes(b"%PDF-fake")
        return Path(dest)

    def upload_document(self, path, **kw):
        self.uploaded.append((Path(path).name, kw))
        return "task"


def rates():
    return {
        "FRED:DGS10": lambda s: back_series(900, lambda i: 4.5),
        "FRED:DGS1": lambda s: back_series(900, lambda i: 4.0),
        "BBK:UMLAUFRENDITE": lambda s: back_series(900, lambda i: 3.0),
        "ECB:EURIBOR12M": lambda s: [(f"{y}-{m:02d}-01", 2.5) for y in (2024, 2025, 2026) for m in range(1, 13)],
    }


class AgentEndToEnd(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.cfg = config.load(None)
        self.cfg.data_dir, self.cfg.reports_dir, self.cfg.provider = root / "data", root / "reports", "csv"
        self.cfg.upload_reports, self.cfg.report_tags = True, [5]
        csvdir = root / "data" / "csv"
        up = lambda i: 100 + i * 0.05
        for sym in ("^GSPC", "^GDAXI", "^IXIC", "^DJU"):
            write_csv(csvdir, sym, back_series(800, up))
        write_csv(csvdir, "BZ=F", back_series(800, lambda i: 80 - i * 0.01))
        write_csv(csvdir, "EURUSD=X", back_series(800, lambda i: 1.10 + i * 0.0001))
        write_csv(csvdir, "EURCHF=X", back_series(800, lambda i: 0.93))
        write_csv(csvdir, "TW.SW", back_series(800, up))
        write_csv(csvdir, "BSP", back_series(800, lambda i: 100 + i * 0.05 if i < 790 else 60))  # crash
        (csvdir / "_search.csv").write_text("query,symbol,name\nUS0000000009,BSP,Beispiel Corp\n")
        self.store = Store(self.cfg.db_path)
        p = root / "p.csv"
        p.write_text("isin,symbol,name,quantity,avg_price,currency,buy_date\n"
                     "CH0000000017,TW.SW,Testwerk,10,50,CHF,2025-12-05\n"
                     "US0000000009,BSP,Beispiel Corp,5,100,USD,2026-01-16\n"
                     ",CASH,Cash,1000,1,EUR,\n")
        import_csv(self.store, p, END)
        self.market = Market(self.store, CSVProvider(csvdir), 3, today=END)
        self.pl = FakePaperless()

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_run_ingests_once_and_writes_both_reports(self):
        with mock.patch.dict(marketdata.RATE_SOURCES, rates()), \
             mock.patch.object(agent_mod, "parse_pdf", return_value=issue()):
            a = Agent(self.cfg, self.store, self.market, self.pl, datetime(2026, 11, 5, 18, 0))
            paths = a.run()
        self.assertEqual([p.name for p in paths], ["2026-22_newsletter-report.md", "2026-11-05_daily-brief.md"])
        self.assertEqual(self.store.issue_keys(), ["2026-22"])                       # duplicate upload skipped
        self.assertEqual(self.store.db.execute("SELECT note FROM seen_docs WHERE doc_id=2").fetchone()[0],
                         "duplicate upload")
        daily = paths[1].read_text()
        bi = paths[0].read_text()
        self.assertIn("**SELL** Beispiel Corp", daily)                              # 9-month low after crash
        self.assertIn("Kandidat AG", bi)                                            # new candidate
        self.assertNotIn("Teuer AG", bi.split("## 5.")[1].split("## Portfolio")[0])  # KUV too high
        self.assertIn("broke the 9-month low", daily)                              # live rule, not just the T flag
        self.assertIn("**ADD** | 85", daily.replace(" (high)", ""))                 # held + newsletter K
        self.assertIn("Saisonfaktor | ＋ ⚠ | －", daily)                             # season turned after the issue
        self.assertEqual(len(self.pl.uploaded), 2)
        self.assertTrue(all(name.endswith(".txt") for name, _ in self.pl.uploaded))

    def test_second_run_same_day_does_not_reingest(self):
        with mock.patch.dict(marketdata.RATE_SOURCES, rates()), \
             mock.patch.object(agent_mod, "parse_pdf", return_value=issue()):
            Agent(self.cfg, self.store, self.market, self.pl, datetime(2026, 11, 5, 18)).run()
            paths = Agent(self.cfg, self.store, self.market, self.pl, datetime(2026, 11, 5, 19)).run()
        self.assertEqual([p.name for p in paths], ["2026-11-05_daily-brief.md"])


if __name__ == "__main__":
    unittest.main()
