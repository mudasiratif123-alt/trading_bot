"""
telegram_bot.py - Full Telegram command interface
"""

import asyncio
import logging
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from bot import SMCBot

logger = logging.getLogger(__name__)

from telegram import Update, Bot
from telegram.ext import (
    Application, CommandHandler, ContextTypes,
)


class TelegramInterface:

    def __init__(self, token: str, chat_id: str, bot_ref=None):
        self.token   = token
        self.chat_id = str(chat_id)
        self.bot_ref = bot_ref
        self._app    = None

    def set_bot(self, bot):
        self.bot_ref = bot

    async def send(self, text: str):
        if not self._app:
            return
        try:
            await self._app.bot.send_message(
                chat_id    = self.chat_id,
                text       = text,
                parse_mode = "Markdown",
            )
        except Exception as e:
            logger.error(f"Telegram send error: {e}")

    async def start(self):
        self._app = Application.builder().token(self.token).build()

        handlers = [
            ("start",         self.cmd_start),
            ("help",          self.cmd_help),
            ("symbol",        self.cmd_symbol),
            ("symbols",       self.cmd_symbols),
            ("remove_symbol", self.cmd_remove_symbol),
            ("zone",          self.cmd_zone),
            ("zones",         self.cmd_zones),
            ("remove_zone",   self.cmd_remove_zone),
            ("status",        self.cmd_status),
            ("reset",         self.cmd_reset),
            ("paper",         self.cmd_paper),
        ]

        for name, handler in handlers:
            self._app.add_handler(CommandHandler(name, handler))

        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling(drop_pending_updates=True)

        await self.send(
            "🤖 *SMC Bot Online!*\n\n"
            "Type /help to see all commands.\n"
            "Paper trading mode: ✅ Active"
        )
        logger.info("Telegram bot started.")

    async def stop(self):
        if self._app:
            await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()

    def _is_authorized(self, update: "Update") -> bool:
        return str(update.effective_chat.id) == self.chat_id

    async def _unauthorized(self, update: "Update"):
        await update.message.reply_text("Unauthorized.")

    async def cmd_start(self, update: "Update", ctx: "ContextTypes.DEFAULT_TYPE"):
        if not self._is_authorized(update):
            return await self._unauthorized(update)
        await update.message.reply_text(
            "🤖 *SMC Trading Bot*\n\n"
            "*Quick Start:*\n"
            "1️⃣ `/symbol BTCUSDT` — Add coin\n"
            "2️⃣ `/zone BTCUSDT 104500 105000 Support` — Add zone\n"
            "3️⃣ Bot monitors automatically!\n\n"
            "Type /help for all commands.",
            parse_mode="Markdown"
        )

    async def cmd_help(self, update: "Update", ctx: "ContextTypes.DEFAULT_TYPE"):
        if not self._is_authorized(update):
            return await self._unauthorized(update)
        await update.message.reply_text(
            "📋 *All Commands*\n\n"
            "*Symbol:*\n"
            "`/symbol BTCUSDT` — monitor a coin\n"
            "`/symbols` — list all coins\n"
            "`/remove_symbol BTCUSDT` — stop monitoring\n\n"
            "*Zone:*\n"
            "`/zone BTCUSDT 104500 105000 Support`\n"
            "`/zones` — list all zones\n"
            "`/remove_zone BTCUSDT 1` — remove zone\n\n"
            "*Zone types:* Support | Resistance | Demand | Supply\n\n"
            "*Control:*\n"
            "`/status` — full status\n"
            "`/reset BTCUSDT` — reset zone cycle\n"
            "`/paper on/off` — toggle paper trading",
            parse_mode="Markdown"
        )

    async def cmd_symbol(self, update: "Update", ctx: "ContextTypes.DEFAULT_TYPE"):
        if not self._is_authorized(update):
            return await self._unauthorized(update)
        if not ctx.args:
            return await update.message.reply_text("Usage: `/symbol BTCUSDT`", parse_mode="Markdown")

        symbol = ctx.args[0].upper()
        if "_" not in symbol:
            symbol = symbol.replace("USDT", "_USDT")

        if self.bot_ref:
            success = await self.bot_ref.add_symbol(symbol)
            if success:
                await update.message.reply_text(
                    f"✅ *{symbol}* added!\n"
                    f"Now add a zone:\n"
                    f"`/zone {symbol} <low> <high> Support`",
                    parse_mode="Markdown")
            else:
                await update.message.reply_text(f"ℹ️ {symbol} already monitored.")

    async def cmd_symbols(self, update: "Update", ctx: "ContextTypes.DEFAULT_TYPE"):
        if not self._is_authorized(update):
            return await self._unauthorized(update)
        if self.bot_ref:
            symbols = list(self.bot_ref.monitors.keys())
            if not symbols:
                await update.message.reply_text("No symbols yet.\nUse `/symbol BTCUSDT`", parse_mode="Markdown")
            else:
                lines = ["📊 *Monitored Symbols:*"] + [f"• {s}" for s in symbols]
                await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

    async def cmd_remove_symbol(self, update: "Update", ctx: "ContextTypes.DEFAULT_TYPE"):
        if not self._is_authorized(update):
            return await self._unauthorized(update)
        if not ctx.args:
            return await update.message.reply_text("Usage: `/remove_symbol BTCUSDT`", parse_mode="Markdown")
        symbol = ctx.args[0].upper()
        if self.bot_ref:
            removed = await self.bot_ref.remove_symbol(symbol)
            await update.message.reply_text(f"✅ {symbol} removed." if removed else f"❌ {symbol} not found.")

    async def cmd_zone(self, update: "Update", ctx: "ContextTypes.DEFAULT_TYPE"):
        if not self._is_authorized(update):
            return await self._unauthorized(update)
        if not ctx.args or len(ctx.args) < 4:
            return await update.message.reply_text(
                "Usage: `/zone BTCUSDT 104500 105000 Support`\n"
                "Types: Support | Resistance | Demand | Supply",
                parse_mode="Markdown")

        symbol = ctx.args[0].upper()
        if "_" not in symbol:
            symbol = symbol.replace("USDT", "_USDT")

        try:
            zone_low  = float(ctx.args[1])
            zone_high = float(ctx.args[2])
            zone_type = ctx.args[3].capitalize()
        except ValueError:
            return await update.message.reply_text("❌ Invalid numbers.")

        if zone_type not in ("Support", "Resistance", "Demand", "Supply"):
            return await update.message.reply_text("❌ Type must be: Support | Resistance | Demand | Supply")

        if self.bot_ref:
            result = await self.bot_ref.add_zone(symbol, zone_low, zone_high, zone_type)
            if result["success"]:
                await update.message.reply_text(
                    f"✅ *Zone Added* | {symbol}\n"
                    f"ID: #{result['zone_id']}\n"
                    f"Type: {zone_type}\n"
                    f"Range: {zone_low} – {zone_high}\n"
                    f"Monitoring started! 🔍",
                    parse_mode="Markdown")
            else:
                await update.message.reply_text(f"❌ Error: {result['error']}")

    async def cmd_zones(self, update: "Update", ctx: "ContextTypes.DEFAULT_TYPE"):
        if not self._is_authorized(update):
            return await self._unauthorized(update)
        if not self.bot_ref:
            return
        lines = ["📋 *All Active Zones:*\n"]
        any_zones = False
        for symbol, monitor in self.bot_ref.monitors.items():
            zones = monitor.list_zones()
            if zones:
                any_zones = True
                lines.append(f"*{symbol}:*")
                for z in zones:
                    status = "🔴 Rejected" if z.rejected else ("🟡 In-zone" if z.interaction_started else "⚪ Watching")
                    lines.append(f"  #{z.zone_id} {z.zone_type} [{z.zone_low}–{z.zone_high}] {status}")
        if not any_zones:
            await update.message.reply_text("No zones yet.\nUse `/zone BTCUSDT 104500 105000 Support`", parse_mode="Markdown")
        else:
            await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

    async def cmd_remove_zone(self, update: "Update", ctx: "ContextTypes.DEFAULT_TYPE"):
        if not self._is_authorized(update):
            return await self._unauthorized(update)
        if not ctx.args or len(ctx.args) < 2:
            return await update.message.reply_text("Usage: `/remove_zone BTCUSDT 1`", parse_mode="Markdown")
        symbol = ctx.args[0].upper()
        if "_" not in symbol:
            symbol = symbol.replace("USDT", "_USDT")
        try:
            zone_id = int(ctx.args[1])
        except ValueError:
            return await update.message.reply_text("❌ Zone ID must be a number.")
        if self.bot_ref and symbol in self.bot_ref.monitors:
            removed = self.bot_ref.monitors[symbol].remove_zone(zone_id)
            await update.message.reply_text(f"✅ Zone #{zone_id} removed." if removed else f"❌ Zone #{zone_id} not found.")

    async def cmd_status(self, update: "Update", ctx: "ContextTypes.DEFAULT_TYPE"):
        if not self._is_authorized(update):
            return await self._unauthorized(update)
        if not self.bot_ref or not self.bot_ref.monitors:
            return await update.message.reply_text("No symbols monitored yet.")
        for symbol, monitor in self.bot_ref.monitors.items():
            text = monitor.get_status_text()
            await update.message.reply_text(text, parse_mode="Markdown")

    async def cmd_reset(self, update: "Update", ctx: "ContextTypes.DEFAULT_TYPE"):
        if not self._is_authorized(update):
            return await self._unauthorized(update)
        if not ctx.args:
            return await update.message.reply_text("Usage: `/reset BTCUSDT`", parse_mode="Markdown")
        symbol = ctx.args[0].upper()
        if "_" not in symbol:
            symbol = symbol.replace("USDT", "_USDT")
        if self.bot_ref and symbol in self.bot_ref.monitors:
            self.bot_ref.monitors[symbol].reset_cycle()
            await update.message.reply_text(f"🔄 *{symbol} reset!*", parse_mode="Markdown")
        else:
            await update.message.reply_text(f"❌ {symbol} not found.")

    async def cmd_paper(self, update: "Update", ctx: "ContextTypes.DEFAULT_TYPE"):
        if not self._is_authorized(update):
            return await self._unauthorized(update)
        if not ctx.args:
            status = "ON ✅" if (self.bot_ref and self.bot_ref.paper_mode) else "OFF ⚠️"
            return await update.message.reply_text(f"Paper trading: {status}\n`/paper on` or `/paper off`", parse_mode="Markdown")
        mode = ctx.args[0].lower()
        if self.bot_ref:
            self.bot_ref.paper_mode = (mode == "on")
            for m in self.bot_ref.monitors.values():
                m.paper_mode = self.bot_ref.paper_mode
            status = "ON ✅" if self.bot_ref.paper_mode else "OFF ⚠️ REAL TRADING"
            await update.message.reply_text(f"Paper trading: {status}")
