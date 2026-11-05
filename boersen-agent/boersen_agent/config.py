"""Configuration: TOML file plus environment variables for secrets."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Rule parameters. Values marked [U] are not stated in the newsletter and are best guesses;
# calibrate them with tools/calibrate.py against the published dashboard states.
DEFAULT_RULES: dict[str, Any] = {
    "nine_month_weeks": 39,        # [V] 9-month low on a weekly close = sell at the latest
    "rs_months": 15,               # [V] RS = price * 100 / 15-month median
    "rs_top_n": 200,               # [V] buy only from the 200 strongest
    "candidate_top_n": 30,         # how many top-RS stocks to consider for new ideas
    "max_new_candidates": 10,
    "kuv_normal_max": 2.0,         # [V] p9: "Normal wäre ein KUV von 0,6 bis 2,0"
    "kbv_fair_max": 4.0,           # [V] 8 Jun: KBV up to 4 for financials
    "near_stop_pct": 3.0,          # warn when price is within this % of the 9-month stop
    "stale_newsletter_days": 16,   # issues come every 14 days
    "no_new_high_weeks": 52,       # [V] 12+ months without a new high -> review
    "zins_smoothing_weeks": 22,    # [V] 17 Aug
    "nasdaq_sell_weeks": 26,       # [V] 16 Mar
    "nasdaq_buy_weeks": 26,        # [U]
    "dju_sell_weeks": 6,           # [V] 30 Mar
    "dju_buy_weeks": 9,            # [V] 22 Jun / 6 Jul
    "dax_sell_weeks": 6,           # [V] 22 Jun
    "dax_buy_weeks": 6,            # [U]
    "bond_weeks": 39,              # [V] 3 Aug (series [U]: default US 10y)
    "oil_weeks": 6,                # [V] negative on a new 6-week high; positive rule [U]
    "dollar_weeks": 15,            # [I] buy on a 15-week high; sell rule [U]
}

DEFAULT_SYMBOLS = {
    "yahoo": {
        "sp500": "^GSPC", "dax": "^GDAXI", "nasdaq": "^IXIC", "dow_utility": "^DJU",
        "brent": "BZ=F", "eurusd": "EURUSD=X", "fx_pattern": "EUR{cur}=X",
    },
    # Verify these in the EODHD docs / their symbol search before relying on them.
    "eodhd": {
        "sp500": "GSPC.INDX", "dax": "GDAXI.INDX", "nasdaq": "IXIC.INDX", "dow_utility": "DJU.INDX",
        "brent": "BZ.COMM", "eurusd": "EURUSD.FOREX", "fx_pattern": "EUR{cur}.FOREX",
    },
}


@dataclass
class Config:
    data_dir: Path
    reports_dir: Path
    paperless_url: str | None
    paperless_token: str | None
    correspondent: str
    upload_reports: bool
    report_tags: list[int]
    provider: str
    provider_token: str | None
    auto_lookup: bool
    history_years: int
    symbols: dict[str, str]
    rules: dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_RULES))
    ai_enabled: bool = True
    ai_mode: str = "api"                    # api: call Claude via the API | handoff: prepare/finish for a Claude session
    ai_model: str = "claude-opus-5-5"
    ai_effort_daily: str = "medium"
    ai_effort_issue: str = "high"
    ai_max_tokens: int = 32000
    ai_base_url: str | None = None          # e.g. LiteLLM pass-through: http://litellm:4000/anthropic
    ai_server_fallbacks: bool = True        # Anthropic server-side refusal fallback (turn off if a gateway rejects it)
    keep_markdown: bool = True

    @property
    def db_path(self) -> Path:
        return self.data_dir / "agent.db"

    def rule(self, key: str):
        return self.rules[key]


def load(path: str | Path | None) -> Config:
    raw: dict[str, Any] = {}
    base = Path.cwd()
    if path:
        p = Path(path)
        raw = tomllib.loads(p.read_text(encoding="utf-8"))
        base = p.resolve().parent
    paths = raw.get("paths", {})
    pl = raw.get("paperless", {})
    mk = raw.get("market", {})
    provider = mk.get("provider", "yahoo")
    if provider not in DEFAULT_SYMBOLS and provider != "csv":
        raise ValueError(f"unknown market.provider {provider!r} (yahoo, eodhd or csv)")
    symbols = dict(DEFAULT_SYMBOLS.get(provider, DEFAULT_SYMBOLS["yahoo"]))
    symbols.update(mk.get("symbols", {}))
    rules = dict(DEFAULT_RULES)
    unknown = set(raw.get("rules", {})) - set(DEFAULT_RULES)
    if unknown:
        raise ValueError(f"unknown rule keys in config: {sorted(unknown)}")
    rules.update(raw.get("rules", {}))

    def rel(v: str) -> Path:
        q = Path(v).expanduser()
        return q if q.is_absolute() else base / q

    token_env = mk.get("token_env", "EODHD_API_TOKEN")
    ai = raw.get("ai", {})
    return Config(
        data_dir=rel(paths.get("data_dir", "data")),
        reports_dir=rel(paths.get("reports_dir", "reports")),
        paperless_url=os.environ.get(pl.get("url_env", "PAPERLESS_URL")) or pl.get("url"),
        paperless_token=os.environ.get(pl.get("token_env", "PAPERLESS_TOKEN")),
        correspondent=pl.get("correspondent", "Börsen Signale"),
        upload_reports=bool(pl.get("upload_reports", False)),
        report_tags=list(pl.get("report_tags", [])),
        provider=provider,
        provider_token=os.environ.get(token_env),
        auto_lookup=bool(mk.get("auto_lookup", True)),
        history_years=int(mk.get("history_years", 3)),
        symbols=symbols,
        rules=rules,
        ai_enabled=bool(ai.get("enabled", True)),
        ai_mode=ai.get("mode", "api"),
        ai_model=ai.get("model", "claude-opus-5-5"),
        ai_effort_daily=ai.get("effort_daily", "medium"),
        ai_effort_issue=ai.get("effort_issue", "high"),
        ai_max_tokens=int(ai.get("max_tokens", 32000)),
        ai_base_url=ai.get("base_url") or None,
        ai_server_fallbacks=bool(ai.get("server_fallbacks", True)),
        keep_markdown=bool(paths.get("keep_markdown", True)),
    )
