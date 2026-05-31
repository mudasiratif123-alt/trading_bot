import asyncio
import logging
from typing import Optional

from smc_core.symbol_monitor import SymbolMonitor
from price.price_feed import PriceFeed, Candle
from tg_bot.telegram_bot import TelegramInterface
from utils.config_loader import load_config
from utils.logger_setup import setup_logging

logger = logging.getLogger(__name__)


class SMCBot:
    def __init__(self, config_path: str = "config/config.yaml"):
        self.cfg = load_config(config_path)
        setup_logging(**self.cfg["logging"])

        self.paper_mode  = self.cfg["trading"].get("paper_mode", True)
        self._risk_usd   = self.cfg["trading"]["risk_per_trade_usd"]
        self._agg_pct    = self.cfg["trading"]["aggressive_entry_pct"]

        self.monitors: dict[str, SymbolMonitor] = {}
        self.feed = PriceFeed()
        self.tg   = TelegramInterface(
            token   = self.cfg["telegram"]["bot_token"],
            chat_id = self.cfg["telegram"]["chat_id"],
            bot_ref = self,
        )
        self.tg.set_bot(self)

    async def add_symbol(self, symbol: str) -> bool:
        if symbol in self.monitors:
            return False
        monitor = SymbolMonitor(
            symbol         = symbol,
            risk_usd       = self._risk_usd,
            aggressive_pct = self._agg_pct,
            paper_mode     = self.paper_mode,
            alert_callback = self._on_alert,
        )
        self.monitors[symbol] = monitor
        candles = await self.feed.fetch_historical(symbol, limit=200)
        for c in candles:
            await monitor.process_candle(c.open, c.high, c.low, c.close)
        logger.info(f"Symbol added: {symbol} — warmed up with {len(candles)} candles")
        self.feed.subscribe(symbol, self._on_candle)
        return True

    async def remove_symbol(self, symbol: str) -> bool:
        if symbol not in self.monitors:
            return False
        del self.monitors[symbol]
        self.feed.unsubscribe(symbol)
        return True

    async def add_zone(self, symbol: str, zone_low: float, zone_high: float, zone_type: str) -> dict:
        if symbol not in self.monitors:
            await self.add_symbol(symbol)
        try:
            zone = self.monitors[symbol].add_zone(zone_low, zone_high, zone_type)
            return {"success": True, "zone_id": zone.zone_id}
        except ValueError as e:
            return {"success": False, "error": str(e)}

    async def _on_candle(self, symbol: str, candle: Candle):
        monitor = self.monitors.get(symbol)
        if monitor:
            await monitor.process_candle(candle.open, candle.high, candle.low, candle.close)

    async def _on_alert(self, symbol: str, event_type: str, message: str):
        await self.tg.send(message)

    async def start(self):
        logger.info("SMC Bot starting...")
        logger.info(f"Mode: {'PAPER TRADING' if self.paper_mode else 'LIVE TRADING'}")
        await self.tg.start()
        await self.feed.start()

    async def stop(self):
        self.feed.stop()
        await self.tg.stop()
        logger.info("Bot stopped.")
