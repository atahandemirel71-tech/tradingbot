"""The 24/7 trading loop: reconnects, reads closed bars, and trades on signals."""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .config import BotConfig
from .risk import DailyLossGuard, TotalLossGuard, lot_size
from .strategy import Signal, add_indicators, generate_signal, stop_levels, trailed_stop

log = logging.getLogger(__name__)


class Trader:
    def __init__(self, cfg: BotConfig, client):
        self.cfg = cfg
        self.client = client
        self.guard = DailyLossGuard(cfg.risk.max_daily_loss_pct)
        self.total_guard: TotalLossGuard | None = None
        self._last_bar: dict[str, pd.Timestamp] = {}
        self._guard_warned = False
        self._halted = False
        self._weekend_logged = False
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
        """One iteration. ``now`` is broker server time (as shown in MT5)."""
        now = now or self._server_time()
        account = self.client.account()
        if account is None:
            log.warning("No account info this tick")
            return
        self.guard.update(now.date(), account.equity)
        if self.total_guard is None:
            self.total_guard = TotalLossGuard(self.cfg.risk.max_total_loss_pct,
                                              self._start_balance(account))

        if not self._check_limits(account, now):
            return

        for symbol in self.cfg.symbols:
            try:
                self._process_symbol(symbol, account, now)
            except Exception:
                log.exception("%s: error while processing", symbol)

    def _server_time(self) -> datetime:
        getter = getattr(self.client, "server_time", None)
        t = getter(self.cfg.symbols[0]) if getter else None
        return t or datetime.now(timezone.utc)

    def _start_balance(self, account) -> float:
        """Reference balance for the total loss limit; remembered across restarts."""
        r = self.cfg.risk
        if r.account_start_balance > 0:
            return r.account_start_balance
        path = Path(self.cfg.state_file)
        key = str(account.login)
        state = {}
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        if key not in state:
            state[key] = {"start_balance": account.balance}
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(state, indent=2), encoding="utf-8")
            except OSError:
                log.warning("Could not save %s", path)
        start = float(state[key]["start_balance"])
        log.info("Total loss limit: start balance %.2f, trading stops at equity %.2f",
                 start, start * (1 - r.max_total_loss_pct / 100))
        return start

    def _check_limits(self, account, now: datetime) -> bool:
        """Enforce loss limits and the weekend close. Returns False when nothing else should run."""
        r = self.cfg.risk
        if self._halted:
            return False
        if self.total_guard.breached(account.equity):
            log.error("TOTAL LOSS LIMIT %.1f%% hit (equity %.2f <= %.2f). Closing everything and "
                      "stopping trading for good. Set a new account_start_balance to resume.",
                      r.max_total_loss_pct, account.equity, self.total_guard.floor)
            self._close_all("total loss limit")
            self._halted = True
            return False
        if not self.guard.trading_allowed(account.equity):
            if not self._guard_warned:
                log.warning("Daily loss limit of %.1f%% reached (start equity %.2f, now %.2f); "
                            "no new trades until tomorrow", r.max_daily_loss_pct,
                            self.guard.start_equity, account.equity)
                self._guard_warned = True
                if r.close_on_limit:
                    self._close_all("daily loss limit")
            return False
        self._guard_warned = False
        if self.cfg.hours.weekend_close_due(now):
            first = not self._weekend_logged
            if first:
                log.info("Weekend close: closing bot positions, no new trades until the market reopens")
                self._weekend_logged = True
            if first or not self.cfg.dry_run:  # in dry run positions stay open; log them only once
                self._close_all("weekend close")
            return False
        self._weekend_logged = False
        return True

    def _close_all(self, reason: str) -> None:
        for pos in self.client.positions(self.cfg.magic_number):
            self._close(pos, reason)

    def _process_symbol(self, symbol: str, account, now: datetime) -> None:
        p = self.cfg.strategy
        bars = self.client.closed_bars(symbol, self.cfg.timeframe, p.min_bars + 50)
        if bars is None or bars.empty:
            return

        last_time = bars["time"].iloc[-1]
        if self._last_bar.get(symbol) == last_time:
            return  # nothing new since last evaluation
        self._last_bar[symbol] = last_time

        if p.trailing_atr_mult > 0 or p.breakeven_at_r > 0:
            self._trail_stops(symbol, bars)

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
                self._close(pos, "opposite signal")
            positions = self.client.positions(self.cfg.magic_number) if not self.cfg.dry_run else \
                [pos for pos in positions if pos not in opposite]
            if any(pos.symbol == symbol for pos in positions):
                log.warning("%s: could not close opposite position; not opening a new one", symbol)
                return

        if not self.cfg.hours.entries_allowed(now):
            log.info("%s: outside trading hours (%s server time), no new trade", symbol, now.strftime("%a %H:%M"))
            return
        self._open(symbol, result, account, len(positions))

    def _trail_stops(self, symbol: str, bars: pd.DataFrame) -> None:
        """Move stop losses of open bot positions after a closed bar (same rule as the backtest)."""
        p = self.cfg.strategy
        mine = [pos for pos in self.client.positions(self.cfg.magic_number) if pos.symbol == symbol]
        if not mine:
            return
        spec = self.client.symbol_spec(symbol)
        quote = self.client.quote(symbol)
        if spec is None or quote is None:
            return
        d = add_indicators(bars, p)
        span = d["time"].diff().median()
        for pos in mine:
            if pos.time is None or pos.sl <= 0:
                continue
            since = d[d["time"] > pos.time - span]          # bars from the entry bar onward
            before = d[d["time"] <= pos.time - span]        # signal bar = last bar before entry
            if since.empty or before.empty:
                continue
            initial_risk = float(before["atr"].iloc[-1]) * p.sl_atr_mult
            best = float(since["high"].max()) if pos.side is Signal.BUY else float(since["low"].min())
            new_sl = round(trailed_stop(pos.side, pos.price_open, pos.sl, initial_risk, best,
                                        float(d["atr"].iloc[-1]), p), spec.digits)
            improves = new_sl > pos.sl if pos.side is Signal.BUY else new_sl < pos.sl
            if not improves or abs(new_sl - pos.sl) < spec.point:
                continue
            bid, ask = quote
            price = bid if pos.side is Signal.BUY else ask
            if abs(price - new_sl) < (spec.stops_level + 1) * spec.point:
                continue  # too close to the market for the broker; retry next bar
            log.info("%s: move SL #%s %.5f -> %.5f", symbol, pos.ticket, pos.sl, new_sl)
            if self.cfg.dry_run:
                log.info("%s: dry_run=true, SL change NOT sent", symbol)
                continue
            self.client.modify_stops(pos, new_sl, pos.tp)

    # ---------- actions ----------
    def _open(self, symbol: str, result, account, open_count: int) -> None:
        r = self.cfg.risk
        if not account.trade_allowed and not self.cfg.dry_run:
            log.warning("%s: trading not allowed on account/terminal (enable Algo Trading)", symbol)
            return
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
        if abs(entry - sl) < min_dist or (tp and abs(entry - tp) < min_dist):
            log.info("%s: SL/TP closer than broker minimum (%d points), skipping", symbol, spec.stops_level)
            return
        sl, tp = round(sl, spec.digits), (round(tp, spec.digits) if tp else 0.0)

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

    def _close(self, pos, reason: str) -> None:
        log.info("%s: CLOSE %s #%s %.2f lots (profit %.2f): %s", pos.symbol,
                 pos.side.value, pos.ticket, pos.volume, pos.profit, reason)
        if self.cfg.dry_run:
            log.info("%s: dry_run=true, close NOT sent", pos.symbol)
            return
        self.client.close_position(pos, self.cfg.magic_number, self.cfg.deviation_points)
