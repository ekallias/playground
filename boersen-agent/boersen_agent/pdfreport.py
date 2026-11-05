"""PDF reports (reportlab): daily brief and biweekly issue report."""
from __future__ import annotations

import os
import re
from pathlib import Path
from xml.sax.saxutils import escape

import matplotlib
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (Image, KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table,
                                TableStyle)

_FONTS = os.path.join(matplotlib.get_data_path(), "fonts", "ttf")
pdfmetrics.registerFont(TTFont("DejaVu", os.path.join(_FONTS, "DejaVuSans.ttf")))
pdfmetrics.registerFont(TTFont("DejaVu-Bold", os.path.join(_FONTS, "DejaVuSans-Bold.ttf")))

INK, INK2, MUTED = colors.HexColor("#0b0b0b"), colors.HexColor("#52514e"), colors.HexColor("#898781")
HAIR, HEAD, BOX = colors.HexColor("#e1e0d9"), colors.HexColor("#f0efec"), colors.HexColor("#eef4fc")
OVERRIDE, CRIT = colors.HexColor("#fbe9e9"), colors.HexColor("#d03b3b")
W = A4[0] - 30 * mm

S = {
    "title": ParagraphStyle("t", fontName="DejaVu-Bold", fontSize=16, leading=20, textColor=INK, spaceAfter=2),
    "sub": ParagraphStyle("s", fontName="DejaVu", fontSize=8.5, leading=11, textColor=MUTED, spaceAfter=8),
    "h1": ParagraphStyle("h1", fontName="DejaVu-Bold", fontSize=11.5, leading=15, textColor=INK, spaceBefore=10, spaceAfter=4),
    "h2": ParagraphStyle("h2", fontName="DejaVu-Bold", fontSize=9.5, leading=12, textColor=INK, spaceBefore=6, spaceAfter=2),
    "body": ParagraphStyle("b", fontName="DejaVu", fontSize=9, leading=12.5, textColor=INK, alignment=TA_LEFT),
    "lead": ParagraphStyle("l", fontName="DejaVu-Bold", fontSize=10.5, leading=14, textColor=INK, spaceAfter=4),
    "small": ParagraphStyle("sm", fontName="DejaVu", fontSize=7.5, leading=10, textColor=INK2),
    "cell": ParagraphStyle("c", fontName="DejaVu", fontSize=7.5, leading=9.5, textColor=INK),
    "cellb": ParagraphStyle("cb", fontName="DejaVu-Bold", fontSize=7.5, leading=9.5, textColor=INK),
    "warn": ParagraphStyle("w", fontName="DejaVu-Bold", fontSize=8.5, leading=11, textColor=CRIT),
}
DISCLAIMER = ("Rule results come from the Börsensignale methods as implemented by the scripts; calls and confidence "
              "come from the AI analyst reading those facts. Confidence = strength of evidence, not a probability "
              "of gain. Decision support for your own account, not investment advice.")
SIGN = {1: "+", -1: "−", None: "?"}
STANCE = {"risk_on": "risk-on", "neutral": "neutral", "cautious": "cautious", "risk_off": "risk-off"}


_MARKUP = re.compile(r"</?(b|i|br|font)\b[^>]*>")


def P(text, style="body"):
    """Paragraph. Strings containing reportlab markup (<b>, <br/>, <font>) are passed through, so any
    dynamic text inside them must already be escaped; everything else is escaped here."""
    t = str(text)
    return Paragraph(t if _MARKUP.search(t) else escape(t), S[style])


def _fmt(v, nd=2, suffix=""):
    return "–" if v is None else f"{v:,.{nd}f}{suffix}".replace(",", " ")


def table(rows, widths, header=True, shade: list[int] | None = None):
    data = [[c if not isinstance(c, str) else Paragraph(escape(c), S["cellb" if header and i == 0 else "cell"])
             for c in r] for i, r in enumerate(rows)]
    t = Table(data, colWidths=widths, repeatRows=1 if header else 0)
    st = [("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEBELOW", (0, 0), (-1, -1), 0.4, HAIR),
          ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]
    if header:
        st.append(("BACKGROUND", (0, 0), (-1, 0), HEAD))
    for r in shade or []:
        st.append(("BACKGROUND", (0, r), (-1, r), OVERRIDE))
    t.setStyle(TableStyle(st))
    return t


