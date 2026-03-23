"""Tests for signal effectiveness analysis."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from django.test import TestCase

from strategy.analysis.signal_effectiveness import (
    analyze_signal_correlation,
    analyze_signal_effectiveness,
)
from strategy.engine.positions import ClosedTrade

D = Decimal
T0 = datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc)


def _make_trade(pnl, entry_reason, asset_id='COIN_A'):
    return ClosedTrade(
        asset_id=asset_id,
        entry_time=T0,
        exit_time=T0 + timedelta(hours=1),
        entry_price=D('100'),
        exit_price=D('100') + pnl,
        amount=D('1'),
        entry_reason=entry_reason,
        exit_reason='take_profit' if pnl > 0 else 'stop_loss',
        pnl=pnl,
        roi_pct=pnl,
        hold_minutes=D('60'),
    )


# ---------------------------------------------------------------------------
# analyze_signal_effectiveness
# ---------------------------------------------------------------------------

class SignalEffectivenessTest(TestCase):

    def test_empty_trades(self):
        result = analyze_signal_effectiveness([])
        self.assertEqual(result, {})

    def test_single_signal_single_trade(self):
        trades = [_make_trade(D('50'), {'SG-001': True})]
        result = analyze_signal_effectiveness(trades)
        self.assertIn('SG-001', result)
        self.assertEqual(result['SG-001']['fire_count'], 1)
        self.assertEqual(result['SG-001']['avg_pnl'], D('50'))
        self.assertEqual(result['SG-001']['win_rate'], D('1'))

    def test_multiple_signals(self):
        trades = [
            _make_trade(D('50'), {'SG-001': True, 'SG-002': True}),
            _make_trade(D('-20'), {'SG-001': True, 'SG-002': False}),
        ]
        result = analyze_signal_effectiveness(trades)
        # SG-001 fired twice: +50 and -20
        self.assertEqual(result['SG-001']['fire_count'], 2)
        self.assertEqual(result['SG-001']['avg_pnl'], D('15'))
        self.assertEqual(result['SG-001']['win_count'], 1)
        # SG-002 fired once: +50
        self.assertEqual(result['SG-002']['fire_count'], 1)
        self.assertEqual(result['SG-002']['avg_pnl'], D('50'))

    def test_false_signals_not_counted(self):
        trades = [_make_trade(D('10'), {'SG-001': True, 'SG-002': False})]
        result = analyze_signal_effectiveness(trades)
        self.assertIn('SG-001', result)
        self.assertNotIn('SG-002', result)

    def test_all_losers(self):
        trades = [
            _make_trade(D('-10'), {'SG-001': True}),
            _make_trade(D('-30'), {'SG-001': True}),
        ]
        result = analyze_signal_effectiveness(trades)
        self.assertEqual(result['SG-001']['win_count'], 0)
        self.assertEqual(result['SG-001']['win_rate'], D('0'))
        self.assertEqual(result['SG-001']['avg_pnl'], D('-20'))

    def test_win_rate_fraction(self):
        trades = [
            _make_trade(D('10'), {'SG-001': True}),
            _make_trade(D('-5'), {'SG-001': True}),
            _make_trade(D('20'), {'SG-001': True}),
        ]
        result = analyze_signal_effectiveness(trades)
        # 2 wins out of 3
        expected_wr = D('2') / D('3')
        self.assertEqual(result['SG-001']['win_rate'], expected_wr)


# ---------------------------------------------------------------------------
# analyze_signal_correlation
# ---------------------------------------------------------------------------

class SignalCorrelationTest(TestCase):

    def test_empty_trades(self):
        result = analyze_signal_correlation([])
        self.assertEqual(result, {})

    def test_single_signal_returns_empty(self):
        trades = [_make_trade(D('10'), {'SG-001': True})]
        result = analyze_signal_correlation(trades)
        self.assertEqual(result, {})

    def test_co_occurrence(self):
        trades = [
            _make_trade(D('50'), {'SG-001': True, 'SG-002': True}),
            _make_trade(D('30'), {'SG-001': True, 'SG-002': True}),
        ]
        result = analyze_signal_correlation(trades)
        pair = ('SG-001', 'SG-002')
        self.assertIn(pair, result)
        self.assertEqual(result[pair]['co_fire_count'], 2)
        self.assertEqual(result[pair]['co_avg_pnl'], D('40'))

    def test_marginal_contribution(self):
        trades = [
            _make_trade(D('100'), {'SG-001': True, 'SG-002': True}),
            _make_trade(D('-50'), {'SG-001': True, 'SG-002': False}),
            _make_trade(D('20'), {'SG-001': False, 'SG-002': True}),
        ]
        result = analyze_signal_correlation(trades)
        pair = ('SG-001', 'SG-002')
        self.assertEqual(result[pair]['co_fire_count'], 1)
        self.assertEqual(result[pair]['co_avg_pnl'], D('100'))
        self.assertEqual(result[pair]['only_a_count'], 1)
        self.assertEqual(result[pair]['only_a_avg_pnl'], D('-50'))
        self.assertEqual(result[pair]['only_b_count'], 1)
        self.assertEqual(result[pair]['only_b_avg_pnl'], D('20'))
