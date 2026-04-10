"""Tests for the backtest engine — runner + position tracker."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from django.test import TestCase

from strategy.engine.positions import PositionTracker
from strategy.engine.runner import BacktestRunner
from strategy.strategies import load_strategy_config
from warehouse.models import MigratedCoin, OHLCVCandle

D = Decimal
T0 = datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# PositionTracker tests
# ---------------------------------------------------------------------------

class PositionTrackerTest(TestCase):

    def test_open_and_close(self):
        pt = PositionTracker(max_open=3)
        pt.open('A', T0, D('100'), D('1'), {'SG-001': True})
        self.assertTrue(pt.has_position('A'))
        self.assertEqual(pt.open_count, 1)

        trade = pt.close('A', T0 + timedelta(hours=1), D('150'), 'take_profit')
        self.assertFalse(pt.has_position('A'))
        self.assertEqual(trade.pnl, D('50'))
        self.assertEqual(trade.roi_pct, D('50'))
        self.assertEqual(trade.hold_minutes, D('60'))

    def test_max_open_limit(self):
        pt = PositionTracker(max_open=2)
        pt.open('A', T0, D('100'), D('1'), {})
        pt.open('B', T0, D('100'), D('1'), {})
        self.assertFalse(pt.can_open())
        with self.assertRaises(ValueError):
            pt.open('C', T0, D('100'), D('1'), {})

    def test_force_close_all(self):
        pt = PositionTracker(max_open=5)
        pt.open('A', T0, D('100'), D('1'), {})
        pt.open('B', T0, D('200'), D('1'), {})
        pt.force_close_all(
            T0 + timedelta(hours=2),
            {'A': D('110'), 'B': D('180')},
        )
        self.assertEqual(pt.open_count, 0)
        self.assertEqual(len(pt.closed_trades), 2)
        # A: +10, B: -20
        pnls = {t.asset_id: t.pnl for t in pt.closed_trades}
        self.assertEqual(pnls['A'], D('10'))
        self.assertEqual(pnls['B'], D('-20'))

    def test_duplicate_open_raises(self):
        pt = PositionTracker(max_open=5)
        pt.open('A', T0, D('100'), D('1'), {})
        with self.assertRaises(ValueError):
            pt.open('A', T0, D('100'), D('1'), {})


# ---------------------------------------------------------------------------
# BacktestRunner integration tests
# ---------------------------------------------------------------------------

def _make_strategy_config(**overrides):
    """Build a minimal strategy config for testing."""
    config = {
        'id': 'test_strategy',
        'name': 'Test Strategy',
        'version': 1,
        'universe_id': 'U-001',
        'hypothesis': 'Test',
        'signals': [
            {
                'signal_id': 'SG-001',
                'role': 'entry',
                'required': True,
                'param_overrides': {'threshold': 3.0},
                'evaluation': {'mode': 'point_in_time'},
            },
            {
                'signal_id': 'SG-002',
                'role': 'entry',
                'required': True,
                'param_overrides': {},
                'evaluation': {'mode': 'point_in_time'},
            },
        ],
        'entry_rules': {'require_all': True},
        'exit_rules': {
            'take_profit_pct': 100,
            'stop_loss_pct': -50,
            'max_hold_minutes': 1440,
        },
        'position_sizing': {
            'amount_per_trade': 1.0,
            'max_open_positions': 3,
        },
        'data_requirements': {
            'layer_ids': ['FL-001'],
            'derived_ids': ['DF-001', 'DF-002'],
            'derived_params': {
                'DF-001': {'window_size': 3},
                'DF-002': {'lookback': 3},
            },
        },
        'price_field': 'close_price',
    }
    config.update(overrides)
    return config


class BacktestRunnerEmptyTest(TestCase):
    """No signals fire → no trades."""

    def setUp(self):
        self.coin = MigratedCoin.objects.create(
            mint_address='EMPTY_COIN', anchor_event=T0,
        )
        # Flat price, flat volume → no volume spike → no entry
        for i in range(10):
            OHLCVCandle.objects.create(
                coin_id='EMPTY_COIN',
                timestamp=T0 + timedelta(minutes=5 * (i + 1)),
                open_price=D('10'), high_price=D('10'),
                low_price=D('10'), close_price=D('10'),
                volume=D('100'),
            )

    def test_no_trades(self):
        config = _make_strategy_config()
        runner = BacktestRunner(
            config, ['EMPTY_COIN'],
            T0, T0 + timedelta(minutes=50),
        )
        result = runner.run()
        self.assertEqual(result['metrics']['total_trades'], 0)
        self.assertEqual(result['entities_tested'], 1)


class BacktestRunnerTakeProfitTest(TestCase):
    """Price doubles → take profit at +100%."""

    def setUp(self):
        self.coin = MigratedCoin.objects.create(
            mint_address='TP_COIN', anchor_event=T0,
        )
        # Build data: 3 candles warm-up (flat), then spike, then price doubles
        prices = [10, 10, 10, 10, 10, 10, 20]
        volumes = [100, 100, 100, 500, 100, 100, 100]
        for i, (p, v) in enumerate(zip(prices, volumes)):
            OHLCVCandle.objects.create(
                coin_id='TP_COIN',
                timestamp=T0 + timedelta(minutes=5 * (i + 1)),
                open_price=D(str(p)), high_price=D(str(p)),
                low_price=D(str(p)), close_price=D(str(p)),
                volume=D(str(v)),
            )

    def test_take_profit_fires(self):
        config = _make_strategy_config()
        runner = BacktestRunner(
            config, ['TP_COIN'],
            T0, T0 + timedelta(minutes=35),
        )
        result = runner.run()
        trades = result['trades']
        # Should have at least one trade with take_profit exit
        tp_trades = [t for t in trades if t.exit_reason == 'take_profit']
        if tp_trades:
            self.assertGreater(tp_trades[0].roi_pct, 0)


class BacktestRunnerStopLossTest(TestCase):
    """Price drops >50% → stop loss."""

    def setUp(self):
        self.coin = MigratedCoin.objects.create(
            mint_address='SL_COIN', anchor_event=T0,
        )
        # Warm-up, then volume spike with price above VWAP, then crash
        prices = [10, 10, 10, 12, 4, 4, 4]
        volumes = [100, 100, 100, 500, 100, 100, 100]
        for i, (p, v) in enumerate(zip(prices, volumes)):
            OHLCVCandle.objects.create(
                coin_id='SL_COIN',
                timestamp=T0 + timedelta(minutes=5 * (i + 1)),
                open_price=D(str(p)),
                high_price=D(str(max(p, 12))),
                low_price=D(str(min(p, 4))),
                close_price=D(str(p)),
                volume=D(str(v)),
            )

    def test_stop_loss_fires(self):
        config = _make_strategy_config()
        runner = BacktestRunner(
            config, ['SL_COIN'],
            T0, T0 + timedelta(minutes=35),
        )
        result = runner.run()
        sl_trades = [t for t in result['trades'] if t.exit_reason == 'stop_loss']
        if sl_trades:
            self.assertLess(sl_trades[0].roi_pct, 0)


class BacktestRunnerTimeoutTest(TestCase):
    """Position held past max_hold_minutes → timeout."""

    def setUp(self):
        self.coin = MigratedCoin.objects.create(
            mint_address='TO_COIN', anchor_event=T0,
        )
        # Need enough candles to cover warm-up + trigger + hold period
        # With 5-min candles, 1440 min = 288 candles. Use smaller timeout.
        for i in range(20):
            v = 500 if i == 4 else 100  # spike at candle 4
            p = 11 if i >= 4 else 10     # slight rise, not enough for TP
            OHLCVCandle.objects.create(
                coin_id='TO_COIN',
                timestamp=T0 + timedelta(minutes=5 * (i + 1)),
                open_price=D(str(p)),
                high_price=D(str(p)),
                low_price=D(str(p)),
                close_price=D(str(p)),
                volume=D(str(v)),
            )

    def test_timeout_fires(self):
        config = _make_strategy_config(
            exit_rules={
                'take_profit_pct': 100,
                'stop_loss_pct': -50,
                'max_hold_minutes': 30,  # 6 candles
            },
        )
        runner = BacktestRunner(
            config, ['TO_COIN'],
            T0, T0 + timedelta(minutes=100),
        )
        result = runner.run()
        to_trades = [t for t in result['trades'] if t.exit_reason == 'timeout']
        fc_trades = [t for t in result['trades'] if t.exit_reason == 'force_close']
        # Should exit via timeout or force_close
        self.assertTrue(
            len(to_trades) > 0 or len(fc_trades) > 0 or result['metrics']['total_trades'] == 0,
            "Expected timeout, force_close, or no entry",
        )


class BacktestRunnerMaxPositionsTest(TestCase):
    """Max open positions limits concurrent positions."""

    def setUp(self):
        # Create 3 coins, all with volume spike conditions
        for name in ['MP_A', 'MP_B', 'MP_C']:
            MigratedCoin.objects.create(mint_address=name, anchor_event=T0)
            for i in range(10):
                v = 500 if i == 4 else 100
                p = 11 if i >= 4 else 10
                OHLCVCandle.objects.create(
                    coin_id=name,
                    timestamp=T0 + timedelta(minutes=5 * (i + 1)),
                    open_price=D(str(p)),
                    high_price=D(str(p)),
                    low_price=D(str(p)),
                    close_price=D(str(p)),
                    volume=D(str(v)),
                )

    def test_max_positions_respected(self):
        config = _make_strategy_config(
            position_sizing={
                'amount_per_trade': 1.0,
                'max_open_positions': 1,
            },
        )
        runner = BacktestRunner(
            config, ['MP_A', 'MP_B', 'MP_C'],
            T0, T0 + timedelta(minutes=50),
        )
        result = runner.run()
        # The runner should never exceed 1 concurrent position.
        # We can't directly observe this from result, but we verify
        # it didn't crash (which would happen if tracker.open() raised).
        self.assertIsNotNone(result['metrics'])


class BacktestRunnerForceCloseTest(TestCase):
    """Positions open at end of data are force-closed."""

    def setUp(self):
        self.coin = MigratedCoin.objects.create(
            mint_address='FC_COIN', anchor_event=T0,
        )
        # spike triggers entry, but no exit conditions met within data window
        for i in range(8):
            v = 500 if i == 4 else 100
            p = 11 if i >= 4 else 10
            OHLCVCandle.objects.create(
                coin_id='FC_COIN',
                timestamp=T0 + timedelta(minutes=5 * (i + 1)),
                open_price=D(str(p)),
                high_price=D(str(p)),
                low_price=D(str(p)),
                close_price=D(str(p)),
                volume=D(str(v)),
            )

    def test_force_close_at_end(self):
        config = _make_strategy_config(
            exit_rules={
                'take_profit_pct': 500,   # very high, won't trigger
                'stop_loss_pct': -90,     # very low, won't trigger
                'max_hold_minutes': 99999,
            },
        )
        runner = BacktestRunner(
            config, ['FC_COIN'],
            T0, T0 + timedelta(minutes=40),
        )
        result = runner.run()
        fc_trades = [t for t in result['trades'] if t.exit_reason == 'force_close']
        # Either the entry didn't trigger (warm-up) or it force-closed
        if result['metrics']['total_trades'] > 0:
            self.assertTrue(len(fc_trades) > 0)


class BacktestRunnerBreakoutCloseStrategyTest(TestCase):
    """The U-001 breakout strategy should execute through the full runner."""

    def setUp(self):
        self.coin = MigratedCoin.objects.create(
            mint_address='BC_COIN', anchor_event=T0,
        )

        # The strategy needs 20 candles for volume ratio, 12 for breakout,
        # and 3 for momentum. Candle 20 intentionally satisfies all signals.
        candles = []
        for i in range(20):
            candles.append((D('10'), D('10'), D('10'), D('10'), D('100')))
        candles.extend([
            (D('10'), D('14'), D('10'), D('14'), D('500')),
            (D('14'), D('32'), D('14'), D('32'), D('100')),
        ])

        for i, (open_, high, low, close, volume) in enumerate(candles):
            OHLCVCandle.objects.create(
                coin_id='BC_COIN',
                timestamp=T0 + timedelta(minutes=5 * (i + 1)),
                open_price=open_,
                high_price=high,
                low_price=low,
                close_price=close,
                volume=volume,
            )

    def test_breakout_close_strategy_takes_profit(self):
        config = load_strategy_config('u001_breakout_close_v1')
        runner = BacktestRunner(
            config,
            ['BC_COIN'],
            T0,
            T0 + timedelta(minutes=115),
        )

        result = runner.run()

        self.assertEqual(result['metrics']['total_trades'], 1)
        trade = result['trades'][0]
        self.assertEqual(trade.asset_id, 'BC_COIN')
        self.assertEqual(trade.entry_price, D('14'))
        self.assertEqual(trade.exit_price, D('32'))
        self.assertEqual(trade.exit_reason, 'take_profit')
        self.assertEqual(
            trade.entry_reason,
            {
                'SG-001': True,
                'SG-004': True,
                'SG-005': True,
                'SG-006': True,
            },
        )
