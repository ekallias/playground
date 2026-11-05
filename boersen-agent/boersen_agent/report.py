"""Markdown reports: biweekly newsletter report and daily brief."""
from __future__ import annotations

from datetime import datetime

DISCLAIMER = ("_Rule results from the Börsensignale methods (docs/METHODS.md); calls from the AI analyst "
              "reading those facts. Confidence = strength of evidence, not a probability of gain. "
              "Not investment advice; you decide and trade yourself._")

SIGN = {1: "＋", -1: "－", None: "?"}
ACTION_ORDER = {"SELL": 0, "REDUCE": 1, "WATCH": 2, "REVIEW": 3, "BUY": 4, "ADD": 5, "HOLD": 6}


def _fmt(v, nd=2, suffix=""):
    if v is None:
        return "–"
    return f"{v:,.{nd}f}{suffix}".replace(",", " ")


def _score(t):
    return f"{t[0]}:{t[1]}" if t else "?"


def market_section(ms, dash) -> list[str]:
    out = ["## Market regime", ""]
    g = _score(ms.gesamt_score)
    gn = _score(tuple(dash["gesamt"]["score"])) if dash and dash.get("gesamt") else "?"
    safe = ms.safety_core
    strict = " **(strict summer exit active)**" if ms.safety_strict else ""
    out += [
        f"- **Gesamtsystem:** {g} {'positive' if ms.gesamt_positive else 'negative' if ms.gesamt_positive is False else 'unknown'}"
        f" (newsletter: {gn})",
        f"- **Sicherheitssystem:** {SIGN[safe.state]} {safe.detail}{strict}",
        f"- **Saisonfaktor:** {'positive (Nov–Apr)' if ms.season_positive else 'negative (May–Oct)'}",
        "",
        "| Component | Now | Newsletter | Source | Rule | Detail |",
        "|---|---|---|---|---|---|",
    ]
    for c in ms.components.values():
        flag = " ⚠" if c.diverges else ""
        out.append(f"| {c.label} | {SIGN[c.state]}{flag} | {SIGN[c.newsletter]} | {c.source} | {c.marker} | {c.detail} |")
    lv = ms.levels
    lines = []
    for k, label in (("sp500_quarter_low", "S&P 500 quarter-rule warning below"),
                     ("dax_quarter_low", "DAX quarter-rule warning below"),
                     ("sp500_quarter_high", "S&P 500 re-entry above"),
                     ("dax_quarter_high", "DAX re-entry above"),
                     ("nasdaq_sell_level", "Nasdaq 26-week-low level")):
        if lv.get(k) is not None:
            lines.append(f"{label} {_fmt(lv[k], 0)}")
    if lines:
        out += ["", "Levels for the next weekly close: " + " · ".join(lines)]
    if any(c.diverges for c in ms.components.values()):
        out += ["", "⚠ = live computation differs from the last newsletter. That is expected when markets moved "
                "since the issue, or where a parameter is inferred (I/U)."]
    for p in ms.provisional:
        out.append(f"- ⏳ {p}")
    for n in ms.notes:
        out.append(f"- {n}")
    return out + [""]


def _live_cell(s):
    lv = s.live or {}
    return (_fmt(lv.get("price")), _fmt(lv.get("stop")),
            _fmt(lv.get("dist_to_stop_pct"), 1, "%") if lv.get("dist_to_stop_pct") is not None else "–")


def suggestion_table(items, with_rec=True) -> list[str]:
    head = "| Action | Conf. | Stock | Price | 9-month stop | Distance |" + (" Newsletter | Check |" if with_rec else "") + " Main reason |"
    sep = "|---|---|---|---|---|---|" + ("---|---|" if with_rec else "") + "---|"
    out = [head, sep]
    for s in sorted(items, key=lambda s: (ACTION_ORDER.get(s.action, 9), -s.confidence)):
        price, stop, dist = _live_cell(s)
        main = s.reasons[0]["text"] if s.reasons else ""
        extra = f" {s.newsletter_rec or '–'} | {s.verification or '–'} |" if with_rec else ""
        out.append(f"| **{s.action}** | {s.confidence} ({s.label}) | {s.name} | {price} | {stop} | {dist} |{extra} {main} |")
    return out


