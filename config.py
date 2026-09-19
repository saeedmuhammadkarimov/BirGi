from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    binance_api_key: str
    binance_api_secret: str
    anthropic_api_key: str
    symbols: list[str]
    interval_minutes: int
    max_position_usdt: float
    min_confidence: float
    max_trades_per_hour: int
    dry_run: bool
    claude_model: str
    cryptopanic_token: str
    memory_depth: int
    stop_loss_pct: float
    take_profit_pct: float
    trailing_stop_pct: float
    telegram_bot_token: str
    telegram_chat_id: str


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value or value.startswith("your_"):
        raise RuntimeError(
            f"Missing required env var: {name}. "
            f"Copy .env.example to .env and fill it in."
        )
    return value


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return default


def _parse_symbols() -> list[str]:
    raw = os.getenv("SYMBOLS", "").strip()
    if not raw:
        raw = os.getenv("SYMBOL", "BTCUSDT").strip()
    parts = [s.strip().upper() for s in raw.split(",") if s.strip()]
    seen: set[str] = set()
    unique: list[str] = []
    for s in parts:
        if s not in seen:
            seen.add(s)
            unique.append(s)
    return unique


def load_settings() -> Settings:
    load_dotenv()
    return Settings(
        binance_api_key=_required("BINANCE_API_KEY"),
        binance_api_secret=_required("BINANCE_API_SECRET"),
        anthropic_api_key=_required("ANTHROPIC_API_KEY"),
        symbols=_parse_symbols(),
        interval_minutes=int(os.getenv("INTERVAL_MINUTES", "15")),
        max_position_usdt=float(os.getenv("MAX_POSITION_USDT", "200")),
        min_confidence=float(os.getenv("MIN_CONFIDENCE", "0.7")),
        max_trades_per_hour=int(os.getenv("MAX_TRADES_PER_HOUR", "4")),
        dry_run=_bool("DRY_RUN", False),
        claude_model=os.getenv("CLAUDE_MODEL", "claude-sonnet-5"),
        cryptopanic_token=os.getenv("CRYPTOPANIC_TOKEN", "").strip(),
        memory_depth=int(os.getenv("MEMORY_DEPTH", "10")),
        stop_loss_pct=float(os.getenv("STOP_LOSS_PCT", "3.0")),
        take_profit_pct=float(os.getenv("TAKE_PROFIT_PCT", "6.0")),
        trailing_stop_pct=float(os.getenv("TRAILING_STOP_PCT", "0")),
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", "").strip(),
    )
