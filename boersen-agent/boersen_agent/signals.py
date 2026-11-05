"""Market-level signals: Gesamtsystem, Sicherheitssystem, Saisonfaktor.

Each component is recomputed from market data where the rule is known. Otherwise it falls back to
the state published in the latest newsletter, labelled as such. Markers follow docs/METHODS.md:
V = rule stated in the newsletter, I = inferred, U = parameter unknown (best guess).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from . import rules as R


@dataclass
class Component:
    key: str
    label: str
    state: int | None              # +1 positive, -1 negative, None unknown
    source: str                    # computed | newsletter | missing
    marker: str                    # V / I / U
    detail: str = ""
    newsletter: int | None = None  # state printed in the latest issue

    @property
    def diverges(self) -> bool:
        return self.source == "computed" and self.newsletter is not None and self.state != self.newsletter


@dataclass
class MarketState:
    asof: str
    components: dict[str, Component]
    gesamt_score: tuple[int, int] | None
    other5_score: tuple[int, int] | None
    safety_core: Component
    safety_strict: bool
    season_positive: bool
    levels: dict[str, float | None] = field(default_factory=dict)
    provisional: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def gesamt_positive(self) -> bool | None:
        return None if not self.gesamt_score else self.gesamt_score[0] > self.gesamt_score[1]

    @property
    def safety_effective(self) -> int | None:
        """Sicherheitssystem incl. the strict summer exit."""
        if self.safety_strict:
            return -1
        return self.safety_core.state


def _wk(series, asof):
    done, prov = R.weekly(series, asof)
    return done, prov


def _by_week(weekly_series, states):
    return {R.d(ds).isocalendar()[:2]: st for (ds, _), st in zip(weekly_series, states)}


def compute_market(market, cfg, dashboard: dict | None, asof: datetime,
                   issue_date: str | None = None) -> MarketState:
    dash = dashboard or {}
    rule = cfg.rule
    sym = cfg.symbols
    comps: dict[str, Component] = {}
    levels: dict[str, float | None] = {}
    provisional: list[str] = []
    notes: list[str] = []

    def nl(key):
        return dash.get(key)

    def fallback(key, label, marker, why):
        st = nl(key)
        return Component(key, label, st, "newsletter" if st is not None else "missing", marker,
                         f"from newsletter ({why})" if st is not None else f"unavailable ({why})", st)

    # --- price series (completed weekly closes)
    def weekly_of(name):
        s = market.daily_with_quote(sym[name]) if sym.get(name) else []
        return _wk(s, asof) if s else ([], None)

    spx, spx_p = weekly_of("sp500")
    dax, dax_p = weekly_of("dax")
    ndq, _ = weekly_of("nasdaq")
    dju, _ = weekly_of("dow_utility")
    brent, _ = weekly_of("brent")
    eurusd, _ = weekly_of("eurusd")

    # --- 1) Zinsstruktur [V]: 22-week smoothed avg of (US 10y-1y) and (DE Umlaufrendite - Euribor 12M)
    try:
        us10, us1 = market.rate("FRED:DGS10"), market.rate("FRED:DGS1")
        um, eur = market.rate("BBK:UMLAUFRENDITE"), market.rate("ECB:EURIBOR12M")
        base, _ = _wk(us10, asof)
        if base and us1 and um and eur:
            a1 = R.align_ffill(base, us1)
            a2 = R.align_ffill(base, um)
            a3 = R.align_ffill(base, eur)
            spread = [((v - b) + (c - e)) / 2 for (ds, v), b, c, e in zip(base, a1, a2, a3)
                      if None not in (b, c, e)]
            sm = R.sma(spread, rule("zins_smoothing_weeks"))
            val = sm[-1] if sm else None
            if val is not None:
                comps["zinsstruktur"] = Component(
                    "zinsstruktur", "Zinsstruktur", 1 if val >= 0 else -1, "computed", "V",
                    f"smoothed spread {val:.2f} (newsletter: {dash.get('zinsstruktur_value')}); "
                    "US+DE averaging [I]", nl("zinsstruktur"))
                levels["zinsstruktur"] = round(val, 3)
    except Exception as e:  # noqa: BLE001 - one bad source must not stop the report
        notes.append(f"Zinsstruktur not computed: {e}")
    comps.setdefault("zinsstruktur", fallback("zinsstruktur", "Zinsstruktur", "V", "rate data missing"))

    # --- 2) Index-Trend [V rules, I unanimity]
    subs = {}
    for key, ser, sell_n, buy_n in (("nasdaq", ndq, rule("nasdaq_sell_weeks"), rule("nasdaq_buy_weeks")),
                                    ("dow_utility", dju, rule("dju_sell_weeks"), rule("dju_buy_weeks")),
                                    ("dax", dax, rule("dax_sell_weeks"), rule("dax_buy_weeks"))):
        if len(ser) > max(sell_n, buy_n):
            closes = [c for _, c in ser]
            subs[key] = _by_week(ser, R.latched(closes, buy_n, sell_n))
            levels[f"{key}_sell_level"] = min(closes[-(sell_n - 1):])
            levels[f"{key}_buy_level"] = max(closes[-(buy_n - 1):])
    if len(subs) == 3:
        weeks = sorted(set().union(*[set(v) for v in subs.values()]))
        state, last = None, {k: None for k in subs}
        for wk in weeks:
            for k in subs:
                if wk in subs[k]:
                    last[k] = subs[k][wk]
            vals = set(last.values())
            if vals == {1}:
                state = 1
            elif vals == {-1}:
                state = -1
        detail = ", ".join(f"{k} {'+' if v == 1 else '-' if v == -1 else '?'}" for k, v in last.items())
        if state is None:
            comps["index_trend"] = fallback("index_trend", "Index-Trend", "I", "no unanimous signal in history")
        else:
            comps["index_trend"] = Component("index_trend", "Index-Trend", state, "computed", "I",
                                             detail + "; switches only when all three agree [I]",
                                             nl("index_trend"))
    else:
        comps["index_trend"] = fallback("index_trend", "Index-Trend", "I", "index data missing")

    # --- 3) other five
    def breakout(key, label, ser, n_up, n_down, up_positive, marker, extra=""):
        if len(ser) > max(n_up, n_down):
            closes = [c for _, c in ser]
            st = R.latched(closes, n_up, n_down, up_positive)[-1]
            if st is not None:
                return Component(key, label, st, "computed", marker,
                                 f"last {closes[-1]:.4g}; {extra}".strip("; "), nl(key))
        return fallback(key, label, marker, "data missing or no breakout yet")

    try:
        us10w, _ = _wk(market.rate("FRED:DGS10"), asof)
    except Exception as e:  # noqa: BLE001
        us10w = []
        notes.append(f"bond yields not loaded: {e}")
    n = rule("bond_weeks")
    comps["anleihezinsen"] = breakout("anleihezinsen", "Anleihezinsen", us10w, n, n, False, "V",
                                      "US 10y; series choice [U]")
    n = rule("oil_weeks")
    comps["oel"] = breakout("oel", "Ölpreis", brent, n, n, False, "U",
                            "negative on 6-week high [V], positive on 6-week low [U]")
    comps["crb"] = fallback("crb", "CRB-Index", "V", "no free CRB data; rule needs the index")
    usd_per_eur_inv = [(ds, 1 / c) for ds, c in eurusd if c]
    n = rule("dollar_weeks")
    comps["dollar"] = breakout("dollar", "US-Dollar-Euro", usd_per_eur_inv, n, n, True, "U",
                               "EUR per USD; buy on 15-week high [I], sell on 15-week low [U]")
    season = R.season_positive(asof.date())
    comps["saison"] = Component("saison", "Saisonfaktor", 1 if season else -1, "computed", "V",
                                "positive end-Oct to end-Apr", nl("saison"))

    five = [comps[k].state for k in ("anleihezinsen", "oel", "crb", "dollar", "saison")]
    other5 = None
    if None not in five:
        other5 = (five.count(1), five.count(-1))
    votes = [comps["zinsstruktur"].state, comps["index_trend"].state,
             None if other5 is None else (1 if other5[0] > other5[1] else -1)]
    gesamt = None if None in votes else (votes.count(1), votes.count(-1))

    # --- Sicherheitssystem [V]
    warns, parts = 0, []
    known = True
    for key, ser, prov in (("sp500", spx, spx_p), ("dax", dax, dax_p)):
        if len(ser) < 30:
            known = False
            continue
        q = R.quarter_rule(ser)
        levels[f"{key}_quarter_low"], levels[f"{key}_quarter_high"] = q.low_level, q.high_level
        st = q.state[-1]
        parts.append(f"{key} {'warning' if st == -1 else 'ok'}")
        if st == -1:
            warns += 1
        if prov and q.low_level and prov[1] < q.low_level:
            provisional.append(f"{key} is below its quarter-rule level intraweek "
                               f"({prov[1]:.0f} < {q.low_level:.0f}); confirmation needs the weekly close")
    z = comps["zinsstruktur"]
    if z.state == -1:
        warns += 1
    parts.append(f"Zinsstruktur {'warning' if z.state == -1 else 'ok' if z.state == 1 else '?'}")
    if known and z.state is not None:
        safety = Component("sicherheit", "Sicherheitssystem (2 of 3)", -1 if warns >= 2 else 1, "computed",
                           "V", "; ".join(parts) + "; re-entry via quarter-rule high [I]", nl("sicherheit"))
    else:
        safety = fallback("sicherheit", "Sicherheitssystem (2 of 3)", "V", "index data missing")
        if (issue_date and safety.state == -1 and R.strict_summer(R.d(issue_date))
                and not R.strict_summer(asof.date())):
            safety.detail += ("; the issue fell in the strict summer window, so its 'negative' may only reflect "
                              f"the summer exit, which ended on {R.last_friday(asof.year, 10).isoformat()}")
    strict = R.strict_summer(asof.date())
    if strict:
        notes.append("Strict variant of the Sicherheitssystem: out of stocks from the last Friday of July "
                     "to the last Friday of October [V].")
    # short-term methods: universes are proprietary, so newsletter only
    comps["short_term"] = fallback("short_term", "Kurzfristige Trend-Methoden", "I", "uses their stock universe")
    return MarketState(asof.isoformat(timespec="minutes"), comps, gesamt, other5, safety, strict, season,
                       levels, provisional, notes)


def zins_at_dates(market, cfg, dates: list[str]) -> dict[str, float | None]:
    """Computed Zinsstruktur as it would have read on each date (data strictly before the date)."""
    try:
        us10, us1 = market.rate("FRED:DGS10"), market.rate("FRED:DGS1")
        um, eur = market.rate("BBK:UMLAUFRENDITE"), market.rate("ECB:EURIBOR12M")
    except Exception:  # noqa: BLE001
        return {}
    out = {}
    for day in dates:
        base, _ = R.weekly([x for x in us10 if x[0] < day])
        vals = [((v - b) + (c - e)) / 2 for (ds, v), b, c, e in
                zip(base, R.align_ffill(base, us1), R.align_ffill(base, um), R.align_ffill(base, eur))
                if None not in (b, c, e)]
        sm = R.sma(vals, cfg.rule("zins_smoothing_weeks"))
        out[day] = sm[-1] if sm else None
    return out
