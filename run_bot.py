"""Entry point: python run_bot.py [--config config.yaml]"""
from __future__ import annotations

import argparse
import logging
import signal
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from bot.config import load_config
from bot.trader import Trader


def setup_logging(log_file: str) -> None:
    Path(log_file).parent.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    file_handler = RotatingFileHandler(log_file, maxBytes=5_000_000, backupCount=10, encoding="utf-8")
    file_handler.setFormatter(fmt)
    root.addHandler(console)
    root.addHandler(file_handler)


def main() -> int:
    parser = argparse.ArgumentParser(description="MT5 automated trading bot")
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args()

    cfg = load_config(args.config)
    setup_logging(cfg.log_file)
    log = logging.getLogger("run_bot")

    try:
        from bot.mt5_client import MT5Client

        c = cfg.credentials
        client = MT5Client(c.login, c.password, c.server, c.terminal_path)
    except ImportError:
        log.error("MetaTrader5 package missing. Run: pip install -r requirements.txt (Windows only)")
        return 1

    if not client.connect():
        log.error("Could not connect to MT5. Is the terminal installed and are the .env credentials correct?")
        return 1
    for symbol in list(cfg.symbols):
        if not client.prepare_symbol(symbol):
            cfg.symbols.remove(symbol)
    if not cfg.symbols:
        log.error("No tradable symbols left; check 'symbols' in config.yaml")
        client.shutdown()
        return 1

    trader = Trader(cfg, client)
    signal.signal(signal.SIGTERM, lambda *_: trader.stop())
    try:
        trader.run_forever()
    except KeyboardInterrupt:
        log.info("Stopped by user (Ctrl+C)")
    finally:
        client.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
