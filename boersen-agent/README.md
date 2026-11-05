# boersen-agent

A daily, rule-based portfolio assistant built on the methods of the *Börsensignale* newsletter
(Uwe Lang / Klaus Haidorfer). It reads each new issue from Paperless and re-checks the newsletter's
signals with market data. It then scores your positions and the newsletter's picks, and writes two
Markdown reports.

Every suggestion comes from explicit rules (see [docs/METHODS.md](docs/METHODS.md)). Each one carries
a confidence score and the list of reasons behind it. **It is a decision aid, not investment advice:
it never trades, and the confidence measures rule agreement, not the chance of a gain.**

Python 3.11+, standard library only, plus `pdftotext`/`pdfinfo` (poppler-utils).

## What it does

| When | What | Output |
|---|---|---|
| Every run (daily) | Checks Paperless for new issues of the correspondent "Börsen Signale" | — |
| New issue (every 2 weeks) | Parses the dashboard (p5), the Dispo-Liste (p8, with ISINs, K/H/V, 9-month stops) and the ~700-stock RS list (p9–15). Re-checks the market signals and the newsletter's picks with live data, and screens the list for new candidates | `reports/<issue>_newsletter-report.md` |
| Daily | Market regime (Gesamtsystem, Sicherheitssystem, season), then every position: 9-month stop, quarter rule, RS rank, review rules | `reports/<date>_daily-brief.md` |

Duplicate uploads of the same issue are detected and skipped. Reports can also be uploaded to
Paperless as text documents (`upload_reports = true`).

## Setup (home server, e.g. the Paperless LXC)

```bash
apt install -y poppler-utils python3
git clone https://github.com/ekallias/playground && cp -r playground/boersen-agent /opt/boersen-agent
cd /opt/boersen-agent
cp config.example.toml config.toml
cp deploy/boersen-agent.env.example /etc/boersen-agent.env   # put PAPERLESS_URL / PAPERLESS_TOKEN there
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
python3 -m boersen_agent run                 # daily job
python3 -m boersen_agent daily               # daily brief only
python3 -m boersen_agent ingest              # pull new issues (+ report)
python3 -m boersen_agent ingest-file X.pdf   # parse a local PDF
python3 -m boersen_agent report [2026-22]    # rewrite an issue report
python3 -m boersen_agent portfolio import|buy|sell|show
python3 -m boersen_agent symbols list|set
```

## Tests and tools

```bash
python3 -m unittest discover -s tests -t .           # synthetic data only, no network
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
- **No data files are committed.** Newsletter PDFs, the database, reports, your portfolio and
  `config.toml` stay in git-ignored paths.
