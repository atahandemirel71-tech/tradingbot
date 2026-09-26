"""Backtest the bot's strategy on historical data.

Examples:
  # Straight from MT5 (Windows, terminal running):
  python backtest.py --symbol EURUSD --from 2024-01-01 --to 2026-01-01

  # From a CSV file (MT5 "Export bars" or time,open,high,low,close[,spread]):
  python backtest.py --csv data/EURUSD_M15.csv --spread 12
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from bot.backtest import BacktestSettings, format_report, run_backtest
from bot.config import load_config
from bot.risk import SymbolSpec


def load_csv(path: str) -> pd.DataFrame:
    """Read a generic OHLC CSV or a file from MT5's 'Export bars' (tab separated, <DATE> <TIME> ...)."""
    df = pd.read_csv(path, sep=None, engine="python")
    df.columns = [c.strip().strip("<>").lower() for c in df.columns]
    if "date" in df.columns and "time" in df.columns:
        df["time"] = pd.to_datetime(df["date"].astype(str) + " " + df["time"].astype(str), utc=True)
    elif "time" in df.columns:
        t = df["time"]
        df["time"] = pd.to_datetime(t, unit="s", utc=True) if pd.api.types.is_numeric_dtype(t) \
            else pd.to_datetime(t, utc=True)
    elif "date" in df.columns:
        df["time"] = pd.to_datetime(df["date"], utc=True)
    else:
        raise ValueError("CSV needs a 'time' or 'date' column")
    missing = {"open", "high", "low", "close"} - set(df.columns)
    if missing:
        raise ValueError(f"CSV is missing columns: {sorted(missing)}")
    cols = ["time", "open", "high", "low", "close"] + (["spread"] if "spread" in df.columns else [])
    return df[cols].sort_values("time").drop_duplicates("time").reset_index(drop=True)


def load_from_mt5(cfg, symbol: str, start: datetime, end: datetime):
    from bot.mt5_client import MT5Client

    c = cfg.credentials
    client = MT5Client(c.login, c.password, c.server, c.terminal_path)
    if not client.connect():
        sys.exit("Kunde inte ansluta till MT5.")
    try:
        if not client.prepare_symbol(symbol):
            sys.exit(f"Symbolen {symbol} finns inte hos din broker.")
        rates = client.mt5.copy_rates_range(symbol, client.timeframe(cfg.timeframe), start, end)
        if rates is None or len(rates) == 0:
            sys.exit(f"Ingen historik för {symbol}: {client.mt5.last_error()} "
                     "(öka 'Max bars in chart' i MT5 eller ladda ned historik)")
        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
        return df[["time", "open", "high", "low", "close", "spread"]], client.symbol_spec(symbol)
    finally:
        client.shutdown()


def parse_date(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def main() -> int:
    ap = argparse.ArgumentParser(description="Backtesta botens strategi på historisk data")
    ap.add_argument("--config", default="config.yaml")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--csv", help="CSV-fil med historiska candles")
    src.add_argument("--symbol", help="Hämta historik från MT5 för denna symbol")
    ap.add_argument("--from", dest="date_from", default="2024-01-01", help="Startdatum (MT5), YYYY-MM-DD")
    ap.add_argument("--to", dest="date_to", default=None, help="Slutdatum (MT5), YYYY-MM-DD, standard idag")
    ap.add_argument("--timeframe", help="Skriv över timeframe i config.yaml")
    ap.add_argument("--balance", type=float, default=10_000.0, help="Startsaldo")
    ap.add_argument("--spread", type=float, default=None,
                    help="Fast spread i points (standard: datans spread-kolumn, annars 10)")
    ap.add_argument("--commission", type=float, default=0.0, help="Courtage per lot tur och retur")
    # Symbol spec for CSV data (MT5 mode reads it from the broker).
    ap.add_argument("--point", type=float, default=0.00001, help="CSV: point-storlek (0.001 för JPY-par)")
    ap.add_argument("--tick-value", type=float, default=None,
                    help="CSV: värde per point för 1 lot i kontovalutan (standard point*100000)")
    ap.add_argument("--out", default="backtest_results", help="Mapp för trades.csv och equity.csv")
    args = ap.parse_args()

    cfg = load_config(args.config)
    if args.timeframe:
        cfg.timeframe = args.timeframe

    if args.symbol:
        end = parse_date(args.date_to) if args.date_to else datetime.now(timezone.utc)
        bars, spec = load_from_mt5(cfg, args.symbol, parse_date(args.date_from), end)
        title = f"{args.symbol} {cfg.timeframe}"
    else:
        bars = load_csv(args.csv)
        tick_value = args.tick_value if args.tick_value is not None else args.point * 100_000
        digits = max(0, len(f"{args.point:.10f}".rstrip("0").split(".")[1]))
        spec = SymbolSpec(tick_size=args.point, tick_value=tick_value, volume_min=0.01,
                          volume_max=100.0, volume_step=0.01, point=args.point, digits=digits)
        title = Path(args.csv).stem

    spread = args.spread
    if spread is None and "spread" not in bars.columns:
        spread = 10.0
    if len(bars) < cfg.strategy.min_bars + 10:
        sys.exit(f"För lite data: {len(bars)} candles, behöver minst {cfg.strategy.min_bars + 10}.")

    settings = BacktestSettings(args.balance, spread, args.commission, cfg.close_on_opposite_signal, cfg.hours)
    result = run_backtest(bars, cfg.strategy, cfg.risk, spec, settings)
    print(format_report(result.stats, title))

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    result.trades_frame().to_csv(out / "trades.csv", index=False)
    result.equity.rename("equity").to_csv(out / "equity.csv", index_label="time")
    print(f"\nAffärer: {out / 'trades.csv'}   Equity-kurva: {out / 'equity.csv'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
