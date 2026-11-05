"""The AI analyst: Claude reads the facts the scripts computed (and, for an issue report, the
newsletter PDF itself) and writes the judgment: market view, a call per position with confidence,
a verdict on each newsletter pick, new ideas, risks and questions.

Division of labour:
  scripts  -> data, rules, numbers, charts, PDF (deterministic, repeatable)
  Claude   -> interpretation, weighing conflicts, confidence, reasoning (structured JSON)
Guardrails here make sure Claude only talks about securities in the fact pack, that confidences
are 0-100, and that any override of a stated newsletter rule is flagged in the report.
"""
from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from pathlib import Path

import anthropic

METHODS_PATH = Path(__file__).resolve().parents[1] / "docs" / "METHODS.md"
FALLBACK_BETA = "server-side-fallback-2026-07-01"
ACTIONS = ["SELL", "REDUCE", "WATCH", "REVIEW", "HOLD", "ADD", "BUY"]

SYSTEM_PROMPT = """You are the analyst behind a private investor's daily portfolio brief. The investor \
follows the German newsletter "Börsensignale" (Uwe Lang / Klaus Haidorfer) and wants its methods applied \
to their own portfolio, with your judgment on top.

Scripts have already done the mechanical work: they parsed the newsletter, fetched market data, \
evaluated the newsletter's rules and valued the portfolio. You receive those results as a JSON fact \
pack, and for issue reports also the newsletter PDF. Your job is the part the scripts cannot do: read \
the situation as a whole, weigh conflicting signals, decide what each signal means for each position, \
and say how sure you are.

How to work:
- Base every statement on the fact pack or the newsletter. Do not invent prices, levels, dates or \
company news. If something you would need is missing, say so and lower your confidence.
- The rule engine's suggestion for each security is a strong prior, not the answer. Agree, add \
nuance, or override it, and say which in `rule_agreement`. An override of a rule marked [V] (stated by \
the newsletter) needs a concrete reason in `override_reason`; the 9-month-low sell rule is the \
newsletter's most important rule, so override it only with a very good reason.
- Confidence is 0-100 and means how strongly the evidence supports the call. Use 70+ only when the \
rules, live data and the newsletter point the same way and the data is complete. Without live price \
data, stay at 50 or below. Disagreement between sources should pull confidence down.
- Funds and ETFs only get the price rules; bonds and cash are cash-like and need no call.
- Be concrete and brief: name the rule, the level, the number. The investor reads this on a phone \
before deciding; one or two sentences per rationale is right.
{language_rule}
- This is decision support for the investor's own account; they decide and trade themselves. Do not \
add generic disclaimers - the report carries one.
- Use `notes_for_next_run` for anything you want to remember next time (open questions, levels to \
watch). It is shown to you in the next run as `previous_ai_notes`.

The newsletter's methods as reconstructed and implemented by the scripts:

"""


def _call(extra: dict | None = None) -> dict:
    props = {
        "key": {"type": "string", "description": "the `key` of the security in the fact pack"},
        "action": {"type": "string", "enum": ACTIONS},
        "confidence": {"type": "integer"},
        "rationale": {"type": "string"},
        "rule_agreement": {"type": "string", "enum": ["agrees", "adds_nuance", "overrides"]},
        "override_reason": {"type": "string", "description": "empty unless rule_agreement is overrides"},
    }
    props.update(extra or {})
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string", "description": "one line, the single most important point today"},
        "summary": {"type": "string", "description": "3-6 sentences: market, portfolio, what to do"},
        "market_view": {
            "type": "object",
            "properties": {
                "stance": {"type": "string", "enum": ["risk_on", "neutral", "cautious", "risk_off"]},
                "confidence": {"type": "integer"},
                "reasoning": {"type": "string"},
            },
            "required": ["stance", "confidence", "reasoning"],
            "additionalProperties": False,
        },
        "position_calls": {"type": "array", "items": _call(),
                           "description": "one entry per non-cash position in portfolio.positions"},
        "newsletter_picks": {
            "type": "array",
            "description": "issue reports only (else empty): one entry per item in newsletter_picks",
            "items": _call({"verdict": {"type": "string", "enum": ["confirmed", "questionable", "contradicted"]}}),
        },
        "new_ideas": {
            "type": "array",
            "description": "issue reports only (else empty): candidates worth a closer look, best first",
            "items": _call(),
        },
        "risks": {"type": "array", "items": {"type": "string"}},
        "questions_for_user": {"type": "array", "items": {"type": "string"}},
        "notes_for_next_run": {"type": "string"},
    },
    "required": ["headline", "summary", "market_view", "position_calls", "newsletter_picks", "new_ideas",
                 "risks", "questions_for_user", "notes_for_next_run"],
    "additionalProperties": False,
}


