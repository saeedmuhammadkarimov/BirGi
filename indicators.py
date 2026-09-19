"""Compact technical indicators computed with pandas.

We compute them locally and feed a small summary into the Claude prompt
instead of raw candles. That is cheaper in tokens and easier for the
model to reason over than a wall of OHLCV numbers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import pandas as pd

from binance_client import Candle


@dataclass
class IndicatorSnapshot:
    timeframe: str
    close: float
    ema_20: float
    ema_50: float
    ema_200: float
    rsi_14: float
    macd: float
    macd_signal: float
    macd_hist: float
    bb_upper: float
    bb_mid: float
    bb_lower: float
    atr_14: float
    stoch_k: float
    stoch_d: float
    volume: float
    volume_sma_20: float

    def to_compact_dict(self) -> dict:
        return {
            "tf": self.timeframe,
            "close": round(self.close, 6),
            "ema20": round(self.ema_20, 6),
            "ema50": round(self.ema_50, 6),
            "ema200": round(self.ema_200, 6),
            "rsi14": round(self.rsi_14, 2),
            "macd": round(self.macd, 6),
            "macd_signal": round(self.macd_signal, 6),
            "macd_hist": round(self.macd_hist, 6),
            "bb_upper": round(self.bb_upper, 6),
            "bb_mid": round(self.bb_mid, 6),
            "bb_lower": round(self.bb_lower, 6),
            "atr14": round(self.atr_14, 6),
            "stoch_k": round(self.stoch_k, 2),
            "stoch_d": round(self.stoch_d, 2),
            "vol": round(self.volume, 4),
            "vol_sma20": round(self.volume_sma_20, 4),
        }


def _to_df(candles: Iterable[Candle]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "t": c.open_time_ms,
                "open": c.open,
                "high": c.high,
                "low": c.low,
                "close": c.close,
                "volume": c.volume,
            }
            for c in candles
        ]
    )


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, 1e-12)
    return 100 - (100 / (1 + rs))


def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def _stoch(df: pd.DataFrame, k_period: int = 14, d_period: int = 3):
    low_min = df["low"].rolling(window=k_period).min()
    high_max = df["high"].rolling(window=k_period).max()
    k = 100 * (df["close"] - low_min) / (high_max - low_min).replace(0, 1e-12)
    d = k.rolling(window=d_period).mean()
    return k, d


def compute(candles: list[Candle], timeframe: str) -> IndicatorSnapshot:
    """Compute indicators for the latest closed candle.

    Requires at least 200 candles for EMA200 to be meaningful; falls back to
    the longest available window otherwise.
    """
    if len(candles) < 20:
        raise ValueError(f"need at least 20 candles for {timeframe}, got {len(candles)}")

    df = _to_df(candles)
    close = df["close"]

    ema20 = close.ewm(span=20, adjust=False).mean()
    ema50 = close.ewm(span=50, adjust=False).mean()
    ema200 = close.ewm(span=200, adjust=False).mean()

    rsi = _rsi(close, 14)

    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    macd_signal = macd.ewm(span=9, adjust=False).mean()
    macd_hist = macd - macd_signal

    bb_mid = close.rolling(window=20).mean()
    bb_std = close.rolling(window=20).std()
    bb_upper = bb_mid + 2 * bb_std
    bb_lower = bb_mid - 2 * bb_std

    atr = _atr(df, 14)
    stoch_k, stoch_d = _stoch(df, 14, 3)

    vol_sma = df["volume"].rolling(window=20).mean()

    i = -1
    return IndicatorSnapshot(
        timeframe=timeframe,
        close=float(close.iloc[i]),
        ema_20=float(ema20.iloc[i]),
        ema_50=float(ema50.iloc[i]),
        ema_200=float(ema200.iloc[i]),
        rsi_14=float(rsi.iloc[i]),
        macd=float(macd.iloc[i]),
        macd_signal=float(macd_signal.iloc[i]),
        macd_hist=float(macd_hist.iloc[i]),
        bb_upper=float(bb_upper.iloc[i]),
        bb_mid=float(bb_mid.iloc[i]),
        bb_lower=float(bb_lower.iloc[i]),
        atr_14=float(atr.iloc[i]),
        stoch_k=float(stoch_k.iloc[i]),
        stoch_d=float(stoch_d.iloc[i]),
        volume=float(df["volume"].iloc[i]),
        volume_sma_20=float(vol_sma.iloc[i]),
    )
