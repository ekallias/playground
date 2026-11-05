"""Pure rule functions on price series. No I/O.

Series are lists of (iso_date, value) sorted by date. Weekly rules use weekly closes (last
observation of each ISO week), matching the newsletter's "Wochenschluss" convention.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import date, datetime, timedelta

Series = list[tuple[str, float]]


def d(s: str) -> date:
    return date.fromisoformat(s[:10])


def week_complete(week_of: date, asof: datetime) -> bool:
    """A week's close is final once the week is over: any later ISO week, the weekend, or Friday
    after 23:00 local time (after the US close)."""
    if week_of.isocalendar()[:2] != asof.date().isocalendar()[:2]:
        return asof.date() > week_of
    wd = asof.weekday()
    return wd >= 5 or (wd == 4 and asof.hour >= 23)


def weekly(series: Series, asof: datetime | None = None) -> tuple[Series, tuple[str, float] | None]:
    """Weekly closes (last value per ISO week). Returns (completed weeks, provisional current week).
    Without asof, every week counts as complete."""
    out: dict[tuple[int, int], tuple[str, float]] = {}
    for ds, v in series:
        y, w, _ = d(ds).isocalendar()
        out[(y, w)] = (ds, v)
    res = [out[k] for k in sorted(out)]
    if asof is not None and res and not week_complete(d(res[-1][0]), asof):
        return res[:-1], res[-1]
    return res, None


def is_n_week_low(closes: list[float], i: int, n: int) -> bool:
    """Close i is below all of the previous n-1 closes (an n-week low incl. this week)."""
    if i < n - 1:
        return False
    return closes[i] < min(closes[i - n + 1:i])


def is_n_week_high(closes: list[float], i: int, n: int) -> bool:
    if i < n - 1:
        return False
    return closes[i] > max(closes[i - n + 1:i])


def latched(closes: list[float], up_n: int | None, down_n: int | None, up_is_positive: bool = True,
            start: int | None = None) -> list[int | None]:
    """Breakout state machine. A new up_n-week high sets 'up', a new down_n-week low sets 'down';
    otherwise the previous state is kept. Returns +1 (positive) / -1 (negative) / None (no signal yet).
    up_is_positive=False flips the meaning (e.g. rising yields or oil are negative for stocks)."""
    state: int | None = None
    out: list[int | None] = []
    for i in range(len(closes)):
        if up_n and is_n_week_high(closes, i, up_n):
            state = 1 if up_is_positive else -1
        elif down_n and is_n_week_low(closes, i, down_n):
            state = -1 if up_is_positive else 1
        out.append(state)
    return out


def quarter_of(day: date) -> tuple[int, int]:
    return day.year, (day.month - 1) // 3


def prev_quarter(q: tuple[int, int]) -> tuple[int, int]:
    y, n = q
    return (y - 1, 3) if n == 0 else (y, n - 1)


@dataclass
class QuarterState:
    state: list[int | None]      # +1 ok / -1 warning (latched)
    low_level: float | None      # current warning level (min of window) for the next weekly close
    high_level: float | None     # current re-entry level (max of window) [I]
    events: list[str | None]     # 'low' / 'high' / None per week


def quarter_rule(weekly_series: Series) -> QuarterState:
    """Gann Quartalregel [V]: warning when a weekly close is below all weekly closes of the current
    and previous calendar quarter (earlier weeks). Re-entry when above all of them [I]."""
    state: int | None = None
    states, events = [], []
    dates = [d(x[0]) for x in weekly_series]
    closes = [x[1] for x in weekly_series]

    def window(i: int) -> list[float]:
        q = quarter_of(dates[i])
        pq = prev_quarter(q)
        return [closes[j] for j in range(i) if quarter_of(dates[j]) in (q, pq)]

    for i in range(len(closes)):
        w = window(i)
        ev = None
        if w and any(quarter_of(dates[j]) == prev_quarter(quarter_of(dates[i])) for j in range(i)):
            if closes[i] < min(w):
                state, ev = -1, "low"
            elif closes[i] > max(w):
                state, ev = 1, "high"
        states.append(state)
        events.append(ev)
    # Levels that apply to the next weekly close.
    low = high = None
    if closes:
        nxt = len(closes)
        q = quarter_of(dates[-1])
        pq = prev_quarter(q)
        w = [closes[j] for j in range(nxt) if quarter_of(dates[j]) in (q, pq)]
        if w:
            low, high = min(w), max(w)
    return QuarterState(states, low, high, events)


@dataclass
class StopInfo:
    level: float | None          # lowest weekly close of the last `weeks` weeks (the next close must stay above)
    confirmed_breach: bool       # a weekly close broke the 9-month low within the last `memory` weeks
    breach_weeks_ago: int | None # 0 = the last completed week
    breach_level: float | None   # the 9-month low that was broken
    new_high: bool               # last completed weekly close is a new `weeks`-week high
    weeks_since_high: int | None


def nine_month(weekly_closes: list[float], weeks: int = 39, memory: int = 4) -> StopInfo:
    """[V] 'Unsere wichtigste Regel': sell at the latest when a weekly close prints a 9-month low.
    A breach stays active for `memory` weeks unless the price recovers above the broken level, so a
    stock that crashes and then moves sideways keeps its sell signal."""
    n = len(weekly_closes)
    if n <= weeks:
        return StopInfo(None, False, None, None, False, None)
    level = min(weekly_closes[-weeks:])
    ago = broken = None
    for k in range(n - 1, max(weeks, n - 1 - memory) - 1, -1):
        prior = min(weekly_closes[k - weeks:k])
        if weekly_closes[k] < prior:
            ago, broken = n - 1 - k, prior
            break
    active = ago is not None and weekly_closes[-1] < broken
    since = None
    for k in range(n - 1, weeks - 1, -1):
        if weekly_closes[k] > max(weekly_closes[k - weeks:k]):
            since = n - 1 - k
            break
    return StopInfo(level, active, ago if active else None, broken if active else None,
                    since == 0, since)


def relative_strength(daily: Series, months: int = 15) -> float | None:
    """[V] p9: current price * 100 / median of the last 15 months."""
    if not daily:
        return None
    end = d(daily[-1][0])
    start = end - timedelta(days=int(months * 30.44))
    window = [v for ds, v in daily if d(ds) >= start]
    if len(window) < 60 or d(daily[0][0]) > start + timedelta(days=31):
        return None   # not enough history for a 15-month median
    med = statistics.median(window)
    return daily[-1][1] * 100 / med if med else None


def last_friday(year: int, month: int) -> date:
    nxt = date(year + (month == 12), month % 12 + 1, 1)
    day = nxt - timedelta(days=1)
    while day.weekday() != 4:
        day -= timedelta(days=1)
    return day


def season_positive(day: date) -> bool:
    """[V] Saisonfaktor: positive from the last weekend of October to the last weekend of April."""
    apr, octo = last_friday(day.year, 4), last_friday(day.year, 10)
    return not (apr <= day < octo)


def strict_summer(day: date) -> bool:
    """[V] Sicherheitssystem strict variant: out from the last Friday of July until the last Friday
    of October."""
    return last_friday(day.year, 7) <= day < last_friday(day.year, 10)


def sma(values: list[float], n: int) -> list[float | None]:
    out: list[float | None] = []
    for i in range(len(values)):
        out.append(sum(values[i - n + 1:i + 1]) / n if i >= n - 1 else None)
    return out


def align_ffill(base: Series, other: Series) -> list[float | None]:
    """For each date in base, the last value of `other` on or before that date."""
    out, j, last = [], 0, None
    for ds, _ in base:
        while j < len(other) and other[j][0] <= ds:
            last = other[j][1]
            j += 1
        out.append(last)
    return out
