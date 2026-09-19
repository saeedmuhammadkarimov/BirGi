from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN

from binance.client import Client
from binance.enums import (
    KLINE_INTERVAL_5MINUTE,
    KLINE_INTERVAL_15MINUTE,
    KLINE_INTERVAL_1HOUR,
    SIDE_BUY,
    SIDE_SELL,
)


TIMEFRAMES = {
    "5m": KLINE_INTERVAL_5MINUTE,
    "15m": KLINE_INTERVAL_15MINUTE,
    "1h": KLINE_INTERVAL_1HOUR,
}


@dataclass
class Candle:
    open_time_ms: int
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class MarketSnapshot:
    symbol: str
    price: float
    candles_by_tf: dict[str, list[Candle]]
    base_asset: str
    quote_asset: str
    base_balance: float
    quote_balance: float


class BinanceTestnet:
    def __init__(self, api_key: str, api_secret: str) -> None:
        # testnet=True already points Client at https://testnet.binance.vision/api
        self.client = Client(api_key, api_secret, testnet=True)

    def get_snapshot(
        self,
        symbol: str,
        timeframes: tuple[str, ...] = ("1h", "15m", "5m"),
        candles_per_tf: int = 250,
    ) -> MarketSnapshot:
        info = self.client.get_symbol_info(symbol)
        if info is None:
            raise ValueError(f"Unknown symbol {symbol} on Binance testnet")
        base_asset = info["baseAsset"]
        quote_asset = info["quoteAsset"]

        price = float(self.client.get_symbol_ticker(symbol=symbol)["price"])

        candles_by_tf: dict[str, list[Candle]] = {}
        for tf in timeframes:
            interval = TIMEFRAMES[tf]
            raw = self.client.get_klines(symbol=symbol, interval=interval, limit=candles_per_tf)
            candles_by_tf[tf] = [
                Candle(
                    open_time_ms=int(k[0]),
                    open=float(k[1]),
                    high=float(k[2]),
                    low=float(k[3]),
                    close=float(k[4]),
                    volume=float(k[5]),
                )
                for k in raw
            ]

        return MarketSnapshot(
            symbol=symbol,
            price=price,
            candles_by_tf=candles_by_tf,
            base_asset=base_asset,
            quote_asset=quote_asset,
            base_balance=self._balance(base_asset),
            quote_balance=self._balance(quote_asset),
        )

    def get_price(self, symbol: str) -> float:
        return float(self.client.get_symbol_ticker(symbol=symbol)["price"])

    def _balance(self, asset: str) -> float:
        bal = self.client.get_asset_balance(asset=asset)
        return float(bal["free"]) if bal else 0.0

    def market_buy_quote(self, symbol: str, quote_amount: float) -> dict:
        """Spend exactly `quote_amount` USDT at market via quoteOrderQty.

        Using quoteOrderQty (instead of computing quantity locally) removes the
        slippage window between our price fetch and Binance's fill: Binance
        spends exactly this many USDT and gives us whatever base it fills.
        """
        info = self.client.get_symbol_info(symbol)
        min_notional = _min_notional(info)
        if quote_amount < min_notional:
            raise ValueError(
                f"quote_amount {quote_amount} below MIN_NOTIONAL {min_notional}"
            )
        # Round DOWN to the quote-precision to avoid "precision" filter errors.
        precision = int(info.get("quoteAssetPrecision", 8))
        quote_amount = float(
            Decimal(str(quote_amount)).quantize(Decimal(10) ** -precision, rounding=ROUND_DOWN)
        )
        return self.client.create_order(
            symbol=symbol,
            side=SIDE_BUY,
            type="MARKET",
            quoteOrderQty=quote_amount,
        )

    def market_sell_base(self, symbol: str, base_amount: float) -> dict:
        info = self.client.get_symbol_info(symbol)
        step = _lot_step(info)
        # Clip request to the actual free base balance, then floor to LOT_SIZE.
        base_asset = info["baseAsset"]
        free = self._balance(base_asset)
        base_amount = min(base_amount, free)
        qty = _floor_step(base_amount, step)
        if qty <= 0:
            raise ValueError(
                f"Sell qty rounds to 0: requested {base_amount}, free {free}, step {step}"
            )
        return self.client.create_order(
            symbol=symbol, side=SIDE_SELL, type="MARKET", quantity=qty
        )


def avg_fill_price(order: dict, fallback: float) -> float:
    """Real fill price averaged across MARKET fills.

    Prefers Binance's cummulativeQuoteQty / executedQty (exact), then per-fill
    weighted average, then `fallback`.
    """
    try:
        cqq = float(order.get("cummulativeQuoteQty", 0) or 0)
        eq = float(order.get("executedQty", 0) or 0)
        if cqq > 0 and eq > 0:
            return cqq / eq
    except (TypeError, ValueError):
        pass
    try:
        fills = order.get("fills") or []
        total_qty = 0.0
        total_notional = 0.0
        for f in fills:
            q = float(f.get("qty", 0))
            p = float(f.get("price", 0))
            total_qty += q
            total_notional += q * p
        if total_qty > 0:
            return total_notional / total_qty
    except (TypeError, ValueError):
        pass
    return fallback


def _lot_step(info: dict) -> Decimal:
    for f in info["filters"]:
        if f["filterType"] == "LOT_SIZE":
            return Decimal(f["stepSize"])
    return Decimal("0.000001")


def _min_notional(info: dict) -> float:
    for f in info["filters"]:
        if f["filterType"] in ("MIN_NOTIONAL", "NOTIONAL"):
            key = "minNotional" if "minNotional" in f else "notional"
            try:
                return float(f.get(key, "0"))
            except (TypeError, ValueError):
                return 0.0
    return 0.0


def _floor_step(qty: float, step: Decimal) -> float:
    d = Decimal(str(qty))
    return float((d // step) * step)
