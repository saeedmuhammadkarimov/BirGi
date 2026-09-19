"""Telegram notifications for the bot.

Uses plain HTTP so no extra dependencies. Failures are logged and swallowed —
a broken Telegram channel must never take the trading loop down with it.

To wire it up:
  1. Message @BotFather → /newbot → get TELEGRAM_BOT_TOKEN.
  2. Message your new bot at least once (any text).
  3. Open https://api.telegram.org/bot<TOKEN>/getUpdates → find "chat":{"id":...}
     → that's your TELEGRAM_CHAT_ID.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request


class TelegramNotifier:
    def __init__(self, bot_token: str, chat_id: str) -> None:
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.base_url = f"https://api.telegram.org/bot{bot_token}"

    def send(self, text: str, *, silent: bool = False) -> None:
        if not self.bot_token or not self.chat_id:
            return
        payload = {
            "chat_id": self.chat_id,
            "text": text[:4000],
            "parse_mode": "Markdown",
            "disable_notification": silent,
            "disable_web_page_preview": True,
        }
        data = urllib.parse.urlencode(payload).encode("utf-8")
        req = urllib.request.Request(f"{self.base_url}/sendMessage", data=data)
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                resp.read()
        except Exception as exc:
            print(f"[telegram] notification failed: {exc!r}")


def _escape(text: str) -> str:
    """Minimal Markdown escape — only for chars we actually use in messages."""
    for ch in ("_", "*", "`", "["):
        text = text.replace(ch, "\\" + ch)
    return text


def format_startup(symbols: list[str], dry_run: bool, sl: float, tp: float, trail: float) -> str:
    mode = "dry-run" if dry_run else "live"
    return (
        f"*Bot started* — {mode}\n"
        f"Symbols: `{','.join(symbols)}`\n"
        f"SL: {sl}% · TP: {tp}% · Trail: {trail}%"
    )


def format_trade(
    symbol: str, action: str, size_usdt: float, price: float, confidence: float, reason: str
) -> str:
    return (
        f"*{action}* `{symbol}` @ {price:.4f}\n"
        f"size: ${size_usdt:.2f} · conf: {confidence:.2f}\n"
        f"_{_escape(reason[:200])}_"
    )


def format_forced_close(symbol: str, price: float, pnl_pct: float, reason: str) -> str:
    return (
        f"*FORCED CLOSE* `{symbol}` @ {price:.4f}\n"
        f"PnL: {pnl_pct:+.2f}%\n"
        f"_{_escape(reason[:200])}_"
    )


def format_error(symbol: str, exc: Exception) -> str:
    return f"*Error* on `{symbol}`\n`{_escape(repr(exc)[:400])}`"
