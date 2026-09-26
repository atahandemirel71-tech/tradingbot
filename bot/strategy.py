"""Signal generation: EMA crossover with RSI, trend and ADX filters, ATR-based stops.

Everything here is pure pandas so it can be tested without MetaTrader 5.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping

import numpy as np
import pandas as pd


class Signal(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    NONE = "NONE"


@dataclass(frozen=True)
class StrategyParams:
    fast_ema: int = 9
    slow_ema: int = 21
    trend_ema: int = 200
    rsi_period: int = 14
    rsi_overbought: float = 70.0
    rsi_oversold: float = 30.0
    atr_period: int = 14
    sl_atr_mult: float = 2.0
    tp_atr_mult: float = 0.0         # 0 = no take profit (exit by trailing stop / opposite signal)
    trailing_atr_mult: float = 3.0   # 0 = off; else SL trails the best price by this many ATR
    breakeven_at_r: float = 1.0      # 0 = off; else move SL to entry once profit reaches this many R
    use_trend_filter: bool = True
    use_adx_filter: bool = False     # tested: with EMA crosses it removes most good entries
    adx_period: int = 14
    adx_min: float = 25.0   # only trade when ADX (trend strength) is at least this

    @property
    def min_bars(self) -> int:
        longest = max(self.fast_ema, self.slow_ema, self.rsi_period, self.atr_period)
        if self.use_adx_filter:
            longest = max(longest, self.adx_period * 2)
        if self.use_trend_filter:
            longest = max(longest, self.trend_ema)
        # Extra bars so the EMAs have warmed up.
        return longest * 2 + 5


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def rsi(close: pd.Series, period: int) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    # Wilder's smoothing.
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100.0 - 100.0 / (1.0 + rs)
    # No losses at all -> RSI 100; no movement at all -> neutral 50.
    out = out.where(avg_loss != 0.0, 100.0)
    out = out.where(~((avg_gain == 0.0) & (avg_loss == 0.0)), 50.0)
    return out


def atr(df: pd.DataFrame, period: int) -> pd.Series:
    prev_close = df["close"].shift(1)
    true_range = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return true_range.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def adx(df: pd.DataFrame, period: int) -> pd.Series:
    """Wilder's ADX (same as MT5's iADXWilder)."""
    up = df["high"].diff()
    down = -df["low"].diff()
    plus_dm = up.where((up > down) & (up > 0), 0.0)
    minus_dm = down.where((down > up) & (down > 0), 0.0)
    alpha = 1.0 / period
    atr_w = atr(df, period)
    plus_di = 100.0 * plus_dm.ewm(alpha=alpha, adjust=False, min_periods=period).mean() / atr_w
    minus_di = 100.0 * minus_dm.ewm(alpha=alpha, adjust=False, min_periods=period).mean() / atr_w
    di_sum = (plus_di + minus_di).replace(0.0, np.nan)
    dx = 100.0 * (plus_di - minus_di).abs() / di_sum
    return dx.ewm(alpha=alpha, adjust=False, min_periods=period).mean()


def add_indicators(df: pd.DataFrame, p: StrategyParams) -> pd.DataFrame:
    out = df.copy()
    out["ema_fast"] = ema(out["close"], p.fast_ema)
    out["ema_slow"] = ema(out["close"], p.slow_ema)
    out["ema_trend"] = ema(out["close"], p.trend_ema)
    out["rsi"] = rsi(out["close"], p.rsi_period)
    out["atr"] = atr(out, p.atr_period)
    out["adx"] = adx(out, p.adx_period)
    return out


@dataclass(frozen=True)
class SignalResult:
    signal: Signal
    atr: float
    close: float
    reason: str


def generate_signal(df: pd.DataFrame, p: StrategyParams) -> SignalResult:
    """Evaluate the last row of ``df``, which must contain only CLOSED bars."""
    if len(df) < p.min_bars:
        return SignalResult(Signal.NONE, float("nan"), float("nan"), f"need {p.min_bars} bars, got {len(df)}")

    d = add_indicators(df, p)
    return evaluate(d.iloc[-2], d.iloc[-1], p)


def evaluate(prev: Mapping, last: Mapping, p: StrategyParams) -> SignalResult:
    """Decide on the bar ``last`` given the previous bar; both carry indicator columns.

    Shared by the live bot and the backtester so both trade identically.
    """
    close, last_atr, last_rsi = float(last["close"]), float(last["atr"]), float(last["rsi"])

    if not np.isfinite(last_atr) or last_atr <= 0 or not np.isfinite(last_rsi):
        return SignalResult(Signal.NONE, last_atr, close, "indicators not ready")

    crossed_up = prev["ema_fast"] <= prev["ema_slow"] and last["ema_fast"] > last["ema_slow"]
    crossed_down = prev["ema_fast"] >= prev["ema_slow"] and last["ema_fast"] < last["ema_slow"]

    if (crossed_up or crossed_down) and p.use_adx_filter:
        last_adx = float(last["adx"])
        if not np.isfinite(last_adx) or last_adx < p.adx_min:
            return SignalResult(Signal.NONE, last_atr, close, f"EMA cross but ADX {last_adx:.1f} < {p.adx_min:g} (no trend)")

    if crossed_up:
        if p.use_trend_filter and close <= last["ema_trend"]:
            return SignalResult(Signal.NONE, last_atr, close, "bullish cross below trend EMA")
        if last_rsi >= p.rsi_overbought:
            return SignalResult(Signal.NONE, last_atr, close, f"bullish cross but RSI {last_rsi:.1f} overbought")
        return SignalResult(Signal.BUY, last_atr, close, f"bullish EMA cross, RSI {last_rsi:.1f}")

    if crossed_down:
        if p.use_trend_filter and close >= last["ema_trend"]:
            return SignalResult(Signal.NONE, last_atr, close, "bearish cross above trend EMA")
        if last_rsi <= p.rsi_oversold:
            return SignalResult(Signal.NONE, last_atr, close, f"bearish cross but RSI {last_rsi:.1f} oversold")
        return SignalResult(Signal.SELL, last_atr, close, f"bearish EMA cross, RSI {last_rsi:.1f}")

    return SignalResult(Signal.NONE, last_atr, close, "no crossover")


def stop_levels(signal: Signal, entry: float, atr_value: float, p: StrategyParams) -> tuple[float, float]:
    """Return (stop_loss, take_profit) prices for an entry. take_profit is 0.0 when disabled."""
    sl_dist = atr_value * p.sl_atr_mult
    tp_dist = atr_value * p.tp_atr_mult
    if signal is Signal.BUY:
        return entry - sl_dist, (entry + tp_dist if tp_dist > 0 else 0.0)
    if signal is Signal.SELL:
        return entry + sl_dist, (entry - tp_dist if tp_dist > 0 else 0.0)
    raise ValueError("stop_levels needs BUY or SELL")


def trailed_stop(side: Signal, entry: float, sl: float, initial_risk: float, best: float,
                 atr_value: float, p: StrategyParams) -> float:
    """New stop loss after a closed bar. Only ever moves in the trade's favour.

    ``best`` is the highest high since entry for a buy, the lowest low for a sell.
    """
    new = sl
    if side is Signal.BUY:
        if p.breakeven_at_r > 0 and best - entry >= p.breakeven_at_r * initial_risk:
            new = max(new, entry)
        if p.trailing_atr_mult > 0 and np.isfinite(atr_value) and atr_value > 0:
            new = max(new, best - p.trailing_atr_mult * atr_value)
    else:
        if p.breakeven_at_r > 0 and entry - best >= p.breakeven_at_r * initial_risk:
            new = min(new, entry)
        if p.trailing_atr_mult > 0 and np.isfinite(atr_value) and atr_value > 0:
            new = min(new, best + p.trailing_atr_mult * atr_value)
    return new
