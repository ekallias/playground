# boersen-agent

A daily portfolio assistant built on the methods of the *Börsensignale* newsletter (Uwe Lang /
Klaus Haidorfer). **Scripts do the repeatable work, an AI analyst (Claude) does the judgment:**

- **Scripts:** pull each issue from Paperless and parse it, fetch market and rate data, evaluate the
  newsletter's rules ([docs/METHODS.md](docs/METHODS.md)), keep your portfolio, draw the charts,
  and lay out the PDFs.
- **Claude** reads the scripts' fact pack (plus the newsletter PDF on issue days). It weighs
  conflicting signals and makes a call per position, with a confidence and a reason. It also gives
  a verdict on each newsletter pick, names new ideas, lists risks and asks you questions. It keeps
  notes between runs.
- **Guardrails:**
  - Claude may only talk about securities in the fact pack, and confidences are clamped to 0–100.
  - Every number in the tables comes from the scripts.
  - Any call that differs from a stated newsletter rule is highlighted in red. That includes, above
    all, the 9-month-low sell rule.
  - Without an API key, or if the call fails, you get the rule-based report with a banner.

Outputs are **PDF reports with charts**: a daily brief, and a detailed report for each new issue.
**It is decision support for your own account, not investment advice: it never trades, and
confidence means strength of evidence, not a probability of gain.**

Python 3.11+, `pip install -r requirements.txt` (anthropic, matplotlib, reportlab), plus
`pdftotext`/`pdfinfo` (poppler-utils).

Deployment guides:
- **[docs/HOMESERVER.md](docs/HOMESERVER.md):** on your own server, next to LiteLLM, n8n and Open
  WebUI.
- **[docs/CLAUDE_CLOUD.md](docs/CLAUDE_CLOUD.md):** entirely on Claude, as a scheduled Claude Code
  session with no API key.
- **[docs/OTHER_MODELS.md](docs/OTHER_MODELS.md):** Ollama or any OpenAI-compatible model instead of
  Claude.

## What it does

| When | What | Output |
|---|---|---|
| Every run (daily) | Checks Paperless for new issues of the correspondent "Börsen Signale" | — |
| New issue (every 2 weeks) | Parses the dashboard (p5), the Dispo-Liste (p8, with ISINs, K/H/V, 9-month stops) and the ~700-stock RS list (p9–15). Re-checks the market signals and picks with live data, screens for candidates; Claude reads the issue and the facts | `reports/<issue>_newsletter-report.pdf` |
| Daily | Market regime, then every position: 9-month stop, quarter rule, RS rank, review rules; Claude's calls with confidence | `reports/<date>_daily-brief.pdf` |

Charts:
- **Daily brief:** allocation, distance to the 9-month stop, price vs. stop per position, and
  portfolio value over time.
- **Issue report:** Gesamtsystem per issue, Zinsstruktur (newsletter vs. computed), the stock list
  as relative strength vs. KUV with your holdings and candidates marked, and allocation.

A Markdown copy of each report is kept as well (`keep_markdown`).

**Language:** set `[report] language = "de"` for German reports. That covers headings, tables, action
labels (VERKAUFEN, HALTEN …), rule reasons, charts, dates and numbers (1.234,56), and the AI writes
its text in German too. Internal codes stay English, so the history and the guardrails don't depend
on the language.

Duplicate uploads of the same issue are detected and skipped. Reports can also be uploaded to
Paperless (`upload_reports = true`).

### AI analyst

Set `ANTHROPIC_API_KEY`, or route through a LiteLLM gateway with `ANTHROPIC_BASE_URL` (see
docs/HOMESERVER.md). Defaults are in `[ai]`:

- **Model and effort:** `claude-opus-5-5`; effort `medium` daily, `high` on issue days.
- **Output:** structured JSON.
- **Fallbacks:** Anthropic's server-side refusal fallback is on (`server_fallbacks`).
- **Cost:** about $3–5 a month.

The fact pack sent to Claude contains your positions and values. Set `enabled = false` to keep
everything local; the reports are then rule-based only.

## Setup (home server, e.g. the Paperless LXC)

```bash
apt install -y poppler-utils python3 python3-pip
git clone https://github.com/ekallias/playground && cp -r playground/boersen-agent /opt/boersen-agent
cd /opt/boersen-agent
pip install -r requirements.txt
cp config.example.toml config.toml
cp deploy/boersen-agent.env.example /etc/boersen-agent.env   # PAPERLESS_URL / _TOKEN, ANTHROPIC_API_KEY
chmod 600 /etc/boersen-agent.env
```

### Import your portfolio once

```bash
set -a; . /etc/boersen-agent.env; set +a
python3 -m boersen_agent portfolio import Bestandsaufstellung.csv
python3 -m boersen_agent portfolio show
```