def system_prompt() -> str:
    """Role + house rules (in the configured report language) + the methods document."""
    from . import i18n
    methods = METHODS_PATH.read_text(encoding="utf-8") if METHODS_PATH.exists() else ""
    return SYSTEM_PROMPT.replace("{language_rule}", i18n.ai_language_rule()) + methods


@dataclass
class Analysis:
    data: dict
    model: str
    usage: dict
    warnings: list[str] = field(default_factory=list)

    def calls_by_key(self) -> dict[str, dict]:
        out = {}
        for section in ("position_calls", "newsletter_picks", "new_ideas"):
            for c in self.data.get(section, []):
                out.setdefault(c["key"], c)
        return out


class AnalystError(RuntimeError):
    pass


# ------------------------------------------------------------------ fact pack
def _sug(s, value=None) -> dict:
    lv = s.live or {}
    u = s.universe or {}
    return {
        "key": s.key, "name": s.name, "isin": s.isin, "symbol": s.symbol,
        "newsletter_rec": s.newsletter_rec,
        "rule_engine": {"action": s.action, "confidence": s.confidence, "check": s.verification,
                        "reasons": [f"{r['text']}" + (f" [{r['marker']}]" if r["marker"] else "") +
                                    (f" ({r['pts']:+d})" if r["pts"] else "") for r in s.reasons]},
        "live": None if not lv else {k: (round(v, 4) if isinstance(v, float) else v) for k, v in lv.items()},
        "stock_list": None if not u else {k: u.get(k) for k in ("list", "list_rank", "global_rank", "rs", "ht", "kuv", "kbv")},
        **(value or {}),
    }


def build_facts(ctx, scope: str, now, changes: list[str] | None = None, previous: dict | None = None) -> dict:
    ms, iss = ctx.market, ctx.issue
    port_rows = {r["key"]: r for r in ctx.portfolio["rows"]}
    held = [s for s in ctx.suggestions if s.in_portfolio]
    facts = {
        "scope": scope,
        "as_of": now.isoformat(timespec="minutes"),
        "newsletter": None if not iss else {
            "issue": iss["issue_key"], "date": iss["issue_date"],
            "dashboard": ctx.dashboard, "model_portfolio": json.loads(iss["depot"]) if iss.get("depot") else None,
            "calls_in_text": {k: [r["name"] for r in ctx.issue_rows["calls"] if r["kind"] == k] for k in ("buy", "sell")},
        },
        "market": {
            "gesamtsystem_now": ms.gesamt_score, "other_five_now": ms.other5_score,
            "sicherheitssystem_core": ms.safety_core.state, "sicherheitssystem_detail": ms.safety_core.detail,
            "strict_summer_exit_active": ms.safety_strict, "season_positive": ms.season_positive,
            "components": [{"key": c.key, "label": c.label, "now": c.state, "newsletter": c.newsletter,
                            "source": c.source, "rule_marker": c.marker, "detail": c.detail}
                           for c in ms.components.values()],
            "levels": {k: (round(v, 2) if isinstance(v, float) else v) for k, v in ms.levels.items()},
            "provisional": ms.provisional, "notes": ms.notes,
        },
        "portfolio": {
            "total_eur": ctx.portfolio["total_eur"] or ctx.portfolio["known_total_eur"],
            "total_complete": ctx.portfolio["total_eur"] is not None,
            "equity_share_pct": ctx.portfolio["equity_share"],
            "cash_like": [{"name": r["name"], "asset": r.get("asset"), "value_eur": r["value_eur"]}
                          for r in ctx.portfolio["rows"] if r.get("cash") or r.get("asset") == "bond"],
            "positions": [_sug(s, {"asset": port_rows.get(s.key, {}).get("asset"),
                                   "value_eur": port_rows.get(s.key, {}).get("value_eur"),
                                   "pnl_pct": port_rows.get(s.key, {}).get("pnl_pct")}) for s in held],
        },
        "data_gaps": list(dict.fromkeys(ctx.notes)),
        "previous_ai_notes": (previous or {}).get("notes_for_next_run"),
        "previous_calls": {c["key"]: f"{c['action']} {c['confidence']}"
                           for c in (previous or {}).get("position_calls", [])},
    }
    if scope == "issue":
        facts["newsletter_picks"] = [_sug(s) for s in ctx.suggestions if s.newsletter_rec and not s.in_portfolio]
        facts["candidates"] = [_sug(s) for s in ctx.suggestions if s.newsletter_rec is None and not s.in_portfolio]
        facts["changes_since_last_issue"] = changes or []
    return facts


