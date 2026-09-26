"""The 24/7 trading loop: reconnects, reads closed bars, and trades on signals."""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

import pandas as pd

from .config import BotConfig
from .risk import DailyLossGuard, lot_size
from .strategy import Signal, generate_signal, stop_levels

log = logging.getLogger(__name__)


class Trader:
    def __init__(self, cfg: BotConfig, client):
        self.cfg = cfg
        self.client = client
        self.guard = DailyLossGuard(cfg.risk.max_daily_loss_pct)
        self._last_bar: dict[str, pd.Timestamp] = {}
        self._guard_warned = False
        self._running = False

    # ---------- lifecycle ----------
    def run_forever(self) -> None:
        mode = "DRY RUN (no real orders)" if self.cfg.dry_run else "LIVE TRADING"
        log.info("Starting bot in %s mode: symbols=%s timeframe=%s", mode, self.cfg.symbols, self.cfg.timeframe)
        self._running = True
        backoff = 5
        while self._running:
            try:
                if not self.client.ensure_connected():
                    log.warning("Not connected; retrying in %ss", backoff)
                    time.sleep(backoff)
                    backoff = min(backoff * 2, 300)
                    continue
                backoff = 5
                self.tick()
            except KeyboardInterrupt:
                raise
            except Exception:  # keep the bot alive on unexpected errors
                log.exception("Unexpected error in trading loop")
            time.sleep(self.cfg.poll_seconds)

    def stop(self) -> None:
        self._running = False

    # ---------- one iteration ----------
    def tick(self, now: datetime | None = None) -> None:
        now = now or datetime.now(timezone.utc)
        account = self.client.account()
        if account is None:
            log.warning("No account info this tick")
            return
        self.guard.update(now.date(), account.equity)

        for symbol in self.cfg.symbols:
            try:
                self._process_symbol(symbol, account)
            except Exception:
                log.exception("%s: error while processing", symbol)

    def _process_symbol(self, symbol: str, account) -> None:
        p = self.cfg.strategy
        bars = self.client.closed_bars(symbol, self.cfg.timeframe, p.min_bars + 50)
        if bars is None or bars.empty:
            return

        last_time = bars["time"].iloc[-1]
        if self._last_bar.get(symbol) == last_time:
            return  # nothing new since last evaluation
        self._last_bar[symbol] = last_time

        result = generate_signal(bars, p)
        log.info("%s [%s] bar %s close=%.5f -> %s (%s)", symbol, self.cfg.timeframe, last_time,
                 result.close, result.signal.value, result.reason)
        if result.signal is Signal.NONE:
            return

        positions = self.client.positions(self.cfg.magic_number)
        mine = [pos for pos in positions if pos.symbol == symbol]

        if any(pos.side is result.signal for pos in mine):
            log.info("%s: already holding a %s position, skipping", symbol, result.signal.value)
            return

        opposite = [pos for pos in mine if pos.side is not result.signal]
        if opposite:
            if not self.cfg.close_on_opposite_signal:
                log.info("%s: opposite position open and close_on_opposite_signal is off", symbol)
                return
            for pos in opposite:
                self._close(pos)
            positions = self.client.positions(self.cfg.magic_number) if not self.cfg.dry_run else \
                [pos for pos in positions if pos not in opposite]
            if any(pos.symbol == symbol for pos in positions):
                log.warning("%s: could not close opposite position; not opening a new one", symbol)
                return

        self._open(symbol, result, account, len(positions))

    # ---------- actions ----------
    def _open(self, symbol: str, result, account, open_count: int) -> None:
        r = self.cfg.risk
        if not account.trade_allowed and not self.cfg.dry_run:
            log.warning("%s: trading not allowed on account/terminal (enable Algo Trading)", symbol)
            return
        if not self.guard.trading_allowed(account.equity):
            if not self._guard_warned:
                log.warning("Daily loss limit of %.1f%% reached (start equity %.2f, now %.2f); "
                            "no new trades until tomorrow (UTC)", r.max_daily_loss_pct,
                            self.guard.start_equity, account.equity)
                self._guard_warned = True
            return
        self._guard_warned = False
        if open_count >= r.max_open_positions:
            log.info("%s: max open positions (%d) reached", symbol, r.max_open_positions)
            return
        if not self.client.can_trade_symbol(symbol):
            log.info("%s: symbol not tradable right now (market closed?)", symbol)
            return

        spec = self.client.symbol_spec(symbol)
        quote = self.client.quote(symbol)
        if spec is None or quote is None:
            log.warning("%s: missing symbol info or quote", symbol)
            return
        bid, ask = quote
        spread_points = (ask - bid) / spec.point
        if spread_points > r.max_spread_points:
            log.info("%s: spread %.0f points > max %d, skipping", symbol, spread_points, r.max_spread_points)
            return

        entry = ask if result.signal is Signal.BUY else bid
        sl, tp = stop_levels(result.signal, entry, result.atr, self.cfg.strategy)
        min_dist = (spec.stops_level + 1) * spec.point
        if abs(entry - sl) < min_dist or abs(entry - tp) < min_dist:
            log.info("%s: SL/TP closer than broker minimum (%d points), skipping", symbol, spec.stops_level)
            return
        sl, tp = round(sl, spec.digits), round(tp, spec.digits)

        volume = lot_size(account.balance, r.risk_per_trade_pct, abs(entry - sl), spec)
        if volume <= 0:
            log.info("%s: position size below broker minimum for %.2f%% risk, skipping",
                     symbol, r.risk_per_trade_pct)
            return

        log.info("%s: OPEN %s %.2f lots @ %.5f SL=%.5f TP=%.5f", symbol, result.signal.value,
                 volume, entry, sl, tp)
        if self.cfg.dry_run:
            log.info("%s: dry_run=true, order NOT sent", symbol)
            return
        self.client.open_position(symbol, result.signal, volume, sl, tp, self.cfg.magic_number,
                                  self.cfg.deviation_points, comment="mt5bot")

    def _close(self, pos) -> None:
        log.info("%s: CLOSE %s #%s %.2f lots (profit %.2f) on opposite signal", pos.symbol,
                 pos.side.value, pos.ticket, pos.volume, pos.profit)
        if self.cfg.dry_run:
            log.info("%s: dry_run=true, close NOT sent", pos.symbol)
            return
        self.client.close_position(pos, self.cfg.magic_number, self.cfg.deviation_points)
