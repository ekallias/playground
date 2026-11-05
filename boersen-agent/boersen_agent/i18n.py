"""Report language (English default, German optional).

Only what the reader sees is translated: PDF/Markdown text, chart labels, action labels, rule reasons
and signal details, dates and number formats. Internal codes (SELL, HOLD, rule keys, schema enums)
stay English so the database, guardrails and history do not depend on the language.
"""
from __future__ import annotations

import re
from datetime import date, datetime

LANG = "en"
SUPPORTED = ("en", "de")


def set_lang(code: str | None) -> None:
    global LANG
    code = (code or "en").lower()[:2]
    if code not in SUPPORTED:
        raise ValueError(f"unsupported report language {code!r} (en or de)")
    LANG = code


def is_de() -> bool:
    return LANG == "de"


# ------------------------------------------------------------------ UI strings (keyed by English)
DE = {
    # document titles and bands
    "Daily brief": "Tagesbrief",
    "Newsletter report · issue of {date}": "Newsletter-Bericht · Ausgabe vom {date}",
    "issue of {date}": "Ausgabe vom {date}",
    "no issue yet": "noch keine Ausgabe",
    "generated": "erstellt",
    "daily brief": "Tagesbrief",
    "issue report": "Ausgabenbericht",
    "page": "Seite",
    # tiles
    "Portfolio": "Depot",
    "all positions priced": "alle Positionen bewertet",
    "incomplete": "unvollständig",
    "Equity share": "Aktienquote",
    "stocks + funds": "Aktien + Fonds",
    "Alerts": "Warnungen",
    "sell / reduce / watch": "verkaufen / reduzieren / beobachten",
    "positive": "positiv",
    "negative": "negativ",
    "unknown": "unbekannt",
    "strict summer exit": "strikter Sommerausstieg",
    "2 of 3 warn → out": "2 von 3 warnen → raus",
    "S&P · DAX · yield curve": "S&P · DAX · Zinsstruktur",
    "Season": "Saison",
    "winter": "Winter",
    "summer": "Sommer",
    "Nov–Apr: positive": "Nov–Apr: positiv",
    "May–Oct: caution": "Mai–Okt: Vorsicht",
    "Model portfolio": "Musterdepot",
    "stocks · {cash}% cash": "Aktien · {cash}% Cash",
    "Buy": "Kaufen",
    "Sell": "Verkaufen",
    "K in the Dispo-Liste": "K in der Dispo-Liste",
    "V in the Dispo-Liste": "V in der Dispo-Liste",
    "New ideas": "Neue Ideen",
    "from the 700-stock list": "aus der 700er-Liste",
    # sections
    "Your positions": "Ihre Positionen",
    "Allocation": "Aufteilung",
    "Portfolio value": "Depotwert",
    "Distance to the 9-month stop": "Abstand zum 9-Monats-Stopp",
    "The newsletter's latest exit point is a weekly close below the 9-month low.":
        "Spätester Ausstieg laut Börsensignale: ein Wochenschluss unter dem 9-Monats-Tief.",
    "Price vs. 9-month stop (completed weekly closes)": "Kurs vs. 9-Monats-Stopp (abgeschlossene Wochenschlusskurse)",
    "Market regime": "Marktlage",
    "Market regime, re-checked with live data": "Marktlage, mit aktuellen Daten nachgeprüft",
    "Your portfolio against this issue": "Ihr Depot im Lichte dieser Ausgabe",
    "Newsletter recommendations, checked": "Empfehlungen der Ausgabe, geprüft",
    "Newsletter = the issue's buy / hold / sell, with the AI analyst's verdict (confirmed · questionable · "
    "contradicted), or the scripts' live check when there is no AI analysis.":
        "Newsletter = Kauf / Halten / Verkauf der Ausgabe, dazu das Urteil der KI (bestätigt · fraglich · "
        "widerlegt) oder ohne KI-Analyse die Live-Prüfung der Skripte.",
    "All of this issue's recommendations are in your portfolio (see above).":
        "Alle Empfehlungen dieser Ausgabe sind in Ihrem Depot (siehe oben).",
    "New ideas from the 700-stock list": "Neue Ideen aus der 700er-Liste",
    "New candidates from the 700-stock list": "Neue Kandidaten aus der 700er-Liste",
    "Top relative strength, a new 9-month high and a KUV in the normal range, not yet in the Dispo-Liste.":
        "Hohe Relative Stärke, neues 9-Monats-Hoch und KUV im Normalbereich, noch nicht in der Dispo-Liste.",
    "Changes since the previous issue": "Änderungen seit der letzten Ausgabe",
    "first ingested issue": "erste eingelesene Ausgabe",
    "Charts": "Grafiken",
    "Gesamtsystem per issue": "Gesamtsystem je Ausgabe",
    "Zinsstruktur: printed in the newsletter vs. computed": "Zinsstruktur: laut Newsletter vs. berechnet",
    "The rule only uses the sign (warning below 0); the computed series runs about 0.1 higher.":
        "Die Regel nutzt nur das Vorzeichen (Warnung unter 0); die berechnete Reihe liegt etwa 0,1 höher.",
    "Stock list: relative strength vs. KUV": "Aktienliste: Relative Stärke vs. KUV",
    "Buy candidates sit right of the top-200 line and below the KUV line.":
        "Kaufkandidaten liegen rechts der Top-200-Linie und unterhalb der KUV-Linie.",
    "Your allocation": "Ihre Aufteilung",
    "Appendix: rule details per security": "Anhang: Regeldetails je Wertpapier",
    "Data notes": "Datenhinweise",
    "Risks": "Risiken",
    "Questions for you": "Fragen an Sie",
    "No portfolio imported yet.": "Noch kein Depot importiert.",
    # tables
    "Security": "Wertpapier", "AI call": "KI", "Rules": "Regeln", "Newsletter": "Newsletter",
    "Rationale": "Begründung", "Component": "Komponente", "Now": "Jetzt", "Issue": "Ausgabe",
    "Source": "Quelle", "Rule": "Regel", "Detail": "Detail", "Position": "Position", "Type": "Art",
    "Qty": "Stück", "Price": "Kurs", "Value €": "Wert €", "P/L": "G/V",
    "stock": "Aktie", "fund": "Fonds", "bond": "Anleihe", "cash": "Cash",
    "buy": "kaufen", "hold": "halten", "sell": "verkaufen",
    "confirmed": "bestätigt", "questionable": "fraglich", "contradicted": "widerlegt", "unverified": "ungeprüft",
    "AI {verdict}": "KI {verdict}",
    "computed": "berechnet", "newsletter": "Newsletter", "missing": "fehlt",
    "No price for: ": "Kein Kurs für: ",
    "Valued at cost: ": "Zum Einstand bewertet: ",
    "Equity share counts stocks and funds; cash and bonds are cash-like, as in the newsletter.":
        "Die Aktienquote zählt Aktien und Fonds; Cash und Anleihen gelten wie im Newsletter als Cash.",
    "⚠ overrides the 9-month-low sell rule:": "⚠ weicht von der 9-Monats-Tief-Verkaufsregel ab:",
    "⚠ differs from the rule:": "⚠ weicht von der Regel ab:",
    "⚠ = live data differs from the last issue (markets moved since, or the rule is inferred: I/U).":
        "⚠ = aktuelle Daten weichen von der letzten Ausgabe ab (Märkte haben sich bewegt, oder die Regel ist erschlossen: I/U).",
    # AI box
    "AI analysis unavailable: {err}. The tables show the rule engine's results only.":
        "KI-Analyse nicht verfügbar: {err}. Die Tabellen zeigen nur die Ergebnisse der Regeln.",
    "Market view:": "Markteinschätzung:",
    "confidence": "Konfidenz",
    "risk-on": "offensiv", "neutral": "neutral", "cautious": "vorsichtig", "risk-off": "defensiv",
    "rules:": "Regeln:", "symbol": "Symbol",
    "Prices typically delayed ~15 min.": "Kurse meist ~15 Min. verzögert.",
    "Rule results come from the Börsensignale methods as implemented by the scripts; calls and confidence "
    "come from the AI analyst reading those facts. Confidence = strength of evidence, not a probability "
    "of gain. Decision support for your own account, not investment advice.":
        "Die Regelergebnisse stammen aus den Börsensignale-Methoden, wie die Skripte sie umsetzen; Empfehlungen "
        "und Konfidenz stammen von der KI, die diese Fakten liest. Konfidenz = Stärke der Belege, keine "
        "Gewinnwahrscheinlichkeit. Entscheidungshilfe für Ihr eigenes Depot, keine Anlageberatung.",
    # charts
    "Stocks": "Aktien", "Funds / ETFs": "Fonds / ETFs", "Cash-like (cash, bonds)": "Cash-ähnlich (Cash, Anleihen)",
    " {pct}% watch line": " {pct}%-Beobachtungslinie",
    "✕ broke the 9-month low": "✕ 9-Monats-Tief unterschritten", "⚠ near stop": "⚠ nahe am Stopp",
    " stop {v}": " Stopp {v}", "positive votes": "positive Stimmen", "positive\nabove": "positiv\ndarüber",
    "computed {v}": "berechnet {v}", "newsletter {v}": "Newsletter {v}", "warning below 0 ": "Warnung unter 0 ",
    "stock list": "Aktienliste", " top {n} by RS →": " Top {n} nach RS →", "KUV {v} (normal ≤) ": "KUV {v} (normal ≤) ",
    "relative strength (price ÷ 15-month median × 100)": "Relative Stärke (Kurs ÷ 15-Monats-Median × 100)",
    "KUV (price / sales, log)": "KUV (Kurs / Umsatz, log)",
    "newsletter buy (K)": "Newsletter-Kauf (K)", "your holdings": "Ihre Positionen", "new candidates": "neue Kandidaten",
    # markdown copy
    "AI analyst": "KI-Analyse", "Unavailable: {err}. Rule results only.": "Nicht verfügbar: {err}. Nur Regelergebnisse.",
    "Today": "Heute", "Position signals": "Signale je Position", "Action": "Aktion", "Conf.": "Konf.",
    "Stock": "Wertpapier", "9-month stop": "9-Monats-Stopp", "Distance": "Abstand", "Check": "Prüfung",
    "Main reason": "Hauptgrund", "high": "hoch", "medium": "mittel", "low": "niedrig",
    # components
    "Sicherheitssystem (2 of 3)": "Sicherheitssystem (2 von 3)",
}

