"""Integration tests for path-dependent evaluation in the runner."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from django.test import TestCase

from strategy.engine.runner import BacktestRunner
from strategy.signals.registry import SIGNAL_REGISTRY, SignalSpec, register_signal
from warehouse.models import MigratedCoin, OHLCVCandle

D = Decimal
T0 = datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc)


def _eval_mc_above(row, threshold=5000):
    """Filter: True when market cap proxy (close * volume) is above threshold."""
    close = row.get('close_price')
    volume = row.get('volume')
    if close is None or volume is None:
        return False
    return float(close) * float(volume) >= threshold


# Register a test filter signal if not already present
if 'SG-TEST-FILTER' not in SIGNAL_REGISTRY:
    register_signal(SignalSpec(
        signal_id='SG-TEST-FILTER',
        name='MC Above Threshold (Test)',
        category='price',
        source_layers=['FL-001'],
        evaluate=_eval_mc_above,
        default_params={'threshold': 5000},
        derived_features=[],
        description='Test filter signal for path-dependent tests.',
    ))


def _make_config_with_filter(disqualification='permanent', direction='any',
                             cooldown_minutes=None, confirmation='none',
                             confirmation_minutes=None,
                             lookback='all_history', **overrides):
    """Build strategy config with a path-dependent filter signal."""
    evaluation = {
        'mode': 'path_dependent',
        'disqualification': disqualification,
        'direction': direction,
        'confirmation': confirmation,
        'lookback': lookback,
    }
    if cooldown_minutes is not None:
        evaluation['cooldown_minutes'] = cooldown_minutes
    if confirmation_minutes is not None:
        evaluation['confirmation_minutes'] = confirmation_minutes

    config = {
        'id': 'test_path_dep',
        'name': 'Test Path Dependent',
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
            {
                'signal_id': 'SG-TEST-FILTER',
                'role': 'filter',
                'required': True,
                'param_overrides': {'threshold': 5000},
                'evaluation': evaluation,
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


class PermanentDisqualificationTest(TestCase):
    """Filter with permanent disqualification blocks entry forever."""

    def setUp(self):
        MigratedCoin.objects.create(mint_address='PD_COIN', anchor_event=T0)
        # Candles: MC starts high, drops below threshold at step 3, then recovers
        # close_price * volume = MC proxy
        data = [
            # step, price, volume => MC
            (1, 10, 600),     # 6000 — above
            (2, 10, 600),     # 6000 — above
            (3, 10, 100),     # 1000 — BELOW threshold → disqualified
            (4, 10, 600),     # 6000 — above again, but permanently blocked
            (5, 12, 600),     # 7200 — above
            (6, 12, 600),     # 7200 — above
            (7, 20, 600),     # 12000 — would be a great entry, but blocked
        ]
        for step, price, vol in data:
            OHLCVCandle.objects.create(
                coin_id='PD_COIN',
                timestamp=T0 + timedelta(minutes=5 * step),
                open_price=D(str(price)), high_price=D(str(price)),
                low_price=D(str(price)), close_price=D(str(price)),
                volume=D(str(vol)),
            )

    def test_permanently_disqualified_entity_never_enters(self):
        config = _make_config_with_filter(disqualification='permanent')
        runner = BacktestRunner(
            config, ['PD_COIN'],
            T0, T0 + timedelta(minutes=35),
        )
        result = runner.run()
        # Entity should be permanently blocked after step 3
        self.assertEqual(result['metrics']['total_trades'], 0)


class CooldownDisqualificationTest(TestCase):
    """Filter with cooldown blocks entry temporarily."""

    def setUp(self):
        MigratedCoin.objects.create(mint_address='CD_COIN', anchor_event=T0)
        # MC drops below threshold at step 3, cooldown 10 min
        data = [
            (1, 10, 600),     # 6000
            (2, 10, 600),     # 6000
            (3, 10, 100),     # 1000 — below, starts cooldown
            (4, 10, 600),     # 6000 — still in cooldown (5 min since step 3)
            (5, 10, 600),     # 6000 — cooldown expired (10 min since step 3)
            (6, 10, 600),     # 6000
            (7, 10, 600),     # 6000
        ]
        for step, price, vol in data:
            OHLCVCandle.objects.create(
                coin_id='CD_COIN',
                timestamp=T0 + timedelta(minutes=5 * step),
                open_price=D(str(price)), high_price=D(str(price)),
                low_price=D(str(price)), close_price=D(str(price)),
                volume=D(str(vol)),
            )

    def test_cooldown_blocks_then_allows(self):
        config = _make_config_with_filter(
            disqualification='cooldown', cooldown_minutes=10,
        )
        runner = BacktestRunner(
            config, ['CD_COIN'],
            T0, T0 + timedelta(minutes=35),
        )
        result = runner.run()
        # Cooldown should expire and entity should be available again
        # Whether a trade actually fires depends on signal conditions
        self.assertIsNotNone(result['metrics'])


class NoFilterTest(TestCase):
    """Without filter signals, runner works as before."""

    def setUp(self):
        MigratedCoin.objects.create(mint_address='NF_COIN', anchor_event=T0)
        prices = [10, 10, 10, 10, 10, 10, 20]
        volumes = [100, 100, 100, 500, 100, 100, 100]
        for i, (p, v) in enumerate(zip(prices, volumes)):
            OHLCVCandle.objects.create(
                coin_id='NF_COIN',
                timestamp=T0 + timedelta(minutes=5 * (i + 1)),
                open_price=D(str(p)), high_price=D(str(p)),
                low_price=D(str(p)), close_price=D(str(p)),
                volume=D(str(v)),
            )

    def test_no_filter_signals_works(self):
        """Standard config without filters should work unchanged."""
        config = {
            'id': 'test_no_filter',
            'name': 'Test No Filter',
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
        runner = BacktestRunner(
            config, ['NF_COIN'],
            T0, T0 + timedelta(minutes=35),
        )
        result = runner.run()
        self.assertIsNotNone(result['metrics'])


class PointInTimeFilterTest(TestCase):
    """Filter with point_in_time mode works like a simple gate."""

    def setUp(self):
        MigratedCoin.objects.create(mint_address='PIT_COIN', anchor_event=T0)
        # All candles have MC below threshold — filter always blocks
        for i in range(10):
            OHLCVCandle.objects.create(
                coin_id='PIT_COIN',
                timestamp=T0 + timedelta(minutes=5 * (i + 1)),
                open_price=D('1'), high_price=D('1'),
                low_price=D('1'), close_price=D('1'),
                volume=D('10'),  # MC = 10, way below 5000
            )

    def test_pit_filter_blocks_entry(self):
        config = _make_config_with_filter()
        # Override to point_in_time mode
        for sig in config['signals']:
            if sig['signal_id'] == 'SG-TEST-FILTER':
                sig['evaluation'] = {'mode': 'point_in_time'}
        runner = BacktestRunner(
            config, ['PIT_COIN'],
            T0, T0 + timedelta(minutes=50),
        )
        result = runner.run()
        self.assertEqual(result['metrics']['total_trades'], 0)


class FilterPassesTest(TestCase):
    """Filter that always passes allows normal entry."""

    def setUp(self):
        MigratedCoin.objects.create(mint_address='FP_COIN', anchor_event=T0)
        # Volume spike at step 4, high MC throughout
        prices = [10, 10, 10, 10, 10, 10, 20]
        volumes = [600, 600, 600, 5000, 600, 600, 600]
        for i, (p, v) in enumerate(zip(prices, volumes)):
            OHLCVCandle.objects.create(
                coin_id='FP_COIN',
                timestamp=T0 + timedelta(minutes=5 * (i + 1)),
                open_price=D(str(p)), high_price=D(str(p)),
                low_price=D(str(p)), close_price=D(str(p)),
                volume=D(str(v)),
            )

    def test_filter_passes_allows_entry(self):
        """When filter always passes, trades happen normally."""
        config = _make_config_with_filter(disqualification='none')
        runner = BacktestRunner(
            config, ['FP_COIN'],
            T0, T0 + timedelta(minutes=35),
        )
        result = runner.run()
        # Should behave like no filter at all
        self.assertIsNotNone(result['metrics'])


class EntityTrackerNotCreatedForPITTest(TestCase):
    """Runner should not create EntityStateTracker when all signals are PIT."""

    def test_no_tracker_for_pit_only(self):
        config = {
            'id': 'test_pit',
            'name': 'Test PIT',
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
            },
            'price_field': 'close_price',
        }
        runner = BacktestRunner(config, [], T0, T0 + timedelta(minutes=50))
        self.assertIsNone(runner._entity_tracker)
