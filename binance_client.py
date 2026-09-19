from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from binance.client import Client
from binance.enums import (
    KLINE_INTERVAL_5MINUTE,
    KLINE_INTERVAL_15MINUTE,
    KLINE_INTERVAL_1HOUR,
    SIDE_BUY,
    SIDE_SELL,
)


TESTNET_URL = "https://testnet.binance.vision/api"

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
        self.client = Client(api_key, api_secret, testnet=True)
        self.client.API_URL = TESTNET_URL

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
        """Spend `quote_amount` USDT to buy base asset at market."""
        info = self.client.get_symbol_info(symbol)
        step = _lot_step(info)
        price = float(self.client.get_symbol_ticker(symbol=symbol)["price"])
        qty = _floor_step(quote_amount / price, step)
        if qty <= 0:
            raise ValueError(f"Computed qty {qty} is below lot step {step}")
        return self.client.create_order(
            symbol=symbol, side=SIDE_BUY, type="MARKET", quantity=qty
        )

    def market_sell_base(self, symbol: str, base_amount: float) -> dict:
        info = self.client.get_symbol_info(symbol)
        step = _lot_step(info)
        qty = _floor_step(base_amount, step)
        if qty <= 0:
            raise ValueError(f"Computed qty {qty} is below lot step {step}")
        return self.client.create_order(
            symbol=symbol, side=SIDE_SELL, type="MARKET", quantity=qty
        )


def _lot_step(info: dict) -> Decimal:
    for f in info["filters"]:
        if f["filterType"] == "LOT_SIZE":
            return Decimal(f["stepSize"])
    return Decimal("0.000001")


def _floor_step(qty: float, step: Decimal) -> float:
    d = Decimal(str(qty))
    return float((d // step) * step)
