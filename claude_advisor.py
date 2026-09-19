from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal

from anthropic import Anthropic

import indicators
from binance_client import MarketSnapshot


Action = Literal["BUY", "SELL", "HOLD"]


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0

    def to_dict(self) -> dict:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_creation_input_tokens": self.cache_creation_input_tokens,
            "cache_read_input_tokens": self.cache_read_input_tokens,
        }


# Sonnet 5 pricing per 1M tokens (as of 2026). Cache reads ~= 10% of input cost;
# cache writes ~= 125% of input cost. Update if you switch models.
PRICING = {
    "claude-sonnet-5": {"in": 2.0, "out": 10.0, "cache_write": 2.5, "cache_read": 0.20},
    "claude-opus-5": {"in": 5.0, "out": 25.0, "cache_write": 6.25, "cache_read": 0.50},
    "claude-haiku-4-5": {"in": 1.0, "out": 5.0, "cache_write": 1.25, "cache_read": 0.10},
}


def usage_cost_usd(model: str, u: Usage) -> float:
    rates = PRICING.get(model)
    if rates is None:
        return 0.0
    return (
        u.input_tokens * rates["in"]
        + u.output_tokens * rates["out"]
        + u.cache_creation_input_tokens * rates["cache_write"]
        + u.cache_read_input_tokens * rates["cache_read"]
    ) / 1_000_000


@dataclass
class Decision:
    action: Action
    size_usdt: float
    confidence: float
    reason: str
    raw: dict
    usage: Usage = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.usage is None:
            self.usage = Usage()


SYSTEM_PROMPT = """You are a cautious crypto trading advisor operating on a Binance TESTNET account.
Every dollar here is virtual, but treat the exercise seriously. Your job is to
reason from the data, not from vibes. Bad trades cost real learning; over-trading
compounds noise into losses. When in doubt, HOLD.

## Inputs you receive
- indicators_by_tf: [1h, 15m, 5m] each with close, EMA20/50/200, RSI14, MACD
  triplet, Bollinger triplet, ATR14, Stoch (%K, %D), volume, volume SMA20.
- balances, current_price, max_position_usdt.
- Optional recent_news: headlines with source and vote counts.
- Optional recent_decisions: your own past calls with realized price move since.

## How to reason (do this in order)

STEP 1 — Regime on 1h.
  • close > EMA50 > EMA200 ⇒ UPTREND. close < EMA50 < EMA200 ⇒ DOWNTREND.
    Anything else ⇒ RANGE.
  • ADX-style proxy: |MACD hist| growing = momentum expanding; flat/shrinking = fading.

STEP 2 — Setup on 15m.
  • Uptrend regime + pullback to EMA20 or Bollinger mid + RSI ~40-55 = long setup.
  • Downtrend regime + rally to EMA20 or Bollinger mid + RSI ~45-60 = short-avoid
    setup (SELL if long, else HOLD).
  • RANGE regime = fade extremes ONLY (RSI<30 near BB lower = long; RSI>70 near BB
    upper = short-avoid).

STEP 3 — Trigger on 5m.
  • Confirmation: MACD hist flip in setup direction, or Stoch %K crossing %D from
    extreme, or a close back inside Bollinger band after wick outside.
  • Volume on trigger candle should be ≥ 1.2× vol_sma20 for a real BUY.

STEP 4 — Sizing.
  • Base size = min(max_position_usdt, 0.5 × quote_balance).
  • Reduce size by 50% if 1h ATR / close > 3% (high vol) or 15m ATR / close > 1.5%.
  • Never exceed max_position_usdt.

STEP 5 — Sanity check against recent_decisions.
  • If you repeatedly BUY into moves that then went against you (>1% down within
    the memory window), require RSI < 40 on 15m before another BUY.
  • If you were in a position 1-2 steps ago, prefer HOLD unless a clear reversal
    signal fires (MACD hist sign flip on 15m + confirmed on 5m).

## Confidence calibration
- 0.85-1.00 : all three timeframes aligned + volume confirmation + no conflicting news.
- 0.70-0.85 : two timeframes aligned, third neutral.
- 0.50-0.70 : mixed signals — return HOLD.
- < 0.50    : always HOLD.

## Few-shot examples

Example A — clean uptrend continuation, BUY:
  1h: close 66200, EMA20 65800, EMA50 64100, EMA200 61000, RSI 58, MACD hist +80
  15m: pullback to EMA20 65900, RSI 44, Stoch %K 32 crossing up %D
  5m:  close 66150 back inside BB, vol 1.8× SMA20, MACD hist just flipped +
  → BUY, size 250, confidence 0.82,
    reason "1h up (px>EMA20>50>200), 15m pullback RSI44 at EMA20, 5m vol 1.8x + MACD flip up."

Example B — overbought into resistance, HOLD (not SELL, since not shorting):
  1h: close 67900 near BB upper 68100, RSI 74, MACD hist +40 shrinking
  15m: RSI 78, Stoch %K 88 rolling over
  5m:  vol 0.7× SMA20, no trigger yet
  → HOLD, size 0, confidence 0.60,
    reason "1h+15m overbought (RSI 74/78), momentum fading, 5m no reversal trigger yet."

Example C — trend break, SELL (liquidate long exposure):
  1h: close 63200 < EMA50 63800, EMA50 rolled below EMA200 for first time, MACD hist -120
  15m: RSI 32 but structural: lower highs, close below EMA200
  5m:  vol 2.1× SMA20, MACD hist deeply negative
  → SELL, size = 60% of base_balance × price, confidence 0.80,
    reason "1h EMA50<EMA200 fresh cross, 15m LH structure, 5m vol 2.1x confirms break — reduce exposure."

Example D — range chop, HOLD:
  1h: close 65000 between EMA20 and EMA50, RSI 51, MACD hist ±20 oscillating
  15m: BB squeeze, ATR shrinking, RSI 48
  5m:  vol 0.9× SMA20
  → HOLD, size 0, confidence 0.35,
    reason "Range: BB squeeze on 15m, no directional edge, volume low."

## Absolute rules
- Never suggest a position larger than max_position_usdt.
- BUY requires quote_balance ≥ size_usdt.
- SELL requires base_balance × current_price ≥ size_usdt.
- Confidence < 0.70 ⇒ action MUST be HOLD (size 0).
- reason ≤ 400 chars, must cite specific indicator values you used.
"""