def reasons_block(items) -> list[str]:
    out = []
    for s in sorted(items, key=lambda s: (ACTION_ORDER.get(s.action, 9), -s.confidence)):
        out.append(f"<details><summary>{s.action} {s.name} — confidence {s.confidence}</summary>\n")
        for r in s.reasons:
            pts = f" ({r['pts']:+d})" if r["pts"] else ""
            mk = f" [{r['marker']}]" if r["marker"] else ""
            out.append(f"- {r['text']}{mk}{pts}")
        if s.symbol:
            out.append(f"- symbol: `{s.symbol}`")
        out.append("\n</details>")
    return out


def portfolio_section(port) -> list[str]:
    out = ["## Portfolio", ""]
    if not port["rows"]:
        return out + ["No portfolio imported yet (`python -m boersen_agent portfolio import FILE.csv`).", ""]
    total = port["total_eur"] if port["total_eur"] is not None else port["known_total_eur"]
    out.append(f"Value: **{_fmt(total)} EUR**" + (" (incomplete)" if port["total_eur"] is None else "")
               + (f" · equity share {_fmt(port['equity_share'], 0, '%')}" if port["equity_share"] is not None else ""))
    if port["missing"]:
        out.append(f"No price for: {', '.join(port['missing'])}")
    if port.get("at_cost"):
        out.append(f"Valued at cost (no quote): {', '.join(port['at_cost'])}")
    out.append("Equity share counts stocks and funds; cash and bonds are cash-like (as in the newsletter).")
    out += ["", "| Position | Type | Qty | Price | Value EUR | P/L |", "|---|---|---|---|---|---|"]
    for r in port["rows"]:
        if r["cash"]:
            out.append(f"| {r['name']} | cash | – | – | {_fmt(r['value_eur'])} | – |")
        else:
            nd = 0 if r["quantity"] == int(r["quantity"]) else 2
            out.append(f"| {r['name']} | {r['asset']} | {_fmt(r['quantity'], nd)} | {_fmt(r['price'])} {r['currency'] or ''} | "
                       f"{_fmt(r['value_eur'])} | {_fmt(r['pnl_pct'], 1, '%') if r['pnl_pct'] is not None else '–'} |")
    return out + [""]


def tracks_section(ms, depot) -> list[str]:
    out = ["## Allocation tracks (from the newsletter)", ""]
    if depot and depot.get("stocks_pct") is not None:
        out.append(f"- Model portfolio (discretionary): {depot['stocks_pct']}% stocks / {depot.get('cash_pct', '?')}% cash in the last issue.")
    eff = ms.safety_effective
    out.append(f"- Sicherheitssystem followers: {'0% stocks (exit all)' if eff == -1 else '100% allowed' if eff == 1 else 'unknown'} by rule.")
    out.append("- The quota mapping from the Gesamtsystem is not published; treat it as judgment.")
    return out + [""]


def dispo_changes(old_rows: list[dict], new_rows: list[dict]) -> list[str]:
    old = {d["isin"]: d for d in old_rows}
    new = {d["isin"]: d for d in new_rows}
    added = [f"new: {d['name']} ({d['rec']})" for k, d in new.items() if k not in old]
    recs = [f"{d['name']}: {old[k]['rec']} → {d['rec']}" for k, d in new.items()
            if k in old and old[k]["rec"] != d["rec"]]
    removed = [f"removed: {d['name']}" for k, d in old.items() if k not in new]
    stops = [f"{d['name']}: stop {_fmt(old[k]['stop_9m'])} → {_fmt(d['stop_9m'])}" for k, d in new.items()
             if k in old and old[k]["rec"] == d["rec"] and (old[k]["stop_9m"] or 0) != (d["stop_9m"] or 0)]
    return added + recs + removed + stops


