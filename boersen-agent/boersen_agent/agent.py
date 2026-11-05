"""Orchestration: newsletter ingestion, evaluation, reports."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from . import report
from .marketdata import DataError
from .newsletter import Issue, issue_age_days, name_key, parse_pdf
from .portfolio import holdings, valuation
from .signals import compute_market
from .suggest import UniverseIndex, evaluate, held_weeks_since, live_metrics, sector_kuv

LOOKUP_BUDGET = 25


@dataclass
class Context:
    issue: dict | None
    dashboard: dict | None
    market: object
    suggestions: list
    portfolio: dict
    previous: dict
    notes: list[str]
    issue_rows: dict


class Agent:
    def __init__(self, cfg, store, market, paperless=None, now: datetime | None = None):
        self.cfg, self.store, self.market, self.paperless = cfg, store, market, paperless
        self.now = now or datetime.now()
        self.today = self.now.date()
        self._lookups = 0
        self.notes: list[str] = []

    # ------------------------------------------------------------------ ingestion
    def _correspondent_id(self) -> int:
        for c in self.paperless.list_correspondents():
            if c["name"].strip().lower() == self.cfg.correspondent.strip().lower():
                return c["id"]
        raise RuntimeError(f"Paperless correspondent {self.cfg.correspondent!r} not found")

    def ingest(self) -> list[str]:
        """Pull newsletters from Paperless that were not seen before. Returns new issue keys."""
        if self.paperless is None:
            raise RuntimeError("Paperless is not configured (PAPERLESS_URL / PAPERLESS_TOKEN)")
        cid = self._correspondent_id()
        folder = self.cfg.data_dir / "newsletters"
        folder.mkdir(parents=True, exist_ok=True)
        new = []
        for doc in self.paperless.iter_documents(correspondent__id=cid, ordering="created"):
            if self.store.has_doc(doc["id"]):
                continue
            pdf = folder / f"{doc['created'][:10]}_{doc['id']}.pdf"
            if not pdf.exists():
                self.paperless.download_document(doc["id"], pdf)
            key = self.ingest_file(pdf, doc_id=doc["id"])
            if key:
                new.append(key)
        return new

    def ingest_file(self, pdf: Path, doc_id: int | None = None) -> str | None:
        try:
            issue: Issue = parse_pdf(Path(pdf))
        except Exception as e:  # noqa: BLE001 - keep going with the other documents
            if doc_id is not None:
                self.store.mark_doc(doc_id, None, f"parse failed: {e}")
            self.notes.append(f"could not parse {Path(pdf).name}: {e}")
            return None
        if self.store.has_issue(issue.key):
            if doc_id is not None:
                self.store.mark_doc(doc_id, issue.key, "duplicate upload")
            return None
        self.store.save_issue(issue, doc_id, self.now.isoformat(timespec="seconds"))
        if doc_id is not None:
            self.store.mark_doc(doc_id, issue.key, "ingested")
        return issue.key

    # ------------------------------------------------------------------ symbols
    def _lookup(self, query: str):
        if not self.cfg.auto_lookup or self._lookups >= LOOKUP_BUDGET:
            return None
        self._lookups += 1
        try:
            hits = self.market.provider.search(query)
        except DataError as e:
            self.market.errors.append(f"symbol lookup failed for {query}: {e}")
            return None
        return hits[0] if hits else None

    def symbol_for(self, isin: str | None, name: str | None) -> tuple[str | None, bool]:
        if isin:
            row = self.store.symbol_for_isin(isin)
            if row:
                return row["symbol"], bool(row["verified"])
            hit = self._lookup(isin)
            if hit:
                self.store.set_symbol(isin, hit.symbol, name or hit.name, hit.currency, "lookup", False)
                return hit.symbol, False
        if name:
            k = name_key(name)
            row = self.store.symbol_for_name(k)
            if row:
                return row["symbol"], bool(row["verified"])
            if not isin:
                hit = self._lookup(name)
                if hit:
                    self.store.set_name_symbol(k, name, hit.symbol, "lookup", False)
                    return hit.symbol, False
        return None, False

    # ------------------------------------------------------------------ evaluation
    def _live(self, symbol: str | None):
        if not symbol:
            return None
        daily = self.market.daily_with_quote(symbol)
        q = self.market.quote(symbol)
        return live_metrics(daily, self.now, self.cfg.rule("nine_month_weeks"), self.cfg.rule("rs_months"),
                            q.previous_close if q else None)

    def context(self, issue_key: str | None = None, with_candidates: bool = True,
                newsletter_scope: bool = True) -> Context:
        iss = (self.store.latest_issue() if issue_key is None else
               next((dict(r) for r in self.store.db.execute("SELECT * FROM issues WHERE issue_key=?",
                                                              (issue_key,))), None))
        dash = json.loads(iss["dashboard"]) if iss else None
        rows = {t: self.store.rows(t, iss["issue_key"]) if iss else [] for t in ("dispo", "universe", "sectors", "calls")}
        uidx = UniverseIndex(rows["universe"])
        age = issue_age_days(iss["issue_date"], self.today) if iss else None
        mstate = compute_market(self.market, self.cfg, dash, self.now, iss["issue_date"] if iss else None)

        hold = holdings(self.store)
        port = valuation(hold, self.market, self.cfg, lambda h: self._resolve_holding(h))
        dispo_by_isin = {d["isin"]: d for d in rows["dispo"]}
        sug, seen = [], set()

        def add(key, name, isin, in_port, dispo, held_weeks=None, candidate=False, uni=None, match=0.0,
                fund=False, symbol=None):
            if key in seen:
                return
            seen.add(key)
            sym, ver = (symbol, True) if symbol else self.symbol_for(isin, name)
            if uni is None and not fund:
                uni, match = uidx.match(name)
            sk = sector_kuv(dispo.get("sector") if dispo else None, rows["sectors"])
            live = self._live(sym)
            s = evaluate(key=key, name=name, isin=isin, symbol=sym, symbol_verified=ver, in_portfolio=in_port,
                         dispo=dispo, uni=uni, uni_match=match,
                         list_size=uidx.list_sizes.get(uni["list"]) if uni else None, sect_kuv=sk,
                         live=live, market=mstate, cfg=self.cfg, newsletter_age=age,
                         held_weeks=held_weeks, candidate=candidate)
            if fund:
                s.reasons.append({"text": "fund/ETF: only the price rules apply (no newsletter stock data)",
                                  "marker": "", "pts": 0})
            sug.append(s)

        for h in hold:
            if h.cash_like:
                continue
            d = dispo_by_isin.get(h.isin) if h.isin else None
            if d is None and rows["dispo"] and h.asset == "stock":
                d = next((x for x in rows["dispo"] if name_key(x["name"]) == name_key(h.name)), None)
            add(h.isin or h.symbol, h.name, h.isin, True, d, held_weeks_since(h.first_date, self.today),
                fund=h.asset == "fund", symbol=h.symbol)
        if newsletter_scope:
            for d in rows["dispo"]:
                add(d["isin"], d["name"], d["isin"], False, d)
        if with_candidates and uidx.rows:
            dispo_keys = {name_key(d["name"]) for d in rows["dispo"]}
            picks = 0
            for r in uidx.rows[: self.cfg.rule("candidate_top_n")]:
                if picks >= self.cfg.rule("max_new_candidates"):
                    break
                if r["ht"] != "H" or name_key(r["name"]) in dispo_keys or name_key(r["name"]) in seen:
                    continue
                kuv_ok = r["kuv"] is not None and r["kuv"] <= self.cfg.rule("kuv_normal_max")
                kbv_ok = r["kuv"] is None and r["kbv"] is not None and r["kbv"] <= self.cfg.rule("kbv_fair_max")
                if not (kuv_ok or kbv_ok):
                    continue
                picks += 1
                add("name:" + name_key(r["name"]), r["name"], None, False, None, candidate=True, uni=r, match=1.0)

        prev = self.store.previous_suggestions("daily", self.today.isoformat())
        return Context(iss, dash, mstate, sug, port, prev, self.notes + self.market.errors, rows)

    def _resolve_holding(self, h):
        if h.symbol:
            return h.symbol, True
        return self.symbol_for(h.isin, h.name)

    # ------------------------------------------------------------------ outputs
    def _write(self, name: str, text: str, title: str) -> Path:
        self.cfg.reports_dir.mkdir(parents=True, exist_ok=True)
        path = self.cfg.reports_dir / name
        path.write_text(text, encoding="utf-8")
        if self.cfg.upload_reports and self.paperless is not None:
            txt = path.with_suffix(".txt")
            txt.write_text(text, encoding="utf-8")
            try:
                self.paperless.upload_document(txt, title=title, tags=self.cfg.report_tags or None)
            except Exception as e:  # noqa: BLE001
                self.notes.append(f"upload to Paperless failed: {e}")
            finally:
                txt.unlink(missing_ok=True)
        return path

    def biweekly(self, issue_key: str | None = None) -> Path:
        ctx = self.context(issue_key, with_candidates=True, newsletter_scope=True)
        if not ctx.issue:
            raise RuntimeError("no newsletter ingested yet")
        prev_issue = self.store.latest_issue(before=ctx.issue["issue_date"])
        prev_rows = self.store.rows("dispo", prev_issue["issue_key"]) if prev_issue else []
        text = report.biweekly(ctx, prev_issue, prev_rows, self.cfg, self.now)
        self.store.save_suggestions(self.today.isoformat(), "issue:" + ctx.issue["issue_key"],
                                    [s.as_dict() for s in ctx.suggestions])
        key = ctx.issue["issue_key"]
        return self._write(f"{key}_newsletter-report.md", text, f"Börsen-Agent Bericht Ausgabe {key}")

    def daily(self) -> Path:
        ctx = self.context(with_candidates=False, newsletter_scope=False)
        text = report.daily(ctx, self.cfg, self.now)
        self.store.save_suggestions(self.today.isoformat(), "daily", [s.as_dict() for s in ctx.suggestions])
        self.store.save_snapshot(self.today.isoformat(), ctx.portfolio["total_eur"],
                                 {"rows": ctx.portfolio["rows"], "equity_share": ctx.portfolio["equity_share"]})
        return self._write(f"{self.today.isoformat()}_daily-brief.md", text,
                           f"Börsen-Agent Tagesbrief {self.today.isoformat()}")

    def run(self) -> list[Path]:
        out = []
        if self.paperless is not None:
            new = self.ingest()
            if new:
                out.append(self.biweekly(new[-1]))
        out.append(self.daily())
        return out


def today_str(d: date | None = None) -> str:
    return (d or date.today()).isoformat()
