# Running boersen-agent on Claude (no own infrastructure)

This setup runs the whole agent on Claude: a **Claude Code scheduled task** starts a fresh cloud
session every weekday. That session runs the scripts, and **Claude in the session writes the
analysis itself**. You need no server, no LiteLLM, no n8n, and no Anthropic API key. The runs count
toward your Claude plan's usage, not API billing.

| Piece | Where it lives |
|---|---|
| Code | this repository (`ekallias/playground`, folder `boersen-agent/`) |
| State: portfolio, history, symbol map, Claude's notes, reports | a small **private** GitHub repository, e.g. `ekallias/boersen-agent-state` |
| Newsletters (input) | your Paperless, reached at `https://paperless.kallias.net` |
| Schedule, compute, analysis | a Claude Code cloud environment + scheduled task |
| Delivery | push notification, the PDFs filed in Paperless and committed to the state repository |

```
scheduled task (weekdays 18:15) ─▶ fresh cloud session
   1. python -m boersen_agent prepare   ─▶ Paperless (new issue?), Yahoo / FRED / Bundesbank / ECB
   2. Claude reads prompt.md + facts (+ issue PDF), writes analysis.json
   3. python -m boersen_agent finish    ─▶ PDFs ─▶ Paperless
   4. git commit + push (state repo)    ─▶ next run starts from here
```

**Why a state repository?** Cloud containers start empty every time. The SQLite database is the
agent's memory: your portfolio and trades, the ingested issues, symbol mappings, daily snapshots
and Claude's notes. It has to survive between runs.

> **Privacy.** The state repository holds your portfolio, so keep it private. Each run also sends
> your positions, the newsletter and the facts through a Claude session.

## 1. Prepare GitHub

1. **Make the code available on the default branch.** Merge the `claude/great-knuth-8bh4a7` branch of
   `ekallias/playground` (open a pull request and merge it). Otherwise, check the branch out in the
   task prompt (step 4); the prompt below does that.
2. **Create a private repository** `ekallias/boersen-agent-state` with these files:

   `config.toml`
   ```toml
   [paths]
   data_dir = "data"
   reports_dir = "reports"
   keep_markdown = true

   [paperless]
   correspondent = "Börsen Signale"
   upload_reports = true          # file every PDF in Paperless
   report_tags = []               # e.g. [12] for a "Börsen-Agent" tag

   [market]
   provider = "yahoo"             # or "eodhd" + an EODHD network secret (see step 3)
   auto_lookup = true

   [ai]
   enabled = true
   mode = "handoff"               # the session's Claude writes the analysis
   ```

   `.gitignore`
   ```
   data/newsletters/
   data/handoff/
   reports/.charts/
   ```
