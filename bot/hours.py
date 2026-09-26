"""Time-based trading rules. All hours are the BROKER SERVER time shown in MT5."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class TradingHours:
    close_before_weekend: bool = True   # close all bot positions on Friday evening
    friday_close_hour: int = 20         # server hour on Friday from which positions are closed
    use_session_filter: bool = False    # only open new trades between the hours below
    session_start_hour: int = 8
    session_end_hour: int = 20          # exclusive

    def weekend_close_due(self, t: datetime) -> bool:
        if not self.close_before_weekend:
            return False
        return t.weekday() >= 5 or (t.weekday() == 4 and t.hour >= self.friday_close_hour)

    def entries_allowed(self, t: datetime) -> bool:
        if self.weekend_close_due(t):
            return False
        if not self.use_session_filter:
            return True
        start, end = self.session_start_hour, self.session_end_hour
        if start <= end:
            return start <= t.hour < end
        return t.hour >= start or t.hour < end  # window across midnight

    def validate(self) -> None:
        for name in ("friday_close_hour", "session_start_hour", "session_end_hour"):
            if not 0 <= getattr(self, name) <= 23:
                raise ValueError(f"config: hours.{name} must be 0-23")
