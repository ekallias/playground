"""Market data providers and interest-rate sources, with a SQLite cache.

Providers implement history(), quote() and search(). Pick one in config.toml:
  yahoo  free, unofficial endpoint; can rate-limit or change without notice
  eodhd  paid (EODHD), API token in $EODHD_API_TOKEN; symbols like SAP.XETRA, GDAXI.INDX
  csv    offline: one CSV per symbol (date,close) in a folder; used by the tests
Quotes from both online providers are typically delayed (about 15 minutes, exchange-dependent).
"""
from __future__ import annotations

import csv
import io
import json
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib import error, parse, request

from .rules import Series

USER_AGENT = "boersen-agent/0.1"  # FRED stalls on longer UA strings with parentheses


class DataError(RuntimeError):
    pass


def http_get(url: str, timeout: float = 45, retries: int = 2, headers: dict | None = None) -> bytes:
    # No default Accept header: FRED stalls on this UA combined with "Accept: */*".
    hdrs = {"User-Agent": USER_AGENT, **(headers or {})}
    for attempt in range(retries + 1):
        try:
            with request.urlopen(request.Request(url, headers=hdrs), timeout=timeout) as r:
                return r.read()
        except error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(2 ** (attempt + 1))
                continue
            raise DataError(f"HTTP {e.code} for {url.split('?')[0]}") from None
        except (error.URLError, TimeoutError, OSError) as e:
            if attempt < retries:
                time.sleep(2 ** (attempt + 1))
                continue
            raise DataError(f"{url.split('?')[0]}: {getattr(e, 'reason', e)}") from None
    raise DataError(f"giving up on {url.split('?')[0]}")


@dataclass
class Quote:
    symbol: str
    price: float
    time: str             # ISO timestamp (UTC)
    previous_close: float | None = None
    currency: str | None = None


@dataclass
class Match:
    symbol: str
    name: str
    exchange: str
    currency: str | None = None
    isin: str | None = None


class Provider:
    name = "base"

    def history(self, symbol: str, start: date) -> Series:
        raise NotImplementedError

    def quote(self, symbol: str) -> Quote:
        raise NotImplementedError

    def search(self, query: str) -> list[Match]:
        return []


class YahooProvider(Provider):
    name = "yahoo"
    BASE = "https://query1.finance.yahoo.com/v8/finance/chart/"

    def _chart(self, symbol: str, params: dict) -> dict:
        url = self.BASE + parse.quote(symbol, safe="") + "?" + parse.urlencode(params)
        data = json.loads(http_get(url))
        res = (data.get("chart") or {}).get("result")
        if not res:
            err = (data.get("chart") or {}).get("error") or {}
            raise DataError(f"yahoo: no data for {symbol}: {err.get('description', 'empty result')}")
        return res[0]

    def history(self, symbol: str, start: date) -> Series:
        p1 = int(datetime(start.year, start.month, start.day, tzinfo=timezone.utc).timestamp())
        r = self._chart(symbol, {"period1": p1, "period2": int(time.time()), "interval": "1d"})
        ts = r.get("timestamp") or []
        closes = ((r.get("indicators") or {}).get("quote") or [{}])[0].get("close") or []
        out = [(datetime.fromtimestamp(t, timezone.utc).date().isoformat(), c)
               for t, c in zip(ts, closes) if c is not None]
        return sorted(dict(out).items())

    def quote(self, symbol: str) -> Quote:
        m = self._chart(symbol, {"range": "1d", "interval": "5m"}).get("meta", {})
        if m.get("regularMarketPrice") is None:
            raise DataError(f"yahoo: no quote for {symbol}")
        t = m.get("regularMarketTime")
        return Quote(symbol, float(m["regularMarketPrice"]),
                     datetime.fromtimestamp(t, timezone.utc).isoformat() if t else "",
                     m.get("chartPreviousClose") or m.get("previousClose"), m.get("currency"))

    def search(self, query: str) -> list[Match]:
        url = "https://query2.finance.yahoo.com/v1/finance/search?" + parse.urlencode(
            {"q": query, "quotesCount": 6, "newsCount": 0})
        data = json.loads(http_get(url))
        return [Match(q["symbol"], q.get("shortname") or q.get("longname") or "", q.get("exchange", ""))
                for q in data.get("quotes", []) if q.get("quoteType") in ("EQUITY", "ETF", None)]