Two CSV layouts are accepted:

- **The broker export ("Bestandsaufstellung")**, as is: `Bezeichnung;WKN;ISIN;Währung;Einstandskurs;Bestand`.
  It has no buy dates, so the import date is used, which delays the "12 months without a new high"
  review by a year.
- **The generic layout** of `portfolio.example.csv`. Add a `CASH` row if you want cash tracked.

Asset types are detected automatically:

- **Funds and ETFs** (LU/IE ISINs or fund names) get the price rules only.
- **Bonds** are valued in percent of nominal and counted as cash-like, as the newsletter does with its
  Bund position.

### Keep it in sync with your trades

The agent cannot see your broker, so record every trade:

```bash
python3 -m boersen_agent portfolio buy  --isin DE0007164600 --qty 10 --price 180.10 --currency EUR
python3 -m boersen_agent portfolio sell --isin DE0007164600 --qty 10 --price 191.00 --currency EUR --date 2026-11-12
```

### Symbols

ISINs are mapped to market-data symbols through the provider's search. Automatic matches are marked
unverified and cost 5 confidence points until you confirm them:

```bash
python3 -m boersen_agent symbols list
python3 -m boersen_agent symbols set DE0007164600 SAP.DE --name SAP   # confirm or correct
```

### First run, then schedule it

```bash
python3 -m boersen_agent run          # ingests all past issues (builds history), reports on the newest
cp deploy/boersen-agent.service deploy/boersen-agent.timer /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now boersen-agent.timer
```

The timer runs on weekdays at 18:15. Cron works as well:

```
15 18 * * 1-5  cd /opt/boersen-agent && set -a && . /etc/boersen-agent.env && python3 -m boersen_agent run
```

## Market data

| Provider | Cost | Notes |
|---|---|---|
| `yahoo` (default) | free | Unofficial endpoint: can rate-limit or change without notice. Fine to start with. |
| `eodhd` | paid (check current plans) | Official API with European exchanges (e.g. `SAP.XETRA`, `NESN.SW`), indices (`GDAXI.INDX`), FX, and ISIN search. Set `provider = "eodhd"` and `EODHD_API_TOKEN`. |
| `csv` | — | Offline/testing: `data/csv/<symbol>.csv` with `date,close`. |

Interest rates come from free official sources: FRED (US 10y/1y), the Bundesbank (Umlaufrendite)
and the ECB (Euribor 12M). Quotes are typically delayed about 15 minutes. The weekly rules only use
completed weeks: a stop broken intraweek shows as WATCH until the Friday close confirms it.

If you pay for a subscription, EODHD is the one this code supports. Check that the index and
commodity symbols in `config.py` match their catalogue.

## Commands

```
python3 -m boersen_agent run [--json]        # daily job (JSON summary for n8n)
python3 -m boersen_agent daily               # daily brief only
python3 -m boersen_agent prepare [--issue K] # hand-off: fact packs for a Claude session / other model
python3 -m boersen_agent finish              # hand-off: validate analysis.json, render + publish PDFs
python3 -m boersen_agent ingest              # pull new issues (+ report)
python3 -m boersen_agent ingest-file X.pdf   # parse a local PDF
python3 -m boersen_agent report [2026-22]    # rewrite an issue report
python3 -m boersen_agent portfolio import|buy|sell|show
python3 -m boersen_agent symbols list|set
```

## Tests and tools

```bash
python3 -m unittest discover -s tests -t .           # synthetic data, fake Claude client, no network
python3 tools/validate_parser.py /path/to/pdfs       # after a newsletter layout change
python3 tools/calibrate_zins.py data/agent.db        # computed vs. published yield curve
```

## Known limits

- **CRB index and the short-term methods** use the newsletter's published state: there is no free CRB
  feed, and the universes behind the short-term methods are proprietary.
- **Inferred (I) and guessed (U) parameters** are configurable: the Nasdaq and DAX buy windows, the oil
  buy rule, and the dollar sell rule.
- **The computed yield curve is about 0.1 above the published values.** The newsletter's exact series
  differ. The rule only uses the sign, which agrees in all 23 issues checked.
- **Name matching.** The 700-stock list has no ISINs, so names are matched fuzzily; approximate matches
  are flagged.
- **The newsletter's model-portfolio quota** (75 → 100 → 50% in 2026) is discretionary and not
  reproduced. The reports show it for context.
- **The AI analyst has only been exercised with a fake client in tests.** Check its first real
  reports critically, especially its confidence levels.
- **No data files are committed.** Newsletter PDFs, the database, reports, your portfolio and
  `config.toml` stay in git-ignored paths.
