from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal

from anthropic import Anthropic

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
not from vibes. You will receive a market snapshot for one trading pair and a small
account state. Return exactly one decision.

Rules:
- Prefer HOLD when signal is weak or ambiguous. Confidence < 0.7 should almost always be HOLD.
- Never suggest a position larger than the caller's max_position_usdt.
- Only suggest BUY if quote (USDT) balance can cover it.
- Only suggest SELL if base balance is non-trivial.
- Base your reasoning on the recent candles (trend, volatility, momentum) — do not invent news.
- Keep the reason under 300 characters. Be specific about what you saw in the data.
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
                    "description": "Short, specific justification grounded in the provided candles.",
                },
            },
        },
    }


def _snapshot_payload(snapshot: MarketSnapshot, max_position_usdt: float) -> dict:
    return {
        "symbol": snapshot.symbol,
        "current_price": snapshot.price,
        "base_asset": snapshot.base_asset,
        "quote_asset": snapshot.quote_asset,
        "balances": {
            snapshot.base_asset: snapshot.base_balance,
            snapshot.quote_asset: snapshot.quote_balance,
        },
        "max_position_usdt": max_position_usdt,
        "candles_15m": [
            {
                "t": c.open_time_ms,
                "o": c.open,
                "h": c.high,
                "l": c.low,
                "c": c.close,
                "v": c.volume,
            }
            for c in snapshot.candles_15m
        ],
    }


class ClaudeAdvisor:
    def __init__(self, api_key: str, model: str) -> None:
        self.client = Anthropic(api_key=api_key)
        self.model = model

    def decide(self, snapshot: MarketSnapshot, max_position_usdt: float) -> Decision:
        payload = _snapshot_payload(snapshot, max_position_usdt)
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