def ai_section(analysis, err) -> list[str]:
    if analysis is None:
        return ["## AI analyst", "", f"_Unavailable: {err}. Rule results only._", ""]
    d = analysis.data
    mv = d["market_view"]
    out = ["## AI analyst", "", f"**{d['headline']}**", "", d["summary"], "",
           f"Market view: {mv['stance']} (confidence {mv['confidence']}) — {mv['reasoning']}", ""]
    for section, title in (("position_calls", "Your positions"), ("newsletter_picks", "Newsletter picks"),
                           ("new_ideas", "New ideas")):
        if d.get(section):
            out += [f"### {title}", "", "| Security | AI call | Rules | Rationale |", "|---|---|---|---|"]
            for c in d[section]:
                flag = " ⚠ overrides rule" if c.get("overrides_stated_rule") else ""
                out.append(f"| {c['key']} | **{c['action']}** {c['confidence']}{flag} | "
                           f"{c.get('rule_action')} {c.get('rule_confidence')} | {c['rationale']} |")
            out.append("")
    for key, title in (("risks", "Risks"), ("questions_for_user", "Questions for you")):
        if d.get(key):
            out += [f"### {title}", ""] + [f"- {x}" for x in d[key]] + [""]
    return out


def daily(ctx, cfg, now: datetime, analysis=None, err=None) -> str:
    ms, iss = ctx.market, ctx.issue
    lines = [f"# Daily brief — {now:%Y-%m-%d %H:%M}", "", DISCLAIMER, ""] + ai_section(analysis, err)
    if iss:
        lines.append(f"Newsletter basis: issue {iss['issue_key']} of {iss['issue_date']}. "
                     f"Prices: {cfg.provider} (usually delayed ~15 min).")
    else:
        lines.append("No newsletter ingested yet: market rules use live data only.")
    lines += [""] + market_section(ms, ctx.dashboard)
    mine = [s for s in ctx.suggestions if s.in_portfolio]
    alerts = [s for s in mine if s.action in ("SELL", "REDUCE", "WATCH")]
    changes = []
    for s in mine:
        p = ctx.previous.get(s.key)
        if p and p["action"] != s.action:
            changes.append(f"- {s.name}: {p['action']} → **{s.action}** ({s.confidence})")
    lines += ["## Today", ""]
    if alerts:
        lines += [f"- **{s.action}** {s.name} ({s.confidence}): {s.reasons[0]['text']}" for s in alerts]
    else:
        lines.append("- No sell or watch signals on your positions.")
    if changes:
        lines += ["", "Changes since the last brief:"] + changes
    lines += [""] + portfolio_section(ctx.portfolio)
    if mine:
        lines += ["## Position signals", ""] + suggestion_table(mine, with_rec=True) + [""] + reasons_block(mine) + [""]
    depot = None
    if iss and iss.get("depot"):
        import json
        depot = json.loads(iss["depot"])
    lines += tracks_section(ms, depot)
    if ctx.notes:
        lines += ["## Data notes", ""] + [f"- {n}" for n in dict.fromkeys(ctx.notes)] + [""]
    return "\n".join(lines)


