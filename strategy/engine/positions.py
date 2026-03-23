"""Position tracking and PnL calculation.

All financial math uses Decimal. No floats.
"""

from dataclasses import dataclass
from decimal import Decimal


@dataclass
class Position:
    """An open position in a single asset."""
    asset_id: str
    entry_time: object          # datetime
    entry_price: Decimal
    amount: Decimal
    entry_reason: dict          # signal info at entry

    def roi_pct(self, current_price):
        """Return ROI as a percentage (e.g. 50.0 for +50%)."""
        if self.entry_price == 0:
            return Decimal('0')
        return ((current_price - self.entry_price) / self.entry_price) * 100

    def pnl(self, exit_price):
        """Return absolute PnL."""
        return (exit_price - self.entry_price) * self.amount


@dataclass
class ClosedTrade:
    """A completed trade — entry + exit."""
    asset_id: str
    entry_time: object
    exit_time: object
    entry_price: Decimal
    exit_price: Decimal
    amount: Decimal
    entry_reason: dict
    exit_reason: str
    pnl: Decimal
    roi_pct: Decimal
    hold_minutes: Decimal


class PositionTracker:
    """Manages open positions and records closed trades."""

    def __init__(self, max_open):
        self.max_open = max_open
        self._positions = {}    # asset_id -> Position
        self.closed_trades = []

    def can_open(self):
        """True if we haven't hit max_open_positions."""
        return len(self._positions) < self.max_open

    def has_position(self, asset_id):
        """True if we have an open position in this asset."""
        return asset_id in self._positions

    def get_position(self, asset_id):
        """Return open Position for asset_id, or None."""
        return self._positions.get(asset_id)

    @property
    def open_count(self):
        return len(self._positions)

    def open(self, asset_id, entry_time, entry_price, amount, entry_reason):
        """Open a new position."""
        if self.has_position(asset_id):
            raise ValueError(f"Already have open position in {asset_id}")
        if not self.can_open():
            raise ValueError(
                f"Max open positions ({self.max_open}) reached"
            )
        self._positions[asset_id] = Position(
            asset_id=asset_id,
            entry_time=entry_time,
            entry_price=Decimal(str(entry_price)),
            amount=Decimal(str(amount)),
            entry_reason=entry_reason,
        )

    def close(self, asset_id, exit_time, exit_price, exit_reason):
        """Close a position and record the trade."""
        if not self.has_position(asset_id):
            raise ValueError(f"No open position in {asset_id}")
        pos = self._positions.pop(asset_id)
        exit_price = Decimal(str(exit_price))
        trade_pnl = pos.pnl(exit_price)
        roi = pos.roi_pct(exit_price)

        delta = exit_time - pos.entry_time
        hold_mins = Decimal(str(delta.total_seconds())) / 60

        trade = ClosedTrade(
            asset_id=asset_id,
            entry_time=pos.entry_time,
            exit_time=exit_time,
            entry_price=pos.entry_price,
            exit_price=exit_price,
            amount=pos.amount,
            entry_reason=pos.entry_reason,
            exit_reason=exit_reason,
            pnl=trade_pnl,
            roi_pct=roi,
            hold_minutes=hold_mins,
        )
        self.closed_trades.append(trade)
        return trade

    def force_close_all(self, exit_time, price_lookup):
        """Close all open positions at given prices.

        Args:
            exit_time: datetime for the exit.
            price_lookup: dict of asset_id -> current price.
        """
        for asset_id in list(self._positions.keys()):
            price = price_lookup.get(asset_id)
            if price is not None:
                self.close(asset_id, exit_time, price, 'force_close')
