"""Local position ledger for SL/TP.

Binance tells us the current base-asset balance, but not our cost basis. To
enforce stop-loss and take-profit we track average entry price and the peak
price since entry in our own JSON file, updated whenever the bot executes
BUY or SELL.

State shape (state/positions.json):
  {
    "BTCUSDT": {
      "base_qty": 0.0031,
      "avg_entry": 65210.5,
      "peak_since_entry": 65890.0,
      "opened_at": "2026-09-19T14:00:00Z"
    },
    ...
  }

If base_qty is 0, avg_entry and peak_since_entry are reset to 0.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class Position:
    base_qty: float = 0.0
    avg_entry: float = 0.0
    peak_since_entry: float = 0.0
    opened_at: str = ""

    def is_open(self) -> bool:
        return self.base_qty > 1e-12

    def pnl_pct(self, price: float) -> float:
        if not self.is_open() or self.avg_entry <= 0:
            return 0.0
        return (price - self.avg_entry) / self.avg_entry * 100

    def to_dict(self) -> dict:
        return {
            "base_qty": self.base_qty,
            "avg_entry": self.avg_entry,
            "peak_since_entry": self.peak_since_entry,
            "opened_at": self.opened_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Position":
        return cls(
            base_qty=float(data.get("base_qty", 0.0)),
            avg_entry=float(data.get("avg_entry", 0.0)),
            peak_since_entry=float(data.get("peak_since_entry", 0.0)),
            opened_at=str(data.get("opened_at", "")),
        )


class PositionLedger:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._data: dict[str, Position] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self._data = {k: Position.from_dict(v) for k, v in raw.items()}
        except (json.JSONDecodeError, OSError):
            self._data = {}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        serial = {k: v.to_dict() for k, v in self._data.items()}
        self.path.write_text(json.dumps(serial, indent=2), encoding="utf-8")

    def get(self, symbol: str) -> Position:
        return self._data.get(symbol, Position())

    def record_buy(self, symbol: str, base_qty: float, price: float) -> None:
        if base_qty <= 0 or price <= 0:
            return
        pos = self._data.get(symbol, Position())
        total_cost = pos.avg_entry * pos.base_qty + price * base_qty
        new_qty = pos.base_qty + base_qty
        pos.avg_entry = total_cost / new_qty if new_qty > 0 else 0.0
        pos.base_qty = new_qty
        pos.peak_since_entry = max(pos.peak_since_entry, price)
        if not pos.opened_at:
            pos.opened_at = datetime.now(timezone.utc).isoformat()
        self._data[symbol] = pos
        self._save()

    def record_sell(self, symbol: str, base_qty: float) -> None:
        pos = self._data.get(symbol, Position())
        pos.base_qty = max(0.0, pos.base_qty - base_qty)
        if pos.base_qty <= 1e-12:
            self._data[symbol] = Position()
        else:
            self._data[symbol] = pos
        self._save()

    def update_peak(self, symbol: str, price: float) -> None:
        pos = self._data.get(symbol)
        if pos is None or not pos.is_open():
            return
        if price > pos.peak_since_entry:
            pos.peak_since_entry = price
            self._data[symbol] = pos
            self._save()


@dataclass
class RiskDecision:
    should_close: bool
    reason: str = ""


def check_risk(
    pos: Position,
    price: float,
    stop_loss_pct: float,
    take_profit_pct: float,
    trailing_stop_pct: float,
) -> RiskDecision:
    """Decide whether an open position should be force-closed."""
    if not pos.is_open() or pos.avg_entry <= 0:
        return RiskDecision(should_close=False)

    pnl = pos.pnl_pct(price)
    if stop_loss_pct > 0 and pnl <= -abs(stop_loss_pct):
        return RiskDecision(True, f"stop-loss hit: pnl {pnl:.2f}% <= -{stop_loss_pct}%")
    if take_profit_pct > 0 and pnl >= take_profit_pct:
        return RiskDecision(True, f"take-profit hit: pnl {pnl:.2f}% >= {take_profit_pct}%")

    if trailing_stop_pct > 0 and pos.peak_since_entry > 0:
        drop_from_peak = (pos.peak_since_entry - price) / pos.peak_since_entry * 100
        if drop_from_peak >= trailing_stop_pct:
            return RiskDecision(
                True,
                f"trailing stop: {drop_from_peak:.2f}% below peak {pos.peak_since_entry:.2f}",
            )

    return RiskDecision(should_close=False)
