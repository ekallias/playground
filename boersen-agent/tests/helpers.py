"""Synthetic data builders for tests (no real newsletter content)."""
import math
from datetime import date, timedelta


def daily_series(start: date, days: int, f):
    """Business-day series with value f(i)."""
    out, d, i = [], start, 0
    while len(out) < days:
        if d.weekday() < 5:
            out.append((d.isoformat(), float(f(i))))
            i += 1
        d += timedelta(days=1)
    return out


def trend(base=100.0, slope=0.1, wiggle=0.0):
    return lambda i: base + slope * i + wiggle * math.sin(i / 3)


def write_csv(folder, symbol, series):
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / (symbol.replace("^", "_").replace("/", "_") + ".csv")
    with p.open("w") as fh:
        fh.write("date,close\n")
        for d, c in series:
            fh.write(f"{d},{c}\n")