def _tool_schema() -> dict:
    return {
        "name": "submit_decision",
        "description": "Submit exactly one trading decision.",
        "input_schema": {
            "type": "object",
            "required": ["action", "size_usdt", "confidence", "reason"],
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["BUY", "SELL", "HOLD"],
                    "description": "BUY = spend USDT for base asset. SELL = liquidate part of base position. HOLD = do nothing.",
                },
                "size_usdt": {
                    "type": "number",
                    "description": "Position size in USDT. Ignored for HOLD; must be > 0 for BUY/SELL.",
                },
                "confidence": {
                    "type": "number",
                    "description": "0.0 to 1.0. How confident the signal is.",
                },
                "reason": {
                    "type": "string",
                    "description": "Short, specific justification. Cite indicators (RSI value, MACD hist, EMA relationship, etc).",
                },
            },
        },
    }


def _build_payload(
    snapshot: MarketSnapshot,
    max_position_usdt: float,
    news: list[dict] | None = None,
    memory: list[dict] | None = None,
) -> dict:
    per_tf = []
    for tf, candles in snapshot.candles_by_tf.items():
        snap = indicators.compute(candles, tf)
        per_tf.append(snap.to_compact_dict())

    payload = {
        "symbol": snapshot.symbol,
        "current_price": snapshot.price,
        "base_asset": snapshot.base_asset,
        "quote_asset": snapshot.quote_asset,
        "balances": {
            snapshot.base_asset: snapshot.base_balance,
            snapshot.quote_asset: snapshot.quote_balance,
        },
        "max_position_usdt": max_position_usdt,
        "indicators_by_tf": per_tf,
    }
    if news:
        payload["recent_news"] = news
    if memory:
        payload["recent_decisions"] = memory
    return payload


class ClaudeAdvisor:
    def __init__(self, api_key: str, model: str) -> None:
        self.client = Anthropic(api_key=api_key)
        self.model = model

    def decide(
        self,
        snapshot: MarketSnapshot,
        max_position_usdt: float,
        news: list[dict] | None = None,
        memory: list[dict] | None = None,
    ) -> Decision:
        payload = _build_payload(snapshot, max_position_usdt, news=news, memory=memory)
        tool = _tool_schema()
        # Prompt caching: mark the (stable) system prompt with cache_control so
        # every subsequent call reads it back at ~10% of input token cost.
        # `tools` renders BEFORE `system`, so the cache breakpoint on system
        # implicitly caches the tool schema too.
        message = self.client.messages.create(
            model=self.model,
            max_tokens=1024,
            system=[
                {
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            tools=[tool],
            tool_choice={"type": "tool", "name": tool["name"]},
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Market snapshot follows as JSON. Return one decision via the tool.\n\n"
                        + json.dumps(payload)
                    ),
                }
            ],
        )
        usage = Usage(
            input_tokens=getattr(message.usage, "input_tokens", 0) or 0,
            output_tokens=getattr(message.usage, "output_tokens", 0) or 0,
            cache_creation_input_tokens=getattr(message.usage, "cache_creation_input_tokens", 0) or 0,
            cache_read_input_tokens=getattr(message.usage, "cache_read_input_tokens", 0) or 0,
        )
        for block in message.content:
            if block.type == "tool_use" and block.name == tool["name"]:
                raw = dict(block.input)
                return Decision(
                    action=raw["action"],
                    size_usdt=float(raw.get("size_usdt", 0.0)),
                    confidence=float(raw.get("confidence", 0.0)),
                    reason=str(raw.get("reason", "")),
                    raw=raw,
                    usage=usage,
                )
        raise RuntimeError("Claude did not return a tool_use block")
