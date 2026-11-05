"""Run the parser over a folder of newsletter PDFs and print a summary per issue.

Use this locally after a newsletter format change. It prints only parsed facts, never text.
    python tools/validate_parser.py /path/to/pdfs
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from boersen_agent.newsletter import parse_pdf  # noqa: E402

S = {1: "+", -1: "-", None: "?"}
seen = set()
bad = 0
for pdf in sorted(Path(sys.argv[1]).glob("*.pdf")):
    iss = parse_pdf(pdf)
    dup = " (dup)" if iss.key in seen else ""
    seen.add(iss.key)
    db = iss.dashboard
    comps = "".join(S[db.get(k)] for k in ("zinsstruktur", "index_trend", "anleihezinsen", "oel", "crb", "dollar", "saison"))
    g = db.get("gesamt", {}).get("score")
    print(f"{iss.key} {iss.date}{dup:6} G={g} comps={comps} safe={S[db.get('sicherheit')]} "
          f"short={S[db.get('short_term')]} z={db.get('zinsstruktur_value')} dispo={len(iss.dispo)} "
          f"uni={len(iss.universe)} sect={len(iss.sectors)} depot={iss.depot} buy={iss.calls['buy']} sell={iss.calls['sell']}")
    for w in iss.warnings:
        bad += 1
        print("   WARN", w)
print("warnings:", bad)
