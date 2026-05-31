"""
state_machine.py — Full zone cycle state machine
All rules exactly as specified.
"""

from enum import Enum, auto
from typing import Optional
from smc_core.trade_manager import TradeDirection, TradeStatus
import logging

logger = logging.getLogger(__name__)


class BotState(Enum):
    IDLE              = auto()
    WATCHING_ZONE     = auto()
    ZONE_INTERACTION  = auto()
    WAITING_CHOCH     = auto()
    TRADE_OPEN        = auto()
    TRADE_1_CLOSED_SL = auto()
    TRADE_1_CLOSED_BE = auto()
    REENTRY_WATCH     = auto()
    ZONE_CYCLE_DONE   = auto()


class StateMachine:
    def __init__(self):
        self.state:             BotState       = BotState.IDLE
        self.trade_count:       int            = 0
        self.reentry_disabled:  bool           = False
        self.signal_locked:     bool           = False
        self.last_trade_status: Optional[TradeStatus] = None

    def on_zone_loaded(self):
        self.state            = BotState.WATCHING_ZONE
        self.trade_count      = 0
        self.reentry_disabled = False
        self.signal_locked    = False
        self.last_trade_status = None
        logger.info(f"STATE → {self.state.name}")

    def on_zone_touched(self):
        if self.state in (BotState.WATCHING_ZONE, BotState.REENTRY_WATCH):
            self.state = BotState.ZONE_INTERACTION
            logger.info(f"STATE → {self.state.name}")

    def on_zone_rejected(self):
        self.state = BotState.WATCHING_ZONE
        logger.info(f"Zone rejected. STATE → {self.state.name}")

    def on_valid_zone_interaction(self):
        if self.state == BotState.ZONE_INTERACTION:
            self.state = BotState.WAITING_CHOCH
            logger.info(f"STATE → {self.state.name}")

    def on_zone_exited(self):
        if self.state in (BotState.ZONE_INTERACTION, BotState.WAITING_CHOCH):
            self.state = BotState.WATCHING_ZONE
            logger.info(f"Price left zone. STATE → {self.state.name}")

    def can_accept_choch(self, signal) -> bool:
        """Only first Internal CHOCH after valid zone interaction."""
        if self.signal_locked:
            return False
        if self.state != BotState.WAITING_CHOCH:
            return False
        if signal.structure != "internal":
            return False
        if signal.signal_type != "CHOCH":
            return False
        return True

    def on_choch_accepted(self, direction: TradeDirection):
        self.state        = BotState.TRADE_OPEN
        self.signal_locked = True
        self.trade_count  += 1
        logger.info(f"STATE → {self.state.name} | Trade #{self.trade_count}")

    def on_trade_closed(self, status: TradeStatus):
        self.last_trade_status = status
        self.signal_locked     = False

        if self.trade_count >= 2:
            self.state = BotState.ZONE_CYCLE_DONE
            logger.info(f"Max trades reached. STATE → {self.state.name}")
            return

        if self.reentry_disabled:
            self.state = BotState.ZONE_CYCLE_DONE
            logger.info(f"Re-entry disabled. STATE → {self.state.name}")
            return

        if status == TradeStatus.CLOSED_SL:
            self.state = BotState.TRADE_1_CLOSED_SL
        elif status == TradeStatus.CLOSED_BE:
            self.state = BotState.TRADE_1_CLOSED_BE
        else:
            # Profitable close (trailing SL hit at 2R+) — no re-entry
            self.state = BotState.ZONE_CYCLE_DONE

        logger.info(f"Trade closed: {status.value}. STATE → {self.state.name}")

    def on_reentry_eligible(self):
        """Price returned to zone after SL/BE close."""
        if self.state in (BotState.TRADE_1_CLOSED_SL, BotState.TRADE_1_CLOSED_BE):
            if not self.reentry_disabled:
                self.state = BotState.REENTRY_WATCH
                logger.info(f"Re-entry eligible. STATE → {self.state.name}")

    def disable_reentry(self):
        """Called when trade reaches 2R — no more re-entries ever."""
        self.reentry_disabled = True
        logger.info("Re-entry DISABLED (trade reached 2R+)")

    def full_reset(self):
        self.__init__()
        logger.info("Full reset. STATE → IDLE")

    @property
    def is_in_zone(self) -> bool:
        return self.state in (BotState.ZONE_INTERACTION, BotState.WAITING_CHOCH)

    @property
    def is_trade_active(self) -> bool:
        return self.state == BotState.TRADE_OPEN

    def to_dict(self) -> dict:
        return {
            "state":             self.state.name,
            "trade_count":       self.trade_count,
            "reentry_disabled":  self.reentry_disabled,
            "signal_locked":     self.signal_locked,
            "last_trade_status": self.last_trade_status.value if self.last_trade_status else None,
        }
