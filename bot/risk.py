"""Position sizing and account-level safety limits."""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class RiskParams:
    risk_per_trade_pct: float = 1.0      # % of balance lost if the stop loss is hit
    max_open_positions: int = 3          # across all symbols, bot positions only
    max_daily_loss_pct: float = 5.0      # stop opening trades after this equity drawdown in a day
    max_spread_points: int = 30          # skip entries when spread is wider than this


@dataclass(frozen=True)
class SymbolSpec:
    tick_size: float
    tick_value: float    # account-currency value of one tick for 1.0 lot
    volume_min: float
    volume_max: float
    volume_step: float
    point: float = 0.00001
    digits: int = 5
    stops_level: int = 0  # broker's minimum SL/TP distance, in points


def lot_size(balance: float, risk_pct: float, sl_distance: float, spec: SymbolSpec) -> float:
    """Lots such that hitting the stop loses about ``risk_pct`` % of ``balance``.

    Rounded DOWN to the broker's volume step. Returns 0.0 when even the minimum
    lot would risk more than allowed, so the caller can skip the trade.
    """
    if balance <= 0 or risk_pct <= 0 or sl_distance <= 0:
        return 0.0
    if spec.tick_size <= 0 or spec.tick_value <= 0 or spec.volume_step <= 0:
        return 0.0

    risk_money = balance * risk_pct / 100.0
    loss_per_lot = sl_distance / spec.tick_size * spec.tick_value
    raw = risk_money / loss_per_lot

    steps = math.floor(raw / spec.volume_step + 1e-9)
    volume = steps * spec.volume_step
    volume = min(volume, spec.volume_max)
    if volume < spec.volume_min:
        return 0.0
    # Avoid float noise like 0.30000000000000004 which brokers reject.
    decimals = max(0, -int(math.floor(math.log10(spec.volume_step))))
    return round(volume, decimals)


class DailyLossGuard:
    """Blocks new trades once equity falls ``max_daily_loss_pct`` below the day's start."""

    def __init__(self, max_daily_loss_pct: float):
        self.max_daily_loss_pct = max_daily_loss_pct
        self._day: date | None = None
        self._start_equity: float = 0.0

    def update(self, today: date, equity: float) -> None:
        if self._day != today:
            self._day = today
            self._start_equity = equity

    @property
    def start_equity(self) -> float:
        return self._start_equity

    def trading_allowed(self, equity: float) -> bool:
        if self._start_equity <= 0 or self.max_daily_loss_pct <= 0:
            return True
        drawdown_pct = (self._start_equity - equity) / self._start_equity * 100.0
        return drawdown_pct < self.max_daily_loss_pct
