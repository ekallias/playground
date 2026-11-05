"""PDF reports (reportlab): daily brief and biweekly issue report.

Layout: a navy header band, key-figure tiles, then sections with a thin accent rule. Tables are
zebra-striped. Actions get a tinted label in their status colour, always with the action text so
colour never carries meaning alone. Overrides of a stated newsletter rule get a red row and say so.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from xml.sax.saxutils import escape

import matplotlib
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (HRFlowable, Image, KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer,
                                Table, TableStyle)

from .names import nice_name

_FONTS = os.path.join(matplotlib.get_data_path(), "fonts", "ttf")
pdfmetrics.registerFont(TTFont("DejaVu", os.path.join(_FONTS, "DejaVuSans.ttf")))
pdfmetrics.registerFont(TTFont("DejaVu-Bold", os.path.join(_FONTS, "DejaVuSans-Bold.ttf")))

H = colors.HexColor
INK, INK2, MUTED = H("#0b0b0b"), H("#52514e"), H("#898781")
NAVY, NAVY_SUB, ACCENT = H("#0d366b"), H("#cde2fb"), H("#2a78d6")
HAIR, HEAD, ZEBRA, TILE = H("#e1e0d9"), H("#eef2f7"), H("#f9f9f7"), H("#f3f6fb")
BOX, OVERRIDE, CRIT, GOOD = H("#eef4fc"), H("#fbe9e9"), H("#d03b3b"), H("#0ca30c")
W = A4[0] - 30 * mm

# Action labels: (tint, text colour). Status semantics; the label text always names the action.
ACTION_STYLE = {
    "SELL": ("#fbe1e1", "#a12b2b"), "REDUCE": ("#fdeadf", "#9a4a26"), "WATCH": ("#fff4d6", "#7a5600"),
    "REVIEW": ("#f1efe6", "#5c5642"), "HOLD": ("#eef0f2", "#52514e"), "ADD": ("#e2f3e2", "#1f6b1f"),
    "BUY": ("#e2f3e2", "#1f6b1f"),
}

S = {
    "band_title": ParagraphStyle("bt", fontName="DejaVu-Bold", fontSize=17, leading=21, textColor=colors.white),
    "band_sub": ParagraphStyle("bs", fontName="DejaVu", fontSize=8.5, leading=11.5, textColor=NAVY_SUB),
    "band_right": ParagraphStyle("br", fontName="DejaVu-Bold", fontSize=8.5, leading=11, textColor=NAVY_SUB,
                                 alignment=TA_RIGHT),
    "sub": ParagraphStyle("s", fontName="DejaVu", fontSize=7.5, leading=10, textColor=MUTED, spaceBefore=4, spaceAfter=6),
    "h1": ParagraphStyle("h1", fontName="DejaVu-Bold", fontSize=12, leading=15, textColor=NAVY, spaceBefore=12, spaceAfter=1),
    "h2": ParagraphStyle("h2", fontName="DejaVu-Bold", fontSize=9.5, leading=12, textColor=INK, spaceBefore=8, spaceAfter=3),
    "body": ParagraphStyle("b", fontName="DejaVu", fontSize=9, leading=12.5, textColor=INK, alignment=TA_LEFT),
    "lead": ParagraphStyle("l", fontName="DejaVu-Bold", fontSize=11, leading=14.5, textColor=NAVY, spaceAfter=4),
    "small": ParagraphStyle("sm", fontName="DejaVu", fontSize=7.5, leading=10, textColor=INK2),
    "cell": ParagraphStyle("c", fontName="DejaVu", fontSize=7.5, leading=9.5, textColor=INK),
    "cellb": ParagraphStyle("cb", fontName="DejaVu-Bold", fontSize=7.5, leading=9.5, textColor=INK),
    "head": ParagraphStyle("hd", fontName="DejaVu-Bold", fontSize=7, leading=9, textColor=NAVY),
    "tile_label": ParagraphStyle("tl", fontName="DejaVu", fontSize=6.8, leading=8.5, textColor=MUTED),
    "tile_value": ParagraphStyle("tv", fontName="DejaVu-Bold", fontSize=13, leading=16, textColor=INK),
    "tile_note": ParagraphStyle("tn", fontName="DejaVu", fontSize=6.8, leading=8.5, textColor=INK2),
    "warn": ParagraphStyle("w", fontName="DejaVu-Bold", fontSize=8.5, leading=11, textColor=CRIT),
}
DISCLAIMER = ("Rule results come from the Börsensignale methods as implemented by the scripts; calls and confidence "
              "come from the AI analyst reading those facts. Confidence = strength of evidence, not a probability "
              "of gain. Decision support for your own account, not investment advice.")
SIGN = {1: "+", -1: "−", None: "?"}
STANCE = {"risk_on": "risk-on", "neutral": "neutral", "cautious": "cautious", "risk_off": "risk-off"}
_MARKUP = re.compile(r"</?(b|i|br|font)\b[^>]*>")


def P(text, style="body"):
    """Paragraph. Strings with reportlab markup (<b>, <br/>, <font>) pass through, so dynamic text inside
    them must already be escaped; everything else is escaped here."""
    t = str(text)
    return Paragraph(t if _MARKUP.search(t) else escape(t), S[style])


def _fmt(v, nd=2, suffix=""):
    return "–" if v is None else f"{v:,.{nd}f}{suffix}".replace(",", " ")


def h1(text):
    return [P(text, "h1"), HRFlowable(width="100%", thickness=1.2, color=NAVY_SUB, spaceBefore=1, spaceAfter=5)]


def label(action: str | None, conf: int | None = None):
    """Tinted action label with its confidence."""
    if not action:
        return P("–", "cell")
    tint, ink = ACTION_STYLE.get(action, ("#eef0f2", "#52514e"))
    txt = f"<font color='{ink}'><b>{action}</b></font>" + (f" <font color='#52514e'>{conf}</font>" if conf is not None else "")
    t = Table([[Paragraph(txt, S["cell"])]], colWidths=[17 * mm])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), H(tint)), ("LEFTPADDING", (0, 0), (-1, -1), 3),
                           ("RIGHTPADDING", (0, 0), (-1, -1), 2), ("TOPPADDING", (0, 0), (-1, -1), 1.5),
                           ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]))
    return t


def table(rows, widths, header=True, shade: list[int] | None = None):
    data = [[c if not isinstance(c, str) else Paragraph(escape(c), S["head" if header and i == 0 else "cell"])
             for c in r] for i, r in enumerate(rows)]
    t = Table(data, colWidths=widths, repeatRows=1 if header else 0)
    st = [("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LINEBELOW", (0, 0), (-1, -1), 0.3, HAIR),
          ("TOPPADDING", (0, 0), (-1, -1), 3.2), ("BOTTOMPADDING", (0, 0), (-1, -1), 3.2),
          ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4)]
    if header:
        st += [("BACKGROUND", (0, 0), (-1, 0), HEAD), ("LINEBELOW", (0, 0), (-1, 0), 0.8, NAVY_SUB)]
    for r in range(1 if header else 0, len(rows)):
        if r % 2 == 0:
            st.append(("BACKGROUND", (0, r), (-1, r), ZEBRA))
    for r in shade or []:
        st.append(("BACKGROUND", (0, r), (-1, r), OVERRIDE))
    t.setStyle(TableStyle(st))
    return t


def box(flow, bg=BOX, bar=ACCENT):
    t = Table([[flow]], colWidths=[W])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), bg), ("LINEBEFORE", (0, 0), (0, -1), 3, bar),
                           ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                           ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 8)]))
    return t


def band(title: str, subtitle: str, right: str):
    t = Table([[[P(title, "band_title"), Spacer(1, 2), P(subtitle, "band_sub")], P(right, "band_right")]],
              colWidths=[W * 0.72, W * 0.28])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), NAVY), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                           ("LEFTPADDING", (0, 0), (-1, -1), 12), ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                           ("TOPPADDING", (0, 0), (-1, -1), 11), ("BOTTOMPADDING", (0, 0), (-1, -1), 11)]))
    return t


def tiles(items: list[tuple[str, str, str, str | None]]):
    """Key figures: (label, value, note, value colour)."""
    cells = []
    for lab, val, note, col in items:
        v = f"<font color='{col}'>{escape(val)}</font>" if col else escape(val)
        cells.append([P(lab.upper(), "tile_label"), Paragraph(v, S["tile_value"]), P(note, "tile_note")])
    gap = 3 * mm
    n = len(cells)
    w = (W - gap * (n - 1)) / n
    row, widths = [], []
    for i, c in enumerate(cells):
        row.append(c)
        widths.append(w)
        if i < n - 1:
            row.append("")
            widths.append(gap)
    t = Table([row], colWidths=widths)
    st = [("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 6),
          ("BOTTOMPADDING", (0, 0), (-1, -1), 6), ("LEFTPADDING", (0, 0), (-1, -1), 7)]
    for i in range(0, len(row), 2):
        st += [("BACKGROUND", (i, 0), (i, 0), TILE), ("LINEABOVE", (i, 0), (i, 0), 2, ACCENT)]
    t.setStyle(TableStyle(st))
    return [Spacer(1, 7), t, Spacer(1, 2)]


def image(path: Path | None, width=W):
    if not path or not Path(path).exists():
        return None
    from reportlab.lib.utils import ImageReader
    iw, ih = ImageReader(str(path)).getSize()
    return Image(str(path), width=width, height=width * ih / iw)


def _footer(canvas, doc):
    canvas.saveState()
    canvas.setStrokeColor(HAIR)
    canvas.setLineWidth(0.5)
    canvas.line(15 * mm, 12 * mm, A4[0] - 15 * mm, 12 * mm)
    canvas.setFont("DejaVu-Bold", 7)
    canvas.setFillColor(NAVY)
    canvas.drawString(15 * mm, 8 * mm, "Börsen-Agent")
    canvas.setFont("DejaVu", 7)
    canvas.setFillColor(MUTED)
    canvas.drawString(15 * mm + 19 * mm, 8 * mm, doc.title.replace("Börsen-Agent ", "· "))
    canvas.drawRightString(A4[0] - 15 * mm, 8 * mm, f"page {doc.page}")
    canvas.restoreState()


def _build(path: Path, title: str, story: list):
    doc = SimpleDocTemplate(str(path), pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm,
                            topMargin=12 * mm, bottomMargin=18 * mm, title=title, author="boersen-agent")
    doc.build([s for s in story if s is not None], onFirstPage=_footer, onLaterPages=_footer)
    return path


# ------------------------------------------------------------------ shared sections
def ai_block(analysis, ai_error) -> list:
    if analysis is None:
        return [box(P(f"AI analysis unavailable: {ai_error or 'disabled in config'}. "
                      "The tables show the rule engine's results only.", "warn"), OVERRIDE, CRIT)]
    d = analysis.data
    mv = d["market_view"]
    inner = [P(d["headline"], "lead"), P(d["summary"]), Spacer(1, 5),
             P(f"<b>Market view:</b> {escape(STANCE.get(mv['stance'], mv['stance']))} "
               f"(confidence {mv['confidence']}) — {escape(mv['reasoning'])}", "small")]
    return [box(inner)]


def calls_table(suggestions, calls: dict, include_rec=True) -> list:
    head = ["Security", "AI call", "Rules", "Rationale"]
    if include_rec:
        head.insert(3, "Newsletter")
    rows, shade = [head], []
    order = {"SELL": 0, "REDUCE": 1, "WATCH": 2, "REVIEW": 3, "BUY": 4, "ADD": 5, "HOLD": 6}

    def sort_key(s):
        c = calls.get(s.key)
        return (order.get(c["action"] if c else s.action, 9), -(c["confidence"] if c else s.confidence))

    for s in sorted(suggestions, key=sort_key):
        c = calls.get(s.key)
        if c:
            why = escape(c["rationale"])
            if c.get("overrides_9m_sell"):
                why = (f"<font color='#d03b3b'><b>⚠ overrides the 9-month-low sell rule:</b></font> "
                       f"{escape(c.get('override_reason') or '')}<br/>" + why)
                shade.append(len(rows))
            elif c.get("overrides_stated_rule"):
                why = (f"<font color='#d03b3b'><b>⚠ differs from the rule:</b></font> "
                       f"{escape(c.get('override_reason') or '')}<br/>" + why)
                shade.append(len(rows))
            ai = label(c["action"], c["confidence"])
        else:
            ai, why = P("–", "cell"), escape(s.reasons[0]["text"] if s.reasons else "")
        row = [Paragraph(f"<b>{escape(nice_name(s.name))}</b>", S["cell"]), ai, label(s.action, s.confidence),
               Paragraph(why, S["cell"])]
        if include_rec:
            rec = {"K": "buy", "H": "hold", "V": "sell"}.get(s.newsletter_rec or "", "–")
            row.insert(3, Paragraph(f"{rec}<br/><font color='#898781'>{s.verification or ''}</font>", S["cell"]))
        rows.append(row)
    widths = ([38 * mm, 20 * mm, 20 * mm, 20 * mm, W - 98 * mm] if include_rec
              else [44 * mm, 20 * mm, 20 * mm, W - 84 * mm])
    return [table(rows, widths, shade=shade)]


def market_table(ms, dash) -> list:
    rows = [["Component", "Now", "Issue", "Source", "Rule", "Detail"]]
    for c in ms.components.values():
        now = f"<b>{SIGN[c.state]}</b>" + (" <font color='#d03b3b'>⚠</font>" if c.diverges else "")
        rows.append([c.label, Paragraph(now, S["cell"]), SIGN[c.newsletter], c.source, c.marker, c.detail])
    out = [table(rows, [34 * mm, 12 * mm, 18 * mm, 20 * mm, 10 * mm, W - 94 * mm])]
    if any(c.diverges for c in ms.components.values()):
        out.append(P("⚠ = live data differs from the last issue (markets moved since, or the rule is inferred: I/U).", "small"))
    for p in ms.provisional:
        out.append(P("⏳ " + p, "small"))
    return out


def portfolio_table(port) -> list:
    rows = [["Position", "Type", "Qty", "Price", "Value €", "P/L"]]
    for r in port["rows"]:
        if r.get("cash"):
            rows.append([nice_name(r["name"]), "cash", "–", "–", _fmt(r["value_eur"]), "–"])
            continue
        q = r["quantity"]
        pnl = r["pnl_pct"]
        pnl_cell = Paragraph("–" if pnl is None else
                             f"<font color='{'#006300' if pnl >= 0 else '#a12b2b'}'>{_fmt(pnl, 1, '%')}</font>", S["cell"])
        rows.append([nice_name(r["name"]), r["asset"], _fmt(q, 0 if q == int(q) else 2),
                     f"{_fmt(r['price'])} {r['currency'] or ''}", _fmt(r["value_eur"]), pnl_cell])
    out = [table(rows, [64 * mm, 14 * mm, 20 * mm, 30 * mm, 26 * mm, W - 154 * mm])]
    notes = []
    if port["missing"]:
        notes.append("No price for: " + ", ".join(nice_name(n) for n in port["missing"]))
    if port.get("at_cost"):
        notes.append("Valued at cost: " + ", ".join(nice_name(n) for n in port["at_cost"]))
    notes.append("Equity share counts stocks and funds; cash and bonds are cash-like, as in the newsletter.")
    return out + [P(n, "small") for n in notes]


def lists_block(analysis) -> list:
    if analysis is None:
        return []
    d, out = analysis.data, []
    cols = []
    if d["risks"]:
        cols.append([P("Risks", "h2")] + [P("• " + r, "small") for r in d["risks"]])
    if d["questions_for_user"]:
        cols.append([P("Questions for you", "h2")] + [P("• " + q, "small") for q in d["questions_for_user"]])
    if cols:
        t = Table([cols], colWidths=[W / len(cols)] * len(cols))
        t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                               ("RIGHTPADDING", (0, 0), (-1, -1), 8)]))
        out.append(t)
    return out


def appendix(suggestions, notes, analysis) -> list:
    out = [PageBreak()] + h1("Appendix: rule details per security")
    for s in suggestions:
        lines = [f"<b>{escape(nice_name(s.name))}</b> — rules: {s.action} {s.confidence}"
                 + (f" · symbol {escape(s.symbol)}" if s.symbol else "")]
        for r in s.reasons:
            lines.append("&nbsp;&nbsp;• " + escape(r["text"]) + (f" [{r['marker']}]" if r["marker"] else "")
                         + (f" ({r['pts']:+d})" if r["pts"] else ""))
        out.append(KeepTogether([P("<br/>".join(lines), "small"), Spacer(1, 4)]))
    if notes:
        out += [P("Data notes", "h2")] + [P("• " + n, "small") for n in dict.fromkeys(notes)]
    if analysis is not None:
        u = analysis.usage
        out.append(P(f"AI: {escape(analysis.model)} · tokens in {u.get('input_tokens')} (cached {u.get('cache_read_input_tokens')}) "
                     f"/ out {u.get('output_tokens')}" + ("" if not analysis.warnings else
                     " · guardrail notes: " + escape("; ".join(analysis.warnings))), "small"))
    return out


def _regime_tiles(ms):
    g = ms.gesamt_score
    gtxt = f"{g[0]}:{g[1]}" if g else "?"
    gcol = None if g is None else ("#0a7d0a" if g[0] > g[1] else "#c02f2f")
    safe = ms.safety_effective
    stxt = {1: "positive", -1: "negative"}.get(safe, "unknown")
    scol = {1: "#0a7d0a", -1: "#c02f2f"}.get(safe)
    snote = "strict summer exit" if ms.safety_strict else ("2 of 3 warn → out" if safe == -1 else "S&P · DAX · yield curve")
    return [("Gesamtsystem", gtxt, "positive" if g and g[0] > g[1] else "negative" if g else "", gcol),
            ("Sicherheitssystem", stxt, snote, scol),
            ("Season", "winter" if ms.season_positive else "summer",
             "Nov–Apr: positive" if ms.season_positive else "May–Oct: caution", None)]


def _chart_section(title, path, note=None):
    img = image(path)
    if not img:
        return []
    parts = [P(title, "h2")] + ([P(note, "small"), Spacer(1, 2)] if note else []) + [img]
    return [KeepTogether(parts)]


# ------------------------------------------------------------------ documents
def daily(path: Path, ctx, analysis, ai_error, charts: dict, now) -> Path:
    iss = ctx.issue
    calls = analysis.calls_by_key() if analysis else {}
    mine = [s for s in ctx.suggestions if s.in_portfolio]
    port = ctx.portfolio
    total = port["total_eur"] if port["total_eur"] is not None else port["known_total_eur"]
    acts = [(calls.get(s.key) or {}).get("action", s.action) for s in mine]
    alerts = sum(a in ("SELL", "REDUCE", "WATCH") for a in acts)
    story = [band("Daily brief", f"{now:%A, %d %B %Y · %H:%M}",
                  (f"Börsensignale {iss['issue_key']}<br/>issue of {iss['issue_date']}" if iss else "no issue yet"))]
    story += tiles([("Portfolio", f"{_fmt(total, 0)} €", "incomplete" if port["total_eur"] is None else "all positions priced", None),
                    ("Equity share", _fmt(port["equity_share"], 0, "%"), "stocks + funds", None)]
                   + _regime_tiles(ctx.market)[:2]
                   + [("Alerts", str(alerts), "sell / reduce / watch", "#c02f2f" if alerts else "#0a7d0a")])
    story += ai_block(analysis, ai_error)
    story += h1("Your positions") + (calls_table(mine, calls) if mine else [P("No portfolio imported yet.")])
    story += lists_block(analysis)
    story += h1("Portfolio")
    story += _chart_section("Allocation", charts.get("allocation")) + [Spacer(1, 4)] + portfolio_table(port)
    story += _chart_section("Portfolio value", charts.get("value_history"))
    story += _chart_section("Distance to the 9-month stop", charts.get("stop_distance"),
                            "The newsletter's latest exit point is a weekly close below the 9-month low.")
    story += _chart_section("Price vs. 9-month stop (completed weekly closes)", charts.get("price_vs_stop"))
    story += [KeepTogether(h1("Market regime") + tiles(_regime_tiles(ctx.market)))] + market_table(ctx.market, ctx.dashboard)
    story += [P(DISCLAIMER + " Prices typically delayed ~15 min.", "sub")]
    story += appendix(mine, ctx.notes, analysis)
    return _build(path, f"Börsen-Agent daily brief {now:%Y-%m-%d}", story)


def issue(path: Path, ctx, analysis, ai_error, charts: dict, changes: list[str], now) -> Path:
    iss, dash = ctx.issue, ctx.dashboard or {}
    calls = analysis.calls_by_key() if analysis else {}
    picks = [s for s in ctx.suggestions if s.newsletter_rec and not s.in_portfolio]
    cands = [s for s in ctx.suggestions if s.newsletter_rec is None and not s.in_portfolio]
    mine = [s for s in ctx.suggestions if s.in_portfolio]
    depot = {}
    if iss.get("depot"):
        import json
        depot = json.loads(iss["depot"])
    recs = [d["rec"] for d in ctx.issue_rows["dispo"]]
    g = dash.get("gesamt", {}).get("score")
    story = [band(f"Börsensignale {iss['issue_key']}", f"Newsletter report · issue of {iss['issue_date']}",
                  f"generated<br/>{now:%d.%m.%Y %H:%M}")]
    story += tiles([("Gesamtsystem", f"{g[0]}:{g[1]}" if g else "?", "positive" if g and g[0] > g[1] else "negative",
                     ("#0a7d0a" if g[0] > g[1] else "#c02f2f") if g else None),
                    ("Model portfolio", f"{depot.get('stocks_pct', '?')}%", f"stocks · {depot.get('cash_pct', '?')}% cash", None),
                    ("Buy", str(recs.count("K")), "K in the Dispo-Liste", "#0a7d0a"),
                    ("Sell", str(recs.count("V")), "V in the Dispo-Liste", "#c02f2f" if recs.count("V") else None),
                    ("New ideas", str(len(analysis.data["new_ideas"]) if analysis else len(cands)), "from the 700-stock list", None)])
    story += ai_block(analysis, ai_error)
    story += h1("Your portfolio against this issue") + (calls_table(mine, calls) if mine else [P("No portfolio imported yet.")])
    story += h1("Newsletter recommendations, checked")
    story += [P("Newsletter = the issue's buy / hold / sell and the scripts' live check (confirmed · contradicted · unverified).", "small"),
              Spacer(1, 3)]
    story += calls_table(picks, calls) if picks else [P("All of this issue's recommendations are in your portfolio (see above).")]
    if analysis and analysis.data["new_ideas"]:
        ideas = {c["key"]: c for c in analysis.data["new_ideas"]}
        story += h1("New ideas from the 700-stock list") + calls_table([s for s in cands if s.key in ideas], ideas, include_rec=False)
    elif cands:
        story += h1("New candidates from the 700-stock list")
        story += [P("Top relative strength, a new 9-month high and a KUV in the normal range, not yet in the Dispo-Liste.", "small"),
                  Spacer(1, 3)] + calls_table(cands, {}, include_rec=False)
    story += lists_block(analysis)
    story += h1("Changes since the previous issue")
    ch = changes or ["first ingested issue"]
    half = (len(ch) + 1) // 2
    cols = [[P("• " + c, "small") for c in ch[:half]], [P("• " + c, "small") for c in ch[half:]]]
    t = Table([cols], colWidths=[W / 2, W / 2])
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    story.append(t)
    story += h1("Charts")
    story += _chart_section("Gesamtsystem per issue", charts.get("gesamt_history"))
    story += _chart_section("Zinsstruktur: printed in the newsletter vs. computed", charts.get("zins_history"),
                            "The rule only uses the sign (warning below 0); the computed series runs about 0.1 higher.")
    story += _chart_section("Stock list: relative strength vs. KUV", charts.get("rs_kuv"),
                            "Buy candidates sit right of the top-200 line and below the KUV line.")
    story += _chart_section("Your allocation", charts.get("allocation"))
    story += [KeepTogether(h1("Market regime, re-checked with live data") + tiles(_regime_tiles(ctx.market)))] \
        + market_table(ctx.market, dash)
    story += h1("Portfolio") + portfolio_table(ctx.portfolio)
    story += [P(DISCLAIMER, "sub")]
    story += appendix(mine + picks + cands, ctx.notes, analysis)
    return _build(path, f"Börsen-Agent issue report {iss['issue_key']}", story)
