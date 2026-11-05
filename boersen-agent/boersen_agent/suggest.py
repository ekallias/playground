"""Stock-level evaluation and suggestions with a confidence score.

Confidence is a 0-100 *rule-agreement* score, not a probability of profit. It starts from the
strongest rule that fired and is adjusted by agreeing or conflicting evidence, the market regime,
and data quality. Every adjustment is listed in `reasons`, so each number can be traced.
"""
from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from datetime import date

from . import rules as R
from .newsletter import name_key

# Base scores per rule (the newsletter's own priority order; V = stated rule, I = inferred).
BASE = {
    "sell_9m_live": 85,        # [V] weekly close at a 9-month low
    "sell_newsletter": 75,     # [V] Dispo-Liste "V"
    "reduce_quarter": 60,      # [V] Gann quarter-rule low ("guter Kompromiss")
    "sell_newsletter_T": 65,   # [V] marked "T" (new 9-month low) in the stock list
    "watch_breach": 60,        # live price below the stop, weekly close pending
    "watch_near_stop": 45,
    "review_rank": 50,         # [V] dropped out of the top 200 by RS
    "review_stale": 45,        # [V] 12+ months without a new high
    "buy_newsletter": 60,      # also used for ADD (already held)      # [V] Dispo-Liste "K"
    "buy_candidate": 40,       # [V] criteria: high RS, low KUV, new "H"
    "hold": 55,
}


@dataclass
class Live:
    price: float | None
    quote_time: str | None
    stop: float | None
    stop_breach_confirmed: bool
    breach_weeks_ago: int | None
    breach_level: float | None
    stop_breach_provisional: bool
    dist_to_stop_pct: float | None
    quarter_low: bool
    quarter_low_level: float | None
    new_high: bool
    weeks_since_high: int | None
    rs: float | None
    day_change_pct: float | None


@dataclass
class Suggestion:
    key: str
    name: str
    action: str                      # SELL | REDUCE | WATCH | REVIEW | BUY | ADD | HOLD
    confidence: int
    isin: str | None = None
    symbol: str | None = None
    in_portfolio: bool = False
    newsletter_rec: str | None = None
    verification: str | None = None  # confirmed | contradicted | unverified (for newsletter calls)
    reasons: list[dict] = field(default_factory=list)
    live: dict | None = None
    universe: dict | None = None

    @property
    def label(self) -> str:
        return "high" if self.confidence >= 70 else "medium" if self.confidence >= 45 else "low"

    def as_dict(self) -> dict:
        d = dict(self.__dict__)
        d["label"] = self.label
        return d


def live_metrics(daily, asof, weeks: int, rs_months: int, prev_close: float | None) -> Live | None:
    if len(daily) < 30:
        return None
    done, prov = R.weekly(daily, asof)
    closes = [c for _, c in done]
    stop = R.nine_month(closes, weeks)
    q = R.quarter_rule(done)
    price = daily[-1][1]
    level = stop.level
    prov_breach = bool(prov and level and prov[1] < level)
    dist = (price - level) / level * 100 if level else None
    chg = (price - prev_close) / prev_close * 100 if prev_close else None
    return Live(price, daily[-1][0], level, stop.confirmed_breach, stop.breach_weeks_ago, stop.breach_level,
                prov_breach, dist,
                bool(q.events and q.events[-1] == "low"), q.low_level, stop.new_high,
                stop.weeks_since_high, R.relative_strength(daily, rs_months), chg)


class UniverseIndex:
    """The ~700-stock list of an issue, ranked globally by RS."""

    def __init__(self, rows: list[dict]):
        ranked = sorted((r for r in rows if r.get("rs") is not None), key=lambda r: -r["rs"])
        self.rows = []
        for i, r in enumerate(ranked, 1):
            r = dict(r)
            r["global_rank"] = i
            self.rows.append(r)
        self.by_key = {}
        for r in self.rows:
            self.by_key.setdefault(name_key(r["name"]), r)
        self.list_sizes: dict[str, int] = {}
        for r in self.rows:
            self.list_sizes[r["list"]] = max(self.list_sizes.get(r["list"], 0), r["list_rank"])

    def match(self, name: str | None) -> tuple[dict | None, float]:
        if not name:
            return None, 0.0
        k = name_key(name)
        if k in self.by_key:
            return self.by_key[k], 1.0
        best = difflib.get_close_matches(k, list(self.by_key), n=1, cutoff=0.86)
        if best:
            return self.by_key[best[0]], difflib.SequenceMatcher(None, k, best[0]).ratio()
        first = k.split()[0] if k else ""
        if len(first) >= 5:
            hits = [v for kk, v in self.by_key.items() if kk.split() and kk.split()[0] == first]
            if len(hits) == 1:
                return hits[0], 0.8
        return None, 0.0


