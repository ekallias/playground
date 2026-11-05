# Running boersen-agent on the home server (LiteLLM · n8n · Open WebUI)

This guide fits the agent into a stack that already runs LiteLLM, Open WebUI and n8n (and
Paperless). Each tool does one job:

| Tool | Role |
|---|---|
| **boersen-agent** (Docker job) | The work: pull the newsletter, compute the rules, call Claude, draw charts, write the PDFs |
| **LiteLLM** | Gateway for the Claude calls: one place for the Anthropic key, budgets and spend tracking |
| **n8n** | Scheduling and delivery: start the job, send the PDFs (Telegram / e-mail), file them |
| **Open WebUI** | Ask questions about the reports afterwards ("why REVIEW on Siemens?") |
| **Paperless** | Source of the newsletters; archive for the reports |

```
n8n (schedule) ──ssh──▶ docker compose run boersen-agent ──▶ Paperless (newsletters)
                                │                         ──▶ market data, FRED/BBK/ECB
                                ├──▶ LiteLLM /anthropic ──▶ Claude API
                                └──▶ /data/reports/*.pdf
n8n ──▶ Telegram / e-mail (PDF) ──▶ Open WebUI knowledge ──▶ Paperless (optional)
```

> **Privacy.** The fact pack sent to Claude contains your positions, quantities, values and P/L,
> and the issue report sends the newsletter PDF. Everything else stays on the server. If that is
> not acceptable, set `[ai] enabled = false`; you still get the rule-based PDFs.

## 1. Build the agent image

On the Docker host, next to the compose file of your stack:

```bash
git clone https://github.com/ekallias/playground /tmp/pg && cp -r /tmp/pg/boersen-agent ./boersen-agent
mkdir -p boersen-agent-data
cp boersen-agent/config.example.toml boersen-agent-data/config.toml
cp boersen-agent/deploy/boersen-agent.env.example boersen-agent.env && chmod 600 boersen-agent.env
cat boersen-agent/deploy/docker-compose.snippet.yml   # merge into your docker-compose.yml
docker compose build boersen-agent
```

Fill in `boersen-agent.env`:

- **`PAPERLESS_URL`:** the internal URL Docker can reach (e.g. `http://paperless:8000` on the same
  network, or the LXC's IP).
- **`PAPERLESS_TOKEN`:** a Paperless API token.
- **`ANTHROPIC_API_KEY` / `ANTHROPIC_BASE_URL`:** see step 2.

Import the portfolio once, then run the job by hand:

```bash
cp Bestandsaufstellung.csv boersen-agent-data/
docker compose run --rm boersen-agent portfolio import /data/Bestandsaufstellung.csv
docker compose run --rm boersen-agent run --json
ls boersen-agent-data/reports/
```

The first run ingests every newsletter already in Paperless and reports on the newest one.

## 2. Route Claude through LiteLLM

The agent uses the official Anthropic SDK. LiteLLM's **Anthropic pass-through** route
(`<proxy>/anthropic`) forwards requests in Anthropic's own format, including the `anthropic-beta`
headers the agent needs for structured output and fallbacks
([LiteLLM docs](https://docs.litellm.ai/docs/pass_through/anthropic_completion.md),
[header forwarding](https://docs.litellm.ai/docs/proxy/forward_client_headers.md)).

1. **Give LiteLLM the Anthropic key.** Your real key goes into LiteLLM's environment
   (`ANTHROPIC_API_KEY` on the LiteLLM container), not into the agent. If you also want the model in
   Open WebUI, add it to LiteLLM's `config.yaml`:

   ```yaml
   model_list:
     - model_name: claude-opus-5-5
       litellm_params:
         model: anthropic/claude-opus-5-5
         api_key: os.environ/ANTHROPIC_API_KEY
   ```

2. **Create a virtual key for the agent.** In the LiteLLM UI (or its `/key/generate` API), create a
   key with a monthly budget, e.g. 15 USD. The agent uses this key instead of the Anthropic key.

3. **Point the agent at the gateway** in `boersen-agent.env`:

   ```bash
   ANTHROPIC_BASE_URL=http://litellm:4000/anthropic
   ANTHROPIC_API_KEY=sk-litellm-virtual-key
   ```

4. **Test it:** run `docker compose run --rm boersen-agent daily`. The PDF's appendix shows the model
   and token counts. LiteLLM's spend log should show the call.

Things to check on your LiteLLM version:

- **Pass-through vs. unified endpoint.** Use the pass-through path. LiteLLM's unified `/v1/messages`
  endpoint has its own `fallbacks` routing parameter, which collides with Anthropic's server-side
  `fallbacks`. If you must use the unified endpoint, set `[ai] server_fallbacks = false`.
- **Budgets.** Confirm that spend and budgets are tracked for pass-through requests in your release;
  if not, set the limit on the Anthropic console as well.

**Expected cost** (Claude Opus 5.5 at $4 / $20 per million tokens; rough estimate, check your
first runs):

| Run | Approx. size | Cost |
|---|---|---|
| Daily brief (`effort_daily = "medium"`) | ~15k tokens in, a few k out | ≈ $0.10–0.20 |
| Issue report (newsletter PDF, `effort_issue = "high"`) | ~50–70k tokens in | ≈ $0.30–0.60 |
| Month (21 trading days + 2 issues) | | ≈ $3–5 |

The system prompt is cached, so repeated runs pay less for it.

**Local model instead of Claude?** If Ollama runs on the same server, see
[OTHER_MODELS.md](OTHER_MODELS.md). The agent then uses `prepare` / `tools/handoff_llm.py` /
`finish` instead of `run`, and nothing leaves your network except market data.

## 3. Schedule and deliver with n8n

n8n runs in its own container, so the simplest way to start a Docker job is an **SSH node** to
the Docker host. Create an SSH credential for a restricted user that may run `docker compose`.

Workflow **"Börsen-Agent daily"**:

1. **Schedule Trigger.** Cron `15 18 * * 1-5` (weekdays 18:15, after the European close).
2. **SSH → Execute Command.**
   `cd /srv/stack && docker compose run --rm boersen-agent run --json`
3. **Code node.** Parse the JSON from stdout:
   ```js
   const r = JSON.parse($json.stdout.trim().split('\n').pop());
   const host = p => p && p.replace('/data/', '/srv/stack/boersen-agent-data/');
   return [{ json: { ...r, daily: host(r.daily_brief), issue: host(r.issue_report) } }];
   ```
4. **SSH → Download File.** Fetch `{{$json.daily}}` (and `{{$json.issue}}` when it isn't null) as
   binary data.
5. **IF** `{{$json.alerts.length}} > 0`.
   - **True:** **Telegram → Send Document** (or e-mail) with the PDF, and the headline plus alerts
     as the caption.
   - **False:** send the PDF quietly, or only on Fridays.
6. **Optional, issue days only:** forward the issue report by e-mail as well.

Error handling: add an **Error Trigger** workflow that messages you when a run fails. The job
also writes its notes (missing data, failed lookups) into the JSON `notes` field.

Alternatively, run the job from a host cron (`deploy/boersen-agent.timer` or cron) and use n8n
only for delivery, with a **Local File Trigger** on the reports folder mounted into the n8n
container.

## 4. Ask questions in Open WebUI

Put the reports into an Open WebUI **Knowledge** collection, so you can chat about them with any
model LiteLLM serves:

1. **Create the collection.** In Open WebUI: *Workspace → Knowledge → +* "Börsen-Agent". Note its id
   (it's in the URL), and create an API key under *Settings → Account*.
2. **Upload the methods once.** Add `boersen-agent/docs/METHODS.md` to the collection, so the model
   knows the rules.
3. **Upload each report from n8n.** After the download step, add three HTTP Request nodes
   ([Open WebUI API](https://docs.openwebui.com/getting-started/api-endpoints/)):
   1. `POST http://open-webui:8080/api/v1/files/`, multipart form, field `file` = the PDF binary,
      header `Authorization: Bearer <key>`. Returns `id`.
   2. Wait until processing is done: poll `GET /api/v1/files/{id}/process/status` until it reports
      completed. Adding a file before that fails with "content is empty".
   3. `POST /api/v1/knowledge/<collection-id>/file/add` with JSON `{"file_id": "<id>"}`.

   Check the payload against your Open WebUI version's API docs (`/docs` on your instance).
4. **Create a model preset** (*Workspace → Models*) "Börsen-Analyst": base model Claude via
   LiteLLM, the Knowledge collection attached, and a short system prompt such as "Answer from the
   attached reports and methods; quote the date of the report you use."
5. **Ask away.** Example questions: "What changed in the Gesamtsystem since August?" or "Which of my
   positions has been closest to its 9-month stop this month?"

## 5. Paperless

Set `upload_reports = true` and `report_tags = [<tag id>]` in `config.toml` to file every PDF
report back into Paperless. The agent uploads them itself; n8n isn't needed for this.

## 6. Checklist

- [ ] `docker compose run --rm boersen-agent run --json` works by hand.
- [ ] `portfolio show` lists your positions; `symbols list` mappings confirmed (`symbols set`).
- [ ] LiteLLM shows the agent's calls under its virtual key, with a budget set.
- [ ] n8n workflow is active; the error workflow is set.
- [ ] Trades are recorded with `portfolio buy` / `portfolio sell`. The agent can't see your broker.
- [ ] `boersen-agent-data/` is in your backup. It holds the database with your history and
      Claude's notes.