3. **Connect GitHub to Claude** for both repositories, at
   [claude.ai/connect-github](https://claude.ai/connect-github). Install the Claude GitHub App on the
   new repository if asked.

## 2. One-time portfolio import

Start a Claude Code session on claude.ai/code with both repositories selected, upload your broker
export (`Bestandsaufstellung_*.csv`) and ask:

> Import this CSV into boersen-agent-state with `python -m boersen_agent --config
> boersen-agent-state/config.toml portfolio import <file>`, run `symbols list`, fix any wrong
> symbol mappings with `symbols set`, then commit `data/agent.db` to boersen-agent-state and push.

This is where to correct the automatic matches, for example Xetra listings for your EUR ETFs instead
of London ones.

## 3. Cloud environment

Create a new environment in Claude Code on the web (the environment menu in the session's title
bar), named for example **boersen-agent**:

- **Repositories:** `ekallias/playground` and `ekallias/boersen-agent-state`.
- **Network access:** *Limited*, with package managers allowed, and these allowed domains:
  ```
  paperless.kallias.net
  query1.finance.yahoo.com
  query2.finance.yahoo.com
  fred.stlouisfed.org
  api.statistiken.bundesbank.de
  data-api.ecb.europa.eu
  ```
  Add `eodhd.com` if you switch to EODHD.
- **Network secrets:**
  - `paperless.kallias.net`: header `Authorization`, prefix `Token`, your Paperless API token. This
    environment already has it; copy it over or create it again.
  - Optional: `eodhd.com` for the EODHD token.
- **Environment variables** (not secret):
  ```
  PAPERLESS_URL=https://paperless.kallias.net
  PAPERLESS_TOKEN=proxy-injected
  ```
  `PAPERLESS_TOKEN` is only a placeholder; the network proxy inserts the real token.
- **Setup script.** It runs on every fresh container:
  ```bash
  apt-get update -qq && apt-get install -y -qq poppler-utils
  if [ -d playground/boersen-agent ]; then pip install -q -r playground/boersen-agent/requirements.txt; fi
  ```

The secrets stay in the environment, never in the repositories or the prompt.

## 4. The scheduled task

Create a scheduled task that runs **weekdays at 18:15 Europe/Berlin** in the **boersen-agent**
environment. Set it to start a **new session on each run**, with **push notifications** on. Use
this prompt, which has to stand on its own:

```
You run the daily Börsen-Agent job. Work in the cloned repositories playground/ and
boersen-agent-state/.

1. Setup: `cd playground && (git checkout claude/great-knuth-8bh4a7 2>/dev/null || true) && cd boersen-agent`
   (the checkout is only needed while the code is not merged to main), then
   `A="python -m boersen_agent --config ../../boersen-agent-state/config.toml"`.
2. Run `$A prepare`. It prints a manifest with tasks (an "issue" task only when a new
   newsletter arrived, always a "daily" task).
3. For each task: read the manifest's prompt.md (your role and the newsletter's methods) and
   schema.json, the task's facts file, and for the issue task the newsletter PDF it names. Then
   write your analysis as JSON exactly matching schema.json to the task's "output" path.
   Follow prompt.md: base every statement on the facts, give one position call per non-cash
   position, confidence 0-100, and explain any override of a stated rule.
4. Run `$A finish`. It validates your JSON, renders the PDFs and files them in Paperless.
   If it reports problems, fix your JSON and run finish again.
5. In ../../boersen-agent-state: commit data/agent.db and reports/ ("run <date>") and push.
6. Reply with the headline, all SELL/REDUCE/WATCH alerts with confidence, and any problems.
   Send the PDFs to the user. Do not trade, and do not change the portfolio unless asked.
```

Run the task once by hand (*Run now*) and check the session transcript, the PDFs in Paperless, and
the commit in the state repository.

## 5. Recording trades

The agent can't see your broker. After a trade, open a session with both repositories and say, for
example: "Record in boersen-agent: sold 3 000 IE00BF0M2Z96 at 24.70 EUR today, then commit." Claude
runs `portfolio sell …` and pushes the database.

## 6. What to expect

- **Usage:** each run is one Claude Code session.
  - **Daily runs:** short, with facts only.
  - **Issue days:** longer, because Claude also reads the 16-page newsletter.
  - **Where it's counted:** your plan's usage limits, not API billing. If a run is cut off by a limit,
    `finish` still writes the rule-based report.
- **Robustness:**
  - A failed run commits nothing, so the next run starts from the last good state and picks the
    newsletter up again.
  - Yahoo occasionally rate-limits. The agent then uses the last cached close and notes it; EODHD
    removes the problem.
- **Guardrails** are the same as in API mode: calls for unknown securities are dropped, confidence
  is clamped, and overrides of a stated rule show in red.

## Alternative: Claude Managed Agents (API)

If you'd rather not depend on Claude Code sessions, Anthropic's **Managed Agents** can run the same
job. The agent config and a scheduled deployment live on the Claude Platform, with a sandbox per
run, and the agent's `run` command uses `[ai] mode = "api"` with an API key. This is billed as API
usage (about $3–5 a month, see docs/HOMESERVER.md). It needs an Anthropic Console account, an API
key stored in a vault, and the same state-repository approach. Choose it when you want API-level
control, logs and budgets, rather than your Claude plan.

## Checklist

- [ ] Code merged to `main`, or the branch checked out in the prompt.
- [ ] Private `boersen-agent-state` repository with `config.toml`, `.gitignore` and the imported
      `data/agent.db`.
- [ ] Environment: both repositories, allowed domains, Paperless secret, variables, setup script.
- [ ] Scheduled task: weekdays 18:15, new session, push on, prompt from step 4; one manual run checked.
- [ ] Symbol mappings confirmed; trades recorded after each change.
