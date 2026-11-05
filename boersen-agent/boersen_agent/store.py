"""SQLite state: newsletter issues, price cache, symbol map, portfolio transactions, suggestions."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS issues (
    issue_key TEXT PRIMARY KEY,         -- '2026-22'
    issue_date TEXT NOT NULL,           -- ISO date printed in the issue
    doc_id INTEGER,                     -- paperless document id
    ingested_at TEXT NOT NULL,
    dashboard TEXT NOT NULL,            -- JSON
    depot TEXT,                         -- JSON (cash/stock split, index level)
    warnings TEXT NOT NULL              -- JSON list
);
CREATE TABLE IF NOT EXISTS seen_docs (doc_id INTEGER PRIMARY KEY, issue_key TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS dispo (
    issue_key TEXT, isin TEXT, name TEXT, list TEXT, sector TEXT, rec_date TEXT,
    rec_price REAL, price_today REAL, kgv REAL, stop_9m REAL, rec TEXT,
    PRIMARY KEY (issue_key, isin)
);
CREATE TABLE IF NOT EXISTS universe (
    issue_key TEXT, list TEXT, list_rank INTEGER, name TEXT, rs REAL, ht TEXT, kuv REAL, kbv REAL
);
CREATE INDEX IF NOT EXISTS universe_issue ON universe(issue_key);
CREATE TABLE IF NOT EXISTS sectors (issue_key TEXT, name TEXT, rs REAL, kuv REAL, kbv REAL);
CREATE TABLE IF NOT EXISTS calls (issue_key TEXT, kind TEXT, name TEXT);
CREATE TABLE IF NOT EXISTS symbols (
    isin TEXT PRIMARY KEY, name TEXT, symbol TEXT NOT NULL, currency TEXT,
    source TEXT NOT NULL,               -- 'user' | 'lookup'
    verified INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS name_symbols (
    name_key TEXT PRIMARY KEY, name TEXT, symbol TEXT NOT NULL, source TEXT NOT NULL,
    verified INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS prices (
    series TEXT, date TEXT, close REAL, PRIMARY KEY (series, date)
);
CREATE TABLE IF NOT EXISTS fetch_log (series TEXT PRIMARY KEY, fetched_on TEXT);
CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL, kind TEXT NOT NULL,  -- initial | buy | sell | cash
    isin TEXT, symbol TEXT, name TEXT, quantity REAL NOT NULL, price REAL NOT NULL,
    currency TEXT NOT NULL, fee REAL NOT NULL DEFAULT 0, note TEXT,
    asset TEXT NOT NULL DEFAULT 'stock'     -- stock | fund | bond | cash
);
CREATE TABLE IF NOT EXISTS snapshots (date TEXT PRIMARY KEY, total_eur REAL, detail TEXT);
CREATE TABLE IF NOT EXISTS analyses (
    run_date TEXT, scope TEXT, model TEXT, data TEXT, usage TEXT, created_at TEXT,
    PRIMARY KEY (run_date, scope)
);
CREATE TABLE IF NOT EXISTS suggestions (
    run_date TEXT, scope TEXT, key TEXT, name TEXT, action TEXT, confidence INTEGER, detail TEXT,
    PRIMARY KEY (run_date, scope, key)
);
"""


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        cols = {r[1] for r in self.db.execute("PRAGMA table_info(transactions)")}
        if "asset" not in cols:  # databases created before v0.1.1
            self.db.execute("ALTER TABLE transactions ADD COLUMN asset TEXT NOT NULL DEFAULT 'stock'")
            self.db.commit()

    def close(self) -> None:
        self.db.close()

    # ---------------------------------------------------------------- issues
    def has_doc(self, doc_id: int) -> bool:
        return self.db.execute("SELECT 1 FROM seen_docs WHERE doc_id=?", (doc_id,)).fetchone() is not None

    def mark_doc(self, doc_id: int, issue_key: str | None, note: str) -> None:
        self.db.execute("INSERT OR REPLACE INTO seen_docs VALUES (?,?,?)", (doc_id, issue_key, note))
        self.db.commit()

    def has_issue(self, issue_key: str) -> bool:
        return self.db.execute("SELECT 1 FROM issues WHERE issue_key=?", (issue_key,)).fetchone() is not None

    def save_issue(self, issue, doc_id: int | None, now: str) -> None:
        k = issue.key
        with self.db:
            for t in ("issues", "dispo", "universe", "sectors", "calls"):
                self.db.execute(f"DELETE FROM {t} WHERE issue_key=?", (k,))
            self.db.execute(
                "INSERT INTO issues VALUES (?,?,?,?,?,?,?)",
                (k, issue.date, doc_id, now, json.dumps(issue.dashboard, ensure_ascii=False),
                 json.dumps(issue.depot, ensure_ascii=False), json.dumps(issue.warnings, ensure_ascii=False)))
            self.db.executemany(
                "INSERT OR REPLACE INTO dispo VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                [(k, d["isin"], d["name"], d["list"], d["sector"], d["rec_date"], d["rec_price"],
                  d["price_today"], d["kgv"], d["stop_9m"], d["rec"]) for d in issue.dispo])
            self.db.executemany(
                "INSERT INTO universe VALUES (?,?,?,?,?,?,?,?)",
                [(k, u["list"], u["list_rank"], u["name"], u["rs"], u["ht"], u["kuv"], u["kbv"])
                 for u in issue.universe])
            self.db.executemany("INSERT INTO sectors VALUES (?,?,?,?,?)",
                                [(k, s["name"], s["rs"], s["kuv"], s["kbv"]) for s in issue.sectors])
            self.db.executemany("INSERT INTO calls VALUES (?,?,?)",
                                [(k, kind, n) for kind, names in issue.calls.items() for n in names])

    def issue_keys(self) -> list[str]:
        return [r[0] for r in self.db.execute("SELECT issue_key FROM issues ORDER BY issue_date")]

    def latest_issue(self, before: str | None = None):
        q = "SELECT * FROM issues"
        args: tuple = ()
        if before:
            q += " WHERE issue_date < ?"
            args = (before,)
        r = self.db.execute(q + " ORDER BY issue_date DESC LIMIT 1", args).fetchone()
        return dict(r) if r else None

    def rows(self, table: str, issue_key: str) -> list[dict]:
        return [dict(r) for r in self.db.execute(f"SELECT * FROM {table} WHERE issue_key=?", (issue_key,))]

    # ---------------------------------------------------------------- prices
    def save_prices(self, series: str, points: list[tuple[str, float]], fetched_on: str) -> None:
        with self.db:
            self.db.executemany("INSERT OR REPLACE INTO prices VALUES (?,?,?)",
                                [(series, d, c) for d, c in points])
            self.db.execute("INSERT OR REPLACE INTO fetch_log VALUES (?,?)", (series, fetched_on))

    def prices(self, series: str, start: str | None = None) -> list[tuple[str, float]]:
        q, args = "SELECT date, close FROM prices WHERE series=?", [series]
        if start:
            q += " AND date >= ?"
            args.append(start)
        return [(r[0], r[1]) for r in self.db.execute(q + " ORDER BY date", args)]

    def fetched_on(self, series: str) -> str | None:
        r = self.db.execute("SELECT fetched_on FROM fetch_log WHERE series=?", (series,)).fetchone()
        return r[0] if r else None

    # ---------------------------------------------------------------- symbols
    def symbol_for_isin(self, isin: str):
        r = self.db.execute("SELECT * FROM symbols WHERE isin=?", (isin,)).fetchone()
        return dict(r) if r else None

    def set_symbol(self, isin: str, symbol: str, name: str | None, currency: str | None,
                   source: str, verified: bool) -> None:
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO symbols VALUES (?,?,?,?,?,?)",
                            (isin, name, symbol, currency, source, int(verified)))

    def symbol_for_name(self, name_key: str):
        r = self.db.execute("SELECT * FROM name_symbols WHERE name_key=?", (name_key,)).fetchone()
        return dict(r) if r else None

    def set_name_symbol(self, name_key: str, name: str, symbol: str, source: str, verified: bool) -> None:
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO name_symbols VALUES (?,?,?,?,?)",
                            (name_key, name, symbol, source, int(verified)))

    # ---------------------------------------------------------------- portfolio
    def add_transaction(self, **t) -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO transactions (date, kind, isin, symbol, name, quantity, price, currency, fee, note, asset)"
                " VALUES (:date,:kind,:isin,:symbol,:name,:quantity,:price,:currency,:fee,:note,:asset)",
                {"fee": 0.0, "note": None, "isin": None, "symbol": None, "name": None, "asset": "stock", **t})

    def transactions(self) -> list[dict]:
        return [dict(r) for r in self.db.execute("SELECT * FROM transactions ORDER BY date, id")]

    def save_snapshot(self, date: str, total_eur: float | None, detail: dict) -> None:
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO snapshots VALUES (?,?,?)",
                            (date, total_eur, json.dumps(detail, ensure_ascii=False)))

    # ---------------------------------------------------------------- AI analyses
    def save_analysis(self, run_date: str, scope: str, analysis, created_at: str) -> None:
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO analyses VALUES (?,?,?,?,?,?)",
                            (run_date, scope, analysis.model, json.dumps(analysis.data, ensure_ascii=False),
                             json.dumps(analysis.usage), created_at))

    def last_analysis(self, before: str | None = None) -> dict | None:
        q, args = "SELECT data FROM analyses", ()
        if before:
            q, args = q + " WHERE run_date < ?", (before,)
        r = self.db.execute(q + " ORDER BY run_date DESC, created_at DESC LIMIT 1", args).fetchone()
        return json.loads(r[0]) if r else None

    def snapshots(self) -> list[tuple[str, float]]:
        return [(r[0], r[1]) for r in self.db.execute("SELECT date, total_eur FROM snapshots ORDER BY date")]

    # ---------------------------------------------------------------- suggestions
    def save_suggestions(self, run_date: str, scope: str, items: list[dict]) -> None:
        with self.db:
            self.db.execute("DELETE FROM suggestions WHERE run_date=? AND scope=?", (run_date, scope))
            self.db.executemany(
                "INSERT INTO suggestions VALUES (?,?,?,?,?,?,?)",
                [(run_date, scope, s["key"], s["name"], s["action"], s["confidence"],
                  json.dumps(s, ensure_ascii=False, default=str)) for s in items])

    def previous_suggestions(self, scope: str, before: str) -> dict[str, dict]:
        r = self.db.execute("SELECT MAX(run_date) FROM suggestions WHERE scope=? AND run_date < ?",
                            (scope, before)).fetchone()
        if not r or not r[0]:
            return {}
        return {row["key"]: dict(row) for row in self.db.execute(
            "SELECT * FROM suggestions WHERE scope=? AND run_date=?", (scope, r[0]))}
