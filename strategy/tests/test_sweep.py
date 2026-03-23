"""Tests for parameter sweep engine."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from django.test import TestCase

from strategy.engine.sweep import (
    apply_param_combination,
    generate_param_grid,
    run_sweep,
)
from warehouse.models import MigratedCoin, OHLCVCandle

D = Decimal
T0 = datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc)


def _base_config(**overrides):
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


# ---------------------------------------------------------------------------
# generate_param_grid
# ---------------------------------------------------------------------------

class GenerateParamGridTest(TestCase):

    def test_signal_param_ranges(self):
        """SG-001 has param_ranges for threshold — should generate grid."""
        config = _base_config()
        grid = generate_param_grid(config)
        # SG-001 has 5 threshold values: [1.5, 2.0, 3.0, 5.0, 10.0]
        self.assertEqual(len(grid), 5)
        thresholds = [c['SG-001.threshold'] for c in grid]
        self.assertEqual(sorted(thresholds), [1.5, 2.0, 3.0, 5.0, 10.0])

    def test_exit_rule_ranges(self):
        config = _base_config(exit_rules={
            'take_profit_pct': 100,
            'take_profit_pct_range': [50, 100, 200],
            'stop_loss_pct': -50,
            'max_hold_minutes': 1440,
        })
        grid = generate_param_grid(config)
        # 5 SG-001 thresholds × 3 TP values = 15
        self.assertEqual(len(grid), 15)
        tp_values = {c['exit.take_profit_pct'] for c in grid}
        self.assertEqual(tp_values, {50, 100, 200})

    def test_sizing_ranges(self):
        config = _base_config(position_sizing={
            'amount_per_trade': 1.0,
            'max_open_positions': 3,
            'max_open_positions_range': [1, 3, 5],
        })
        grid = generate_param_grid(config)
        # 5 SG-001 thresholds × 3 max_open = 15
        self.assertEqual(len(grid), 15)

    def test_no_ranges_returns_single_empty(self):
        """Config with no sweep ranges produces one empty combination."""
        config = _base_config()
        # Remove SG-001's param_ranges from registry temporarily — easier
        # to just test with a signal that has no ranges
        config['signals'] = [
            {
                'signal_id': 'SG-002',
                'role': 'entry',
                'required': True,
                'param_overrides': {},
                'evaluation': {'mode': 'point_in_time'},
            },
        ]
        grid = generate_param_grid(config)
        self.assertEqual(len(grid), 1)
        self.assertEqual(grid[0], {})

    def test_cartesian_product(self):
        """Multiple axes produce correct cartesian product."""
        config = _base_config(
            exit_rules={
                'take_profit_pct': 100,
                'take_profit_pct_range': [50, 100],
                'stop_loss_pct': -50,
                'stop_loss_pct_range': [-30, -50],
                'max_hold_minutes': 1440,
            },
        )
        grid = generate_param_grid(config)
        # 5 thresholds × 2 TP × 2 SL = 20
        self.assertEqual(len(grid), 20)


# ---------------------------------------------------------------------------
# apply_param_combination
# ---------------------------------------------------------------------------

class ApplyParamCombinationTest(TestCase):

    def test_signal_param_applied(self):
        config = _base_config()
        combo = {'SG-001.threshold': 5.0}
        modified = apply_param_combination(config, combo)
        # Should update param_overrides for SG-001
        for sig in modified['signals']:
            if sig['signal_id'] == 'SG-001':
                self.assertEqual(sig['param_overrides']['threshold'], 5.0)

    def test_exit_param_applied(self):
        config = _base_config()
        combo = {'exit.take_profit_pct': 200}
        modified = apply_param_combination(config, combo)
        self.assertEqual(modified['exit_rules']['take_profit_pct'], 200)

    def test_sizing_param_applied(self):
        config = _base_config()
        combo = {'sizing.max_open_positions': 5}
        modified = apply_param_combination(config, combo)
        self.assertEqual(modified['position_sizing']['max_open_positions'], 5)

    def test_original_not_mutated(self):
        config = _base_config()
        original_tp = config['exit_rules']['take_profit_pct']
        apply_param_combination(config, {'exit.take_profit_pct': 999})
        self.assertEqual(config['exit_rules']['take_profit_pct'], original_tp)


# ---------------------------------------------------------------------------
# run_sweep integration
# ---------------------------------------------------------------------------

class RunSweepTest(TestCase):

    def setUp(self):
        self.coin = MigratedCoin.objects.create(
            mint_address='SWEEP_COIN', anchor_event=T0,
        )
        # 10 candles: flat volume/price — no entries expected
        for i in range(10):
            OHLCVCandle.objects.create(
                coin_id='SWEEP_COIN',
                timestamp=T0 + timedelta(minutes=5 * (i + 1)),
                open_price=D('10'), high_price=D('10'),
                low_price=D('10'), close_price=D('10'),
                volume=D('100'),
            )

    def test_sweep_returns_results_per_combo(self):
        config = _base_config()
        sweep_id, results = run_sweep(
            config, ['SWEEP_COIN'],
            T0, T0 + timedelta(minutes=50),
            sweep_id='test_sweep_001',
        )
        self.assertEqual(sweep_id, 'test_sweep_001')
        grid = generate_param_grid(config)
        self.assertEqual(len(results), len(grid))

    def test_sweep_generates_id_if_not_provided(self):
        config = _base_config()
        config['signals'] = [{
            'signal_id': 'SG-002', 'role': 'entry',
            'required': True, 'param_overrides': {},
            'evaluation': {'mode': 'point_in_time'},
        }]
        sweep_id, results = run_sweep(
            config, ['SWEEP_COIN'],
            T0, T0 + timedelta(minutes=50),
        )
        self.assertTrue(sweep_id.startswith('sweep_'))
        self.assertEqual(len(results), 1)

    def test_each_result_has_sweep_id_and_combination(self):
        config = _base_config()
        config['signals'] = [{
            'signal_id': 'SG-002', 'role': 'entry',
            'required': True, 'param_overrides': {},
            'evaluation': {'mode': 'point_in_time'},
        }]
        sweep_id, results = run_sweep(
            config, ['SWEEP_COIN'],
            T0, T0 + timedelta(minutes=50),
        )
        combo, result = results[0]
        self.assertEqual(result['sweep_id'], sweep_id)
        self.assertIn('combination', result)