# ------------------------------------------------------------------ guardrails
def validate(data: dict, facts: dict) -> tuple[dict, list[str]]:
    warn: list[str] = []
    pos = {p["key"]: p for p in facts["portfolio"]["positions"]}
    picks = {p["key"]: p for p in facts.get("newsletter_picks", [])}
    cands = {p["key"]: p for p in facts.get("candidates", [])}
    allowed = {"position_calls": pos, "newsletter_picks": picks, "new_ideas": cands}

    def clamp(v):
        try:
            return max(0, min(100, int(v)))
        except (TypeError, ValueError):
            return 0

    mv = data.get("market_view", {})
    mv["confidence"] = clamp(mv.get("confidence"))
    for section, valid in allowed.items():
        kept = []
        for c in data.get(section, []):
            if c.get("key") not in valid:
                warn.append(f"dropped {section} entry for unknown key {c.get('key')!r}")
                continue
            c["confidence"] = clamp(c.get("confidence"))
            rule = valid[c["key"]]["rule_engine"]
            c["rule_action"], c["rule_confidence"] = rule["action"], rule["confidence"]
            main = rule["reasons"][0] if rule["reasons"] else ""
            fired = rule["action"] != "HOLD" and "[V]" in main          # a stated newsletter rule fired
            c["overrides_stated_rule"] = fired and c["action"] != rule["action"]
            c["overrides_9m_sell"] = (rule["action"] == "SELL" and "9-month low" in main
                                      and c["action"] not in ("SELL", "REDUCE"))
            if c["overrides_stated_rule"] and not c.get("override_reason"):
                c["override_reason"] = "(no reason given)"
                warn.append(f"{section}: override without reason for {c['key']}")
            kept.append(c)
        data[section] = kept
    missing = sorted(set(pos) - {c["key"] for c in data.get("position_calls", [])})
    if missing:
        warn.append("no AI call for: " + ", ".join(pos[k]["name"] for k in missing))
    return data, warn


# ------------------------------------------------------------------ the call
def analyse(facts: dict, *, model: str, effort: str, pdf: Path | None = None, client=None,
            max_tokens: int = 32000, base_url: str | None = None, server_fallbacks: bool = True) -> Analysis:
    """One structured Claude call. Raises AnalystError on refusal, truncation or API errors.
    Credentials: ANTHROPIC_API_KEY (a LiteLLM virtual key when going through a gateway);
    base URL: `base_url` or ANTHROPIC_BASE_URL."""
    system = system_prompt()
    content: list[dict] = []
    if pdf is not None and Path(pdf).exists():
        content.append({"type": "document", "title": f"Börsensignale {facts['newsletter']['issue']}",
                        "source": {"type": "base64", "media_type": "application/pdf",
                                   "data": base64.standard_b64encode(Path(pdf).read_bytes()).decode()}})
    task = ("Write the biweekly issue report: read the attached issue, check its recommendations against "
            "the live facts, and apply it to the portfolio." if facts["scope"] == "issue" else
            "Write today's brief for the portfolio.")
    content.append({"type": "text", "text": f"{task}\n\nFact pack:\n```json\n"
                    f"{json.dumps(facts, ensure_ascii=False, indent=1, default=str)}\n```"})
    try:
        client = client or (anthropic.Anthropic(base_url=base_url) if base_url else anthropic.Anthropic())
        fb = {"betas": [FALLBACK_BETA], "fallbacks": "default"} if server_fallbacks else {}
        with client.beta.messages.stream(
            model=model,
            max_tokens=max_tokens,
            **fb,                                     # server-side fallback if a request is declined
            thinking={"type": "adaptive"},
            output_config={"effort": effort, "format": {"type": "json_schema", "schema": SCHEMA}},
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": content}],
        ) as stream:
            msg = stream.get_final_message()
    except TypeError as e:  # raised by the SDK when no credentials are configured at all
        if "authentication" not in str(e).lower():
            raise
        raise AnalystError("no Anthropic credentials (set ANTHROPIC_API_KEY, or ANTHROPIC_BASE_URL for a gateway)") from e
    except anthropic.AuthenticationError as e:
        raise AnalystError("Anthropic API key missing or invalid (set ANTHROPIC_API_KEY)") from e
    except anthropic.RateLimitError as e:
        raise AnalystError("Anthropic API rate limit reached; try again later") from e
    except anthropic.APIStatusError as e:
        raise AnalystError(f"Anthropic API error {e.status_code}: {e.message}") from e
    except anthropic.APIConnectionError as e:
        raise AnalystError(f"cannot reach the Anthropic API: {e}") from e

    if msg.stop_reason == "refusal":
        cat = getattr(getattr(msg, "stop_details", None), "category", None)
        raise AnalystError(f"the model declined the request (category: {cat})")
    if msg.stop_reason == "max_tokens":
        raise AnalystError("analysis was cut off (max_tokens); raise ai.max_tokens")
    text = next((b.text for b in msg.content if b.type == "text"), None)
    if not text:
        raise AnalystError("no analysis text in the response")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise AnalystError(f"analysis is not valid JSON: {e}") from e
    data, warnings = validate(data, facts)
    u = msg.usage
    usage = {k: getattr(u, k, None) for k in ("input_tokens", "output_tokens", "cache_read_input_tokens",
                                             "cache_creation_input_tokens")}
    return Analysis(data, getattr(msg, "model", model), usage, warnings)
