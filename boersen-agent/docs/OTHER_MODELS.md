# Using a different model provider (Ollama, LM Studio, vLLM, OpenRouter …)

The agent is split so that any model can write the analysis. **Scripts** prepare the facts and
render the PDFs; the **model** only has to return one JSON object matching
`schema.json`. `finish` then validates that object with the same guardrails as the Claude path:

- calls for unknown securities are dropped,
- confidence is clamped to 0–100,
- overrides of a stated newsletter rule are highlighted,
- a missing or invalid answer falls back to the rule-based report.

So you can swap Claude for a local model without touching the rules or the reports.

```
python -m boersen_agent prepare        → handoff/manifest.json, prompt.md, schema.json,
                                          *_facts.json, issue-*_newsletter.txt
python tools/handoff_llm.py …          → your model writes *_analysis.json
python -m boersen_agent finish         → validated PDFs (+ Paperless upload)
```

## Option A — Ollama (recommended way for local models)

Set `mode = "handoff"` under `[ai]` in `config.toml`, then run:

```bash
A="python -m boersen_agent --config /data/config.toml"
$A prepare
python tools/handoff_llm.py --manifest /data/data/handoff/manifest.json \
    --api ollama --url http://ollama:11434 --model qwen3:32b --ctx 32768
$A finish --model "ollama qwen3:32b"
```

- **Structured output.** `handoff_llm.py` uses Ollama's native `/api/chat` with `"format": <schema>`,
  which constrains the answer to the schema.
- **Newsletter as text.** Most local models can't read PDFs, so `prepare` also writes the issue as
  text (`issue-*_newsletter.txt`, pages 1–8: commentary, dashboard and Dispo-Liste), and the tool
  sends that instead of the PDF.
- **Context window.** An issue run is roughly 20–25k tokens (facts + newsletter text + prompt); a
  daily run about 8–12k. Ollama **silently truncates** prompts longer than `num_ctx`, so keep
  `--ctx` at 32768 or more. Raise it if answers ignore positions.
- **Model choice.**
  - **What it needs:** good instruction following, reliable JSON, German reading, and a 32k+
    context. A 24–70B instruction model is a reasonable start (e.g. a Qwen 3, Mistral Small or
    Llama 3.3 class model).
  - **What it runs on:** pick what your hardware runs at an acceptable speed.
  - **Untested:** none of these has been tested with this agent; compare a few runs with a Claude
    run before you trust it.
- **Docker / compose.** If Ollama runs in the same compose stack, use `http://ollama:11434`. On
  the host, use `http://localhost:11434`.

### In n8n

Replace the single `run --json` step of [HOMESERVER.md](HOMESERVER.md) with three commands in the
SSH node (or three SSH nodes):

```bash
cd /srv/stack
docker compose run --rm boersen-agent prepare
docker compose run --rm --entrypoint python boersen-agent tools/handoff_llm.py \
  --manifest /data/data/handoff/manifest.json --api ollama --url http://ollama:11434 --model qwen3:32b
docker compose run --rm boersen-agent finish --model "ollama qwen3:32b"
```

`finish` prints the same JSON summary (`reports`, `headline`, `alerts`, `problems`) that the rest of
the workflow already uses.

## Option B — any OpenAI-compatible endpoint

LM Studio, vLLM, llama.cpp server, LiteLLM's OpenAI route and OpenRouter all speak
`/v1/chat/completions`:

```bash
export LLM_API_KEY=...            # only if the endpoint needs one
python tools/handoff_llm.py --manifest …/manifest.json --api openai \
    --url http://litellm:4000 --model my-local-model
```

The tool asks for `response_format: json_schema`. Endpoints that don't support it may return
free text. The tool rejects output that isn't valid JSON, and `finish` then falls back to the
rule-based report and says so.

**Through LiteLLM.** If you route through LiteLLM, keep the model behind a LiteLLM alias. That gives
you one place for the keys, the budget and the spend log, whichever model is behind the alias.

## Option C — keep `run`, translate in LiteLLM (not recommended)

LiteLLM's unified Anthropic-format endpoint can translate the agent's Claude API calls to other
providers: set `ANTHROPIC_BASE_URL` to the LiteLLM root (not `/anthropic`), name a LiteLLM model
alias in `[ai] model`, and set `server_fallbacks = false`. It's convenient, but untested, and three
Claude-specific features may not translate:

- schema-constrained output (`output_config.format`),
- adaptive thinking,
- the newsletter PDF as a document block.

Prefer Option A or B: the schema is enforced by the model server, and the newsletter is sent as text.

## What changes in quality

- **Guardrails apply unchanged.** `finish` drops unknown securities and flags rule overrides, but it
  cannot judge whether the reasoning is good.
- **Confidence calibration.** Local models tend to be less calibrated. The rule engine's own score
  sits next to every AI call in the PDF, so large gaps are easy to spot.
- **Language.** The prompt and schema are English; the newsletter is German. Check that rationales
  quote the right levels.
- **Recommendation.** Run Claude and the local model side by side for a few days (`finish` can be
  re-run with a different `analysis.json`) before switching.

## Privacy

With Ollama or another self-hosted model, nothing leaves your network except the market-data and
interest-rate requests.