class EODHDProvider(Provider):
    """EODHD (eodhd.com). Endpoints per their public docs: /eod, /real-time, /search."""
    name = "eodhd"
    BASE = "https://eodhd.com/api"

    def __init__(self, token: str | None):
        if not token:
            raise DataError("EODHD needs an API token in $EODHD_API_TOKEN")
        self.token = token

    def _get(self, path: str, **params):
        params.update({"api_token": self.token, "fmt": "json"})
        return json.loads(http_get(f"{self.BASE}/{path}?{parse.urlencode(params)}"))

    def history(self, symbol: str, start: date) -> Series:
        rows = self._get(f"eod/{parse.quote(symbol)}", **{"from": start.isoformat(), "period": "d"})
        if not isinstance(rows, list):
            raise DataError(f"eodhd: unexpected history response for {symbol}")
        return [(r["date"], float(r["close"])) for r in rows if r.get("close") not in (None, "NA")]

    def quote(self, symbol: str) -> Quote:
        r = self._get(f"real-time/{parse.quote(symbol)}")
        if not isinstance(r, dict) or r.get("close") in (None, "NA"):
            raise DataError(f"eodhd: no quote for {symbol}")
        ts = r.get("timestamp")
        prev = r.get("previousClose")
        return Quote(symbol, float(r["close"]),
                     datetime.fromtimestamp(int(ts), timezone.utc).isoformat() if ts else "",
                     float(prev) if prev not in (None, "NA") else None)

    def search(self, query: str) -> list[Match]:
        rows = self._get(f"search/{parse.quote(query)}", limit=6)
        return [Match(f"{r['Code']}.{r['Exchange']}", r.get("Name", ""), r.get("Exchange", ""),
                      r.get("Currency"), r.get("ISIN")) for r in rows if isinstance(r, dict)]


class CSVProvider(Provider):
    """Offline provider: <dir>/<symbol>.csv with 'date,close' rows; quote = last row."""
    name = "csv"

    def __init__(self, folder: Path):
        self.folder = Path(folder)

    def _file(self, symbol: str) -> Path:
        return self.folder / (symbol.replace("^", "_").replace("/", "_") + ".csv")

    def history(self, symbol: str, start: date) -> Series:
        f = self._file(symbol)
        if not f.exists():
            raise DataError(f"csv: no file for {symbol}")
        with f.open(newline="") as fh:
            return [(r["date"], float(r["close"])) for r in csv.DictReader(fh) if r["date"] >= start.isoformat()]

    def quote(self, symbol: str) -> Quote:
        h = self.history(symbol, date(1900, 1, 1))
        if not h:
            raise DataError(f"csv: empty file for {symbol}")
        prev = h[-2][1] if len(h) > 1 else None
        return Quote(symbol, h[-1][1], h[-1][0] + "T17:30:00+00:00", prev)

    def search(self, query: str) -> list[Match]:
        idx = self.folder / "_search.csv"   # optional: query,symbol,name
        if not idx.exists():
            return []
        with idx.open(newline="") as fh:
            return [Match(r["symbol"], r["name"], "csv") for r in csv.DictReader(fh) if r["query"] == query]


def make_provider(cfg) -> Provider:
    if cfg.provider == "eodhd":
        return EODHDProvider(cfg.provider_token)
    if cfg.provider == "csv":
        return CSVProvider(cfg.data_dir / "csv")
    return YahooProvider()


# ------------------------------------------------------------------ interest rates (free sources)
def fred(series_id: str, start: date) -> Series:
    url = "https://fred.stlouisfed.org/graph/fredgraph.csv?" + parse.urlencode(
        {"id": series_id, "cosd": start.isoformat()})
    rows = csv.reader(io.StringIO(http_get(url).decode()))
    next(rows, None)
    return [(r[0], float(r[1])) for r in rows if len(r) == 2 and r[1] not in ("", ".")]


