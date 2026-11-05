"""End-to-end: fake Paperless + CSV prices + synthetic rates -> both reports."""
import json
import tempfile
import unittest
from contextlib import contextmanager
from types import SimpleNamespace
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


class FakeAI:
    """Stands in for anthropic.Anthropic(): returns a schema-shaped analysis for the fact pack it gets."""

    def __init__(self):
        self.requests = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self._stream))

    @contextmanager
    def _stream(self, **kw):
        self.requests.append(kw)
        text = kw["messages"][0]["content"][-1]["text"]
        facts = json.loads(text.split("```json\n", 1)[1].rsplit("\n```", 1)[0])

        def call(p, override=False):
            act = "HOLD" if override else p["rule_engine"]["action"]
            return {"key": p["key"], "action": act, "confidence": 140 if override else 61,
                    "rationale": f"test rationale for {p['name']}",
                    "rule_agreement": "overrides" if override else "agrees",
                    "override_reason": "test override" if override else ""}

        pos = facts["portfolio"]["positions"]
        data = {"headline": "Test headline", "summary": "Test summary.",
                "market_view": {"stance": "cautious", "confidence": 55, "reasoning": "test"},
                "position_calls": [call(p, override=p["rule_engine"]["action"] == "SELL") for p in pos]
                + [{"key": "INVENTED", "action": "BUY", "confidence": 90, "rationale": "x",
                    "rule_agreement": "agrees", "override_reason": ""}],
                "newsletter_picks": [dict(call(p), verdict="confirmed") for p in facts.get("newsletter_picks", [])],
                "new_ideas": [call(p) for p in facts.get("candidates", [])],
                "risks": ["test risk"], "questions_for_user": ["test question"], "notes_for_next_run": "watch BSP"}
        msg = SimpleNamespace(stop_reason="end_turn", model="claude-opus-5-5",
                              content=[SimpleNamespace(type="text", text=json.dumps(data))],
                              usage=SimpleNamespace(input_tokens=1000, output_tokens=200,
                                                    cache_read_input_tokens=0, cache_creation_input_tokens=0))
        yield SimpleNamespace(get_final_message=lambda: msg)


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

    def run_agent(self, ai=None, hour=18):
        with mock.patch.dict(marketdata.RATE_SOURCES, rates()), \
             mock.patch.object(agent_mod, "parse_pdf", return_value=issue()):
            a = Agent(self.cfg, self.store, self.market, self.pl, datetime(2026, 11, 5, hour, 0), ai_client=ai)
            return a.run()

    def test_run_ingests_once_and_writes_pdf_reports_with_ai(self):
        ai = FakeAI()
        paths = self.run_agent(ai)
        self.assertEqual([p.name for p in paths], ["2026-22_newsletter-report.pdf", "2026-11-05_daily-brief.pdf"])
        for p in paths:
            self.assertEqual(p.read_bytes()[:5], b"%PDF-")
            self.assertGreater(p.stat().st_size, 20_000)                            # charts are embedded
        self.assertEqual(self.store.issue_keys(), ["2026-22"])                       # duplicate upload skipped
        daily = (self.cfg.reports_dir / "2026-11-05_daily-brief.md").read_text()
        self.assertIn("Test headline", daily)
        self.assertIn("broke the 9-month low", daily)                               # rule engine still runs
        self.assertIn("⚠ overrides rule", daily)                                    # AI override is flagged
        self.assertNotIn("INVENTED", daily)                                         # unknown key dropped
        bi = (self.cfg.reports_dir / "2026-22_newsletter-report.md").read_text()
        self.assertIn("Kandidat AG", bi)
        # issue request carried the newsletter PDF; daily did not; fallbacks + structured output requested
        issue_req, daily_req = ai.requests
        self.assertEqual(issue_req["messages"][0]["content"][0]["type"], "document")
        self.assertEqual(daily_req["messages"][0]["content"][0]["type"], "text")
        self.assertEqual(issue_req["fallbacks"], "default")
        self.assertEqual(issue_req["output_config"]["format"]["type"], "json_schema")
        self.assertEqual(issue_req["output_config"]["effort"], "high")
        self.assertEqual(daily_req["output_config"]["effort"], "medium")
        # memory: the daily run saw the issue run's notes
        self.assertIn('"previous_ai_notes": "watch BSP"', daily_req["messages"][0]["content"][-1]["text"])
        stored = self.store.last_analysis()
        self.assertEqual(stored["position_calls"][0]["confidence"] <= 100, True)     # clamped
        self.assertEqual(len(self.pl.uploaded), 2)
        self.assertTrue(all(name.endswith(".pdf") for name, _ in self.pl.uploaded))

    def test_without_credentials_reports_fall_back_to_rules(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            paths = self.run_agent(ai=None)
        daily = (self.cfg.reports_dir / "2026-11-05_daily-brief.md").read_text()
        self.assertIn("Unavailable: no Anthropic credentials", daily)
        self.assertTrue(all(p.read_bytes()[:5] == b"%PDF-" for p in paths))

    def test_second_run_same_day_does_not_reingest(self):
        self.run_agent(FakeAI())
        paths = self.run_agent(FakeAI(), hour=19)
        self.assertEqual([p.name for p in paths], ["2026-11-05_daily-brief.pdf"])


if __name__ == "__main__":
    unittest.main()


class HandoffMode(AgentEndToEnd):
    """prepare -> (a Claude session writes analysis.json) -> finish, without any API call."""

    def test_prepare_then_finish(self):
        self.cfg.ai_mode = "handoff"
        out = self.cfg.data_dir / "handoff"
        with mock.patch.dict(marketdata.RATE_SOURCES, rates()), \
             mock.patch.object(agent_mod, "parse_pdf", return_value=issue()):
            a = Agent(self.cfg, self.store, self.market, self.pl, datetime(2026, 11, 5, 18, 0))
            man = a.prepare(out)
            self.assertEqual([t["scope"] for t in man["tasks"]], ["issue", "daily"])
            self.assertTrue((out / "prompt.md").read_text().startswith("You are the analyst"))
            self.assertIn("position_calls", json.loads((out / "schema.json").read_text())["properties"])
            # the "session" answers the daily task only, with one invented key
            facts = json.loads(Path(man["tasks"][1]["facts"]).read_text())
            calls = [{"key": p["key"], "action": p["rule_engine"]["action"], "confidence": 64, "rationale": "ok",
                      "rule_agreement": "agrees", "override_reason": ""} for p in facts["portfolio"]["positions"]]
            calls.append({"key": "INVENTED", "action": "BUY", "confidence": 99, "rationale": "x",
                          "rule_agreement": "agrees", "override_reason": ""})
            Path(man["tasks"][1]["output"]).write_text(json.dumps({
                "headline": "Session headline", "summary": "s", "position_calls": calls,
                "market_view": {"stance": "neutral", "confidence": 50, "reasoning": "r"}}))
            res = Agent(self.cfg, self.store, self.market, self.pl, datetime(2026, 11, 5, 18, 5)).finish(out)
        self.assertEqual(len(res["reports"]), 2)
        self.assertTrue(all(Path(p).read_bytes()[:5] == b"%PDF-" for p in res["reports"]))
        self.assertEqual(res["problems"], ["issue: no analysis file was written"])
        self.assertEqual(res["headline"], "Session headline")
        daily = (self.cfg.reports_dir / "2026-11-05_daily-brief.md").read_text()
        self.assertIn("Session headline", daily)
        self.assertNotIn("INVENTED", daily)
        bi = (self.cfg.reports_dir / "2026-22_newsletter-report.md").read_text()
        self.assertIn("Unavailable: no analysis file was written", bi)

    def test_handoff_mode_never_calls_the_api(self):
        self.cfg.ai_mode = "handoff"
        ai = FakeAI()
        self.run_agent(ai)
        self.assertEqual(ai.requests, [])