def box(flow, bg=BOX):
    t = Table([[flow]], colWidths=[W])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), bg), ("LEFTPADDING", (0, 0), (-1, -1), 8),
                           ("RIGHTPADDING", (0, 0), (-1, -1), 8), ("TOPPADDING", (0, 0), (-1, -1), 6),
                           ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    return t


def image(path: Path | None, width=W):
    if not path or not Path(path).exists():
        return None
    from reportlab.lib.utils import ImageReader
    iw, ih = ImageReader(str(path)).getSize()
    return Image(str(path), width=width, height=width * ih / iw)


def _footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("DejaVu", 7)
    canvas.setFillColor(MUTED)
    canvas.drawString(15 * mm, 9 * mm, doc.title)
    canvas.drawRightString(A4[0] - 15 * mm, 9 * mm, f"page {doc.page}")
    canvas.restoreState()


def _build(path: Path, title: str, story: list):
    doc = SimpleDocTemplate(str(path), pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm,
                            topMargin=14 * mm, bottomMargin=16 * mm, title=title, author="boersen-agent")
    doc.build([s for s in story if s is not None], onFirstPage=_footer, onLaterPages=_footer)
    return path


# ------------------------------------------------------------------ shared sections
def ai_block(analysis, ai_error) -> list:
    if analysis is None:
        return [box(P(f"AI analysis unavailable: {ai_error or 'disabled in config'}. "
                      "The tables below show the rule engine's results only.", "warn"), OVERRIDE)]
    d = analysis.data
    mv = d["market_view"]
    inner = [P(d["headline"], "lead"), P(d["summary"]), Spacer(1, 4),
             P(f"<b>Market view:</b> {escape(STANCE.get(mv['stance'], mv['stance']))} "
               f"(confidence {mv['confidence']}) — {escape(mv['reasoning'])}")]
    return [box(inner)]


def calls_table(suggestions, calls: dict, include_rec=True) -> list:
    head = ["Security", "AI call", "Rules", "Rationale"]
    if include_rec:
        head.insert(3, "Newsl.")
    rows, shade = [head], []
    order = {"SELL": 0, "REDUCE": 1, "WATCH": 2, "REVIEW": 3, "BUY": 4, "ADD": 5, "HOLD": 6}

    def sort_key(s):
        c = calls.get(s.key)
        return (order.get(c["action"] if c else s.action, 9), -(c["confidence"] if c else s.confidence))

    for s in sorted(suggestions, key=sort_key):
        c = calls.get(s.key)
        rule = f"{s.action} {s.confidence}"
        if c:
            ai = f"<b>{c['action']}</b> {c['confidence']}"
            why = escape(c["rationale"])
            if c.get("overrides_9m_sell"):
                why = f"<font color='#d03b3b'><b>⚠ overrides the 9-month-low sell rule:</b></font> " \
                      f"{escape(c.get('override_reason') or '')}<br/>" + why
                shade.append(len(rows))
            elif c.get("overrides_stated_rule"):
                why = f"<font color='#d03b3b'><b>⚠ differs from the rule:</b></font> " \
                      f"{escape(c.get('override_reason') or '')}<br/>" + why
                shade.append(len(rows))
        else:
            ai, why = "–", escape(s.reasons[0]["text"] if s.reasons else "")
        row = [s.name, Paragraph(ai, S["cell"]), rule, Paragraph(why, S["cell"])]
        if include_rec:
            row.insert(3, f"{s.newsletter_rec or '–'} {('· ' + s.verification) if s.verification else ''}")
        rows.append(row)
    widths = [36 * mm, 18 * mm, 18 * mm, 27 * mm, W - 99 * mm] if include_rec else [42 * mm, 18 * mm, 18 * mm, W - 78 * mm]
    return [table(rows, widths, shade=shade)]