ACTIONS_DE = {"SELL": "VERKAUFEN", "REDUCE": "REDUZIEREN", "WATCH": "BEOBACHTEN", "REVIEW": "PRÜFEN",
              "HOLD": "HALTEN", "ADD": "AUFSTOCKEN", "BUY": "KAUFEN"}


def _(s: str) -> str:
    return DE.get(s, s) if LANG == "de" else s


def action(code: str | None) -> str:
    if not code:
        return "–"
    return ACTIONS_DE.get(code, code) if LANG == "de" else code


# ------------------------------------------------------------------ generated sentences (reasons, details)
_N = r"-?[\d.,]+"
REASONS_DE: list[tuple[str, str]] = [
    (rf"weekly close broke the 9-month low ({_N}) \(last week\); the newsletter's latest exit point",
     r"Wochenschluss unter dem 9-Monats-Tief \1 (letzte Woche); laut Börsensignale der späteste Ausstieg"),
    (rf"weekly close broke the 9-month low ({_N}) \((\d+) weeks? ago\); the newsletter's latest exit point",
     r"Wochenschluss unter dem 9-Monats-Tief \1 (vor \2 Wochen); laut Börsensignale der späteste Ausstieg"),
    (r"newsletter also says sell \(V\)", "auch der Newsletter sagt Verkaufen (V)"),
    (r"live: quarter-rule low confirms weakness", "live: Tief nach der Quartalsregel bestätigt die Schwäche"),
    (rf"weekly close below all closes of this and last quarter \(({_N})\)",
     r"Wochenschluss unter allen Schlusskursen dieses und des letzten Quartals (\1)"),
    (r"stock list marks a new 9-month low \(T\)", "Aktienliste meldet ein neues 9-Monats-Tief (T)"),
    (rf"price ({_N}) is below the stop ({_N}); sell if this week closes below",
     r"Kurs \1 liegt unter dem Stopp \2; verkaufen, wenn die Woche darunter schließt"),
    (rf"only ({_N})% above the 9-month stop \(({_N})\)", r"nur \1 % über dem 9-Monats-Stopp (\2)"),
    (r"RS rank (\d+) is outside the top (\d+)", r"RS-Rang \1 liegt außerhalb der Top \2"),
    (r"RS rank (\d+) outside the top (\d+)", r"RS-Rang \1 außerhalb der Top \2"),
    (r"RS rank (\d+) within the top (\d+)", r"RS-Rang \1 innerhalb der Top \2"),
    (r"no new 9-month high for (\d+) weeks", r"seit \1 Wochen kein neues 9-Monats-Hoch"),
    (r"bottom third of its list \((\d+)/(\d+)\) in a rising market", r"im unteren Drittel seiner Liste (\1/\2) bei steigendem Markt"),
    (r"newsletter Dispo-Liste: Kaufen \(K\)", "Dispo-Liste des Newsletters: Kaufen (K)"),
    (r"newsletter Dispo-Liste: Verkaufen \(V\)", "Dispo-Liste des Newsletters: Verkaufen (V)"),
    (r"meets the stock-list criteria \(high RS, low KUV, new high\)", "erfüllt die Kriterien der Aktienliste (hohe RS, niedriges KUV, neues Hoch)"),
    (r"RS (\d+), global rank (\d+)", r"RS \1, Gesamtrang \2"),
    (r"new 9-month high \(H\)", "neues 9-Monats-Hoch (H)"),
    (r"new 9-month high this week", "neues 9-Monats-Hoch diese Woche"),
    (rf"KUV ({_N}) at or below sector average \(({_N})\)", r"KUV \1 auf/unter Branchenschnitt (\2)"),
    (rf"KUV ({_N}) above sector average \(({_N})\)", r"KUV \1 über Branchenschnitt (\2)"),
    (rf"KUV ({_N}) at or below normal range \(({_N})\)", r"KUV \1 im Normalbereich (≤ \2)"),
    (rf"KUV ({_N}) above normal range \(({_N})\)", r"KUV \1 über dem Normalbereich (\2)"),
    (rf"KBV ({_N}) within ({_N})", r"KBV \1 innerhalb \2"),
    (rf"KBV ({_N}) above ({_N})", r"KBV \1 über \2"),
    (r"live data: no stop or quarter-rule breach", "Live-Daten: kein Stopp- oder Quartalsregel-Bruch"),
    (r"live RS (\d+) \(newsletter list: (\d+)\)", r"Live-RS \1 (Newsletter-Liste: \2)"),
    (r"live RS (\d+)", r"Live-RS \1"),
    (r"no sell or review rule triggered", "keine Verkaufs- oder Prüfregel ausgelöst"),
    (r"newsletter: Halten \(H\)", "Newsletter: Halten (H)"),
    (r"no live price data: rules checked on newsletter data only", "keine Live-Kurse: Regeln nur mit Newsletter-Daten geprüft"),
    (r"symbol (\S+) found by automatic lookup; please verify", r"Symbol \1 automatisch gefunden; bitte prüfen"),
    (r"name matched to the stock list approximately \((.+)\)", r"Name nur ungefähr der Aktienliste zugeordnet (\1)"),
    (r"newsletter data is (\d+) days old", r"Newsletter-Daten sind \1 Tage alt"),
    (r"Gesamtsystem negative: newsletter advises caution", "Gesamtsystem negativ: der Newsletter rät zur Vorsicht"),
    (r"Sicherheitssystem negative \(strict summer exit\)", "Sicherheitssystem negativ (strikter Sommerausstieg)"),
    (r"Sicherheitssystem negative \(2 of 3 warnings\)", "Sicherheitssystem negativ (2 von 3 Warnungen)"),
    (r"Saisonfaktor negative \(May-Oct\): the newsletter does not add risk",
     "Saisonfaktor negativ (Mai–Okt): der Newsletter erhöht kein Risiko"),
    (r"fund/ETF: only the price rules apply \(no newsletter stock data\)",
     "Fonds/ETF: nur die Kursregeln gelten (keine Newsletter-Aktiendaten)"),
    # signal details
    (rf"smoothed spread ({_N}) \(newsletter: ({_N}|None)\); US\+DE averaging \[I\]",
     r"geglätteter Abstand \1 (Newsletter: \2); Mittel USA+DE [I]"),
    (r"switches only when all three agree \[I\]", "wechselt nur, wenn alle drei übereinstimmen [I]"),
    (r"US 10y; series choice \[U\]", "US 10J; Wahl der Reihe [U]"),
    (r"negative on 6-week high \[V\], positive on 6-week low \[U\]", "negativ bei 6-Wochen-Hoch [V], positiv bei 6-Wochen-Tief [U]"),
    (r"EUR per USD; buy on 15-week high \[I\], sell on 15-week low \[U\]",
     "EUR je USD; Kauf bei 15-Wochen-Hoch [I], Verkauf bei 15-Wochen-Tief [U]"),
    (r"positive end-Oct to end-Apr", "positiv von Ende Okt. bis Ende Apr."),
    (rf"\blast ({_N})", r"zuletzt \1"),
    (r"from newsletter \(no free CRB data; rule needs the index\)", "aus dem Newsletter (keine freien CRB-Daten)"),
    (r"from newsletter \(uses their stock universe\)", "aus dem Newsletter (nutzt dessen Aktienuniversum)"),
    (r"from newsletter \(index data missing\)", "aus dem Newsletter (Indexdaten fehlen)"),
    (r"from newsletter \(rate data missing\)", "aus dem Newsletter (Zinsdaten fehlen)"),
    (r"from newsletter \(data missing or no breakout yet\)", "aus dem Newsletter (Daten fehlen oder noch kein Ausbruch)"),
    (r"from newsletter \(no unanimous signal in history\)", "aus dem Newsletter (noch kein einstimmiges Signal)"),
    (r"re-entry via quarter-rule high \[I\]", "Wiedereinstieg über Quartalsregel-Hoch [I]"),
    (r"\bwarning\b", "Warnung"),
    (rf"(\w+) is below its quarter-rule level intraweek \(({_N}) < ({_N})\); confirmation needs the weekly close",
     r"\1 liegt im Wochenverlauf unter seiner Quartalsregel-Marke (\2 < \3); bestätigt erst der Wochenschluss"),
]
_COMPILED = [(re.compile(p), r) for p, r in REASONS_DE]


