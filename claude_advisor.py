from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal

from anthropic import Anthropic

import indicators
from binance_client import MarketSnapshot


Action = Literal["BUY", "SELL", "HOLD"]


@dataclass
class Decision:
    action: Action
    size_usdt: float
    confidence: float
    reason: str
    raw: dict


SYSTEM_PROMPT = """You are a cautious crypto trading advisor operating on a Binance TESTNET account.
Every dollar here is virtual, but treat the exercise seriously: reason from the data,
not from vibes. You will receive an indicator snapshot at three timeframes (1h, 15m, 5m),
optional news headlines, optional recent decision history, and account state.
Return exactly one decision.

How to reason:
- The 1h timeframe sets the dominant trend. Don't fight it.
- The 15m timeframe is your setup timeframe.
- The 5m timeframe is your trigger.
- Alignment across timeframes = higher confidence. Divergence = HOLD.

Signals to watch:
- Price vs EMA20/50/200 (trend regime)
- RSI extremes (>70 overbought, <30 oversold) + divergence
- MACD histogram sign + slope
- Bollinger squeeze / breakout
- Volume vs its SMA20 (conviction)
- ATR (volatility — size positions inversely)

If recent_decisions is present, treat it as your own track record on this symbol.
Each item shows what you decided, at what price, and how the price moved since.
Look for patterns you got wrong (e.g. BUY at $60k while trend was down, price now
lower) and adjust — do not blindly repeat losing biases. Also do not over-trade:
if you were recently in a position, HOLD is often the right answer.

Rules:
- Prefer HOLD when signal is weak or ambiguous. Confidence < 0.7 should almost always be HOLD.
- Never suggest a position larger than the caller's max_position_usdt.
- Only suggest BUY if quote balance can cover it.
- Only suggest SELL if base balance is non-trivial.
- Keep the reason under 400 characters. Be specific: cite indicator names and values you used.
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
        message = self.client.messages.create(
            model=self.model,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
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
        for block in message.content:
            if block.type == "tool_use" and block.name == tool["name"]:
                raw = dict(block.input)
                return Decision(
                    action=raw["action"],
                    size_usdt=float(raw.get("size_usdt", 0.0)),
                    confidence=float(raw.get("confidence", 0.0)),
                    reason=str(raw.get("reason", "")),
                    raw=raw,
                )
        raise RuntimeError("Claude did not return a tool_use block")