def market_table(ms, dash) -> list:
    g = f"{ms.gesamt_score[0]}:{ms.gesamt_score[1]}" if ms.gesamt_score else "?"
    gn = ":".join(map(str, dash["gesamt"]["score"])) if dash and dash.get("gesamt") else "?"
    out = [P(f"<b>Gesamtsystem</b> {g} now (newsletter {gn}) · <b>Sicherheitssystem</b> "
             f"{SIGN[ms.safety_core.state]}{' (strict summer exit active)' if ms.safety_strict else ''} · "
             f"<b>Saisonfaktor</b> {'positive (Nov–Apr)' if ms.season_positive else 'negative (May–Oct)'}"), Spacer(1, 3)]
    rows = [["Component", "Now", "Newsl.", "Source", "Rule", "Detail"]]
    for c in ms.components.values():
        rows.append([c.label, SIGN[c.state] + (" ⚠" if c.diverges else ""), SIGN[c.newsletter], c.source, c.marker, c.detail])
    out.append(table(rows, [34 * mm, 12 * mm, 16 * mm, 20 * mm, 11 * mm, W - 93 * mm]))
    if any(c.diverges for c in ms.components.values()):
        out.append(P("⚠ = live data differs from the last issue (markets moved since, or the rule is inferred: I/U).", "small"))
    for p in ms.provisional:
        out.append(P("⏳ " + p, "small"))
    return out


def portfolio_table(port) -> list:
    rows = [["Position", "Type", "Qty", "Price", "Value €", "P/L"]]
    for r in port["rows"]:
        if r.get("cash"):
            rows.append([r["name"], "cash", "–", "–", _fmt(r["value_eur"]), "–"])
        else:
            q = r["quantity"]
            rows.append([r["name"], r["asset"], _fmt(q, 0 if q == int(q) else 2),
                         f"{_fmt(r['price'])} {r['currency'] or ''}", _fmt(r["value_eur"]),
                         _fmt(r["pnl_pct"], 1, "%") if r["pnl_pct"] is not None else "–"])
    total = port["total_eur"] if port["total_eur"] is not None else port["known_total_eur"]
    out = [P(f"Value <b>{_fmt(total)} €</b>{' (incomplete)' if port['total_eur'] is None else ''}"
             + (f" · equity share {_fmt(port['equity_share'], 0, '%')}" if port["equity_share"] is not None else "")), Spacer(1, 3),
           table(rows, [62 * mm, 14 * mm, 18 * mm, 28 * mm, 26 * mm, W - 148 * mm])]
    notes = []
    if port["missing"]:
        notes.append("No price for: " + ", ".join(port["missing"]))
    if port.get("at_cost"):
        notes.append("Valued at cost: " + ", ".join(port["at_cost"]))
    return out + [P(n, "small") for n in notes]


def lists_block(analysis) -> list:
    if analysis is None:
        return []
    d, out = analysis.data, []
    if d["risks"]:
        out += [P("Risks", "h2")] + [P("• " + r) for r in d["risks"]]
    if d["questions_for_user"]:
        out += [P("Questions for you", "h2")] + [P("• " + q) for q in d["questions_for_user"]]
    return out


def appendix(suggestions, notes, analysis) -> list:
    out = [PageBreak(), P("Appendix: rule details per security", "h1")]
    for s in suggestions:
        lines = [f"<b>{escape(s.name)}</b> — rules: {s.action} {s.confidence}" + (f" · symbol {escape(s.symbol)}" if s.symbol else "")]
        for r in s.reasons:
            lines.append("&nbsp;&nbsp;• " + escape(r["text"]) + (f" [{r['marker']}]" if r["marker"] else "")
                         + (f" ({r['pts']:+d})" if r["pts"] else ""))
        out.append(KeepTogether([P("<br/>".join(lines), "small"), Spacer(1, 3)]))
    if notes:
        out += [P("Data notes", "h2")] + [P("• " + n, "small") for n in dict.fromkeys(notes)]
    if analysis is not None:
        u = analysis.usage
        out.append(P(f"AI: {escape(analysis.model)} · tokens in {u.get('input_tokens')} (cached {u.get('cache_read_input_tokens')}) "
                     f"/ out {u.get('output_tokens')}" + ("" if not analysis.warnings else
                     " · guardrail notes: " + escape("; ".join(analysis.warnings))), "small"))
    return out


