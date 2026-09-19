"""Recent decision memory — so Claude sees what it suggested before and how it played out.

We read the last N entries from logs/decisions.jsonl, compute how the price moved
from each decision timestamp to the current price, and hand Claude a compact list.
This gives the model feedback on its own track record without any RL training.
"""

from __future__ import annotations

import json
from pathlib import Path


def load_recent_decisions(
    log_path: Path,
    current_price: float,
    depth: int,
    symbol: str | None = None,
) -> list[dict]:
    """Return up to `depth` most-recent decisions.

    If `symbol` is given, only matching entries count toward `depth` — this
    keeps the memory relevant when the bot trades multiple pairs.
    """
    if depth <= 0 or not log_path.exists():
        return []

    lines = log_path.read_text(encoding="utf-8").splitlines()

    matched: list[dict] = []
    for line in reversed(lines):
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if symbol is not None and entry.get("symbol") != symbol:
            continue
        matched.append(entry)
        if len(matched) >= depth:
            break

    out: list[dict] = []
    for entry in reversed(matched):
        decision = entry.get("decision", {}) or {}
        price_then = entry.get("price")
        if price_then is None or not isinstance(price_then, (int, float)):
            continue
        pct_move = ((current_price - price_then) / price_then) * 100 if price_then else 0.0
        out.append(
            {
                "ts": entry.get("ts"),
                "action": decision.get("action"),
                "confidence": round(float(decision.get("confidence", 0.0)), 2),
                "size_usdt": float(decision.get("size_usdt", 0.0)),
                "price_at_decision": price_then,
                "current_price": current_price,
                "price_move_pct_since": round(pct_move, 2),
                "reason": (decision.get("reason") or "")[:200],
            }
        )
    return out
