"""Fill boersen-agent hand-off tasks with another model provider (Ollama, or any OpenAI-compatible API).

    python -m boersen_agent --config CONFIG prepare
    python tools/handoff_llm.py --manifest DATA/handoff/manifest.json --api ollama \
        --url http://localhost:11434 --model qwen3:32b
    python -m boersen_agent --config CONFIG finish --model "ollama qwen3:32b"

--api ollama   POST {url}/api/chat with "format": <JSON schema> (Ollama structured outputs)
--api openai   POST {url}/v1/chat/completions with response_format json_schema (LM Studio, vLLM,
               LiteLLM's OpenAI route, OpenRouter, ...); key from $LLM_API_KEY if set
The newsletter is sent as extracted text (most local models cannot read PDFs). `finish` validates
the result with the same guardrails as the Claude path.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from urllib import error, request


def build_messages(prompt: str, task: dict) -> list[dict]:
    facts = Path(task["facts"]).read_text(encoding="utf-8")
    parts = [("Write the biweekly issue report: read the newsletter text, check its recommendations against "
              "the live facts, and apply it to the portfolio." if task["scope"] == "issue" else
              "Write today's brief for the portfolio."),
             "Answer with one JSON object that matches the given schema, nothing else."]
    if task.get("newsletter_text") and Path(task["newsletter_text"]).exists():
        parts.append("Newsletter (extracted text, German):\n" + Path(task["newsletter_text"]).read_text(encoding="utf-8"))
    parts.append(f"Fact pack:\n```json\n{facts}\n```")
    return [{"role": "system", "content": prompt}, {"role": "user", "content": "\n\n".join(parts)}]


def post(url: str, body: dict, key: str | None, timeout: float) -> dict:
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    req = request.Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")
    try:
        with request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except error.HTTPError as e:
        raise SystemExit(f"{url}: HTTP {e.code}: {e.read()[:300]!r}") from None
    except error.URLError as e:
        raise SystemExit(f"{url}: {e.reason}") from None


def complete(api: str, url: str, model: str, messages: list[dict], schema: dict, ctx: int, key: str | None,
             timeout: float) -> str:
    base = url.rstrip("/")
    if api == "ollama":
        r = post(base + "/api/chat", {"model": model, "messages": messages, "format": schema, "stream": False,
                                       "options": {"num_ctx": ctx, "temperature": 0.2}}, key, timeout)
        return r["message"]["content"]
    r = post(base + "/v1/chat/completions",
             {"model": model, "messages": messages, "temperature": 0.2,
              "response_format": {"type": "json_schema",
                                  "json_schema": {"name": "analysis", "schema": schema, "strict": True}}},
             key, timeout)
    return r["choices"][0]["message"]["content"]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--api", choices=["ollama", "openai"], default="ollama")
    ap.add_argument("--url", default="http://localhost:11434")
    ap.add_argument("--model", required=True)
    ap.add_argument("--ctx", type=int, default=32768, help="context window for Ollama (num_ctx)")
    ap.add_argument("--timeout", type=float, default=900)
    args = ap.parse_args(argv)

    man = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    prompt = Path(man["prompt"]).read_text(encoding="utf-8")
    schema = json.loads(Path(man["schema"]).read_text(encoding="utf-8"))
    key = os.environ.get("LLM_API_KEY")
    for t in man["tasks"]:
        text = complete(args.api, args.url, args.model, build_messages(prompt, t), schema, args.ctx, key, args.timeout)
        text = text.strip()
        if text.startswith("```"):
            text = text.strip("`").split("\n", 1)[1].rsplit("```", 1)[0]
        try:
            json.loads(text)
        except json.JSONDecodeError as e:
            print(f"{t['scope']}: model output is not valid JSON ({e}); finish will fall back to rules", file=sys.stderr)
            continue
        Path(t["output"]).write_text(text, encoding="utf-8")
        print(f"{t['scope']}: wrote {t['output']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