def reason(text: str) -> str:
    if LANG != "de" or not text:
        return text
    out = text
    for pat, rep in _COMPILED:
        out = pat.sub(rep, out)
    return out


# ------------------------------------------------------------------ numbers and dates
def num(v, nd: int = 2, suffix: str = "") -> str:
    if v is None:
        return "–"
    s = f"{v:,.{nd}f}"
    if LANG == "de":
        s = s.replace(",", "X").replace(".", ",").replace("X", ".")
        if suffix == "%":
            suffix = " %"
    else:
        s = s.replace(",", " ")
    return s + suffix


_DAYS_DE = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
_MONTHS_DE = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober",
              "November", "Dezember"]


def date_long(d: datetime) -> str:
    if LANG == "de":
        return f"{_DAYS_DE[d.weekday()]}, {d.day:02d}. {_MONTHS_DE[d.month - 1]} {d.year} · {d:%H:%M}"
    return f"{d:%A, %d %B %Y · %H:%M}"


def date_short(iso: str | date) -> str:
    d = date.fromisoformat(str(iso)[:10]) if not isinstance(iso, date) else iso
    return f"{d:%d.%m.%Y}" if LANG == "de" else d.isoformat()


def ai_language_rule() -> str:
    if LANG == "de":
        return ("- Write every text field (headline, summary, reasoning, rationale, override_reason, risks, questions, "
                "notes) in German (Deutsch), in plain, natural language for a private investor. Keep the JSON keys "
                "and enum values (SELL, HOLD, confirmed, …) exactly as the schema defines them.")
    return ("- Write in English. Keep German method names (Gesamtsystem, Sicherheitssystem, 9-Monats-Tief) as they "
            "appear in the newsletter.")