# ------------------------------------------------------------------ documents
def daily(path: Path, ctx, analysis, ai_error, charts: dict, now) -> Path:
    iss = ctx.issue
    calls = analysis.calls_by_key() if analysis else {}
    mine = [s for s in ctx.suggestions if s.in_portfolio]
    story = [P(f"Daily brief · {now:%A %d %B %Y, %H:%M}", "title"),
             P((f"Newsletter basis: issue {iss['issue_key']} of {iss['issue_date']}. " if iss else "No newsletter ingested yet. ")
               + "Prices typically delayed ~15 min. " + DISCLAIMER, "sub")]
    story += ai_block(analysis, ai_error)
    story += [P("Your positions", "h1")] + (calls_table(mine, calls) if mine else [P("No portfolio imported yet.")])
    story += lists_block(analysis)
    story += [KeepTogether([P("Portfolio", "h1")] + [x for x in [image(charts.get("allocation"))] if x])] \
        + portfolio_table(ctx.portfolio)
    if charts.get("value_history"):
        story += [KeepTogether([P("Portfolio value", "h2"), image(charts["value_history"])])]
    if charts.get("stop_distance"):
        story += [KeepTogether([P("Distance to the 9-month stop", "h1"),
                                P("The newsletter's latest exit point is a weekly close below the 9-month low.", "small"),
                                image(charts["stop_distance"])])]
    if charts.get("price_vs_stop"):
        story += [KeepTogether([P("Price vs. 9-month stop (weekly closes)", "h2"), image(charts["price_vs_stop"])])]
    story += [P("Market regime", "h1")] + market_table(ctx.market, ctx.dashboard)
    story += appendix(mine, ctx.notes, analysis)
    return _build(path, f"Börsen-Agent daily brief {now:%Y-%m-%d}", story)


def issue(path: Path, ctx, analysis, ai_error, charts: dict, changes: list[str], now) -> Path:
    iss, dash = ctx.issue, ctx.dashboard or {}
    calls = analysis.calls_by_key() if analysis else {}
    picks = [s for s in ctx.suggestions if s.newsletter_rec and not s.in_portfolio]
    cands = [s for s in ctx.suggestions if s.newsletter_rec is None and not s.in_portfolio]
    mine = [s for s in ctx.suggestions if s.in_portfolio]
    story = [P(f"Börsensignale {iss['issue_key']} · newsletter report", "title"),
             P(f"Issue of {iss['issue_date']} · generated {now:%Y-%m-%d %H:%M}. " + DISCLAIMER, "sub")]
    story += ai_block(analysis, ai_error)
    story += [P("Your portfolio against this issue", "h1")] + (calls_table(mine, calls) if mine else [P("No portfolio imported yet.")])
    story += [P("Newsletter recommendations, checked", "h1"),
              P("Newsl. = the newsletter's K/H/V and the scripts' live check (confirmed / contradicted / unverified).", "small")]
    story += calls_table(picks, calls) if picks else [P("All of this issue's recommendations are in your portfolio (see above).")]
    if analysis and analysis.data["new_ideas"]:
        ideas = {c["key"]: c for c in analysis.data["new_ideas"]}
        story += [P("New ideas from the 700-stock list", "h1")] + calls_table([s for s in cands if s.key in ideas], ideas, include_rec=False)
    elif cands:
        story += [P("New candidates from the 700-stock list", "h1")] + calls_table(cands, {}, include_rec=False)
    story += lists_block(analysis)
    story += [P("Changes since the previous issue", "h1")] + [P("• " + c, "small") for c in (changes or ["first ingested issue"])]
    story += [P("Charts", "h1")]
    for key, title in (("gesamt_history", "Gesamtsystem per issue"), ("zins_history", "Zinsstruktur: newsletter vs. computed"),
                       ("rs_kuv", "Stock list: relative strength vs. KUV"), ("allocation", "Your allocation")):
        if charts.get(key):
            story += [KeepTogether([P(title, "h2"), image(charts[key])])]
    story += [P("Market regime (re-checked with live data)", "h1")] + market_table(ctx.market, dash)
    story += [P("Portfolio", "h1")] + portfolio_table(ctx.portfolio)
    story += appendix(mine + picks + cands, ctx.notes, analysis)
    return _build(path, f"Börsen-Agent issue report {iss['issue_key']}", story)
