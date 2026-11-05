"""Compare the computed Zinsstruktur with the values printed in each ingested issue.

    python tools/calibrate_zins.py data/agent.db

Run the agent once first so the rate series are cached. The rule only uses the sign (< 0 = warning),
so a constant offset is harmless; a sign disagreement would matter.
"""
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from boersen_agent import rules as R  # noqa: E402

db = sqlite3.connect(sys.argv[1] if len(sys.argv) > 1 else "data/agent.db")


def ser(k):
    return list(db.execute("SELECT date, close FROM prices WHERE series=? ORDER BY date", (k,)))


us10, us1, um, eur = (ser(k) for k in ("FRED:DGS10", "FRED:DGS1", "BBK:UMLAUFRENDITE", "ECB:EURIBOR12M"))


def calc(asof, n=22):
    w, _ = R.weekly([x for x in us10 if x[0] < asof])
    rows = [(v - b + c - e) / 2 for (d, v), b, c, e in
            zip(w, R.align_ffill(w, us1), R.align_ffill(w, um), R.align_ffill(w, eur)) if None not in (b, c, e)]
    s = R.sma(rows, n)
    return s[-1] if s else None


errs, signs = [], 0
for d, j in db.execute("SELECT issue_date, dashboard FROM issues ORDER BY issue_date"):
    pub, ours = json.loads(j).get("zinsstruktur_value"), calc(d)
    if pub is None or ours is None:
        print(d, "published", pub, "computed", ours)
        continue
    errs.append(ours - pub)
    signs += (ours < 0) != (pub < 0)
    print(f"{d} published {pub:5.2f} computed {ours:5.2f} diff {ours - pub:+.2f}")
if errs:
    print(f"mean diff {sum(errs) / len(errs):+.3f}; sign disagreements: {signs}")
