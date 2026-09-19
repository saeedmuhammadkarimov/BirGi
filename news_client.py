"""Free crypto news via public RSS feeds — no API key, no rate limits.

CoinDesk and Cointelegraph both publish RSS 2.0 feeds. We fetch both,
filter items to the currency we're trading (by matching the ticker symbol
or its full name in the title/description), and hand Claude a compact list.

Unlike the old CryptoPanic client this has no community vote counts —
sentiment is inferred by Claude from the title tone itself. Feeds return
general crypto news, so a symbol we don't recognize (rare altcoin) just
gets an empty list and the bot proceeds without news.
"""

from __future__ import annotations

import re
import urllib.request
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree


FEEDS = [
    ("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("Cointelegraph", "https://cointelegraph.com/rss"),
]

# Ticker → list of names/aliases to match in headlines (case-insensitive).
# Symbol itself is always matched as a whole word, in addition to these.
CURRENCY_NAMES: dict[str, list[str]] = {
    "BTC": ["bitcoin"],
    "ETH": ["ethereum", "ether"],
    "SOL": ["solana"],
    "BNB": ["binance coin", "bnb"],
    "XRP": ["ripple", "xrp"],
    "ADA": ["cardano"],
    "DOGE": ["dogecoin"],
    "AVAX": ["avalanche"],
    "DOT": ["polkadot"],
    "MATIC": ["polygon", "matic"],
    "LINK": ["chainlink"],
    "LTC": ["litecoin"],
    "UNI": ["uniswap"],
    "ATOM": ["cosmos"],
    "TRX": ["tron"],
    "SHIB": ["shiba inu", "shib"],
    "NEAR": ["near protocol", "near"],
    "APT": ["aptos"],
    "ARB": ["arbitrum"],
    "OP": ["optimism"],
}


class NewsClient:
    """Kept name-compatible with the old CryptoPanic version so bot.py is unchanged."""

    def __init__(self, token: str = "") -> None:
        # token kept for signature compatibility; RSS needs no auth
        self.token = token

    def fetch(self, currency_code: str, limit: int = 10) -> list[dict]:
        patterns = _match_patterns(currency_code)
        collected: list[dict] = []
        for source_name, url in FEEDS:
            try:
                items = _fetch_feed(url)
            except Exception as exc:
                print(f"[news] {source_name} fetch failed: {exc!r}")
                continue
            for item in items:
                if _matches(item["title"], item["description"], patterns):
                    collected.append(
                        {
                            "title": item["title"][:200],
                            "source": source_name,
                            "published_at": item["published_at"],
                        }
                    )
        collected.sort(key=lambda x: x["published_at"] or "", reverse=True)
        return collected[:limit]


def infer_currency_code(symbol: str) -> str:
    """BTCUSDT -> BTC, ETHUSDT -> ETH, etc. Same helper as before."""
    for quote in ("USDT", "USDC", "BUSD", "FDUSD", "TUSD", "BTC", "ETH", "BNB"):
        if symbol.endswith(quote) and len(symbol) > len(quote):
            return symbol[: -len(quote)]
    return symbol


def _match_patterns(currency: str) -> list[re.Pattern]:
    currency = currency.upper()
    tokens = [currency] + CURRENCY_NAMES.get(currency, [])
    patterns = []
    for t in tokens:
        # Whole-word match, case-insensitive
        patterns.append(re.compile(r"\b" + re.escape(t) + r"\b", re.IGNORECASE))
    return patterns


def _matches(title: str, description: str, patterns: list[re.Pattern]) -> bool:
    haystack = f"{title}\n{description}"
    return any(p.search(haystack) for p in patterns)


def _fetch_feed(url: str) -> list[dict]:
    req = urllib.request.Request(url, headers={"User-Agent": "birgi-bot/0.1 (+https://github.com)"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        body = resp.read()
    root = ElementTree.fromstring(body)
    channel = root.find("channel") if root.tag != "channel" else root
    if channel is None:
        return []
    items = []
    for it in channel.findall("item"):
        title = (it.findtext("title") or "").strip()
        desc = (it.findtext("description") or "").strip()
        pub = (it.findtext("pubDate") or "").strip()
        items.append(
            {
                "title": _strip_html(title),
                "description": _strip_html(desc)[:500],
                "published_at": _normalize_date(pub),
            }
        )
    return items


_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(text: str) -> str:
    return _TAG_RE.sub("", text).strip()


def _normalize_date(raw: str) -> str:
    if not raw:
        return ""
    try:
        return parsedate_to_datetime(raw).isoformat()
    except (TypeError, ValueError):
        return raw