def bundesbank_umlaufrendite(start: date) -> Series:
    url = ("https://api.statistiken.bundesbank.de/rest/data/BBSIS/D.I.UMR.RD.EUR.A.B.A.A.R.A.A._Z._Z.A?"
           + parse.urlencode({"format": "csv", "startPeriod": start.isoformat()}))
    out = []
    for line in http_get(url).decode("utf-8-sig").splitlines():
        parts = line.split(";")
        if len(parts) >= 2 and len(parts[0]) == 10 and parts[0][4] == "-" and parts[1] not in ("", "."):
            out.append((parts[0], float(parts[1].replace(",", "."))))
    return out


def ecb_euribor_12m(start: date) -> Series:
    """Monthly average of Euribor 12M, dated at the first day of the month after (no look-ahead)."""
    url = ("https://data-api.ecb.europa.eu/service/data/FM/M.U2.EUR.RT.MM.EURIBOR1YD_.HSTA?"
           + parse.urlencode({"format": "csvdata", "startPeriod": f"{start.year}-{start.month:02d}"}))
    out = []
    for r in csv.DictReader(io.StringIO(http_get(url).decode())):
        y, m = map(int, r["TIME_PERIOD"].split("-"))
        nxt = date(y + (m == 12), m % 12 + 1, 1)
        out.append((nxt.isoformat(), float(r["OBS_VALUE"])))
    return out


RATE_SOURCES = {
    "FRED:DGS10": lambda s: fred("DGS10", s),
    "FRED:DGS1": lambda s: fred("DGS1", s),
    "BBK:UMLAUFRENDITE": bundesbank_umlaufrendite,
    "ECB:EURIBOR12M": ecb_euribor_12m,
}


# ------------------------------------------------------------------ cached access
class Market:
    """Cached access to price histories, quotes and rates. Errors are collected, not raised, so one
    missing symbol never stops a report."""

    def __init__(self, store, provider: Provider, history_years: int = 3, today: date | None = None):
        self.store, self.provider = store, provider
        self.today = today or date.today()
        self.start = self.today - timedelta(days=365 * history_years + 30)
        self.errors: list[str] = []
        self._quotes: dict[str, Quote | None] = {}

    def _refresh(self, series: str, fetch) -> None:
        if self.store.fetched_on(series) == self.today.isoformat():
            return
        have = self.store.prices(series)
        start = self.start if not have else date.fromisoformat(have[-1][0]) - timedelta(days=10)
        try:
            pts = fetch(start)
        except DataError as e:
            self.errors.append(str(e))
            return
        self.store.save_prices(series, pts, self.today.isoformat())

    def history(self, symbol: str) -> Series:
        key = f"{self.provider.name}:{symbol}"
        self._refresh(key, lambda s: self.provider.history(symbol, s))
        return self.store.prices(key, self.start.isoformat())

    def rate(self, series: str) -> Series:
        self._refresh(series, RATE_SOURCES[series])
        return self.store.prices(series, self.start.isoformat())

    def quote(self, symbol: str) -> Quote | None:
        """Live quote; if the provider fails (e.g. rate limit), the last cached daily close is used
        instead and the fallback is noted."""
        if symbol not in self._quotes:
            try:
                self._quotes[symbol] = self.provider.quote(symbol)
            except DataError as e:
                h = self.store.prices(f"{self.provider.name}:{symbol}")
                if h:
                    prev = h[-2][1] if len(h) > 1 else None
                    self._quotes[symbol] = Quote(symbol, h[-1][1], h[-1][0] + "T00:00:00+00:00", prev)
                    self.errors.append(f"{e}; used last close of {h[-1][0]} for {symbol}")
                else:
                    self.errors.append(str(e))
                    self._quotes[symbol] = None
        return self._quotes[symbol]

    def daily_with_quote(self, symbol: str) -> Series:
        """Daily history with today's live quote appended (or replacing today's row)."""
        h = list(self.history(symbol))
        q = self.quote(symbol)
        if q:
            day = (q.time[:10] or self.today.isoformat())
            if h and h[-1][0] == day:
                h[-1] = (day, q.price)
            elif not h or h[-1][0] < day:
                h.append((day, q.price))
        return h
