"""CryptoPanic news feed for fundamental context.

Free public API — https://cryptopanic.com/developers/api/. Register to get a
token (free tier is plenty for a bot polling every 15 minutes).

Response includes each post's title, kind, votes (positive/negative/important),
and source. We pass a compact summary to Claude, not full article text.
"""

from __future__ import annotations

import urllib.parse
import urllib.request
import json


CRYPTOPANIC_URL = "https://cryptopanic.com/api/v1/posts/"


class NewsClient:
    def __init__(self, token: str) -> None:
        self.token = token

    def fetch(self, currency_code: str, limit: int = 10) -> list[dict]:
        """Fetch recent posts filtered to `currency_code` (e.g. 'BTC', 'ETH').

        Returns a list of compact dicts safe to embed in a prompt.
        """
        params = {
            "auth_token": self.token,
            "currencies": currency_code,
            "public": "true",
            "kind": "news",
        }
        url = f"{CRYPTOPANIC_URL}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers={"User-Agent": "birgi-bot/0.1"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        posts = data.get("results", [])[:limit]
        compact = []
        for p in posts:
            votes = p.get("votes") or {}
            compact.append(
                {
                    "title": p.get("title", "")[:200],
                    "published_at": p.get("published_at", ""),
                    "source": (p.get("source") or {}).get("title", ""),
                    "positive": votes.get("positive", 0),
                    "negative": votes.get("negative", 0),
                    "important": votes.get("important", 0),
                }
            )
        return compact


def infer_currency_code(symbol: str) -> str:
    """BTCUSDT -> BTC, ETHUSDT -> ETH, SOLUSDT -> SOL.

    Handles the common quote assets Binance uses.
    """
    for quote in ("USDT", "USDC", "BUSD", "FDUSD", "TUSD", "BTC", "ETH", "BNB"):
        if symbol.endswith(quote) and len(symbol) > len(quote):
            return symbol[: -len(quote)]
    return symbol
