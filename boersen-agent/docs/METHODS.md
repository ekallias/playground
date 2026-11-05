# Rules implemented by boersen-agent

These are the Börsensignale (Uwe Lang) methods as reconstructed from 23 issues (Dec 2025 to Oct 2026),
paraphrased. Markers: **V** stated in the newsletter · **I** inferred, consistent with every issue
checked · **U** parameter not stated (best guess, configurable in `[rules]`).

The agent recomputes a rule from market data when it can, and otherwise shows the state printed in
the newsletter, labelled `newsletter`.

## Market level

| Rule | Definition | Marker | Computed from |
|---|---|---|---|
| Gesamtsystem | 3 votes: Zinsstruktur, Index-Trend, majority of the 5 "other" indicators; positive at 2:1 or 3:0 | V | the components below |
| Zinsstruktur | (US 10y − US 1y) and (DE Umlaufrendite − Euribor 12M), averaged, smoothed over 22 weeks; negative below 0 | V (averaging I) | FRED, Bundesbank, ECB |
| Index-Trend | Nasdaq: sell on a 26-week low; Dow Utility: sell on a 6-week low, buy on a 9-week high; DAX: sell on a 6-week low. Switches only when all three agree | V / I; buy sides for Nasdaq and DAX U | index prices |
| Anleihezinsen | positive after a 39-week low in yields, negative after a 39-week high | V (series U: US 10y) | FRED |
| Ölpreis | negative on a new 6-week high (Brent), positive on a 6-week low | V / U | Brent |
| CRB | YoY % change, 7-week smoothed, compared with its value a year earlier | V | newsletter only (no free data) |
| US-Dollar | buy on a 15-week high of EUR per USD, sell on a 15-week low | I / U | EUR/USD |
| Saisonfaktor | positive from the last Friday of October to the last Friday of April | V | calendar |
| Sicherheitssystem | S&P 500 and DAX under the Gann quarter rule, plus Zinsstruktur < 0: 2 of 3 warnings = exit all stocks | V (re-entry level I) | index prices, rates |
| Strict summer exit | out of stocks from the last Friday of July to the last Friday of October | V | calendar |
| Short-term methods | 9-month / 32-week / 25-index quarter counts, majority of 3 | I | newsletter only (proprietary universes) |

Gann quarter rule: warning when a weekly close is below **all** weekly closes of the current and
previous calendar quarter. Weekly close = last trading day of the ISO week; a week counts as complete
after Friday 23:00 local time. Before that, breaches are reported as provisional.

## Stock level (ordered by priority)

| Rule | Action | Marker |
|---|---|---|
| Weekly close breaks the 9-month (39-week) low; the signal stays active up to 4 weeks unless the price recovers above the broken level | SELL | V |
| Newsletter Dispo-Liste "V" | SELL | V |
| Gann quarter-rule low | REDUCE | V ("compromise", earlier than the 9-month low) |
| Stock list marks a new 9-month low (T) | SELL (if held or in the Dispo-Liste) | V |
| Price below the 9-month stop intraweek | WATCH (confirm on the weekly close) | V |
| Within 3% of the 9-month stop | WATCH | configurable |
| Held and outside the top 200 by RS | REVIEW | V |
| Held 12+ months without a new 9-month high | REVIEW | V |
| Bottom third of its country list while the Gesamtsystem is positive | REVIEW | V |
| Newsletter "K" | BUY (ADD if held) | V |
| Top RS + new high (H) + KUV ≤ 2.0 (or KBV ≤ 4 for financials), not in the Dispo-Liste | BUY candidate | V (criteria from p9) |

RS = current price × 100 / median price of the last 15 months (V, p9). The global rank is computed
across the ~700 stocks in the issue.

## Confidence

The score starts from the base of the rule that fired (SELL at a 9-month low 85, newsletter V 75,
quarter-rule low 60, newsletter K 60, new candidate 40, HOLD 55). It is then adjusted:

- **Agreement:** RS rank, new high, KUV vs. sector average, live confirmation (+).
- **Regime:** Gesamtsystem negative −15, Sicherheitssystem negative −10, summer season −10 (buys only).
- **Data quality:**
  - no live data −15
  - unverified symbol −5
  - approximate name match −5
  - newsletter older than 16 days −10

The result is clamped to 5–95. Labels: ≥ 70 high, 45–69 medium, < 45 low. The number measures how
strongly the rules agree. **It is not a probability of profit.**

## Not reproduced as rules

These come from the newsletter and are treated as judgment, or are too vague to test:

- The model-portfolio equity quota (75 → 100 → 50% in 2026).
- Gold "fair value".
- "Sell at the first setback".
- The 200-day method's "be cautious after a few months at 100%".