def sector_kuv(sector: str | None, sectors: list[dict]) -> float | None:
    if not sector:
        return None
    sk = name_key(sector)[:5]
    for s in sectors:
        if name_key(s["name"]).startswith(sk) and s.get("kuv"):
            return s["kuv"]
    return None


def regime_adjust(market, reasons: list[dict]) -> int:
    pts = 0
    if market is None:
        return 0
    if market.gesamt_positive is False:
        reasons.append({"text": "Gesamtsystem negative: newsletter advises caution", "marker": "V", "pts": -15})
        pts -= 15
    if market.safety_effective == -1:
        why = "strict summer exit" if market.safety_strict else "2 of 3 warnings"
        reasons.append({"text": f"Sicherheitssystem negative ({why})", "marker": "V", "pts": -10})
        pts -= 10
    if not market.season_positive:
        reasons.append({"text": "Saisonfaktor negative (May-Oct): the newsletter does not add risk",
                        "marker": "V", "pts": -10})
        pts -= 10
    return pts


def _r(reasons, text, marker, pts=0):
    reasons.append({"text": text, "marker": marker, "pts": pts})
    return pts


def evaluate(*, key: str, name: str, isin: str | None, symbol: str | None, symbol_verified: bool,
             in_portfolio: bool, dispo: dict | None, uni: dict | None, uni_match: float,
             list_size: int | None, sect_kuv: float | None, live: Live | None, market, cfg,
             newsletter_age: int | None, held_weeks: int | None = None, candidate: bool = False) -> Suggestion:
    rule = cfg.rule
    reasons: list[dict] = []
    action, score = None, 0
    rec = dispo["rec"] if dispo else None

    # ---------------- sell side (most important rule first)
    if live and live.stop_breach_confirmed:
        action, score = "SELL", BASE["sell_9m_live"]
        when = "last week" if not live.breach_weeks_ago else f"{live.breach_weeks_ago} weeks ago"
        _r(reasons, f"weekly close broke the 9-month low {live.breach_level:.2f} ({when}); "
                    "the newsletter's latest exit point", "V")
        if rec == "V":
            score += _r(reasons, "newsletter also says sell (V)", "V", 7)
    elif rec == "V":
        action, score = "SELL", BASE["sell_newsletter"]
        _r(reasons, "newsletter Dispo-Liste: Verkaufen (V)", "V")
        if live and live.quarter_low:
            score += _r(reasons, "live: quarter-rule low confirms weakness", "V", 5)
    elif live and live.quarter_low:
        action, score = "REDUCE", BASE["reduce_quarter"]
        _r(reasons, f"weekly close below all closes of this and last quarter ({live.quarter_low_level:.2f})", "V")
    elif uni and uni.get("ht") == "T" and (in_portfolio or rec):
        action, score = "SELL", BASE["sell_newsletter_T"]
        _r(reasons, "stock list marks a new 9-month low (T)", "V")
    elif live and live.stop_breach_provisional:
        action, score = "WATCH", BASE["watch_breach"]
        _r(reasons, f"price {live.price:.2f} is below the stop {live.stop:.2f}; sell if this week closes below", "V")
    elif live and live.dist_to_stop_pct is not None and live.dist_to_stop_pct < rule("near_stop_pct"):
        action, score = "WATCH", BASE["watch_near_stop"]
        _r(reasons, f"only {live.dist_to_stop_pct:.1f}% above the 9-month stop ({live.stop:.2f})", "V")

    # ---------------- review rules for holdings
    if action is None and in_portfolio:
        if uni and uni["global_rank"] > rule("rs_top_n"):
            action, score = "REVIEW", BASE["review_rank"]
            _r(reasons, f"RS rank {uni['global_rank']} is outside the top {rule('rs_top_n')}", "V")
        elif live and live.weeks_since_high is not None and live.weeks_since_high >= rule("no_new_high_weeks") \
                and (held_weeks or 0) >= rule("no_new_high_weeks"):
            action, score = "REVIEW", BASE["review_stale"]
            _r(reasons, f"no new 9-month high for {live.weeks_since_high} weeks", "V")
        elif uni and list_size and uni["list_rank"] > list_size * 2 / 3 and market and market.gesamt_positive:
            action, score = "REVIEW", 40
            _r(reasons, f"bottom third of its list ({uni['list_rank']}/{list_size}) in a rising market", "V")

    # ---------------- buy side
    if action is None and (rec == "K" or candidate):
        action = "ADD" if in_portfolio else "BUY"
        score = BASE["buy_newsletter"] if rec == "K" else BASE["buy_candidate"]
        _r(reasons, "newsletter Dispo-Liste: Kaufen (K)" if rec == "K"
           else "meets the stock-list criteria (high RS, low KUV, new high)", "V")
        if uni:
            if uni["global_rank"] <= rule("rs_top_n"):
                score += _r(reasons, f"RS {uni['rs']:.0f}, global rank {uni['global_rank']}", "V", 5)
            else:
                score += _r(reasons, f"RS rank {uni['global_rank']} outside the top {rule('rs_top_n')}", "V", -15)
            if uni.get("ht") == "H":
                score += _r(reasons, "new 9-month high (H)", "V", 5)
            kuv = uni.get("kuv")
            limit = sect_kuv or rule("kuv_normal_max")
            if kuv is not None:
                score += (_r(reasons, f"KUV {kuv:.2f} at or below {'sector average' if sect_kuv else 'normal range'} "
                             f"({limit:.2f})", "V", 5) if kuv <= limit else
                          _r(reasons, f"KUV {kuv:.2f} above {'sector average' if sect_kuv else 'normal range'} "
                             f"({limit:.2f})", "V", -5))
            elif uni.get("kbv") is not None:
                score += (_r(reasons, f"KBV {uni['kbv']:.2f} within {rule('kbv_fair_max')}", "V", 3)
                          if uni["kbv"] <= rule("kbv_fair_max") else
                          _r(reasons, f"KBV {uni['kbv']:.2f} above {rule('kbv_fair_max')}", "V", -5))
        if live:
            score += _r(reasons, "live data: no stop or quarter-rule breach", "V", 10)
            if live.rs is not None:
                _r(reasons, f"live RS {live.rs:.0f} (newsletter list: {uni['rs']:.0f})" if uni else
                   f"live RS {live.rs:.0f}", "V")
        score += regime_adjust(market, reasons)

    if action is None:
        action, score = "HOLD", BASE["hold"]
        _r(reasons, "no sell or review rule triggered", "V")
        if uni and uni["global_rank"] <= rule("rs_top_n"):
            score += _r(reasons, f"RS rank {uni['global_rank']} within the top {rule('rs_top_n')}", "V", 10)
        if live and live.new_high:
            score += _r(reasons, "new 9-month high this week", "V", 5)
        if rec == "H":
            score += _r(reasons, "newsletter: Halten (H)", "V", 5)

    # ---------------- data quality
    if live is None:
        score += _r(reasons, "no live price data: rules checked on newsletter data only", "", -15)
    if symbol and not symbol_verified:
        score += _r(reasons, f"symbol {symbol} found by automatic lookup; please verify", "", -5)
    if uni and uni_match < 1.0:
        score += _r(reasons, f"name matched to the stock list approximately ({uni['name']})", "", -5)
    if newsletter_age is not None and newsletter_age > rule("stale_newsletter_days"):
        score += _r(reasons, f"newsletter data is {newsletter_age} days old", "", -10)

    verification = None
    if rec in ("K", "V", "H"):
        if live is None:
            verification = "unverified"
        elif rec == "K" and (live.stop_breach_confirmed or live.quarter_low):
            verification = "contradicted"
        elif rec == "H" and live.stop_breach_confirmed:
            verification = "contradicted"
        elif rec == "V" and not (live.stop_breach_confirmed or live.quarter_low) and live.new_high:
            verification = "contradicted"
        else:
            verification = "confirmed"

    return Suggestion(
        key=key, name=name, action=action, confidence=max(5, min(95, int(round(score)))), isin=isin,
        symbol=symbol, in_portfolio=in_portfolio, newsletter_rec=rec, verification=verification,
        reasons=reasons, live=None if live is None else dict(live.__dict__), universe=uni)


def held_weeks_since(first_date: str | None, today: date) -> int | None:
    if not first_date:
        return None
    return (today - date.fromisoformat(first_date[:10])).days // 7
