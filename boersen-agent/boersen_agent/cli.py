"""Command line: python -m boersen_agent [--config config.toml] COMMAND ..."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from . import config as C
from .agent import Agent
from .marketdata import Market, make_provider
from .paperless import PaperlessClient
from .portfolio import holdings, import_csv, record_trade
from .store import Store


def build(args):
    cfg = C.load(args.config)
    store = Store(cfg.db_path)
    market = Market(store, make_provider(cfg), cfg.history_years)
    pl = None
    if cfg.paperless_url and cfg.paperless_token:
        pl = PaperlessClient(cfg.paperless_url, cfg.paperless_token)
    return cfg, store, Agent(cfg, store, market, pl, datetime.now())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="boersen_agent", description=__doc__)
    ap.add_argument("--config", default="config.toml" if Path("config.toml").exists() else None,
                    help="config file (default: ./config.toml if present)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("run", help="daily job: ingest new newsletters (+ report), then the daily brief")
    p.add_argument("--json", action="store_true", help="print a JSON summary (for n8n and other automation)")
    sub.add_parser("daily", help="write the daily brief only")
    sub.add_parser("ingest", help="pull new newsletters from Paperless; report on the newest new one")
    p = sub.add_parser("ingest-file", help="parse a local newsletter PDF (no Paperless)")
    p.add_argument("pdf")
    p = sub.add_parser("report", help="(re)write the newsletter report for an issue")
    p.add_argument("issue", nargs="?", help="issue key like 2026-22 (default: latest)")

    pp = sub.add_parser("portfolio", help="portfolio commands")
    ps = pp.add_subparsers(dest="pcmd", required=True)
    p = ps.add_parser("import", help="initial import from CSV")
    p.add_argument("csv")
    p.add_argument("--replace", action="store_true", help="drop all recorded transactions first")
    for kind in ("buy", "sell"):
        p = ps.add_parser(kind, help=f"record a {kind}")
        p.add_argument("--isin")
        p.add_argument("--symbol")
        p.add_argument("--name")
        p.add_argument("--qty", type=float, required=True)
        p.add_argument("--price", type=float, required=True)
        p.add_argument("--currency", default="EUR")
        p.add_argument("--fee", type=float, default=0.0)
        p.add_argument("--date", help="YYYY-MM-DD (default today)")
    ps.add_parser("show", help="list holdings")

    sp = sub.add_parser("symbols", help="ISIN/name -> market symbol mapping")
    ss = sp.add_subparsers(dest="scmd", required=True)
    p = ss.add_parser("set", help="set or confirm a mapping")
    p.add_argument("isin")
    p.add_argument("symbol")
    p.add_argument("--name")
    ss.add_parser("list", help="show mappings, unverified first")

    args = ap.parse_args(argv)
    cfg, store, agent = build(args)
    try:
        if args.cmd == "run":
            paths = agent.run()
            if args.json:
                last = store.last_analysis() or {}
                print(json.dumps({
                    "reports": [str(p) for p in paths],
                    "issue_report": next((str(p) for p in paths if "newsletter-report" in p.name), None),
                    "daily_brief": next((str(p) for p in paths if "daily-brief" in p.name), None),
                    "headline": last.get("headline"),
                    "alerts": [f"{c['key']}: {c['action']} ({c['confidence']})" for c in last.get("position_calls", [])
                               if c["action"] in ("SELL", "REDUCE", "WATCH")],
                    "notes": agent.notes,
                }, ensure_ascii=False))
            else:
                for path in paths:
                    print(path)
        elif args.cmd == "daily":
            print(agent.daily())
        elif args.cmd == "ingest":
            new = agent.ingest()
            print("new issues:", ", ".join(new) or "none")
            if new:
                print(agent.biweekly(new[-1]))
        elif args.cmd == "ingest-file":
            key = agent.ingest_file(Path(args.pdf))
            print(f"ingested {key}" if key else "already known or not parseable")
            for n in agent.notes:
                print("note:", n)
        elif args.cmd == "report":
            print(agent.biweekly(args.issue))
        elif args.cmd == "portfolio":
            if args.pcmd == "import":
                n = import_csv(store, Path(args.csv), agent.today, args.replace)
                print(f"imported {n} rows")
            elif args.pcmd in ("buy", "sell"):
                record_trade(store, args.pcmd, args.qty, args.price, args.currency, agent.today,
                             isin=args.isin, symbol=args.symbol, name=args.name, fee=args.fee, when=args.date)
                print("recorded")
            else:
                for h in holdings(store):
                    print(f"{h.name:<32} {h.quantity:>12.2f} @ {h.avg_price:>10.2f} {h.currency}  "
                          f"{h.symbol or ''} {h.isin or ''}")
        elif args.cmd == "symbols":
            if args.scmd == "set":
                store.set_symbol(args.isin, args.symbol, args.name, None, "user", True)
                print("saved")
            else:
                for r in store.db.execute("SELECT * FROM symbols ORDER BY verified, isin"):
                    print(f"{'ok ' if r['verified'] else '?? '} {r['isin']}  {r['symbol']:<14} {r['name'] or ''} ({r['source']})")
                for r in store.db.execute("SELECT * FROM name_symbols ORDER BY verified, name"):
                    print(f"{'ok ' if r['verified'] else '?? '} {r['name']:<28} {r['symbol']} ({r['source']})")
    except (ValueError, RuntimeError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    finally:
        store.close()
    return 0