def biweekly(ctx, prev_issue, prev_dispo, cfg, now: datetime, analysis=None, err=None) -> str:
    import json
    iss, dash, ms = ctx.issue, ctx.dashboard or {}, ctx.market
    depot = json.loads(iss["depot"]) if iss.get("depot") else {}
    warnings = json.loads(iss["warnings"]) if iss.get("warnings") else []
    lines = [f"# Newsletter report — Börsensignale {iss['issue_key']} ({iss['issue_date']})", "",
             f"Generated {now:%Y-%m-%d %H:%M}. {DISCLAIMER}", ""] + ai_section(analysis, err)
    # 1. what the newsletter says
    g = dash.get("gesamt", {})
    lines += ["## 1. What the issue says", "",
              f"- Gesamtsystem {_score(tuple(g['score'])) if g else '?'}; "
              f"Sicherheitssystem {SIGN[dash.get('sicherheit')]}; short-term methods {SIGN[dash.get('short_term')]}",
              f"- Components: " + ", ".join(f"{k} {SIGN[dash.get(k)]}" for k in
                                             ("zinsstruktur", "index_trend", "anleihezinsen", "oel", "crb", "dollar", "saison")),
              f"- Yield-curve value: {dash.get('zinsstruktur_value', '–')}; 9-month method {dash.get('m9')}, "
              f"32-week {dash.get('w32')}, 25-index quarter {dash.get('q25')}",
              f"- Fazit: {dash.get('fazit') or '–'}",
              f"- Model portfolio: {depot.get('stocks_pct', '?')}% stocks, {depot.get('cash_pct', '?')}% cash; "
              f"index level {depot.get('index_level', '–')}",
              f"- Calls in the text: buy {', '.join(r['name'] for r in ctx.issue_rows['calls'] if r['kind'] == 'buy') or '–'}; "
              f"sell {', '.join(r['name'] for r in ctx.issue_rows['calls'] if r['kind'] == 'sell') or '–'}"]
    if warnings:
        lines += ["", "Parser warnings: " + "; ".join(warnings)]
    lines += [""]
    # 2. verification of market signals
    lines += ["## 2. Market signals re-checked with live data", ""] + market_section(ms, dash)[2:]
    # 3. changes vs previous issue
    lines += ["## 3. Changes since the previous issue", ""]
    if prev_issue:
        old = {d["isin"]: d for d in prev_dispo}
        new = {d["isin"]: d for d in ctx.issue_rows["dispo"]}
        ch = ["- " + c for c in dispo_changes(prev_dispo, ctx.issue_rows["dispo"])]
        lines += ch or ["- no changes in the Dispo-Liste"]
    else:
        lines.append("- first ingested issue")
    lines += [""]
    # 4. newsletter recommendations verified
    dispo = [s for s in ctx.suggestions if s.newsletter_rec in ("K", "H", "V")]
    lines += ["## 4. Newsletter recommendations, verified", "",
              "`Check`: confirmed = live data agrees; contradicted = live data disagrees; unverified = no price data.", ""]
    lines += suggestion_table(dispo) + [""]
    # 5. new candidates
    cands = [s for s in ctx.suggestions if s.newsletter_rec is None and not s.in_portfolio]
    lines += ["## 5. New candidates from the 700-stock list", "",
              "Top RS, new 9-month high (H), KUV in the normal range (or KBV ≤ 4 for financials), not in the Dispo-Liste.", ""]
    lines += (suggestion_table(cands, with_rec=False) if cands else ["None met all criteria."]) + [""]
    # 6. portfolio
    mine = [s for s in ctx.suggestions if s.in_portfolio]
    lines += portfolio_section(ctx.portfolio)
    if mine:
        lines += ["### Your positions against this issue", ""] + suggestion_table(mine) + [""]
    lines += tracks_section(ms, depot)
    lines += ["## 7. Reasons per suggestion", ""] + reasons_block(ctx.suggestions) + [""]
    lines += ["## 8. Method caveats", "",
              "- Rules marked I are inferred from the issues; U parameters are best guesses (see config).",
              "- CRB and the short-term methods come from the newsletter only (no free data / proprietary universe).",
              "- The model-portfolio quota is discretionary in the newsletter and not reproduced as a rule.", ""]
    if ctx.notes:
        lines += ["## Data notes", ""] + [f"- {n}" for n in dict.fromkeys(ctx.notes)] + [""]
    return "\n".join(lines)
