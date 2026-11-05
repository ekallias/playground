"""Parse a Börsensignale issue (PDF) into structured data.

Needs `pdftotext` / `pdfinfo` (poppler-utils). Parsing functions take plain text so they can be
tested without PDFs. Only facts (names, ISINs, numbers, signal states) are extracted; no article
text is stored.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

COMPONENTS = ("zinsstruktur", "index_trend", "anleihezinsen", "oel", "crb", "dollar", "saison")
OTHER5 = ("anleihezinsen", "oel", "crb", "dollar", "saison")


@dataclass
class Issue:
    number: int
    year: int
    date: str
    dashboard: dict
    dispo: list[dict] = field(default_factory=list)
    depot: dict = field(default_factory=dict)
    universe: list[dict] = field(default_factory=list)
    sectors: list[dict] = field(default_factory=list)
    calls: dict[str, list[str]] = field(default_factory=lambda: {"buy": [], "sell": []})
    warnings: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{self.year}-{self.number:02d}"


# ------------------------------------------------------------------ helpers
def num(s: str | None) -> float | None:
    """German number '1.234,56' -> 1234.56; 'k.A.', 'Neu!', 'Verl.' -> None."""
    if s is None:
        return None
    s = s.strip()
    if not re.fullmatch(r"-?[\d.]*\d(,\d+)?", s):
        return None
    return float(s.replace(".", "").replace(",", "."))


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def name_key(name: str) -> str:
    """Normalised key for matching stock names across sources."""
    s = unicodedata.normalize("NFKD", name.lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"\(.*?\)", " ", s)
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    drop = {"ag", "se", "nv", "sa", "plc", "inc", "corp", "co", "ltd", "adr", "na", "inh", "hold",
            "holding", "holdings", "group", "gs", "the", "kgaa", "spa", "asa", "ab", "oyj", "vz",
            # broker export noise (share class / nominal value)
            "namens", "aktien", "namensaktien", "inhaber", "inhaberaktien", "registered", "shares", "shareso",
            "sh", "rg", "cl", "sf", "dl", "on", "ant", "vink", "vorzugsaktien", "stammaktien"}
    return " ".join(t for t in s.split() if t not in drop and len(t) > 1 and not t.isdigit())


def _verdict(s: str, start_pat: str, end_pat: str | None) -> int | None:
    m = re.search(start_pat, s)
    if not m:
        return None
    end = len(s)
    if end_pat:
        e = re.search(end_pat, s[m.end():])
        if e:
            end = m.end() + e.start()
    v = re.search(r"(Positiv|Negativ)!", s[m.end():end])
    return None if not v else (1 if v.group(1) == "Positiv" else -1)


# ------------------------------------------------------------------ meta
def parse_meta(p1: str) -> tuple[int, int, str]:
    s = norm(p1)
    m = re.search(r"Ausgabe\s+(\d{1,2})\s*/\s*(\d{4})", s)
    if not m:
        raise ValueError("issue number ('Ausgabe NN/YYYY') not found on page 1")
    dm = re.search(r"(\d{2})\.(\d{2})\.(\d{4})", s[m.end():m.end() + 80]) or re.search(r"(\d{2})\.(\d{2})\.(\d{4})", s)
    if not dm:
        raise ValueError("issue date not found on page 1")
    iso = date(int(dm.group(3)), int(dm.group(2)), int(dm.group(1))).isoformat()
    return int(m.group(1)), int(m.group(2)), iso


# ------------------------------------------------------------------ dashboard (page 5)
def parse_dashboard(p5: str) -> tuple[dict, list[str]]:
    s = norm(p5)
    warn: list[str] = []
    db: dict = {}
    g = re.search(r"Gesamtsystem:\s*(Positiv|Negativ)\s*(\d)\s*:\s*(\d)", s)
    if g:
        db["gesamt"] = {"positive": g.group(1) == "Positiv", "score": [int(g.group(2)), int(g.group(3))]}
    else:
        warn.append("Gesamtsystem score not found")
    db["zinsstruktur"] = _verdict(s, r"1\)\s*Zinsstruktur", r"2\)\s*Index-Trend")
    db["index_trend"] = _verdict(s, r"2\)\s*Index-Trend", r"3\)\s*Übrige")
    for key, label in (("anleihezinsen", "Anleihezinsen"), ("oel", "Ölpreis"), ("crb", "CRB-Index"),
                       ("dollar", "US-Dollar-Euro"), ("saison", "Saisonfaktor")):
        m = re.search(re.escape(label) + r"\s+(Positiv|Negativ)!", s)
        db[key] = None if not m else (1 if m.group(1) == "Positiv" else -1)
    u = re.search(r"3\)\s*Übrige Indikatoren\s*(?:positiv|negativ)\s*(\d)\s*:\s*(\d)", s)
    if u:
        db["other5_score"] = [int(u.group(1)), int(u.group(2))]
    zv = re.search(r"1\)\s*Zinsstruktur", s)
    if zv:
        seg = s[zv.end():zv.end() + 900]
        vm = re.search(r"(Positiv|Negativ)!", seg)
        if vm:
            val = re.search(r"(?<![\d,])(-?\d,\d{2})(?![\d%])", seg[vm.end():vm.end() + 260])
            db["zinsstruktur_value"] = num(val.group(1)) if val else None
    st = re.search(r"Kurzfristige Trend-Methoden:\s*(positiv|negativ)", s)
    db["short_term"] = None if not st else (1 if st.group(1) == "positiv" else -1)
    for key, pat in (("m9", r"9-Monats-Hoch-Tief-Methode:\s*(\d+)\s*Hoch zu\s*(\d+)\s*Tief"),
                     ("w32", r"32-Wochen-Indizes-Methode:\s*(\d+)\s*Hoch zu\s*(\d+)\s*Tief"),
                     ("q25", r"Indizes-Quartal-Methode[^:]*:\s*(\d+)\s*Hoch zu\s*(\d+)\s*Tief")):
        m = re.search(pat, s)
        db[key] = [int(m.group(1)), int(m.group(2))] if m else None
    sf = re.search(r"Sicherheitssystem:\s*(positiv|negativ)", s)
    db["sicherheit"] = None if not sf else (1 if sf.group(1) == "positiv" else -1)
    fz = re.search(r"Fazit:\s*(.{5,90}?)(?=\s+[A-ZÄÖÜ][a-zäöü]+\s+[a-zäöü]|\s*www\.|$)", s)
    db["fazit"] = fz.group(1).strip() if fz else None

    missing = [k for k in COMPONENTS + ("sicherheit", "short_term") if db.get(k) is None]
    if missing:
        warn.append("dashboard fields not found: " + ", ".join(missing))
    # Consistency: Gesamtsystem = Zinsstruktur + Index-Trend + majority of the other five [V].
    if "gesamt" in db and db.get("zinsstruktur") and db.get("index_trend") and "other5_score" in db:
        p, n = db["other5_score"]
        votes = [db["zinsstruktur"], db["index_trend"], 1 if p > n else -1]
        if [votes.count(1), votes.count(-1)] != db["gesamt"]["score"]:
            warn.append(f"Gesamtsystem {db['gesamt']['score']} does not match parsed votes {votes}")
        five = [db.get(k) for k in OTHER5]
        if None not in five and [five.count(1), five.count(-1)] != db["other5_score"]:
            warn.append(f"other-five score {db['other5_score']} does not match parsed states {five}")
    return db, warn


# ------------------------------------------------------------------ Dispo-Liste (page 8, layout)
ISIN_RE = r"[A-Z]{2}[A-Z0-9]{9}\d"
ROW_RE = re.compile(rf"^\s*(?P<list>.*?)\s*(?P<isin>{ISIN_RE})\s+(?P<rest>.+)$")
DATE_RE = re.compile(r"\b(\d{2})\.(\d{2})\.(\d{4}|\d{2})\b")


def parse_dispo(page: str) -> tuple[list[dict], dict, list[str]]:
    rows, warn = [], []
    for line in page.splitlines():
        m = ROW_RE.match(line)
        if not m:
            continue
        rest = m.group("rest")
        dm = DATE_RE.search(rest)
        if not dm:
            continue  # fund price lines below the table carry an ISIN but no recommendation
        parts = re.split(r"\s{2,}", rest[:dm.start()].strip())
        name = parts[0].strip()
        sector = parts[-1].strip() if len(parts) > 1 else ""
        tail = rest[dm.end():].split()
        if not tail or tail[-1] not in ("K", "H", "V") or len(tail) < 3:
            warn.append(f"Dispo row not understood: {m.group('isin')} {name}")
            continue
        rec, vals = tail[-1], tail[:-1]
        at, today = vals[0], vals[1]
        kgv = vals[2] if len(vals) >= 4 else None
        stop = vals[-1] if len(vals) >= 3 else None   # certificates have no KGV column
        y = dm.group(3)
        year = int(y) if len(y) == 4 else 2000 + int(y)
        rows.append({
            "isin": m.group("isin"), "name": name, "list": m.group("list").strip(), "sector": sector,
            "rec_date": f"{year:04d}-{dm.group(2)}-{dm.group(1)}",
            "rec_price": num(at), "price_today": num(today), "kgv": num(kgv), "stop_9m": num(stop),
            "rec": rec, "new": today.startswith("Neu"),
        })
    depot: dict = {}
    s = norm(page)
    m = re.search(r"Börsensignale-Index\),?\s*Stand am\s*(\d{2}\.\d{2}\.\d{2,4}):\s*([\d.,]+)", s)
    if m:
        depot["index_level"] = num(m.group(2))
    m = re.search(r"\((\d+)%\),\s*(\d+)\s*Aktien\s*\((\d+)%\)", s)
    if m:
        depot["cash_pct"], depot["stocks_pct"] = int(m.group(1)), int(m.group(3))
    if not rows:
        warn.append("Dispo-Liste: no rows parsed")
    return rows, depot, warn


# ------------------------------------------------------------------ 700-stock list (layout)
ENTRY_RE = re.compile(
    r"(?<!\S)(\d{1,3})\s+(\S.*?)\s+(\d{1,3},\d{2})\s+(?:([HT])\s+)?(k\.A\.|\d+,\d{2})\s+(k\.A\.|\d+,\d{2})(?!\S)")


def parse_universe(pages: list[str]) -> tuple[list[dict], list[dict], list[str]]:
    rows, sectors, warn = [], [], []
    current = None
    in_sectors = False
    for page in pages:
        lines = page.splitlines()
        for i, line in enumerate(lines):
            st = line.strip()
            nxt = next((x.strip() for x in lines[i + 1:] if x.strip()), "")
            if st and not re.search(r"\d", st) and nxt.startswith("Nr.") and len(st) < 60:
                current = st
                in_sectors = st.startswith("Branchen")
                continue
            for m in ENTRY_RE.finditer(line):
                rank, name, rs, ht, kuv, kbv = m.groups()
                if in_sectors:
                    sectors.append({"name": name.strip(), "rs": num(rs), "kuv": num(kuv), "kbv": num(kbv)})
                else:
                    rows.append({"list": current or "?", "list_rank": int(rank), "name": name.strip(),
                                 "rs": num(rs), "ht": ht or "", "kuv": num(kuv), "kbv": num(kbv)})
    if len(rows) < 500:
        warn.append(f"stock list: only {len(rows)} rows parsed (expected ~700)")
    return rows, sectors, warn


# ------------------------------------------------------------------ buy / sell calls (pages 1-4)
STOP_WORDS = {"Wir", "Die", "Der", "Das", "Unser", "Unsere", "Gesamtsystem", "Änderungen", "Probleme",
              "Technische", "Kaufen", "Verkauf", "Verkaufen", "Halten", "Aktie", "Aktien", "Seite", "Inhalt"}


def parse_calls(text: str) -> dict[str, list[str]]:
    s = norm(text)
    out: dict[str, list[str]] = {"buy": [], "sell": []}
    for m in re.finditer(r"\b(Kaufen|Verkauf(?:en)?)\s*:\s*", s):
        kind = "buy" if m.group(1) == "Kaufen" else "sell"
        toks = s[m.end():m.end() + 200].split(" ")
        names, cur = [], []
        for j, t in enumerate(toks):
            nxt = toks[j + 1] if j + 1 < len(toks) else ""
            if re.fullmatch(r"\d+", t) or t.rstrip(",") in STOP_WORDS:
                break
            if not t or not (t[0].isupper() or t[0].isdigit()):
                break
            if nxt and nxt[0].islower() and not t.endswith(","):
                break  # sentence start, e.g. "Wir kaufen"
            cur.append(t.rstrip(","))
            if t.endswith(","):
                names.append(" ".join(cur))
                cur = []
            if len(names) >= 6:
                break
        if cur:
            names.append(" ".join(cur))
        for n in names:
            if len(n) > 1 and n not in out[kind]:
                out[kind].append(n)
    return out


# ------------------------------------------------------------------ PDF driver
def _need(tool: str) -> str:
    p = shutil.which(tool)
    if not p:
        raise RuntimeError(f"{tool} not found; install poppler-utils (apt install poppler-utils)")
    return p


def page_count(pdf: Path) -> int:
    out = subprocess.run([_need("pdfinfo"), str(pdf)], capture_output=True, text=True, check=True).stdout
    m = re.search(r"Pages:\s+(\d+)", out)
    return int(m.group(1)) if m else 0


def page_text(pdf: Path, page: int, layout: bool) -> str:
    args = [_need("pdftotext"), "-f", str(page), "-l", str(page)]
    if layout:
        args.append("-layout")
    return subprocess.run(args + [str(pdf), "-"], capture_output=True, text=True, check=True).stdout


def parse_pdf(pdf: Path) -> Issue:
    n = page_count(pdf)
    plain = [page_text(pdf, p, False) for p in range(1, min(n, 5) + 1)]
    layout = {p: page_text(pdf, p, True) for p in range(6, n + 1)}
    number, year, iso = parse_meta(plain[0])
    dash_page = next((t for t in plain if "Hausse-Baisse" in t), plain[-1])
    dashboard, warnings = parse_dashboard(dash_page)
    dispo_page = next((t for t in layout.values() if "Dispo-Liste" in t), "")
    dispo, depot, w2 = parse_dispo(dispo_page)
    start = next((p for p, t in layout.items() if "Relativer Stärke und KUV" in t), None)
    uni_pages = [t for p, t in layout.items() if start and p >= start]
    end_idx = next((i for i, t in enumerate(uni_pages) if re.search(r"^\s*Branchen\s*$", t, re.M)), None)
    if end_idx is not None:
        uni_pages = uni_pages[:end_idx + 1]
    universe, sectors, w3 = parse_universe(uni_pages)
    calls = parse_calls("\n".join(plain[:4]))
    return Issue(number, year, iso, dashboard, dispo, depot, universe, sectors, calls,
                 warnings + w2 + w3)


def issue_age_days(issue_date: str, today: date | None = None) -> int:
    return ((today or date.today()) - datetime.fromisoformat(issue_date).date()).days
