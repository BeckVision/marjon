"""Tests for backtest metrics — pure function tests, no DB."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from django.test import TestCase

from strategy.engine.metrics import (
    compute_max_drawdown,
    compute_metrics,
    compute_sharpe,
    compute_sortino,
)
from strategy.engine.positions import ClosedTrade

D = Decimal
T0 = datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc)


def _make_trade(pnl, asset_id='COIN_A', hold_min=60):
    return ClosedTrade(
        asset_id=asset_id,
        entry_time=T0,
        exit_time=T0 + timedelta(minutes=hold_min),
        entry_price=D('100'),
        exit_price=D('100') + pnl,
        amount=D('1'),
        entry_reason={'SG-001': True},
        exit_reason='take_profit' if pnl > 0 else 'stop_loss',
        pnl=pnl,
        roi_pct=pnl,  # simplified: 1 unit at 100
        hold_minutes=D(str(hold_min)),
    )


class ComputeMetricsTest(TestCase):

    def test_zero_trades(self):
        m = compute_metrics([])
        self.assertEqual(m['total_trades'], 0)
        self.assertIsNone(m['win_rate'])
        self.assertIsNone(m['sharpe_ratio'])

    def test_all_winners(self):
        trades = [_make_trade(D('50')), _make_trade(D('30'))]
        m = compute_metrics(trades)
        self.assertEqual(m['total_trades'], 2)
        self.assertEqual(m['winning_trades'], 2)
        self.assertEqual(m['losing_trades'], 0)
        self.assertEqual(m['total_pnl'], D('80'))
        self.assertEqual(m['win_rate'], D('1'))
        # No gross loss → profit_factor is None
        self.assertIsNone(m['profit_factor'])

    def test_mixed_trades(self):
        trades = [
            _make_trade(D('100'), asset_id='A'),
            _make_trade(D('-50'), asset_id='B'),
            _make_trade(D('25'), asset_id='A'),
        ]
        m = compute_metrics(trades)
        self.assertEqual(m['total_trades'], 3)
        self.assertEqual(m['winning_trades'], 2)
        self.assertEqual(m['losing_trades'], 1)
        self.assertEqual(m['total_pnl'], D('75'))
        # win_rate = 2/3
        self.assertEqual(m['win_rate'], D('2') / D('3'))
        # profit_factor = 125 / 50 = 2.5
        self.assertEqual(m['profit_factor'], D('2.5'))
        # entities: A = +125 (profitable), B = -50 (not)
        self.assertEqual(m['entities_traded'], 2)
        self.assertEqual(m['entities_profitable'], 1)

    def test_avg_hold_minutes(self):
        trades = [
            _make_trade(D('10'), hold_min=30),
            _make_trade(D('20'), hold_min=90),
        ]
        m = compute_metrics(trades)
        self.assertEqual(m['avg_hold_minutes'], D('60'))

    def test_max_win_and_loss(self):
        trades = [
            _make_trade(D('100')),
            _make_trade(D('-30')),
            _make_trade(D('50')),
        ]
        m = compute_metrics(trades)
        self.assertEqual(m['max_win'], D('100'))
        self.assertEqual(m['max_loss'], D('-30'))


class SharpeTest(TestCase):

    def test_positive_sharpe(self):
        pnls = [D('10'), D('20'), D('15'), D('12')]
        result = compute_sharpe(pnls)
        self.assertIsNotNone(result)
        self.assertGreater(result, 0)

    def test_negative_sharpe(self):
        pnls = [D('-10'), D('-20'), D('-15'), D('-12')]
        result = compute_sharpe(pnls)
        self.assertIsNotNone(result)
        self.assertLess(result, 0)

    def test_single_trade_returns_none(self):
        self.assertIsNone(compute_sharpe([D('10')]))

    def test_zero_std_returns_none(self):
        """All same PnL → zero std → None."""
        self.assertIsNone(compute_sharpe([D('10'), D('10'), D('10')]))


class SortinoTest(TestCase):

    def test_all_positive_returns_none(self):
        """No downside → zero downside std → None."""
        pnls = [D('10'), D('20'), D('15')]
        self.assertIsNone(compute_sortino(pnls))

    def test_mixed_returns_value(self):
        pnls = [D('10'), D('-5'), D('20'), D('-3')]
        result = compute_sortino(pnls)
        self.assertIsNotNone(result)
        self.assertGreater(result, 0)

    def test_single_trade_returns_none(self):
        self.assertIsNone(compute_sortino([D('10')]))


class MaxDrawdownTest(TestCase):

    def test_no_drawdown(self):
        """Monotonically increasing PnL → no drawdown."""
        pnls = [D('10'), D('10'), D('10')]
        self.assertIsNone(compute_max_drawdown(pnls))

    def test_known_drawdown(self):
        """Peak at 20, drops to 10, then recovers."""
        pnls = [D('20'), D('-10'), D('5')]
        # cumulative: 20, 10, 15
        # peak: 20, drawdown at step 2: (10-20)/20 = -50%
        result = compute_max_drawdown(pnls)
        self.assertIsNotNone(result)
        self.assertEqual(result, D('-50'))

    def test_empty_returns_none(self):
        self.assertIsNone(compute_max_drawdown([]))

    def test_deeper_drawdown_wins(self):
        """Two drawdowns — the deeper one is reported."""
        pnls = [D('10'), D('-5'), D('20'), D('-20')]
        # cumulative: 10, 5, 25, 5
        # dd1: (5-10)/10 = -50%
        # dd2: (5-25)/25 = -80%
        result = compute_max_drawdown(pnls)
        self.assertEqual(result, D('-80'))
