"""Orchestration: newsletter ingestion, evaluation, reports."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from . import charts, pdfreport, report
from . import rules as R
from .analyst import AnalystError, analyse, build_facts
from .marketdata import DataError
from .newsletter import Issue, issue_age_days, name_key, parse_pdf
from .portfolio import holdings, valuation
from .signals import compute_market, zins_at_dates
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
    def __init__(self, cfg, store, market, paperless=None, now: datetime | None = None, ai_client=None):
        self.cfg, self.store, self.market, self.paperless = cfg, store, market, paperless
        self.ai_client = ai_client
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

    # ------------------------------------------------------------------ AI
    def _analyse(self, ctx, scope: str, changes=None, pdf: Path | None = None):
        if not self.cfg.ai_enabled:
            return None, "disabled in config ([ai] enabled = false)"
        if self.cfg.ai_mode == "handoff":
            return None, "hand-off mode: run `prepare`, write the analysis, then `finish`"
        prev = self.store.last_analysis(before=None)
        facts = build_facts(ctx, scope, self.now, changes, prev)
        effort = self.cfg.ai_effort_issue if scope == "issue" else self.cfg.ai_effort_daily
        try:
            a = analyse(facts, model=self.cfg.ai_model, effort=effort, pdf=pdf, client=self.ai_client,
                        max_tokens=self.cfg.ai_max_tokens, base_url=self.cfg.ai_base_url,
                        server_fallbacks=self.cfg.ai_server_fallbacks)
        except AnalystError as e:
            return None, str(e)
        self.store.save_analysis(self.today.isoformat(), scope, a, self.now.isoformat(timespec="seconds"))
        return a, None

    # ------------------------------------------------------------------ charts
    def _charts(self, ctx, name: str, issue_scope: bool) -> dict:
        folder = self.cfg.reports_dir / ".charts"
        folder.mkdir(parents=True, exist_ok=True)
        out: dict = {}

        def go(key, fn, *args):
            try:
                out[key] = fn(*args, folder / f"{name}_{key}.png")
            except Exception as e:  # noqa: BLE001 - a broken chart must not stop the report
                self.notes.append(f"chart {key} failed: {e}")

        go("allocation", charts.allocation, ctx.portfolio["rows"])
        held = [s for s in ctx.suggestions if s.in_portfolio and s.live]
        try:
            out["stop_distance"] = charts.stop_distance(
                [{"name": s.name, "dist": s.live.get("dist_to_stop_pct"),
                  "breach": s.live.get("stop_breach_confirmed")} for s in held],
                folder / f"{name}_stop_distance.png", self.cfg.rule("near_stop_pct"))
            panels = [{"name": s.name, "weekly": R.weekly(self.market.daily_with_quote(s.symbol), self.now)[0]}
                      for s in held if s.symbol]
            out["price_vs_stop"] = charts.price_vs_stop(panels, folder / f"{name}_price_vs_stop.png",
                                                        self.cfg.rule("nine_month_weeks"))
        except Exception as e:  # noqa: BLE001 - a broken chart must not stop the report
            self.notes.append(f"position charts failed: {e}")
        go("value_history", charts.value_history, self.store.snapshots())
        if issue_scope:
            hist = [dict(r) for r in self.store.db.execute("SELECT issue_date, dashboard FROM issues ORDER BY issue_date")]
            go("gesamt_history", charts.gesamt_history,
               [{"date": h["issue_date"], "score": json.loads(h["dashboard"]).get("gesamt", {}).get("score")} for h in hist])
            comp = zins_at_dates(self.market, self.cfg, [h["issue_date"] for h in hist])
            go("zins_history", charts.zins_history,
               [(h["issue_date"], json.loads(h["dashboard"]).get("zinsstruktur_value"), comp.get(h["issue_date"])) for h in hist])
            groups = {"newsletter buy (K)": [s.universe for s in ctx.suggestions if s.newsletter_rec == "K" and s.universe],
                      "your holdings": [s.universe for s in ctx.suggestions if s.in_portfolio and s.universe],
                      "new candidates": [s.universe for s in ctx.suggestions if s.newsletter_rec is None
                                         and not s.in_portfolio and s.universe]}
            go("rs_kuv", charts.rs_kuv, ctx.issue_rows["universe"], groups, self.cfg.rule("rs_top_n"),
               self.cfg.rule("kuv_normal_max"))
        return {k: v for k, v in out.items() if v}

    # ------------------------------------------------------------------ outputs
    def _publish(self, pdf: Path, title: str) -> None:
        if self.cfg.upload_reports and self.paperless is not None:
            try:
                self.paperless.upload_document(pdf, title=title, tags=self.cfg.report_tags or None)
            except Exception as e:  # noqa: BLE001
                self.notes.append(f"upload to Paperless failed: {e}")

    def _issue_pdf_path(self, issue_key: str) -> Path | None:
        r = self.store.db.execute("SELECT doc_id FROM issues WHERE issue_key=?", (issue_key,)).fetchone()
        folder = self.cfg.data_dir / "newsletters"
        if r and r[0]:
            hits = sorted(folder.glob(f"*_{r[0]}.pdf"))
            if hits:
                return hits[0]
            if self.paperless is not None:
                folder.mkdir(parents=True, exist_ok=True)
                dest = folder / f"issue-{issue_key}_{r[0]}.pdf"
                try:
                    return self.paperless.download_document(r[0], dest)
                except Exception as e:  # noqa: BLE001
                    self.notes.append(f"could not download the issue PDF from Paperless: {e}")
        return None

    def biweekly(self, issue_key: str | None = None, pdf: Path | None = None, precomputed=None) -> Path:
        ctx = self.context(issue_key, with_candidates=True, newsletter_scope=True)
        if not ctx.issue:
            raise RuntimeError("no newsletter ingested yet")
        key = ctx.issue["issue_key"]
        prev_issue = self.store.latest_issue(before=ctx.issue["issue_date"])
        prev_rows = self.store.rows("dispo", prev_issue["issue_key"]) if prev_issue else []
        changes = report.dispo_changes(prev_rows, ctx.issue_rows["dispo"]) if prev_issue else []
        analysis, err = precomputed or self._analyse(ctx, "issue", changes, pdf or self._issue_pdf_path(key))
        figs = self._charts(ctx, key, True)
        ctx.notes = list(dict.fromkeys(ctx.notes + self.notes))
        self.store.save_suggestions(self.today.isoformat(), "issue:" + key, [s.as_dict() for s in ctx.suggestions])
        self.cfg.reports_dir.mkdir(parents=True, exist_ok=True)
        if self.cfg.keep_markdown:
            (self.cfg.reports_dir / f"{key}_newsletter-report.md").write_text(
                report.biweekly(ctx, prev_issue, prev_rows, self.cfg, self.now, analysis, err), encoding="utf-8")
        out = pdfreport.issue(self.cfg.reports_dir / f"{key}_newsletter-report.pdf", ctx, analysis, err,
                              figs, changes, self.now)
        self._publish(out, f"Börsen-Agent Bericht Ausgabe {key}")
        return out

    def daily(self, precomputed=None) -> Path:
        ctx = self.context(with_candidates=False, newsletter_scope=False)
        day = self.today.isoformat()
        self.store.save_snapshot(day, ctx.portfolio["total_eur"],
                                 {"rows": ctx.portfolio["rows"], "equity_share": ctx.portfolio["equity_share"]})
        analysis, err = precomputed or self._analyse(ctx, "daily")
        figs = self._charts(ctx, day, False)
        ctx.notes = list(dict.fromkeys(ctx.notes + self.notes))
        self.store.save_suggestions(day, "daily", [s.as_dict() for s in ctx.suggestions])
        self.cfg.reports_dir.mkdir(parents=True, exist_ok=True)
        if self.cfg.keep_markdown:
            (self.cfg.reports_dir / f"{day}_daily-brief.md").write_text(
                report.daily(ctx, self.cfg, self.now, analysis, err), encoding="utf-8")
        out = pdfreport.daily(self.cfg.reports_dir / f"{day}_daily-brief.pdf", ctx, analysis, err, figs, self.now)
        self._publish(out, f"Börsen-Agent Tagesbrief {day}")
        return out

    # ------------------------------------------------------------------ hand-off mode
    def prepare(self, out: Path, issue_key: str | None = None) -> dict:
        """Ingest new issues and write, per report, the fact pack, prompt and schema for an outside analyst
        (e.g. the Claude session that runs this command). Returns the task list."""
        from .analyst import SCHEMA, METHODS_PATH, SYSTEM_PROMPT
        out.mkdir(parents=True, exist_ok=True)
        for old in out.glob("*"):
            old.unlink()
        new = self.ingest() if self.paperless is not None else []
        (out / "prompt.md").write_text(SYSTEM_PROMPT + (METHODS_PATH.read_text(encoding="utf-8")
                                                        if METHODS_PATH.exists() else ""), encoding="utf-8")
        (out / "schema.json").write_text(json.dumps(SCHEMA, indent=1), encoding="utf-8")
        prev = self.store.last_analysis()
        tasks = []
        if new or issue_key:
            key = issue_key or new[-1]
            ctx = self.context(key, with_candidates=True, newsletter_scope=True)
            prev_issue = self.store.latest_issue(before=ctx.issue["issue_date"])
            changes = (report.dispo_changes(self.store.rows("dispo", prev_issue["issue_key"]), ctx.issue_rows["dispo"])
                       if prev_issue else [])
            facts = build_facts(ctx, "issue", self.now, changes, prev)
            pdf = self._issue_pdf_path(key)
            txt = None
            if pdf:
                try:
                    from .newsletter import page_count, page_text
                    n = page_count(pdf)
                    pages = [page_text(pdf, p, layout=p >= 6) for p in range(1, min(n, 8) + 1)]
                    txt = out / f"issue-{key}_newsletter.txt"
                    txt.write_text("\n\f".join(pages), encoding="utf-8")   # pages 1-8: text, dashboard, Dispo-Liste
                except Exception as e:  # noqa: BLE001
                    self.notes.append(f"could not extract the newsletter text: {e}")
            tasks.append({"scope": "issue", "issue": key, "facts": str(out / f"issue-{key}_facts.json"),
                          "newsletter_pdf": str(pdf) if pdf else None,
                          "newsletter_text": str(txt) if txt else None,
                          "output": str(out / f"issue-{key}_analysis.json")})
            Path(tasks[-1]["facts"]).write_text(json.dumps(facts, ensure_ascii=False, indent=1, default=str), "utf-8")
        ctx = self.context(with_candidates=False, newsletter_scope=False)
        facts = build_facts(ctx, "daily", self.now, None, prev)
        tasks.append({"scope": "daily", "facts": str(out / "daily_facts.json"), "newsletter_pdf": None,
                      "output": str(out / "daily_analysis.json")})
        Path(tasks[-1]["facts"]).write_text(json.dumps(facts, ensure_ascii=False, indent=1, default=str), "utf-8")
        manifest = {"prompt": str(out / "prompt.md"), "schema": str(out / "schema.json"), "tasks": tasks,
                    "notes": list(dict.fromkeys(self.notes + self.market.errors))}
        (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), "utf-8")
        return manifest

    def finish(self, out: Path, model: str = "claude-code-session") -> dict:
        """Validate the analyses written for `prepare`'s tasks, then render and publish the reports.
        A missing or invalid analysis still produces the rule-based report, with the reason."""
        from .analyst import Analysis, validate
        manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
        reports, problems, done = [], [], {}
        for t in manifest["tasks"]:
            facts = json.loads(Path(t["facts"]).read_text(encoding="utf-8"))
            pre = (None, "no analysis file was written")
            outp = Path(t["output"])
            if outp.exists():
                try:
                    data, warn = validate(json.loads(outp.read_text(encoding="utf-8")), facts)
                    missing = [k for k in ("headline", "summary", "market_view", "position_calls") if k not in data]
                    if missing:
                        raise ValueError("missing fields: " + ", ".join(missing))
                    for k in ("newsletter_picks", "new_ideas", "risks", "questions_for_user"):
                        data.setdefault(k, [])
                    data.setdefault("notes_for_next_run", "")
                    a = Analysis(data, model, {}, warn)
                    self.store.save_analysis(self.today.isoformat(), t["scope"], a, self.now.isoformat(timespec="seconds"))
                    pre = (a, None)
                    done[t["scope"]] = data
                except (ValueError, KeyError, TypeError) as e:
                    pre = (None, f"analysis file invalid: {e}")
            if pre[0] is None:
                problems.append(f"{t['scope']}: {pre[1]}")
            path = self.biweekly(t["issue"], precomputed=pre) if t["scope"] == "issue" else self.daily(precomputed=pre)
            reports.append(str(path))
        last = done.get("daily") or done.get("issue") or {}
        return {"reports": reports, "problems": problems, "headline": last.get("headline"),
                "alerts": [f"{c['key']}: {c['action']} ({c['confidence']})" for c in last.get("position_calls", [])
                           if c["action"] in ("SELL", "REDUCE", "WATCH")]}

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
