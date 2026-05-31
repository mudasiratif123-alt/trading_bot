"""
symbol_monitor.py
=================
One SymbolMonitor per trading pair.
Manages: SMCEngine + ZoneManager + TradeManager + StateMachine for ONE symbol.
Paper trading mode = no real orders, full simulation.
"""

import asyncio
import logging
from typing import Optional, Callable

from smc_core.smc_engine    import SMCEngine, StructureSignal
from smc_core.zone_manager  import ZoneManager, Zone
from smc_core.trade_manager import TradeManager, TradeDirection, TradeStatus
from smc_core.state_machine import StateMachine, BotState

logger = logging.getLogger(__name__)


class SymbolMonitor:
    """
    Full trading logic for ONE symbol.
    Multiple SymbolMonitors run in parallel for multiple pairs.
    """

    def __init__(self,
                 symbol:            str,
                 risk_usd:          float = 2.0,
                 aggressive_pct:    float = 0.5,
                 paper_mode:        bool  = True,
                 alert_callback:    Optional[Callable] = None):

        self.symbol         = symbol
        self.paper_mode     = paper_mode
        self.alert_callback = alert_callback   # async fn(symbol, event_type, msg)

        self.engine    = SMCEngine(internal_size=5, swing_size=50)
        self.zone_mgr  = ZoneManager(aggressive_pct=aggressive_pct)
        self.trade_mgr = TradeManager(risk_per_trade_usd=risk_usd)
        self.sm        = StateMachine()

        self._current_price: float = 0.0
        self._active_zone_id: Optional[int] = None   # which zone we're currently working

    # ── Zone management ──────────────────────────────────────────────────────

    def add_zone(self, zone_low: float, zone_high: float, zone_type: str) -> Zone:
        zone = self.zone_mgr.add_zone(zone_low, zone_high, zone_type)
        # If first zone added, start state machine
        if self.sm.state == BotState.IDLE:
            self.sm.on_zone_loaded()
        return zone

    def remove_zone(self, zone_id: int) -> bool:
        return self.zone_mgr.remove_zone(zone_id)

    def list_zones(self) -> list:
        return self.zone_mgr.list_zones()

    def reset_cycle(self):
        """Reset for new zone cycle."""
        self.sm.full_reset()
        self._active_zone_id = None
        if self.zone_mgr.zones:
            self.sm.on_zone_loaded()

    # ── Main candle processor ─────────────────────────────────────────────────

    async def process_candle(self, open_: float, high: float, low: float, close: float):
        self._current_price = close

        # 1. SMC signals
        signals = self.engine.update(open_, high, low, close)

        # 2. Zone events
        zone_events = self.zone_mgr.update(open_, high, low, close)

        # 3. Trade events (if trade open)
        if self.trade_mgr.is_trade_open:
            trade_events = self.trade_mgr.update(close)
            await self._handle_trade_events(trade_events, close)

        # 4. Zone state transitions
        for ev in zone_events:
            await self._handle_zone_event(ev, open_, close)

        # 5. Signal processing
        for sig in signals:
            await self._handle_signal(sig, close)

    # ── Zone event handling ───────────────────────────────────────────────────

    async def _handle_zone_event(self, ev: dict, open_: float, close: float):
        zone = ev["zone"]

        # Zone touched for first time
        if ev["touched"] and not ev["rejected"] and not self.sm.is_in_zone:
            if self.sm.state in (BotState.WATCHING_ZONE, BotState.REENTRY_WATCH):
                self._active_zone_id = zone.zone_id
                self.sm.on_zone_touched()
                await self._alert("zone_touched",
                    f"📍 *Zone Touched* | {self.symbol}\n"
                    f"Type: {zone.zone_type}\n"
                    f"Zone: {zone.zone_low} – {zone.zone_high}\n"
                    f"Price: {close}")

        # Aggressive entry rejection
        if ev["rejected"] and self.sm.is_in_zone:
            candle_pct = abs(close - open_) / open_ * 100
            self.sm.on_zone_rejected()
            await self._alert("zone_rejected",
                f"🚫 *Zone Rejected* | {self.symbol}\n"
                f"Reason: Aggressive candle {candle_pct:.2f}% > 0.5%\n"
                f"Zone #{zone.zone_id} | Waiting for fresh interaction")

        # Valid zone interaction → ready for CHOCH
        if ev["valid"] and self.sm.state == BotState.ZONE_INTERACTION:
            self.sm.on_valid_zone_interaction()

        # Price left zone without CHOCH
        if not ev["touched"] and self.sm.is_in_zone and zone.zone_id == self._active_zone_id:
            self.sm.on_zone_exited()

        # Re-entry: price returned to zone after SL/BE
        if ev["touched"] and self.sm.state in (BotState.TRADE_1_CLOSED_SL, BotState.TRADE_1_CLOSED_BE):
            self.sm.on_reentry_eligible()
            await self._alert("reentry",
                f"🔁 *Re-entry Eligible* | {self.symbol}\n"
                f"Zone revisited. Waiting for fresh CHoCH.")

    # ── Signal handling ───────────────────────────────────────────────────────

    async def _handle_signal(self, sig: StructureSignal, close: float):
        # Always send BOS alerts (informational only — never trigger trade)
        if sig.signal_type == "BOS":
            emoji = "🔵" if sig.direction == "bullish" else "🟠"
            await self._alert("bos",
                f"{emoji} *BOS [{sig.structure.upper()}]* | {self.symbol}\n"
                f"Direction: {sig.direction.capitalize()}\n"
                f"Level broken: {sig.level:.4f}")
            return

        # CHOCH signal
        if sig.signal_type == "CHOCH":
            emoji = "🟢" if sig.direction == "bullish" else "🔴"
            await self._alert("choch",
                f"{emoji} *CHoCH [{sig.structure.upper()}]* | {self.symbol}\n"
                f"Direction: {sig.direction.capitalize()}\n"
                f"Level: {sig.level:.4f}\n"
                f"Close: {close:.4f}")

            # Check if this CHOCH triggers entry
            if not self.sm.can_accept_choch(sig):
                return

            direction = TradeDirection.BUY if sig.direction == "bullish" else TradeDirection.SELL

            # Get active zone
            active_zone = self._get_active_zone()
            if active_zone is None:
                return

            sl = self._calculate_sl(direction, active_zone)
            if sl is None:
                return

            await self._open_trade(direction, close, sl, active_zone)

    # ── Trade opening ─────────────────────────────────────────────────────────

    async def _open_trade(self, direction: TradeDirection, entry: float,
                           sl: float, zone: Zone):
        try:
            trade = self.trade_mgr.open_trade(direction, entry, sl, zone.zone_id, self.symbol)
            self.sm.on_choch_accepted(direction)

            mode_label = "📋 PAPER" if self.paper_mode else "⚡ LIVE"
            await self._alert("trade_opened",
                f"{'📈' if direction == TradeDirection.BUY else '📉'} *Trade #{self.sm.trade_count} Opened* {mode_label}\n"
                f"Symbol: {self.symbol}\n"
                f"Direction: {direction.value}\n"
                f"Entry: {entry:.4f}\n"
                f"Stop Loss: {sl:.4f}\n"
                f"Quantity: {trade.quantity:.6f}\n"
                f"Risk: ${trade.risk_usd:.2f}\n"
                f"Zone: {zone.zone_type} [{zone.zone_low}–{zone.zone_high}]")

        except Exception as e:
            logger.error(f"Failed to open trade: {e}")
            self.sm.signal_locked = False

    # ── Trade event handling ──────────────────────────────────────────────────

    async def _handle_trade_events(self, events: dict, price: float):
        t = self.trade_mgr.trade

        if events["r_reached"] is not None:
            r      = events["r_reached"]
            new_sl = events["sl_moved_to"]

            # Disable re-entry at 2R+
            if r >= 2:
                self.sm.disable_reentry()

            if events["be_reached"]:
                await self._alert("breakeven",
                    f"⚖️ *Breakeven Activated* | {self.symbol}\n"
                    f"SL moved to entry: {t.entry_price:.4f}\n"
                    f"Trade is now risk-free ✅")
            else:
                await self._alert("trailing",
                    f"🔄 *Trailing Stop Updated* | {self.symbol}\n"
                    f"R Level: {r}R reached\n"
                    f"New SL: {new_sl:.4f}\n"
                    f"Price: {price:.4f}")

        if events["sl_hit"]:
            r      = t.current_r
            status = t.status

            if status == TradeStatus.CLOSED_SL:
                result_text = f"❌ Stop Loss Hit (−$2)\nR reached: 0R"
            elif status == TradeStatus.CLOSED_BE:
                result_text = f"⚖️ Closed at Breakeven ($0)\nR reached: 1R"
            else:
                result_text = f"✅ Profit Taken\nR reached: {r}R"

            await self._alert("trade_closed",
                f"🏁 *Trade Closed* | {self.symbol}\n"
                f"{result_text}\n"
                f"Entry: {t.entry_price:.4f}\n"
                f"Exit: {price:.4f}")

            close_status = t.status
            self.sm.on_trade_closed(close_status)

            if self.sm.state == BotState.ZONE_CYCLE_DONE:
                await self._alert("cycle_done",
                    f"🔚 *Zone Cycle Complete* | {self.symbol}\n"
                    f"Please set a new zone with /zone command.")

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _get_active_zone(self) -> Optional[Zone]:
        if self._active_zone_id is None:
            return None
        for z in self.zone_mgr.zones:
            if z.zone_id == self._active_zone_id:
                return z
        return None

    def _calculate_sl(self, direction: TradeDirection, zone: Zone) -> Optional[float]:
        il = zone.interaction_low
        ih = zone.interaction_high
        if il is None or ih is None:
            return None
        if direction == TradeDirection.BUY:
            return round(il * 0.9999, 6)   # just below interaction low
        else:
            return round(ih * 1.0001, 6)   # just above interaction high

    async def _alert(self, event_type: str, message: str):
        if self.alert_callback:
            try:
                await self.alert_callback(self.symbol, event_type, message)
            except Exception as e:
                logger.error(f"Alert failed: {e}")

    def get_status_text(self) -> str:
        sm   = self.sm
        zones = self.zone_mgr.list_zones()
        trade = self.trade_mgr.trade

        lines = [
            f"*{self.symbol}* {'📋 PAPER' if self.paper_mode else '⚡ LIVE'}",
            f"State: `{sm.state.name}`",
            f"Trade count: {sm.trade_count}/2",
            f"Re-entry: {'❌ Disabled' if sm.reentry_disabled else '✅ Enabled'}",
            f"Zones: {len(zones)} active",
        ]

        for z in zones:
            status = "🔴 Rejected" if z.rejected else ("🟡 Active" if z.interaction_started else "⚪ Watching")
            lines.append(f"  Zone #{z.zone_id}: {z.zone_type} [{z.zone_low}–{z.zone_high}] {status}")

        if trade and trade.is_open:
            lines.append(f"Trade: {trade.direction.value} @ {trade.entry_price:.4f} | SL: {trade.current_stop_loss:.4f} | R: {trade.current_r}")

        return "\n".join(lines)
